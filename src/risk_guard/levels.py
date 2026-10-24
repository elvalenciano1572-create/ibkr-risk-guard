"""Would a protective order execute immediately if placed right now?

Born from a real incident: a 2:1 overnight split made the broker cancel the protective
orders. An automated repair job re-placed them with the stored *pre-split* levels, putting
a sell stop at $88 under a stock trading at $46. A sell stop above the market fires at once,
so the guardian itself liquidated the position.

This check knows nothing about splits on purpose. It asks the only question that matters,
so it also covers reverse splits, special dividends and corrupted stored levels.
"""

from __future__ import annotations

import math

from .models import Direction


def levels_coherent(
    direction: Direction, stop: float, target: float, price: float | None
) -> tuple[bool, str]:
    """Return (ok, reason).

    With no usable reference price we return True: leaving a position unprotected because a
    quote failed is worse than placing the protection.
    """
    if price is None or (isinstance(price, float) and math.isnan(price)) or price <= 0:
        return True, "no reference price: placing anyway (unprotected is worse)"

    if direction is Direction.LONG:
        if stop >= price:
            return False, f"sell stop ${stop:.2f} is at/above market ${price:.2f}: would fire now"
        if target <= price:
            return False, f"sell target ${target:.2f} is at/below market ${price:.2f}: would fill now"
    else:
        if stop <= price:
            return False, f"buy stop ${stop:.2f} is at/below market ${price:.2f}: would fire now"
        if target >= price:
            return False, f"buy target ${target:.2f} is at/above market ${price:.2f}: would fill now"
    return True, f"levels coherent with market ${price:.2f}"
