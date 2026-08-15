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


def crosses(side: str, limit_price: float, market_price: float) -> bool:
    """Whether a resting limit order would trade against `market_price`:
    a buy limit crosses when the market is at or below it, a sell limit
    crosses when the market is at or above it."""
    if side.lower() == "buy":
        return market_price <= limit_price
    return market_price >= limit_price


@dataclass
class Fill:
    price: float
    qty: float
    route: str
    status: str = "filled"


class PaperRouter:
    route_name = "paper"

    def fill(self, side: str, qty: float, order_type: str,
             ref_price: float | None, limit_price: float | None,
             symbol: str = "") -> Fill:
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


class AlpacaRouter:
    """Routes fills through a REAL Alpaca account - see
    app/alpaca_broker.py for the client and the (deliberately awkward,
    two-signal) live/paper mode gate. route_name reflects which mode is
    actually active ("alpaca-paper" or "alpaca-live") so a fill's real
    destination is never ambiguous from Order.route.

    Unlike PaperRouter's instant ref-price fill, Alpaca order placement is
    asynchronous (new -> accepted -> filled) - this blocks briefly polling
    for the real fill (AlpacaClient.wait_for_fill), so a call here is
    slower than the internal simulator by design, not a bug. Needs `symbol`
    (the internal PaperRouter never did, since it doesn't place a real
    order anywhere) - raises OrderError if it's missing rather than
    silently guessing which instrument to trade."""

    def __init__(self, client, live: bool = False):
        self.client = client
        self.route_name = "alpaca-live" if live else "alpaca-paper"

    def fill(self, side: str, qty: float, order_type: str,
             ref_price: float | None, limit_price: float | None,
             symbol: str = "") -> Fill:
        if not symbol:
            raise OrderError("alpaca order routing needs a symbol")
        try:
            placed = self.client.submit_order(
                symbol=symbol, side=side, qty=qty, order_type=order_type,
                limit_price=limit_price,
            )
            filled = self.client.wait_for_fill(placed["id"])
        except Exception as exc:  # noqa: BLE001 - AlpacaError or any transport failure
            raise OrderError(f"alpaca order failed: {exc}") from exc
        return Fill(price=float(filled.get("filled_avg_price") or 0.0),
                   qty=float(filled.get("filled_qty") or qty), route=self.route_name)


class IBKRRouter:
    """Routes fills through a real Interactive Brokers account via a
    Client Portal Gateway - see app/ibkr_broker.py for the client, the
    live/paper mode gate, and the important caveat that (unlike Alpaca)
    "paper" here depends on which account the gateway you're pointed at is
    actually logged into, which this router double-checks (via
    ibkr_broker.verify_account_matches_mode) before every single order,
    not just once at startup - a gateway can be logged out and back into a
    different account between orders, and this must not trust a stale
    assumption.

    route_name is "ibkr-paper" or "ibkr-live" per the configured mode -
    note this reflects CONFIGURED mode, not a live-verified fact about the
    account, for the (rare, refused-before-trading) case where the
    cross-check itself fails to run."""

    def __init__(self, client, mode: str):
        self.client = client
        self.mode = mode
        self.route_name = f"ibkr-{mode}"

    def fill(self, side: str, qty: float, order_type: str,
             ref_price: float | None, limit_price: float | None,
             symbol: str = "") -> Fill:
        if not symbol:
            raise OrderError("ibkr order routing needs a symbol")
        from . import ibkr_broker
        mismatch = ibkr_broker.verify_account_matches_mode(self.client, self.mode)
        if mismatch:
            raise OrderError(mismatch)
        try:
            conid = self.client.resolve_conid(symbol)
            if conid is None:
                raise ibkr_broker.IBKRError(f"could not resolve an IBKR contract id for '{symbol}'")
            account_id = self.client.primary_account_id()
            if not account_id:
                raise ibkr_broker.IBKRError("no account associated with this gateway session")
            ib_type = "LMT" if order_type == "limit" else "MKT"
            placed = self.client.submit_order(account_id, conid, side, qty,
                                              order_type=ib_type, limit_price=limit_price)
            order_id = placed.get("order_id") or placed.get("orderId")
            filled = self.client.wait_for_fill(order_id)
        except Exception as exc:  # noqa: BLE001 - IBKRError or any transport failure
            raise OrderError(f"ibkr order failed: {exc}") from exc
        price = filled.get("avg_price") or filled.get("avgPrice") or ref_price or limit_price or 0.0
        return Fill(price=float(price), qty=qty, route=self.route_name)


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
