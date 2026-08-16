"""
Tests for alpaca_broker.py - almost entirely focused on trading_mode()'s
gating logic, since that's the one thing standing between "paper simulation"
and "real money moves". No live network calls (AlpacaClient._request isn't
exercised here - see the mocked HTTP tests below for those).
"""
import pytest

from app import alpaca_broker


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from a totally unconfigured state - no test here
    should ever accidentally inherit a real key from the environment."""
    for var in ("ALPACA_TRADING_MODE", "ALPACA_LIVE_TRADING_ACK",
               "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)


# ---- trading_mode(): the safety-critical gate -----------------------------

def test_mode_defaults_to_off_with_nothing_configured():
    assert alpaca_broker.trading_mode() == "off"


def test_mode_off_explicitly(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "off")
    assert alpaca_broker.trading_mode() == "off"


def test_mode_paper_needs_no_second_signal(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    assert alpaca_broker.trading_mode() == "paper"


def test_mode_live_without_ack_downgrades_to_paper_not_off(monkeypatch):
    # this is the core safety property: a bare ALPACA_TRADING_MODE=live
    # with no ack must NOT enable live trading - and critically, it must
    # not silently disable trading entirely either (which could surprise
    # someone expecting paper mode) - it downgrades to paper.
    monkeypatch.setenv("ALPACA_TRADING_MODE", "live")
    assert alpaca_broker.trading_mode() == "paper"


def test_mode_live_with_wrong_ack_string_downgrades_to_paper(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "live")
    monkeypatch.setenv("ALPACA_LIVE_TRADING_ACK", "yes")
    assert alpaca_broker.trading_mode() == "paper"


def test_mode_live_with_exact_ack_phrase_is_actually_live(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "live")
    monkeypatch.setenv("ALPACA_LIVE_TRADING_ACK", alpaca_broker.LIVE_ACK_PHRASE)
    assert alpaca_broker.trading_mode() == "live"


def test_mode_live_ack_is_case_and_whitespace_sensitive(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "live")
    monkeypatch.setenv("ALPACA_LIVE_TRADING_ACK", alpaca_broker.LIVE_ACK_PHRASE.lower())
    assert alpaca_broker.trading_mode() == "paper"
    monkeypatch.setenv("ALPACA_LIVE_TRADING_ACK", f" {alpaca_broker.LIVE_ACK_PHRASE} ")
    assert alpaca_broker.trading_mode() == "paper"


def test_mode_garbage_value_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "yolo")
    assert alpaca_broker.trading_mode() == "off"


def test_mode_is_case_insensitive_for_the_mode_itself(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "PAPER")
    assert alpaca_broker.trading_mode() == "paper"


# ---- client_from_env(): the other half of the gate ------------------------

def test_client_from_env_none_when_off():
    assert alpaca_broker.client_from_env() is None


def test_client_from_env_none_when_paper_mode_but_no_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    assert alpaca_broker.client_from_env() is None


def test_client_from_env_none_when_only_key_id_set(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-only")
    assert alpaca_broker.client_from_env() is None


def test_client_from_env_paper_client_when_fully_configured(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    client = alpaca_broker.client_from_env()
    assert client is not None
    assert client.live is False
    assert client.base_url == alpaca_broker.PAPER_BASE_URL


def test_client_from_env_live_client_only_with_full_double_confirmation(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "live")
    monkeypatch.setenv("ALPACA_LIVE_TRADING_ACK", alpaca_broker.LIVE_ACK_PHRASE)
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    client = alpaca_broker.client_from_env()
    assert client is not None
    assert client.live is True
    assert client.base_url == alpaca_broker.LIVE_BASE_URL


def test_client_from_env_live_without_ack_gives_a_paper_client_not_live(monkeypatch):
    # the critical end-to-end safety property: even with real credentials
    # present and ALPACA_TRADING_MODE=live set, a missing/wrong ack means
    # the client this function hands back points at the PAPER endpoint.
    monkeypatch.setenv("ALPACA_TRADING_MODE", "live")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    client = alpaca_broker.client_from_env()
    assert client is not None
    assert client.live is False
    assert client.base_url == alpaca_broker.PAPER_BASE_URL


# ---- AlpacaClient request plumbing (mocked HTTP, no network) --------------

class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_submit_order_posts_expected_body(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _FakeHTTPResponse(b'{"id": "abc123", "status": "accepted"}')

    monkeypatch.setattr(alpaca_broker.urllib.request, "urlopen", fake_urlopen)
    client = alpaca_broker.AlpacaClient("key", "secret", live=False)
    result = client.submit_order("AAPL", "buy", 10, order_type="market")

    assert result["id"] == "abc123"
    assert captured["method"] == "POST"
    assert captured["url"] == f"{alpaca_broker.PAPER_BASE_URL}/v2/orders"
    assert captured["headers"]["apca-api-key-id"] == "key"
    assert captured["headers"]["apca-api-secret-key"] == "secret"
    import json
    body = json.loads(captured["body"])
    assert body == {"symbol": "AAPL", "side": "buy", "qty": "10", "type": "market", "time_in_force": "day"}


def test_wait_for_fill_returns_once_status_is_filled(monkeypatch):
    calls = {"n": 0}

    def fake_get_order(order_id):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"id": order_id, "status": "accepted"}
        return {"id": order_id, "status": "filled", "filled_avg_price": "180.5", "filled_qty": "10"}

    client = alpaca_broker.AlpacaClient("key", "secret")
    monkeypatch.setattr(client, "get_order", fake_get_order)
    monkeypatch.setattr(alpaca_broker.time, "sleep", lambda s: None)
    order = client.wait_for_fill("abc123", timeout_sec=5, poll_sec=0)
    assert order["status"] == "filled"
    assert calls["n"] == 3


def test_wait_for_fill_raises_on_rejection(monkeypatch):
    client = alpaca_broker.AlpacaClient("key", "secret")
    monkeypatch.setattr(client, "get_order",
                        lambda oid: {"id": oid, "status": "rejected", "reject_reason": "insufficient buying power"})
    with pytest.raises(alpaca_broker.AlpacaError, match="insufficient buying power"):
        client.wait_for_fill("abc123")


def test_wait_for_fill_times_out_if_never_filled(monkeypatch):
    client = alpaca_broker.AlpacaClient("key", "secret")
    monkeypatch.setattr(client, "get_order", lambda oid: {"id": oid, "status": "pending_new"})
    monkeypatch.setattr(alpaca_broker.time, "sleep", lambda s: None)
    # timeout_sec=0 means the while loop's deadline is already in the past
    with pytest.raises(alpaca_broker.AlpacaError, match="did not fill"):
        client.wait_for_fill("abc123", timeout_sec=0)


# ---- cached_is_tradable(): shared by the auto-bot AND manual order placement
# (oms.AlpacaRouter.fill) - one cache, one TTL, not two drifting copies.

@pytest.fixture(autouse=True)
def clean_tradability_cache():
    """The cache is module-global (deliberately, so the bot's poll loop and
    a human's order placement share hits) - reset it around each test so
    one test's cached answer can't leak into another's assertions."""
    alpaca_broker._tradability_cache.clear()
    yield
    alpaca_broker._tradability_cache.clear()


def test_cached_is_tradable_true_when_no_broker_configured():
    # ALPACA_TRADING_MODE unset (clean_env fixture) -> client_from_env() is
    # None -> nothing to check against, same as PaperRouter accepting any symbol.
    assert alpaca_broker.cached_is_tradable("ANYTHING-AT-ALL") is True


def test_cached_is_tradable_reflects_the_client_and_caches_the_result(monkeypatch):
    calls = {"n": 0}

    class _FakeClient:
        def is_tradable(self, symbol):
            calls["n"] += 1
            return symbol == "AAPL"

    monkeypatch.setattr(alpaca_broker, "client_from_env", lambda: _FakeClient())
    assert alpaca_broker.cached_is_tradable("AAPL") is True
    assert alpaca_broker.cached_is_tradable("BTC-GBP") is False
    assert calls["n"] == 2

    # second call for the same symbols hits the cache, not the client again
    assert alpaca_broker.cached_is_tradable("AAPL") is True
    assert alpaca_broker.cached_is_tradable("BTC-GBP") is False
    assert calls["n"] == 2


def test_cached_is_tradable_rechecks_after_ttl_expires(monkeypatch):
    calls = {"n": 0}

    class _FakeClient:
        def is_tradable(self, symbol):
            calls["n"] += 1
            return True

    monkeypatch.setattr(alpaca_broker, "client_from_env", lambda: _FakeClient())
    now = [1000.0]
    monkeypatch.setattr(alpaca_broker.time, "monotonic", lambda: now[0])

    alpaca_broker.cached_is_tradable("AAPL")
    assert calls["n"] == 1
    now[0] += alpaca_broker._TRADABILITY_TTL_SEC + 1
    alpaca_broker.cached_is_tradable("AAPL")
    assert calls["n"] == 2
