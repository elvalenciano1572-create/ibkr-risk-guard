"""Adapter tests with a stubbed ib_async.IB, so no TWS/Gateway is needed."""

import sys
import types
from types import SimpleNamespace as NS

import pytest

from risk_guard.config import BrokerConfig, LiveTradingRefused


class StubIB:
    accounts = ["DU111111"]

    def __init__(self):
        self.connected = False
        self.placed = []

    def connect(self, *a, **k):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):
        return self.accounts

    def accountSummary(self, acc):
        return [NS(tag="NetLiquidation", value="10000"), NS(tag="GrossPositionValue", value="2500")]

    def positions(self, acc):
        return [NS(contract=NS(symbol="aapl"), position=5.0, avgCost=190.0)]

    def reqAllOpenOrders(self):
        pass

    def sleep(self, s):
        pass

    def openTrades(self):
        return [
            NS(
                contract=NS(symbol="AAPL"),
                orderStatus=NS(status="Submitted"),
                order=NS(
                    action="SELL",
                    orderType="STP",
                    totalQuantity=5,
                    auxPrice=185.0,
                    lmtPrice=0,
                    orderId=7,
                    ocaGroup="g",
                ),
            ),
            NS(contract=NS(symbol="OLD"), orderStatus=NS(status="Cancelled"), order=NS()),
        ]


@pytest.fixture
def stub(monkeypatch):
    mod = types.ModuleType("ib_async")
    mod.IB = StubIB
    monkeypatch.setitem(sys.modules, "ib_async", mod)
    StubIB.accounts = ["DU111111"]
    from risk_guard.ibkr import IBKRBroker

    return IBKRBroker


def test_connects_to_paper_and_maps_state(stub):
    b = stub(BrokerConfig(port=4002)).connect()
    assert b.account().net_liquidation == 10_000
    assert b.positions()[0].symbol == "AAPL"
    [o] = b.open_orders()
    assert (o.order_type, o.price, o.oca_group) == ("STP", 185.0, "g")


def test_live_account_disconnects_and_refuses(stub):
    StubIB.accounts = ["DU111111", "U2222222"]
    b = stub(BrokerConfig(port=4002))
    with pytest.raises(LiveTradingRefused):
        b.connect()
    assert not b.ib.connected


def test_live_port_refused_before_connecting(stub):
    with pytest.raises(LiveTradingRefused):
        stub(BrokerConfig(port=7496))
