"""
Tests for providers/alpaca.py's run() connect/auth/subscribe handshake -
specifically a regression test for a confirmed-live bug: `authed` (the
"has THIS connection completed its auth handshake" flag) was declared once
outside on_open/on_message, so it stayed True forever after the first
successful auth. websocket-client's run_forever(reconnect=5) calls on_open
again on every reconnect (and a fresh auth frame really was sent), but
on_message's subscribe-gate never re-armed - so every reconnect after the
first silently re-authenticated and then never resubscribed to anything.
Confirmed live: a container that had been running 2+ days had reconnected
~160 times but only ever logged "authenticated, subscribed" once, at the
very first connection - no trade data flowed for the rest of that run.

No real network: providers.alpaca.py's `import websocket` is lazy (inside
run()), so replacing sys.modules["websocket"] with a fake before calling
run() intercepts it. The fake WebSocketApp captures on_open/on_message and
a fake run_forever() drives them directly, simulating however many
connect/auth/message/reconnect cycles a test wants - no sockets, no thread.
"""
import json
import sys
import types

import pytest

from providers import get_provider
from providers.tests.test_registry import FakePublisher

AUTH_SUCCESS = json.dumps([{"T": "success", "msg": "authenticated"}])
TRADE_MSG = json.dumps([{"T": "t", "S": "AAPL", "p": 150.0, "s": 100, "t": "2026-01-01T00:00:00Z"}])


class FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(json.loads(msg))


class FakeWebSocketApp:
    """Stands in for websocket.WebSocketApp - captures the real callbacks
    the provider passes in, and exposes a fake run_forever() a test drives
    by calling .simulate_connect() / .simulate_message() as many times as it
    wants, instead of actually opening a socket."""
    instances = []

    def __init__(self, url, on_open=None, on_message=None, on_error=None):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.ws = FakeWS()
        FakeWebSocketApp.instances.append(self)

    def run_forever(self, reconnect=None):
        pass  # driven externally by the test via simulate_connect/simulate_message

    def simulate_connect(self):
        self.on_open(self.ws)

    def simulate_message(self, raw):
        self.on_message(self.ws, raw)


@pytest.fixture(autouse=True)
def fake_websocket_module(monkeypatch):
    FakeWebSocketApp.instances = []
    fake_module = types.ModuleType("websocket")
    fake_module.WebSocketApp = FakeWebSocketApp
    monkeypatch.setitem(sys.modules, "websocket", fake_module)
    yield


def _make_provider():
    pub = FakePublisher()
    prov = get_provider("alpaca")(["AAPL"], pub, shard_count=1,
                                  api_key="key", api_secret="secret")
    return prov, pub


def test_first_connection_authenticates_and_subscribes():
    prov, pub = _make_provider()
    prov.run()
    app = FakeWebSocketApp.instances[0]

    app.simulate_connect()
    assert app.ws.sent == [{"action": "auth", "key": "key", "secret": "secret"}]

    app.simulate_message(AUTH_SUCCESS)
    assert app.ws.sent[-1] == {"action": "subscribe", "trades": ["AAPL"]}


def test_trade_messages_publish_after_auth():
    prov, pub = _make_provider()
    prov.run()
    app = FakeWebSocketApp.instances[0]
    app.simulate_connect()
    app.simulate_message(AUTH_SUCCESS)

    app.simulate_message(TRADE_MSG)
    assert len(pub.rows) == 1
    assert pub.rows[0][1] == "AAPL" and pub.rows[0][2] == 150.0


def test_reconnect_resubscribes_instead_of_going_silent_forever():
    """The actual regression: a second on_open (websocket-client's own
    automatic-reconnect behavior) must re-arm the auth-then-subscribe
    handshake, not silently skip it because a PREVIOUS connection already
    authenticated once. Before the fix, the second simulate_message(
    AUTH_SUCCESS) below fell through to _handle_raw (treated as a trade
    message, parsed to 0 rows) instead of sending a second subscribe -
    the connection would have looked "fine" (reconnected, no errors) while
    never asking for data again."""
    prov, pub = _make_provider()
    prov.run()
    app = FakeWebSocketApp.instances[0]

    # first connection: connect, auth, subscribe, one trade flows
    app.simulate_connect()
    app.simulate_message(AUTH_SUCCESS)
    app.simulate_message(TRADE_MSG)
    assert len(pub.rows) == 1

    # connection drops and reconnects - websocket-client calls on_open again
    # on the SAME WebSocketApp instance/closures, exactly as run_forever's
    # real reconnect does within one run() call.
    app.simulate_connect()
    assert app.ws.sent[-1] == {"action": "auth", "key": "key", "secret": "secret"}

    app.simulate_message(AUTH_SUCCESS)
    assert app.ws.sent[-1] == {"action": "subscribe", "trades": ["AAPL"]}, (
        "reconnect did not resubscribe - this is the bug: a stale 'already "
        "authenticated' flag from the first connection silently swallowed "
        "the second connection's auth confirmation as if it were a trade")

    # and data flows again after the resubscribe
    app.simulate_message(TRADE_MSG)
    assert len(pub.rows) == 2
