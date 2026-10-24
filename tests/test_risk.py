import pytest

from risk_guard.config import RiskConfig
from risk_guard.models import Direction, Position, TradeIdea
from risk_guard.risk import check_friction, check_portfolio_caps, open_risk_usd, size_position

CFG = RiskConfig()


def idea(entry, stop, target=None, d=Direction.LONG):
    target = target if target is not None else entry + 2 * abs(entry - stop)
    return TradeIdea("TEST", d, entry, stop, target)


class TestSizing:
    def test_one_percent_of_nav(self):
        s = size_position(idea(100, 95), nav=10_000, cfg=CFG)
        assert s.quantity == 15  # $100 budget / $5 per share = 20, capped by 15% -> 15
        assert s.notional_usd <= 1_500

    def test_risk_budget_binds_when_concentration_allows(self):
        s = size_position(idea(50, 45), nav=10_000, cfg=CFG)
        assert s.quantity == 20
        assert s.risk_usd == pytest.approx(100.0)

    def test_single_share_within_tolerance_is_allowed(self):
        s = size_position(idea(150, 30), nav=10_000, cfg=CFG)  # $120 risk vs $100 budget
        assert s.quantity == 1

    def test_single_share_above_tolerance_is_skipped_not_forced(self):
        s = size_position(idea(400, 200), nav=10_000, cfg=CFG)  # $200 risk = 2x budget
        assert s.quantity == 0
        assert "budget" in s.reason

    def test_share_price_above_concentration_cap_is_skipped(self):
        # The GWW case: one share at $1,349 on a ~$5.6k account is 24% of NAV.
        s = size_position(idea(1349, 1301), nav=5_600, cfg=CFG)
        assert s.quantity == 0
        assert "concentration" in s.reason

    @pytest.mark.parametrize("nav", [0, -1])
    def test_bad_nav(self, nav):
        assert size_position(idea(100, 95), nav=nav, cfg=CFG).quantity == 0


class TestPortfolioCaps:
    def test_unknown_state_rejects(self):
        ok, msg = check_portfolio_caps(50, 500, 10_000, None, 0, CFG)
        assert not ok and "unknown" in msg
        ok, _ = check_portfolio_caps(50, 500, 10_000, 0, None, CFG)
        assert not ok

    def test_risk_cap(self):
        ok, msg = check_portfolio_caps(100, 500, 10_000, 750, 0, CFG)
        assert not ok and "portfolio risk" in msg

    def test_gross_cap(self):
        ok, msg = check_portfolio_caps(10, 1_500, 10_000, 0, 19_000, CFG)
        assert not ok and "gross exposure" in msg

    def test_passes(self):
        ok, _ = check_portfolio_caps(100, 1_000, 10_000, 200, 5_000, CFG)
        assert ok


def test_open_risk_flags_positions_without_stop():
    positions = [Position("A", 10, 100), Position("B", -5, 50)]
    total, missing = open_risk_usd(positions, {"A": 95})
    assert total == 50
    assert missing == ["B"]


def test_open_risk_ignores_stops_in_profit():
    positions = [Position("A", 10, 100), Position("S", -10, 50)]
    total, _ = open_risk_usd(positions, {"A": 105, "S": 52})
    assert total == 20  # long stop above cost = 0; short stop $2 above cost x 10


class TestFriction:
    def test_small_trade_rejected(self):
        ok, msg = check_friction(risk_usd=5, quantity=10, spread_usd=None, cfg=CFG)
        assert not ok and "friction" in msg

    def test_large_trade_ok(self):
        ok, _ = check_friction(risk_usd=100, quantity=20, spread_usd=0.02, cfg=CFG)
        assert ok

    def test_wide_spread_rejected(self):
        ok, _ = check_friction(risk_usd=100, quantity=200, spread_usd=0.20, cfg=CFG)
        assert not ok

    def test_missing_spread_is_flagged_as_lower_bound(self):
        _, msg = check_friction(risk_usd=100, quantity=20, spread_usd=None, cfg=CFG)
        assert "lower bound" in msg


def test_open_risk_ignores_zero_quantity_positions():
    positions = [Position("A", 10, 100), Position("CLOSED", 0, 50)]
    total, missing = open_risk_usd(positions, {"A": 95})
    assert total == 50
    assert missing == []


def test_open_risk_case_insensitive():
    positions = [Position("aapl", 10, 100)]
    total, missing = open_risk_usd(positions, {"AAPL": 95})
    assert total == 50
    assert missing == []


def test_config_validation():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=0)
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=0.1, max_portfolio_risk_pct=0.05)


def test_portfolio_caps_non_positive_nav():
    ok, msg = check_portfolio_caps(100, 1_000, 0, 0, 0, CFG)
    assert not ok and "NAV must be positive" in msg
    ok, msg = check_portfolio_caps(100, 1_000, -500, 0, 0, CFG)
    assert not ok and "NAV must be positive" in msg


def test_friction_zero_expected_edge():
    cfg = RiskConfig(expected_edge_r=0.0)
    ok, msg = check_friction(risk_usd=100, quantity=10, spread_usd=0.01, cfg=cfg)
    assert not ok and "expected edge must be positive" in msg


def test_sizing_non_positive_entry():
    s = size_position(TradeIdea("A", Direction.LONG, 0.0, -1.0, 1.0), nav=10_000, cfg=CFG)
    assert s.quantity == 0
    assert "entry price" in s.reason
