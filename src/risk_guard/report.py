"""Daily health report.

Sent every trading day *even when nothing happened*. If the report only arrived when
something went wrong, silence would be ambiguous (quiet day, or dead process?). Because it
always arrives, its absence is itself the alarm.
"""

from __future__ import annotations

import os
import smtplib
from datetime import date
from email.message import EmailMessage

from .broker import Broker
from .guardian import run_guard


def build_report(broker: Broker, today: date | None = None) -> tuple[str, str]:
    """Return (subject, body). Read-only: never places or cancels anything."""
    today = today or date.today()
    acct = broker.account()
    guard = run_guard(broker, repair=False)
    positions = broker.positions()

    flag = "" if guard.all_protected else "[ACTION NEEDED] "
    subject = f"{flag}risk-guard {today.isoformat()}: NAV ${acct.net_liquidation:,.2f}, {guard.summary()}"

    leverage = acct.gross_position_value / acct.net_liquidation if acct.net_liquidation else 0.0
    lines = [
        f"Account     {acct.account_id} (paper)",
        f"NAV         ${acct.net_liquidation:,.2f}",
        f"Gross       ${acct.gross_position_value:,.2f}  ({leverage:.2f}x)",
        f"Positions   {len(positions)}",
        "",
        "Protection",
    ]
    if not guard.findings:
        lines.append("  (no open positions)")
    for f in guard.findings:
        mark = "ok " if f.ok else "!! "
        lines.append(f"  {mark}{f.symbol:<6} {f.status.value:<15} {f.detail}")
    lines += ["", "This report is sent every trading day. If it stops arriving, investigate."]
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    """SMTP settings come from env vars only. Nothing secret lives in the code."""
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    to = os.environ.get("REPORT_TO", user)

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
