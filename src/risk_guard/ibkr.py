"""Interactive Brokers adapter (ib_async). Paper accounts only.

Two independent locks: the port must be a paper port, and after connecting, every managed
account must start with 'DU'. Either failing disconnects and raises LiveTradingRefused.
"""

from __future__ import annotations

import logging
import math

from .config import BrokerConfig, assert_paper_account
from .models import AccountSnapshot, BracketPlan, Direction, OpenOrder, Position

log = logging.getLogger(__name__)

_DEAD = {"Cancelled", "Filled", "Inactive", "ApiCancelled"}


class IBKRBroker:
    def __init__(self, cfg: BrokerConfig | None = None) -> None:
        try:
            from ib_async import IB  # imported lazily so tests never need it
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("install the 'ibkr' extra: pip install ibkr-risk-guard[ibkr]") from exc
        self.cfg = cfg or BrokerConfig.from_env()
        self.cfg.assert_paper_port()
        self.ib = IB()

    # -- lifecycle -------------------------------------------------------------------
    def connect(self) -> IBKRBroker:
        self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id, timeout=15)
        try:
            accounts = self.ib.managedAccounts()
            if not accounts:
                raise RuntimeError("no managed accounts reported")
            for acc in accounts:
                assert_paper_account(acc)
        except Exception:
            self.ib.disconnect()
            raise
        self.account_id = accounts[0]
        return self

    def close(self) -> None:
        self.ib.disconnect()

    def __enter__(self) -> IBKRBroker:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- read side -------------------------------------------------------------------
    def account(self) -> AccountSnapshot:
        tags = {i.tag: i.value for i in self.ib.accountSummary(self.account_id)}
        return AccountSnapshot(
            self.account_id,
            float(tags.get("NetLiquidation", 0) or 0),
            float(tags.get("GrossPositionValue", 0) or 0),
        )

    def positions(self) -> list[Position]:
        return [
            Position(p.contract.symbol.upper(), int(p.position), float(p.avgCost))
            for p in self.ib.positions(self.account_id)
            if p.position
        ]

    def open_orders(self) -> list[OpenOrder]:
        self.ib.reqAllOpenOrders()
        self.ib.sleep(1.0)
        out = []
        for t in self.ib.openTrades():
            if t.orderStatus.status in _DEAD:
                continue
            o = t.order
            price = o.auxPrice if o.orderType == "STP" else o.lmtPrice
            out.append(
                OpenOrder(
                    t.contract.symbol.upper(),
                    o.action,
                    o.orderType,
                    int(o.totalQuantity),
                    float(price or 0),
                    o.orderId,
                    o.ocaGroup or "",
                )
            )
        return out

    def _ticker(self, symbol: str):
        from ib_async import Stock

        contract = Stock(symbol.upper(), "SMART", "USD")
        self.ib.qualifyContracts(contract)
        [ticker] = self.ib.reqTickers(contract)
        return ticker

    def last_price(self, symbol: str) -> float | None:
        try:
            px = self._ticker(symbol).marketPrice()
        except Exception as exc:  # quote failures must never block protection
            log.warning("price for %s unavailable: %s", symbol, exc)
            return None
        return None if px is None or math.isnan(px) or px <= 0 else float(px)

    def spread(self, symbol: str) -> float | None:
        try:
            t = self._ticker(symbol)
        except Exception:
            return None
        if t.bid and t.ask and t.ask > t.bid > 0:
            return round(t.ask - t.bid, 4)
        return None

    # -- write side ------------------------------------------------------------------
    def place_bracket(self, plan: BracketPlan) -> list[int]:
        from ib_async import Stock

        contract = Stock(plan.symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        bracket = self.ib.bracketOrder(
            plan.direction.entry_action, plan.quantity, plan.entry, plan.target, plan.stop
        )
        for order in bracket:
            order.tif = plan.tif
        for child in (bracket.takeProfit, bracket.stopLoss):
            child.ocaGroup = plan.oca_group
            child.ocaType = 1  # cancel the sibling, with block
        return [self.ib.placeOrder(contract, o).order.orderId for o in bracket]

    def place_protection(
        self, symbol: str, direction: Direction, quantity: int, stop: float, target: float, oca_group: str
    ) -> list[int]:
        from ib_async import LimitOrder, Stock, StopOrder

        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        action = direction.exit_action
        stp = StopOrder(action, quantity, round(stop, 2), tif="GTC", ocaGroup=oca_group, ocaType=1)
        lmt = LimitOrder(action, quantity, round(target, 2), tif="GTC", ocaGroup=oca_group, ocaType=1)
        return [self.ib.placeOrder(contract, o).order.orderId for o in (stp, lmt)]

    def cancel(self, order_id: int) -> None:
        for t in self.ib.openTrades():
            if t.order.orderId == order_id:
                self.ib.cancelOrder(t.order)
                return
