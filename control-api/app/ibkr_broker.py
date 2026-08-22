"""
ibkr_broker.py - order execution through Interactive Brokers' Client Portal
Web API (CPAPI).

READ THIS BEFORE ENABLING: unlike alpaca_broker.py, this does NOT talk to a
stateless cloud endpoint. CPAPI is served by a locally-running Client Portal
Gateway (a small Java process IBKR ships) that must already be started AND
logged in (username/password + 2FA) before this module can do anything -
IBKR_GATEWAY_BASE_URL just points at wherever that gateway is reachable.
Keeping it authenticated (daily restarts, weekly forced re-auth) is a real,
ongoing operational task outside this module's scope - IBeam
(https://github.com/Voyz/ibeam) is the community-standard tool for
automating that. This code is written carefully to CPAPI's documented
request/response shapes but, unlike the Alpaca integration, has NOT been
exercised against a live, authenticated gateway (no such gateway is
reachable from this development environment) - verify every code path here
against your own real gateway/paper account before trusting it with orders,
same as this repo already tells you to do for its "licensed" market-data
feeds (NYSE/LSEG/NSE/BSE).

Trading mode - same two-signal safety pattern as alpaca_broker.py, with one
IMPORTANT difference: for IBKR, "paper" vs "live" isn't a base-URL choice
the way it is for Alpaca - it's a property of WHICH ACCOUNT the gateway you
pointed IBKR_GATEWAY_BASE_URL at is actually logged into (paper accounts use
a separate paper-trading username IBKR provisions alongside your live one).
This module cannot itself force that; as defense in depth, it checks the
connected account ID's prefix against IBKR's own documented convention
(paper account IDs start with "DU") and refuses to place ANY order if that
doesn't match what IBKR_TRADING_MODE claims - so a gateway accidentally
logged into the wrong account fails closed instead of silently trading the
wrong book.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Optional


class IBKRError(RuntimeError):
    pass


# Same double-confirmation requirement as alpaca_broker.py's LIVE_ACK_PHRASE -
# see that module for why. A bare IBKR_TRADING_MODE=live is not enough by
# itself.
LIVE_ACK_PHRASE = "I_UNDERSTAND_THIS_TRADES_REAL_MONEY"

# IBKR's own documented convention: paper-trading account IDs are prefixed
# "DU"; live accounts use other prefixes (typically "U"). Used as a
# best-effort cross-check, not the sole gate - see module docstring.
_PAPER_ACCOUNT_PREFIX = "DU"


def trading_mode() -> str:
    """'off' (default) | 'paper' | 'live' - identical gating logic to
    alpaca_broker.trading_mode(); see that function's docstring for the
    exact "live without a matching ack silently downgrades to paper, never
    to something more dangerous" guarantee, which applies here unchanged."""
    mode = os.environ.get("IBKR_TRADING_MODE", "off").strip().lower()
    if mode not in ("off", "paper", "live"):
        return "off"
    if mode == "live" and os.environ.get("IBKR_LIVE_TRADING_ACK", "") != LIVE_ACK_PHRASE:
        return "paper"
    return mode


