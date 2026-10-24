import pytest

from risk_guard.broker import FakeBroker
from risk_guard.config import LiveTradingRefused, RiskConfig
from risk_guard.executor import evaluate, execute
from risk_guard.guardian import Status, run_guard
from risk_guard.models import Direction, OpenOrder, TradeIdea
from risk_guard.report import build_report

CFG = RiskConfig()
AAPL = TradeIdea("AAPL", Direction.LONG, 190.0, 185.0, 200.0)


@pytest.fixture
def broker():
    return FakeBroker(nav=10_000, prices={"AAPL": 190.0, "XYZ": 45.94}, spreads={"AAPL": 0.02})


def protected(b, sym, qty, stop, target, action="SELL"):
    b.add_position(sym, qty if action == "SELL" else -qty, (stop + target) / 2)
    b.add_order(OpenOrder(sym, action, "STP", qty, stop, 900 + len(b.open_orders())))
    b.add_order(OpenOrder(sym, action, "LMT", qty, target, 950 + len(b.open_orders())))


class TestExecutor:
    def test_approves_and_dry_run_sends_nothing(self, broker):
        d = execute(AAPL, broker, CFG, dry_run=True)
        assert d.approved and d.quantity == 7  # 15% cap: $1,500 / $190
        assert broker.placed == []

    def test_send_places_oca_linked_bracket(self, broker):
        d = execute(AAPL, broker, CFG, dry_run=False)
        assert d.approved
        [plan] = broker.placed
        exits = [o for o in broker.open_orders() if o.order_type in ("STP", "LMT") and o.action == "SELL"]
        assert len(exits) == 2
        assert {o.oca_group for o in exits} == {plan.oca_group}
        assert plan.oca_group.startswith("rg-AAPL-")

    def test_refuses_live_account(self, broker):
        broker.account_id = "U7654321"
        with pytest.raises(LiveTradingRefused):
            evaluate(AAPL, broker, CFG)

    def test_duplicate_position(self, broker):
        protected(broker, "AAPL", 5, 180, 210)
        d = evaluate(AAPL, broker, CFG)
        assert not d.approved and "duplicate" in d.reason

    def test_duplicate_working_order(self, broker):
        broker.add_order(OpenOrder("AAPL", "BUY", "LMT", 5, 189.0, 1))
        assert "working orders" in evaluate(AAPL, broker, CFG).reason

    def test_unstopped_position_blocks_new_risk(self, broker):
        broker.add_position("XYZ", 10, 44.0)
        d = evaluate(AAPL, broker, CFG)
        assert not d.approved and "unbounded" in d.reason

    def test_portfolio_risk_cap(self, broker):
        for i, sym in enumerate(["A", "B", "C", "D"]):
            protected(broker, sym, 10, 80.0, 120.0)  # $200 risk each = $800 = 8%
            broker.prices[sym] = 100.0 + i
        d = evaluate(AAPL, broker, CFG)
        assert not d.approved and "portfolio risk" in d.reason

    def test_market_incoherent_levels(self, broker):
        broker.prices["AAPL"] = 184.0  # already below the stop
        d = evaluate(AAPL, broker, CFG)
        assert not d.approved and "would fire now" in d.reason

    def test_invalid_idea(self, broker):
        bad = TradeIdea("AAPL", Direction.LONG, 190, 195, 200)
        assert "invalid" in evaluate(bad, broker, CFG).reason

    def test_friction(self, broker):
        broker.prices["PENNY"] = 5.0
        tiny = TradeIdea("PENNY", Direction.LONG, 5.0, 4.98, 5.10)
        assert "friction" in evaluate(tiny, broker, CFG).reason


class TestGuardian:
    def test_all_protected(self, broker):
        protected(broker, "AAPL", 5, 180, 210)
        r = run_guard(broker)
        assert r.all_protected and r.summary() == "all 1 positions protected"

    def test_detects_each_failure_mode(self, broker):
        broker.add_position("NONE", 10, 50)
        broker.add_position("NOSTP", 10, 50)
        broker.add_order(OpenOrder("NOSTP", "SELL", "LMT", 10, 60, 1))
        broker.add_position("NOTGT", 10, 50)
        broker.add_order(OpenOrder("NOTGT", "SELL", "STP", 10, 45, 2))
        broker.add_position("SPLIT", 18, 44)
        broker.add_order(OpenOrder("SPLIT", "SELL", "STP", 9, 88, 3))
        broker.add_order(OpenOrder("SPLIT", "SELL", "LMT", 9, 96, 4))
        status = {f.symbol: f.status for f in run_guard(broker).findings}
        assert status == {
            "NONE": Status.UNPROTECTED,
            "NOSTP": Status.MISSING_STOP,
            "NOTGT": Status.MISSING_TARGET,
            "SPLIT": Status.QTY_MISMATCH,
        }

    def test_live_stop_above_market_is_flagged(self, broker):
        broker.add_position("XYZ", 18, 44)
        broker.add_order(OpenOrder("XYZ", "SELL", "STP", 18, 88.06, 3))
        broker.add_order(OpenOrder("XYZ", "SELL", "LMT", 18, 96.0, 4))
        assert run_guard(broker).findings[0].status is Status.INCOHERENT_STOP

    def test_wrong_side_exits_do_not_count(self, broker):
        broker.add_position("AAPL", 5, 190)
        broker.add_order(OpenOrder("AAPL", "BUY", "STP", 5, 180, 1))
        assert run_guard(broker).findings[0].status is Status.UNPROTECTED

    def test_repair_refuses_stale_pre_split_levels(self, broker):
        broker.add_position("XYZ", 18, 44)
        broker.add_order(OpenOrder("XYZ", "SELL", "STP", 9, 88.06, 3))
        [f] = run_guard(broker, {"XYZ": (88.06, 96.0)}, repair=True).findings
        assert f.action.startswith("NOT repaired")
        assert broker.cancelled == []  # the existing (wrong) order is left for a human

    def test_repair_replaces_with_coherent_levels(self, broker):
        broker.add_position("XYZ", 18, 44)
        broker.add_order(OpenOrder("XYZ", "SELL", "STP", 9, 40.0, 3))
        [f] = run_guard(broker, {"XYZ": (41.0, 50.0)}, repair=True).findings
        assert f.action.startswith("repaired")
        assert broker.cancelled == [3]
        assert run_guard(broker).all_protected

    def test_repair_without_levels_is_manual(self, broker):
        broker.add_position("XYZ", 18, 44)
        [f] = run_guard(broker, repair=True).findings
        assert f.action.startswith("manual")

    def test_short_position(self, broker):
        protected(broker, "XYZ", 10, 50.0, 40.0, action="BUY")
        assert run_guard(broker).all_protected


class TestReport:
    def test_report_always_has_content(self, broker):
        subject, body = build_report(broker)
        assert "no open positions" in subject and "paper" in body

    def test_report_flags_problems(self, broker):
        broker.add_position("XYZ", 10, 44)
        subject, body = build_report(broker)
        assert subject.startswith("[ACTION NEEDED]")
        assert "UNPROTECTED" in body


def test_demo_runs(capsys):
    from risk_guard.demo import main

    main()
    out = capsys.readouterr().out
    assert "APPROVED" in out and "NOT repaired" in out


def test_cli_demo():
    from risk_guard.cli import main

    assert main(["demo"]) == 0
