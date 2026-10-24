"""Plain data types shared by every module. No broker imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def entry_action(self) -> str:
        return "BUY" if self is Direction.LONG else "SELL"

    @property
    def exit_action(self) -> str:
        return "SELL" if self is Direction.LONG else "BUY"


@dataclass(frozen=True)
class TradeIdea:
    """What the caller wants to do. The library never generates these: no strategy inside."""

    symbol: str
    direction: Direction
    entry: float
    stop: float
    target: float

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.stop)

    def validate(self) -> str | None:
        """Return an error message if the levels are not a valid bracket, else None."""
        if min(self.entry, self.stop, self.target) <= 0:
            return "prices must be positive"
        if self.direction is Direction.LONG and not (self.stop < self.entry < self.target):
            return "LONG requires stop < entry < target"
        if self.direction is Direction.SHORT and not (self.target < self.entry < self.stop):
            return "SHORT requires target < entry < stop"
        return None


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int  # signed: >0 long, <0 short
    avg_cost: float

    @property
    def direction(self) -> Direction:
        return Direction.LONG if self.quantity > 0 else Direction.SHORT


@dataclass(frozen=True)
class OpenOrder:
    symbol: str
    action: str  # BUY / SELL
    order_type: str  # LMT / STP
    quantity: int
    price: float
    order_id: int = 0
    oca_group: str = ""


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    net_liquidation: float
    gross_position_value: float


@dataclass(frozen=True)
class BracketPlan:
    """A fully-specified bracket: parent limit entry + OCA-linked stop and target, all GTC.

    The original system linked children only via parentId. Here the two exits also share
    an explicit OCA group, so a fill on one always cancels the other even if the parent
    link is lost (e.g. after manual edits or a broker-side corporate action).
    """

    symbol: str
    direction: Direction
    quantity: int
    entry: float
    stop: float
    target: float
    oca_group: str
    tif: str = "GTC"

    def __str__(self) -> str:
        return (
            f"{self.direction.entry_action} {self.quantity} {self.symbol} @ {self.entry:.2f} LMT, "
            f"stop {self.stop:.2f} / target {self.target:.2f} ({self.tif}, OCA {self.oca_group})"
        )


@dataclass
class Decision:
    """Outcome of a pre-trade check. `approved=False` always carries a human-readable reason."""

    approved: bool
    reason: str
    quantity: int = 0
    risk_usd: float = 0.0
    notional_usd: float = 0.0
    checks: list[str] = field(default_factory=list)

    @classmethod
    def reject(cls, reason: str, checks: list[str] | None = None) -> Decision:
        return cls(approved=False, reason=reason, checks=list(checks or []))