class IBKRClient:
    def __init__(self, base_url: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self._ssl_ctx = None if verify_ssl else _insecure_context()
        self._conids: dict = {}

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15, context=self._ssl_ctx) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise IBKRError(f"IBKR {method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise IBKRError(
                f"IBKR gateway unreachable at {self.base_url}{path}: {exc.reason} "
                f"- is the Client Portal Gateway actually running and authenticated?") from exc

    def auth_status(self) -> dict:
        return self._request("GET", "/iserver/auth/status")

    def accounts(self) -> list:
        """The account(s) this gateway session is logged into. CPAPI docs
        describe this as a list; a retail login typically has exactly one."""
        result = self._request("GET", "/iserver/accounts")
        return result.get("accounts", []) if isinstance(result, dict) else []

    def primary_account_id(self) -> Optional[str]:
        accts = self.accounts()
        return accts[0] if accts else None

    def resolve_conid(self, symbol: str) -> Optional[int]:
        if symbol in self._conids:
            return self._conids[symbol]
        matches = self._request("GET", f"/iserver/secdef/search?symbol={symbol}")
        conid = None
        for m in matches if isinstance(matches, list) else []:
            if isinstance(m, dict) and m.get("conid"):
                conid = m["conid"]
                break
        self._conids[symbol] = conid
        return conid

    def submit_order(self, account_id: str, conid: int, side: str, qty: float,
                     order_type: str = "MKT", limit_price: Optional[float] = None,
                     tif: str = "DAY", max_confirmations: int = 5) -> dict:
        """POST /iserver/account/{id}/orders, then follow CPAPI's documented
        confirmation loop: a real submission commonly comes back as a
        "message requires confirmation" reply (risk warnings, e.g. trading
        outside regular hours) rather than an immediate order id - each
        must be POSTed back to /iserver/reply/{id} with {"confirmed": true}
        before the order actually goes in. Bounded at max_confirmations
        rather than looping forever against a malformed/unexpected
        response shape."""
        order = {"conid": conid, "orderType": order_type, "side": side.upper(),
                 "quantity": qty, "tif": tif}
        if order_type == "LMT":
            if limit_price is None:
                raise IBKRError("limit order needs a limit_price")
            order["price"] = limit_price

        result = self._request("POST", f"/iserver/account/{account_id}/orders", {"orders": [order]})
        for _ in range(max_confirmations):
            entries = result if isinstance(result, list) else [result]
            entry = entries[0] if entries else {}
            if not isinstance(entry, dict):
                raise IBKRError(f"unexpected order response shape: {result!r}")
            if entry.get("order_id") or entry.get("orderId"):
                return entry
            reply_id = entry.get("id")
            if reply_id is None:
                raise IBKRError(f"order response had neither an order id nor a reply id: {entry!r}")
            result = self._request("POST", f"/iserver/reply/{reply_id}", {"confirmed": True})
        raise IBKRError(f"order confirmation loop did not resolve within {max_confirmations} rounds")

    def order_status(self, order_id: str) -> dict:
        return self._request("GET", f"/iserver/account/order/status/{order_id}")

    def wait_for_fill(self, order_id: str, timeout_sec: float = 15.0, poll_sec: float = 1.0) -> dict:
        """CPAPI order status is asynchronous, same shape of problem as
        Alpaca's - poll rather than assume the submit response is the fill.
        Status string values here are CPAPI's own ("Filled", "Cancelled",
        "Inactive" among others per IBKR's docs) - if your gateway reports a
        status this doesn't recognize, that's exactly the kind of thing to
        verify against a real account before trusting this in production."""
        deadline = time.monotonic() + timeout_sec
        last = None
        while time.monotonic() < deadline:
            last = self.order_status(order_id)
            status = str(last.get("order_status") or last.get("status") or "").lower()
            if status == "filled":
                return last
            if status in ("cancelled", "canceled", "rejected", "inactive"):
                raise IBKRError(f"order {order_id} ended as '{status}'")
            time.sleep(poll_sec)
        raise IBKRError(
            f"order {order_id} did not fill within {timeout_sec}s "
            f"(last known status: {(last or {}).get('order_status', 'unknown')})")


def _insecure_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def client_from_env() -> Optional[IBKRClient]:
    """None if trading_mode() is 'off' - callers must treat None as "fall
    back to the internal PaperRouter", never raise. Does NOT itself verify
    the gateway is reachable/authenticated - that surfaces as an IBKRError
    on first real call (account lookup, order submit, ...), same as any
    other network failure."""
    mode = trading_mode()
    if mode == "off":
        return None
    base_url = os.environ.get("IBKR_GATEWAY_BASE_URL", "https://localhost:5000/v1/api")
    verify_ssl = os.environ.get("IBKR_GATEWAY_VERIFY_SSL", "false").lower() == "true"
    return IBKRClient(base_url, verify_ssl=verify_ssl)


def verify_account_matches_mode(client: IBKRClient, mode: str) -> Optional[str]:
    """Best-effort cross-check (see module docstring): fetch which account
    the gateway is actually logged into and compare its prefix against
    IBKR's documented paper-account convention. Returns an error string to
    refuse the order on, or None if it looks consistent (including if the
    check itself couldn't be completed - a gateway that's unreachable will
    fail on the actual order call anyway, with a clearer error, so this
    doesn't duplicate that failure mode)."""
    try:
        account_id = client.primary_account_id()
    except IBKRError:
        return None
    if not account_id:
        return None
    looks_paper = account_id.upper().startswith(_PAPER_ACCOUNT_PREFIX)
    if mode == "paper" and not looks_paper:
        return (f"IBKR_TRADING_MODE=paper but the connected gateway account "
                f"'{account_id}' doesn't look like a paper account (expected an ID "
                f"starting with '{_PAPER_ACCOUNT_PREFIX}') - refusing to trade until "
                f"you confirm which account this gateway is actually logged into.")
    if mode == "live" and looks_paper:
        return (f"IBKR_TRADING_MODE=live but the connected gateway account "
                f"'{account_id}' looks like a PAPER account (starts with "
                f"'{_PAPER_ACCOUNT_PREFIX}') - refusing to trade: this would have placed "
                f"what you configured as a live order into a paper account, which is safe "
                f"but clearly not what was intended.")
    return None
