"""Broker abstraction plus an in-memory FakeBroker for tests and the offline demo.

Everything above this layer (executor, guardian, report) depends only on the `Broker`
protocol, which is why the whole library can be tested without TWS or IB Gateway.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Protocol

from .models import AccountSnapshot, BracketPlan, Direction, OpenOrder, Position


class Broker(Protocol):
    def account(self) -> AccountSnapshot: ...
    def positions(self) -> list[Position]: ...
    def open_orders(self) -> list[OpenOrder]: ...
    def last_price(self, symbol: str) -> float | None: ...
    def spread(self, symbol: str) -> float | None: ...
    def place_bracket(self, plan: BracketPlan) -> list[int]: ...
    def place_protection(
        self, symbol: str, direction: Direction, quantity: int, stop: float, target: float, oca_group: str
    ) -> list[int]: ...
    def cancel(self, order_id: int) -> None: ...


@dataclass
class FakeBroker:
    """Deterministic broker double. Never touches the network."""

    account_id: str = "DU0000001"
    nav: float = 10_000.0
    prices: dict[str, float] = field(default_factory=dict)
    spreads: dict[str, float] = field(default_factory=dict)
    _positions: list[Position] = field(default_factory=list)
    _orders: list[OpenOrder] = field(default_factory=list)
    placed: list[BracketPlan] = field(default_factory=list)
    cancelled: list[int] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1000))

    # -- read side -------------------------------------------------------------------
    def account(self) -> AccountSnapshot:
        gross = sum(abs(p.quantity) * self.prices.get(p.symbol, p.avg_cost) for p in self._positions)
        return AccountSnapshot(self.account_id, self.nav, round(gross, 2))

    def positions(self) -> list[Position]:
        return list(self._positions)

    def open_orders(self) -> list[OpenOrder]:
        return list(self._orders)

    def last_price(self, symbol: str) -> float | None:
        return self.prices.get(symbol)

    def spread(self, symbol: str) -> float | None:
        return self.spreads.get(symbol)

    # -- write side ------------------------------------------------------------------
    def place_bracket(self, plan: BracketPlan) -> list[int]:
        self.placed.append(plan)
        parent = next(self._ids)
        self._orders.append(
            OpenOrder(plan.symbol, plan.direction.entry_action, "LMT", plan.quantity, plan.entry, parent)
        )
        exits = self._add_exits(
            plan.symbol, plan.direction, plan.quantity, plan.stop, plan.target, plan.oca_group
        )
        return [parent, *exits]

    def place_protection(
        self, symbol: str, direction: Direction, quantity: int, stop: float, target: float, oca_group: str
    ) -> list[int]:
        return self._add_exits(symbol, direction, quantity, stop, target, oca_group)

    def cancel(self, order_id: int) -> None:
        self.cancelled.append(order_id)
        self._orders = [o for o in self._orders if o.order_id != order_id]

    # -- test helpers ----------------------------------------------------------------
    def add_position(self, symbol: str, quantity: int, avg_cost: float) -> None:
        self._positions.append(Position(symbol, quantity, avg_cost))

    def add_order(self, order: OpenOrder) -> None:
        self._orders.append(order)

    def _add_exits(
        self, symbol: str, direction: Direction, qty: int, stop: float, target: float, oca: str
    ) -> list[int]:
        sid, tid = next(self._ids), next(self._ids)
        action = direction.exit_action
        self._orders.append(OpenOrder(symbol, action, "STP", qty, stop, sid, oca))
        self._orders.append(OpenOrder(symbol, action, "LMT", qty, target, tid, oca))
        return [sid, tid]
