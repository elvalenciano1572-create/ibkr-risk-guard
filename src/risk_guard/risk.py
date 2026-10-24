"""Pure risk math: position sizing, portfolio caps and the friction filter.

No I/O and no broker calls, so every rule is unit-testable in milliseconds.

Design rule: when an input needed for a *loss* limit is missing, reject. When an input
needed only for a *cost* estimate is missing, approve with a warning. A risk cap protects
capital; the friction filter only protects the cost/benefit ratio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import RiskConfig
from .models import Position, TradeIdea


@dataclass(frozen=True)
class Sizing:
    quantity: int
    risk_usd: float
    notional_usd: float
    reason: str


def size_position(idea: TradeIdea, nav: float, cfg: RiskConfig) -> Sizing:
    """Shares to buy so the stop-out loss is ~risk_per_trade_pct of NAV.

    Returns quantity 0 (= skip the trade) rather than silently breaking a limit:
      * one share already risks more than `single_share_risk_tolerance` x the budget
      * one share is worth more than the concentration cap
    Both cases come from real incidents in the system this was extracted from, where a
    `max(1, qty)` quietly forced a 2.3R trade and a 24%-of-NAV position.
    """
    if nav <= 0:
        return Sizing(0, 0.0, 0.0, "NAV must be positive")
    if idea.entry <= 0:
        return Sizing(0, 0.0, 0.0, "entry price must be positive")
    per_share = idea.risk_per_share
    if per_share <= 0:
        return Sizing(0, 0.0, 0.0, "stop distance must be positive")

    risk_budget = nav * cfg.risk_per_trade_pct
    max_notional = nav * cfg.max_position_pct

    qty = math.floor(risk_budget / per_share)
    if qty < 1:
        if per_share > risk_budget * cfg.single_share_risk_tolerance:
            return Sizing(
                0,
                0.0,
                0.0,
                f"one share risks ${per_share:.2f}, above {cfg.single_share_risk_tolerance}x "
                f"the ${risk_budget:.2f} budget",
            )
        qty = 1

    if qty * idea.entry > max_notional:
        qty = math.floor(max_notional / idea.entry)
        if qty < 1:
            return Sizing(
                0,
                0.0,
                0.0,
                f"one share costs ${idea.entry:.2f}, above the "
                f"{cfg.max_position_pct:.0%} concentration cap (${max_notional:.2f})",
            )

    risk = round(qty * per_share, 2)
    notional = round(qty * idea.entry, 2)
    return Sizing(qty, risk, notional, f"{qty} shares, ${risk:.2f} at risk, ${notional:.2f} notional")


def open_risk_usd(positions: list[Position], stops: dict[str, float]) -> tuple[float, list[str]]:
    """Sum of (distance to stop x shares) across open positions.

    Returns (total, symbols_without_stop). A position without a known stop has unbounded
    risk, so callers must treat a non-empty second element as a reason to reject.
    """
    total = 0.0
    missing: list[str] = []
    stops_upper = {k.upper(): v for k, v in stops.items()}
    for p in positions:
        if p.quantity == 0:
            continue
        sym = p.symbol.upper()
        stop = stops_upper.get(sym)
        if stop is None:
            missing.append(sym)
            continue
        # A stop beyond the entry (locked-in profit) carries no downside risk.
        per_share = (p.avg_cost - stop) if p.quantity > 0 else (stop - p.avg_cost)
        total += max(0.0, per_share) * abs(p.quantity)
    return round(total, 2), missing


def check_portfolio_caps(
    new_risk_usd: float,
    new_notional_usd: float,
    nav: float,
    current_open_risk_usd: float | None,
    current_gross_usd: float | None,
    cfg: RiskConfig,
) -> tuple[bool, str]:
    """Would one more trade break an aggregate limit?

    Individually-correct trades can add up to a dangerous book (the original system once
    reached 214% gross exposure one sensible trade at a time). Unknown state -> reject.
    """
    if nav <= 0:
        return False, "NAV must be positive: refusing to add exposure"
    if current_open_risk_usd is None:
        return False, "open portfolio risk unknown: refusing to add exposure"
    if current_gross_usd is None:
        return False, "gross exposure unknown: refusing to add exposure"

    risk_cap = nav * cfg.max_portfolio_risk_pct
    total_risk = current_open_risk_usd + new_risk_usd
    if total_risk > risk_cap:
        return False, (
            f"portfolio risk would be ${total_risk:.2f} ({total_risk / nav:.1%} of NAV), "
            f"above the {cfg.max_portfolio_risk_pct:.0%} cap"
        )

    gross_cap = nav * cfg.max_gross_exposure_pct
    total_gross = current_gross_usd + new_notional_usd
    if total_gross > gross_cap:
        return False, (
            f"gross exposure would be ${total_gross:.2f} ({total_gross / nav:.0%} of NAV), "
            f"above the {cfg.max_gross_exposure_pct:.0%} cap"
        )

    return True, (
        f"portfolio caps OK: risk {total_risk / nav:.1%}/{cfg.max_portfolio_risk_pct:.0%}, "
        f"gross {total_gross / nav:.0%}/{cfg.max_gross_exposure_pct:.0%}"
    )


def check_friction(
    risk_usd: float, quantity: int, spread_usd: float | None, cfg: RiskConfig
) -> tuple[bool, str]:
    """Do commissions + expected spread eat too much of the assumed edge?

    Commission is almost fixed per order, so small trades pay proportionally more. A trade
    can have positive gross expectancy and negative net expectancy before it is ever sent.
    """
    if risk_usd <= 0 or quantity <= 0:
        return False, "invalid trade size"
    edge = cfg.expected_edge_r * risk_usd
    if edge <= 0:
        return False, "expected edge must be positive"
    cost = cfg.round_trip_commission_usd
    note = ""
    if spread_usd is not None and spread_usd > 0:
        cost += spread_usd * quantity * cfg.spread_fraction_paid
    else:
        note = " (no spread data: this is a lower bound)"
    ratio = cost / edge
    msg = f"costs ${cost:.2f} vs expected edge ${edge:.2f} = {ratio:.0%} of edge{note}"
    if ratio > cfg.max_friction_of_edge:
        return False, f"rejected on friction: {msg}"
    return True, f"friction OK: {msg}"
