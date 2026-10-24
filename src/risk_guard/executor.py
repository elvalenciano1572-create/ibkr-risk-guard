"""Pre-trade checks and bracket placement.

The order of checks is the point of this module. Each one exists because its absence cost
money once in the system this was extracted from:

1. idea sanity      - levels must form a valid bracket
2. duplicates       - re-checked against the broker *right before sending*, because minutes
                      or hours can pass between seeing an idea and confirming it
3. sizing           - 1% of NAV, never silently forced to 1 share
4. portfolio caps   - aggregate risk and gross exposure, rejecting when state is unknown
5. friction         - costs must not eat more than half the assumed edge
6. market coherence - the protective levels must not fire the instant they are placed
"""

from __future__ import annotations

import logging
import uuid

from .broker import Broker
from .config import RiskConfig, assert_paper_account
from .levels import levels_coherent
from .models import BracketPlan, Decision, TradeIdea
from .risk import check_friction, check_portfolio_caps, open_risk_usd, size_position

log = logging.getLogger(__name__)


def protective_stops(broker: Broker) -> dict[str, float]:
    """Map symbol -> live stop price, read from the broker's open STP orders."""
    pos_map = {p.symbol.upper(): p for p in broker.positions() if p.quantity != 0}
    stops: dict[str, float] = {}
    for o in broker.open_orders():
        if o.order_type != "STP":
            continue
        sym = o.symbol.upper()
        pos = pos_map.get(sym)
        if pos is not None and o.action != pos.direction.exit_action:
            continue
        stops[sym] = o.price
    return stops


def evaluate(idea: TradeIdea, broker: Broker, cfg: RiskConfig) -> Decision:
    """Run every pre-trade check without sending anything."""
    checks: list[str] = []

    err = idea.validate()
    if err:
        return Decision.reject(f"invalid idea: {err}", checks)
    checks.append("levels form a valid bracket")

    account = broker.account()
    assert_paper_account(account.account_id)

    sym = idea.symbol.upper()
    if any(p.symbol.upper() == sym and p.quantity != 0 for p in broker.positions()):
        return Decision.reject(f"duplicate: already holding {sym}", checks)
    if any(o.symbol.upper() == sym for o in broker.open_orders()):
        return Decision.reject(f"duplicate: {sym} already has working orders", checks)
    checks.append("no existing position or working orders")

    sizing = size_position(idea, account.net_liquidation, cfg)
    if sizing.quantity < 1:
        return Decision.reject(f"sizing: {sizing.reason}", checks)
    checks.append(f"sizing: {sizing.reason}")

    current_risk, unstopped = open_risk_usd(broker.positions(), protective_stops(broker))
    if unstopped:
        return Decision.reject(
            f"positions without a live stop ({', '.join(unstopped)}): portfolio risk is unbounded",
            checks,
        )
    ok, msg = check_portfolio_caps(
        sizing.risk_usd,
        sizing.notional_usd,
        account.net_liquidation,
        current_risk,
        account.gross_position_value,
        cfg,
    )
    if not ok:
        return Decision.reject(msg, checks)
    checks.append(msg)

    ok, msg = check_friction(sizing.risk_usd, sizing.quantity, broker.spread(sym), cfg)
    if not ok:
        return Decision.reject(msg, checks)
    checks.append(msg)

    ok, msg = levels_coherent(idea.direction, idea.stop, idea.target, broker.last_price(sym))
    if not ok:
        return Decision.reject(msg, checks)
    checks.append(msg)

    return Decision(
        approved=True,
        reason="all checks passed",
        quantity=sizing.quantity,
        risk_usd=sizing.risk_usd,
        notional_usd=sizing.notional_usd,
        checks=checks,
    )


def build_plan(idea: TradeIdea, quantity: int) -> BracketPlan:
    return BracketPlan(
        symbol=idea.symbol.upper(),
        direction=idea.direction,
        quantity=quantity,
        entry=round(idea.entry, 2),
        stop=round(idea.stop, 2),
        target=round(idea.target, 2),
        oca_group=f"rg-{idea.symbol.upper()}-{uuid.uuid4().hex[:8]}",
    )


def execute(idea: TradeIdea, broker: Broker, cfg: RiskConfig, *, dry_run: bool = True) -> Decision:
    """Evaluate and, only if approved and not a dry run, send the bracket."""
    decision = evaluate(idea, broker, cfg)
    if not decision.approved:
        log.warning("REJECTED %s: %s", idea.symbol, decision.reason)
        return decision
    plan = build_plan(idea, decision.quantity)
    if dry_run:
        decision.checks.append(f"dry run: would send {plan}")
        return decision
    ids = broker.place_bracket(plan)
    decision.checks.append(f"sent bracket, order ids {ids}, OCA {plan.oca_group}")
    log.info("SENT %s x%d (OCA %s)", plan.symbol, plan.quantity, plan.oca_group)
    return decision
