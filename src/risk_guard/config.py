"""Configuration and the paper-trading safety lock.

The paper lock is deliberately hard to bypass: this project is a portfolio demo of risk
engineering, not a tool for sending live orders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PAPER_PORTS = frozenset({7497, 4002})  # TWS paper, IB Gateway paper
LIVE_PORTS = frozenset({7496, 4001})
PAPER_ACCOUNT_PREFIX = "DU"


class LiveTradingRefused(RuntimeError):
    """Raised whenever anything points at a live (non-paper) account or port."""


@dataclass(frozen=True)
class RiskConfig:
    """Hard limits. Every number is a cap the system cannot exceed, not a target."""

    risk_per_trade_pct: float = 0.01  # 1% of NAV at risk per trade
    max_position_pct: float = 0.15  # no single position above 15% of NAV
    max_portfolio_risk_pct: float = 0.08  # sum of open stop-risk across positions
    max_gross_exposure_pct: float = 2.00  # gross notional / NAV (leverage ceiling)
    single_share_risk_tolerance: float = 1.5  # allow 1 share up to 1.5x the risk budget
    round_trip_commission_usd: float = 0.80
    expected_edge_r: float = 0.10  # assumed edge, in R, used only by the friction filter
    max_friction_of_edge: float = 0.50  # reject if costs eat more than half the edge
    spread_fraction_paid: float = 0.50  # share of the bid/ask spread expected to be paid

    def __post_init__(self) -> None:
        for name in (
            "risk_per_trade_pct",
            "max_position_pct",
            "max_portfolio_risk_pct",
            "max_gross_exposure_pct",
        ):
            value = getattr(self, name)
            if not 0 < value <= 10:
                raise ValueError(f"{name} out of range: {value}")
        if self.risk_per_trade_pct > self.max_portfolio_risk_pct:
            raise ValueError("risk_per_trade_pct cannot exceed max_portfolio_risk_pct")


@dataclass(frozen=True)
class BrokerConfig:
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 17

    @classmethod
    def from_env(cls) -> BrokerConfig:
        return cls(
            host=os.getenv("IB_HOST", "127.0.0.1"),
            port=int(os.getenv("IB_PORT", "4002")),
            client_id=int(os.getenv("IB_CLIENT_ID", "17")),
        )

    def assert_paper_port(self) -> None:
        if self.port in LIVE_PORTS or self.port not in PAPER_PORTS:
            raise LiveTradingRefused(
                f"port {self.port} is not a paper-trading port {sorted(PAPER_PORTS)}; refusing"
            )


def assert_paper_account(account_id: str) -> None:
    """IBKR paper accounts start with 'DU'. Anything else is treated as live and refused."""
    if not account_id or not account_id.upper().startswith(PAPER_ACCOUNT_PREFIX):
        raise LiveTradingRefused(
            f"account {account_id!r} is not a paper account (expected prefix "
            f"{PAPER_ACCOUNT_PREFIX!r}); refusing to continue"
        )
