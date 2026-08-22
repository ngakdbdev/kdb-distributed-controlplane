"""
Tests for ibkr_broker.py - the trading_mode() gate (identical safety
contract to alpaca_broker.py's), the account-prefix cross-check, and the
order-confirmation-loop client logic, all against mocked HTTP (no real
gateway reachable in this environment - see the module's own docstring on
why this is coded-to-spec, not live-verified).
"""
import pytest

from app import ibkr_broker


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("IBKR_TRADING_MODE", "IBKR_LIVE_TRADING_ACK",
               "IBKR_GATEWAY_BASE_URL", "IBKR_GATEWAY_VERIFY_SSL"):
        monkeypatch.delenv(var, raising=False)


# ---- trading_mode(): same safety contract as alpaca_broker's -------------

def test_mode_defaults_to_off():
    assert ibkr_broker.trading_mode() == "off"


def test_mode_paper_needs_no_second_signal(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "paper")
    assert ibkr_broker.trading_mode() == "paper"


def test_mode_live_without_ack_downgrades_to_paper(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "live")
    assert ibkr_broker.trading_mode() == "paper"


def test_mode_live_with_exact_ack_is_live(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "live")
    monkeypatch.setenv("IBKR_LIVE_TRADING_ACK", ibkr_broker.LIVE_ACK_PHRASE)
    assert ibkr_broker.trading_mode() == "live"


def test_mode_garbage_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "yolo")
    assert ibkr_broker.trading_mode() == "off"


# ---- client_from_env() -----------------------------------------------------

def test_client_from_env_none_when_off():
    assert ibkr_broker.client_from_env() is None


def test_client_from_env_uses_default_gateway_url(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "paper")
    client = ibkr_broker.client_from_env()
    assert client.base_url == "https://localhost:5000/v1/api"


def test_client_from_env_uses_configured_gateway_url(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "paper")
    monkeypatch.setenv("IBKR_GATEWAY_BASE_URL", "https://ibeam-host:5000/v1/api")
    client = ibkr_broker.client_from_env()
    assert client.base_url == "https://ibeam-host:5000/v1/api"


# ---- verify_account_matches_mode(): the paper/live cross-check -----------

class _FakeAccountClient:
    def __init__(self, account_id=None, raise_on_lookup=False):
        self._account_id = account_id
        self._raise = raise_on_lookup

    def primary_account_id(self):
        if self._raise:
            raise ibkr_broker.IBKRError("gateway unreachable")
        return self._account_id


def test_verify_paper_mode_with_paper_account_passes():
    client = _FakeAccountClient(account_id="DU1234567")
    assert ibkr_broker.verify_account_matches_mode(client, "paper") is None


def test_verify_paper_mode_with_live_looking_account_refuses():
    client = _FakeAccountClient(account_id="U1234567")
    msg = ibkr_broker.verify_account_matches_mode(client, "paper")
    assert msg is not None and "doesn't look like a paper account" in msg


def test_verify_live_mode_with_live_account_passes():
    client = _FakeAccountClient(account_id="U1234567")
    assert ibkr_broker.verify_account_matches_mode(client, "live") is None


def test_verify_live_mode_with_paper_looking_account_refuses():
    client = _FakeAccountClient(account_id="DU1234567")
    msg = ibkr_broker.verify_account_matches_mode(client, "live")
    assert msg is not None and "looks like a PAPER account" in msg


def test_verify_when_account_lookup_fails_does_not_block_here():
    # a genuinely unreachable gateway should fail on the real order call
    # with a clearer error, not be masked by this cross-check
    client = _FakeAccountClient(raise_on_lookup=True)
    assert ibkr_broker.verify_account_matches_mode(client, "paper") is None


def test_verify_when_no_account_returned_does_not_block():
    client = _FakeAccountClient(account_id=None)
    assert ibkr_broker.verify_account_matches_mode(client, "paper") is None


# ---- IBKRClient request plumbing (mocked HTTP, no network) ---------------

