"""Protection guardian: every open position must have a live stop AND a live target whose
quantity matches the position.

Failure modes it catches, all observed in production on the original system:
  * protection cancelled by the broker after a corporate action (split)
  * quantity mismatch after a partial fill or a split (protection covers 9, position is 18)
  * a stop with no target, or the reverse
  * exits on the wrong side (e.g. BUY exits on a long)

Repairs are opt-in. Stored levels are only re-used after the market-coherence check, so a
stale pre-split stop is reported instead of being placed and firing instantly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from .broker import Broker
from .config import assert_paper_account
from .levels import levels_coherent
from .models import Direction, OpenOrder, Position


class Status(str, Enum):
    OK = "OK"
    MISSING_STOP = "MISSING_STOP"
    MISSING_TARGET = "MISSING_TARGET"
    UNPROTECTED = "UNPROTECTED"
    QTY_MISMATCH = "QTY_MISMATCH"
    INCOHERENT_STOP = "INCOHERENT_STOP"


@dataclass
class Finding:
    symbol: str
    status: Status
    detail: str
    action: str = "none"

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


@dataclass
class GuardReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def all_protected(self) -> bool:
        return all(f.ok for f in self.findings)

    def summary(self) -> str:
        bad = [f for f in self.findings if not f.ok]
        if not self.findings:
            return "no open positions"
        if not bad:
            return f"all {len(self.findings)} positions protected"
        return f"{len(bad)} of {len(self.findings)} positions need attention"


def _exits_for(position: Position, orders: list[OpenOrder]) -> tuple[list[OpenOrder], list[OpenOrder]]:
    action = position.direction.exit_action
    mine = [o for o in orders if o.symbol == position.symbol and o.action == action]
    return [o for o in mine if o.order_type == "STP"], [o for o in mine if o.order_type == "LMT"]


def inspect_position(position: Position, orders: list[OpenOrder], price: float | None = None) -> Finding:
    stops, targets = _exits_for(position, orders)
    qty = abs(position.quantity)
    if not stops and not targets:
        return Finding(position.symbol, Status.UNPROTECTED, f"{qty} shares with no exit orders")
    if stops and price:
        # A live stop on the wrong side of the market is the most dangerous state of all.
        far = Direction.LONG if position.quantity > 0 else Direction.SHORT
        dummy_target = price * (2 if far is Direction.LONG else 0.5)
        ok, why = levels_coherent(far, stops[0].price, dummy_target, price)
        if not ok:
            return Finding(position.symbol, Status.INCOHERENT_STOP, why)
    if not stops:
        return Finding(position.symbol, Status.MISSING_STOP, "target present but no stop")
    if not targets:
        return Finding(position.symbol, Status.MISSING_TARGET, "stop present but no target")
    stop_qty = sum(o.quantity for o in stops)
    target_qty = sum(o.quantity for o in targets)
    if stop_qty != qty or target_qty != qty:
        return Finding(
            position.symbol,
            Status.QTY_MISMATCH,
            f"position {qty}, stop covers {stop_qty}, target covers {target_qty}",
        )
    return Finding(position.symbol, Status.OK, f"{qty} shares, stop and target live")


def run_guard(
    broker: Broker,
    stored_levels: dict[str, tuple[float, float]] | None = None,
    *,
    repair: bool = False,
) -> GuardReport:
    """Inspect every position; optionally repair using `stored_levels[symbol] = (stop, target)`."""
    assert_paper_account(broker.account().account_id)
    stored_levels = stored_levels or {}
    orders = broker.open_orders()
    report = GuardReport()

    for pos in broker.positions():
        if pos.quantity == 0:
            continue
        finding = inspect_position(pos, orders, broker.last_price(pos.symbol))
        report.findings.append(finding)
        if finding.ok or not repair:
            continue

        levels = stored_levels.get(pos.symbol)
        if levels is None:
            finding.action = "manual: no stored levels to rebuild protection from"
            continue
        stop, target = levels
        ok, why = levels_coherent(pos.direction, stop, target, broker.last_price(pos.symbol))
        if not ok:
            finding.action = f"NOT repaired: {why} (stale levels? check for a split)"
            continue

        stops, targets = _exits_for(pos, orders)
        for o in stops + targets:
            broker.cancel(o.order_id)
        oca = f"rg-guard-{pos.symbol}-{uuid.uuid4().hex[:8]}"
        ids = broker.place_protection(pos.symbol, pos.direction, abs(pos.quantity), stop, target, oca)
        finding.action = f"repaired: replaced exits with stop {stop} / target {target}, ids {ids}"

    return report
