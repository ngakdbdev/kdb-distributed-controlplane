"""
oms.py - order routing + position keeping.

Two routers behind one interface:
  * PaperRouter (default) - simulates a fill at the reference/limit price. This
    is PAPER TRADING: nothing reaches a real market. Every paper order is
    tagged route="paper" so it's never mistaken for a live fill.
  * BrokerRouter (seam) - where a real broker/FIX adapter plugs in. It REFUSES
    until configured, because live order routing is regulated activity that
    needs broker connectivity, entitlements, and compliance sign-off. We do not
    ship a path that fires live orders at a real market.

`apply_to_position` (pure, tested) keeps a weighted-average position and books
realized P&L when a fill reduces or flips it.
"""
from __future__ import annotations

from dataclasses import dataclass


class OrderError(RuntimeError):
    pass


class OrderRoutingNotConfigured(OrderError):
    """A live broker route with no adapter/credentials configured."""


def signed_qty(side: str, qty: float) -> float:
    return qty if side.lower() == "buy" else -qty


@dataclass
class Fill:
    price: float
    qty: float
    route: str
    status: str = "filled"


class PaperRouter:
    route_name = "paper"

    def fill(self, side: str, qty: float, order_type: str,
             ref_price: float | None, limit_price: float | None) -> Fill:
        if order_type == "limit":
            if limit_price is None:
                raise OrderError("limit order needs a limit price")
            price = limit_price
        else:  # market
            if ref_price is None:
                raise OrderError("market order needs a reference price (ref_price)")
            price = ref_price
        return Fill(price=price, qty=qty, route=self.route_name)


class BrokerRouter:
    """Real broker/FIX seam - refuses until wired to your broker."""
    route_name = "broker"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def fill(self, *args, **kwargs) -> Fill:
        raise OrderRoutingNotConfigured(
            "Live order routing isn't configured. Wiring a real broker/FIX adapter here is "
            "regulated: it needs broker connectivity, market entitlements, and compliance "
            "sign-off. Until then, orders run in paper mode.")


def apply_to_position(qty: float, avg: float, fill_side: str, fill_qty: float,
                      fill_price: float) -> tuple:
    """Fold a fill into a position. Returns (new_qty, new_avg, realized_pnl).

    - adding in the same direction -> weighted-average cost
    - reducing (opposite direction, not crossing zero) -> avg unchanged, books
      realized P&L on the reduced quantity
    - flipping through zero -> books P&L on the closed part, new avg = fill price
    """
    signed = signed_qty(fill_side, fill_qty)
    new_qty = qty + signed
    realized = 0.0

    if qty == 0 or (qty > 0) == (signed > 0):
        # opening or increasing -> weighted average by absolute size
        denom = abs(new_qty)
        new_avg = (abs(qty) * avg + abs(signed) * fill_price) / denom if denom else 0.0
        return new_qty, new_avg, realized

    # reducing or flipping
    closing = min(abs(signed), abs(qty))
    direction = 1.0 if qty > 0 else -1.0
    realized = closing * (fill_price - avg) * direction
    if abs(signed) <= abs(qty):
        # partial/full close, same-side remainder keeps its avg
        new_avg = avg if new_qty != 0 else 0.0
        return new_qty, new_avg, realized
    # flipped through zero: remainder is at the fill price
    return new_qty, fill_price, realized
