"""Offline walkthrough on a FakeBroker: no TWS, no network, no account needed."""

from __future__ import annotations

from .broker import FakeBroker
from .config import RiskConfig
from .executor import execute
from .guardian import run_guard
from .models import Direction, OpenOrder, TradeIdea
from .report import build_report


def _print_decision(title: str, decision) -> None:
    verdict = "APPROVED" if decision.approved else "REJECTED"
    print(f"\n== {title}: {verdict} - {decision.reason}")
    for c in decision.checks:
        print(f"   - {c}")


def main() -> None:
    cfg = RiskConfig()
    broker = FakeBroker(
        nav=10_000,
        prices={"AAPL": 190.0, "MSFT": 410.0, "NVDA": 120.0, "XYZ": 45.9, "CHEAP": 5.0},
        spreads={"AAPL": 0.02, "MSFT": 0.05, "NVDA": 0.02, "CHEAP": 0.03},
    )

    # Existing book: one healthy position, one hit by a 2:1 split (qty doubled, stale levels).
    broker.add_position("MSFT", 3, 400.0)
    broker.add_order(OpenOrder("MSFT", "SELL", "STP", 3, 380.0, 1))
    broker.add_order(OpenOrder("MSFT", "SELL", "LMT", 3, 440.0, 2))
    broker.add_position("XYZ", 18, 44.0)
    broker.add_order(OpenOrder("XYZ", "SELL", "STP", 9, 88.06, 3))

    print("ibkr-risk-guard demo (FakeBroker, paper account DU0000001)")

    ideas = [
        ("Normal long", TradeIdea("AAPL", Direction.LONG, 190.0, 185.0, 200.0)),
        ("Duplicate", TradeIdea("MSFT", Direction.LONG, 410.0, 400.0, 430.0)),
        ("Tiny risk (friction)", TradeIdea("CHEAP", Direction.LONG, 5.0, 4.98, 5.10)),
        ("Broken levels", TradeIdea("NVDA", Direction.LONG, 120.0, 125.0, 130.0)),
    ]
    for title, idea in ideas:
        _print_decision(title, execute(idea, broker, cfg, dry_run=True))

    print("\n== Guardian (repair enabled, stored levels are pre-split for XYZ)")
    report = run_guard(broker, {"XYZ": (88.06, 96.0)}, repair=True)
    for f in report.findings:
        print(f"   {f.symbol:<5} {f.status.value:<14} {f.detail} -> {f.action}")

    subject, body = build_report(broker)
    print(f"\n== Daily report (dry run)\nSubject: {subject}\n{body}")


if __name__ == "__main__":
    main()
