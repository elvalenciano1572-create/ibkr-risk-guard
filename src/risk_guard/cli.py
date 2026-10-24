"""Command line entry point: `risk-guard {demo,check,guard,report}`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import LiveTradingRefused, RiskConfig
from .models import Direction, TradeIdea


def _broker():
    from .ibkr import IBKRBroker

    return IBKRBroker().connect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk-guard", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="offline walkthrough on a fake broker")

    chk = sub.add_parser("check", help="run pre-trade checks for one idea (paper account)")
    chk.add_argument("symbol")
    chk.add_argument("direction", choices=[d.value for d in Direction])
    chk.add_argument("entry", type=float)
    chk.add_argument("stop", type=float)
    chk.add_argument("target", type=float)
    chk.add_argument("--send", action="store_true", help="actually send the bracket (paper only)")

    sub.add_parser("guard", help="audit protection on every open position (read-only)")

    rep = sub.add_parser("report", help="build the daily report")
    rep.add_argument("--send", action="store_true", help="email it (needs SMTP_* env vars)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        if args.cmd == "demo":
            from .demo import main as demo

            demo()
            return 0

        broker = _broker()
        try:
            if args.cmd == "check":
                from .executor import execute

                idea = TradeIdea(
                    args.symbol.upper(), Direction(args.direction), args.entry, args.stop, args.target
                )
                d = execute(idea, broker, RiskConfig(), dry_run=not args.send)
                print(("APPROVED: " if d.approved else "REJECTED: ") + d.reason)
                for c in d.checks:
                    print(f"  - {c}")
                return 0 if d.approved else 2
            if args.cmd == "guard":
                from .guardian import run_guard

                r = run_guard(broker)
                print(r.summary())
                for f in r.findings:
                    print(f"  {f.symbol:<6} {f.status.value:<15} {f.detail}")
                return 0 if r.all_protected else 1
            if args.cmd == "report":
                from .report import build_report, send_email

                subject, body = build_report(broker)
                if args.send:
                    send_email(subject, body)
                    print(f"sent: {subject}")
                else:
                    print(f"Subject: {subject}\n\n{body}")
                return 0
        finally:
            broker.close()
    except LiveTradingRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