class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_resolve_conid_returns_first_match(monkeypatch):
    def fake_urlopen(req, timeout=15, context=None):
        return _FakeHTTPResponse(b'[{"conid": 265598, "symbol": "AAPL"}]')
    monkeypatch.setattr(ibkr_broker.urllib.request, "urlopen", fake_urlopen)
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    assert client.resolve_conid("AAPL") == 265598
    assert client._conids["AAPL"] == 265598  # cached


def test_resolve_conid_caches_across_calls(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=15, context=None):
        calls["n"] += 1
        return _FakeHTTPResponse(b'[{"conid": 1}]')
    monkeypatch.setattr(ibkr_broker.urllib.request, "urlopen", fake_urlopen)
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    client.resolve_conid("AAPL")
    client.resolve_conid("AAPL")
    assert calls["n"] == 1


def test_submit_order_returns_immediately_when_no_confirmation_needed(monkeypatch):
    def fake_urlopen(req, timeout=15, context=None):
        return _FakeHTTPResponse(b'[{"order_id": "abc123", "order_status": "Submitted"}]')
    monkeypatch.setattr(ibkr_broker.urllib.request, "urlopen", fake_urlopen)
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    result = client.submit_order("DU123", 265598, "buy", 10)
    assert result["order_id"] == "abc123"


def test_submit_order_follows_confirmation_loop(monkeypatch):
    # first response: a message that needs confirming; second (after
    # POSTing the confirmation): the real order id
    responses = [
        b'[{"id": "reply-1", "message": ["outside regular trading hours"]}]',
        b'[{"order_id": "abc123", "order_status": "Submitted"}]',
    ]
    calls = []

    def fake_urlopen(req, timeout=15, context=None):
        calls.append(req.full_url)
        return _FakeHTTPResponse(responses.pop(0))
    monkeypatch.setattr(ibkr_broker.urllib.request, "urlopen", fake_urlopen)
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    result = client.submit_order("DU123", 265598, "buy", 10)
    assert result["order_id"] == "abc123"
    assert any("/iserver/reply/reply-1" in c for c in calls)


def test_submit_order_raises_if_confirmation_loop_never_resolves(monkeypatch):
    def fake_urlopen(req, timeout=15, context=None):
        return _FakeHTTPResponse(b'[{"id": "reply-1", "message": ["still asking"]}]')
    monkeypatch.setattr(ibkr_broker.urllib.request, "urlopen", fake_urlopen)
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    with pytest.raises(ibkr_broker.IBKRError, match="did not resolve"):
        client.submit_order("DU123", 265598, "buy", 10, max_confirmations=3)


def test_wait_for_fill_returns_once_filled(monkeypatch):
    calls = {"n": 0}

    def fake_order_status(order_id):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"order_status": "Submitted"}
        return {"order_status": "Filled", "avg_price": "180.5"}

    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    monkeypatch.setattr(client, "order_status", fake_order_status)
    monkeypatch.setattr(ibkr_broker.time, "sleep", lambda s: None)
    result = client.wait_for_fill("abc123", timeout_sec=5, poll_sec=0)
    assert result["order_status"] == "Filled"


def test_wait_for_fill_raises_on_cancellation(monkeypatch):
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    monkeypatch.setattr(client, "order_status", lambda oid: {"order_status": "Cancelled"})
    with pytest.raises(ibkr_broker.IBKRError, match="cancelled"):
        client.wait_for_fill("abc123")


def test_wait_for_fill_times_out(monkeypatch):
    client = ibkr_broker.IBKRClient("https://localhost:5000/v1/api")
    monkeypatch.setattr(client, "order_status", lambda oid: {"order_status": "Submitted"})
    monkeypatch.setattr(ibkr_broker.time, "sleep", lambda s: None)
    with pytest.raises(ibkr_broker.IBKRError, match="did not fill"):
        client.wait_for_fill("abc123", timeout_sec=0)
