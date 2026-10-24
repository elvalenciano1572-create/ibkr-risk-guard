import math

import pytest

from risk_guard.config import BrokerConfig, LiveTradingRefused, assert_paper_account
from risk_guard.levels import levels_coherent
from risk_guard.models import Direction, TradeIdea


class TestLevels:
    def test_pre_split_stop_above_market_is_rejected(self):
        # The MNST incident: stored stop $88.06, stock at $45.94 after a 2:1 split.
        ok, msg = levels_coherent(Direction.LONG, 88.06, 96.0, 45.94)
        assert not ok and "would fire now" in msg

    def test_long_ok(self):
        assert levels_coherent(Direction.LONG, 40, 55, 46)[0]

    def test_long_target_below_market(self):
        assert not levels_coherent(Direction.LONG, 40, 45, 46)[0]

    def test_short(self):
        assert levels_coherent(Direction.SHORT, 55, 40, 46)[0]
        assert not levels_coherent(Direction.SHORT, 45, 40, 46)[0]
        assert not levels_coherent(Direction.SHORT, 55, 47, 46)[0]

    @pytest.mark.parametrize("price", [None, 0, math.nan])
    def test_missing_price_still_protects(self, price):
        assert levels_coherent(Direction.LONG, 88, 96, price)[0]


class TestIdea:
    def test_valid(self):
        assert TradeIdea("A", Direction.LONG, 10, 9, 12).validate() is None
        assert TradeIdea("A", Direction.SHORT, 10, 11, 8).validate() is None

    def test_invalid(self):
        assert TradeIdea("A", Direction.LONG, 10, 11, 12).validate()
        assert TradeIdea("A", Direction.SHORT, 10, 9, 8).validate()
        assert TradeIdea("A", Direction.LONG, 0, -1, 2).validate()


class TestPaperLock:
    @pytest.mark.parametrize("acc", ["DU123456", "du999"])
    def test_paper_accounts_pass(self, acc):
        assert_paper_account(acc)

    @pytest.mark.parametrize("acc", ["U1234567", "", "F123", "XDU1"])
    def test_live_accounts_refused(self, acc):
        with pytest.raises(LiveTradingRefused):
            assert_paper_account(acc)

    @pytest.mark.parametrize("port", [7497, 4002])
    def test_paper_ports(self, port):
        BrokerConfig(port=port).assert_paper_port()

    @pytest.mark.parametrize("port", [7496, 4001, 8080])
    def test_live_or_unknown_ports_refused(self, port):
        with pytest.raises(LiveTradingRefused):
            BrokerConfig(port=port).assert_paper_port()

    def test_env(self, monkeypatch):
        monkeypatch.setenv("IB_PORT", "7497")
        assert BrokerConfig.from_env().port == 7497
