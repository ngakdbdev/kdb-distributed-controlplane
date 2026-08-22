"""Tests for the feed-handler admin portal (routers/feedhandlers.py) - the
control-api side of data-plane/feedhandler-cpp's protocol/venue adapter
engine. Covers the catalog, CRUD, secret encryption-at-rest, and the
require_admin-gated decrypted engine-config endpoint."""
import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_catalog_lists_known_providers(client, tadmin):
    r = client.get("/feedhandlers/catalog", headers=tadmin)
    assert r.status_code == 200, r.text
    providers = {p["provider"] for p in r.json()["providers"]}
    assert {"COINBASE", "NASDAQ", "CME", "GENERIC_FIX"} <= providers


def test_create_coinbase_instance_needs_no_credentials(client, tadmin):
    r = client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES", "enabled": True},
                    headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "COINBASE"
    assert body["has_secrets"] is False
    assert body["config"]["venue_adapter"] == "generic_wsjson"


def test_create_generic_fix_instance_requires_credentials(client, tadmin):
    r = client.post("/feedhandlers", json={"provider": "GENERIC_FIX", "feed": "MARKET_DATA"}, headers=tadmin)
    assert r.status_code == 400
    assert "username" in r.json()["detail"]

    r2 = client.post("/feedhandlers", json={
        "provider": "GENERIC_FIX", "feed": "MARKET_DATA",
        "secrets": {"username": "trader1", "password": "s3cr3t"},
    }, headers=tadmin)
    assert r2.status_code == 200, r2.text
    assert r2.json()["has_secrets"] is True


def test_unknown_provider_feed_rejected(client, tadmin):
    r = client.post("/feedhandlers", json={"provider": "NOPE", "feed": "NOPE"}, headers=tadmin)
    assert r.status_code == 400


def test_secrets_never_appear_in_list_or_get_responses(client, tadmin):
    client.post("/feedhandlers", json={
        "provider": "GENERIC_FIX", "feed": "MARKET_DATA",
        "secrets": {"username": "trader1", "password": "s3cr3t-value"},
    }, headers=tadmin)

    r = client.get("/feedhandlers", headers=tadmin)
    assert r.status_code == 200
    body_text = r.text
    assert "s3cr3t-value" not in body_text
    assert "trader1" not in body_text


def test_secrets_are_encrypted_at_rest_in_the_database(client, tadmin):
    from sqlmodel import Session, select
    from app.db import engine
    from app.models import FeedHandlerInstance

    created = client.post("/feedhandlers", json={
        "provider": "GENERIC_FIX", "feed": "MARKET_DATA",
        "secrets": {"username": "trader2", "password": "another-secret-value"},
    }, headers=tadmin).json()

    with Session(engine) as session:
        row = session.get(FeedHandlerInstance, created["id"])
        assert "another-secret-value" not in row.secrets_json
        assert row.secrets_json.startswith('{"username": "enc:') or "enc:v1:" in row.secrets_json


def test_engine_config_returns_decrypted_secrets(client, tadmin):
    created = client.post("/feedhandlers", json={
        "provider": "GENERIC_FIX", "feed": "MARKET_DATA",
        "secrets": {"username": "trader3", "password": "decrypt-me"},
    }, headers=tadmin).json()

    r = client.get(f"/feedhandlers/{created['id']}/engine-config", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secrets"]["username"] == "trader3"
    assert body["secrets"]["password"] == "decrypt-me"
    assert body["config"]["venue_adapter"] == "generic_fix"


def test_update_and_delete_roundtrip(client, tadmin):
    created = client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES"}, headers=tadmin).json()
    fid = created["id"]

    r = client.put(f"/feedhandlers/{fid}", json={"provider": "COINBASE", "feed": "MATCHES", "enabled": True},
                   headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    r_del = client.delete(f"/feedhandlers/{fid}", headers=tadmin)
    assert r_del.status_code == 200

    remaining = client.get("/feedhandlers", headers=tadmin).json()
    assert fid not in [f["id"] for f in remaining]


def test_delete_missing_instance_404s(client, tadmin):
    r = client.delete("/feedhandlers/999999", headers=tadmin)
    assert r.status_code == 404


def test_feedhandlers_requires_auth(client):
    assert client.get("/feedhandlers/catalog").status_code in (401, 403)


def _create_tickhouse(client, tadmin, name="acme-fh-test"):
    body = {"name": name, "location": "onprem", "os": "ubuntu-22.04", "profile": "balanced",
           "shard_ranges": "a-m, n-z", "target_config": {"compose_project_dir": "/srv/kdb"}}
    r = client.post("/tickhouses", json=body, headers=tadmin)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_create_with_tickhouse_id_links_it(client, tadmin):
    th_id = _create_tickhouse(client, tadmin)
    r = client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES", "tickhouse_id": th_id},
                    headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["tickhouse_id"] == th_id


def test_create_with_unknown_tickhouse_id_404s(client, tadmin):
    r = client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES", "tickhouse_id": 999999},
                    headers=tadmin)
    assert r.status_code == 404


def test_update_can_attach_and_detach_a_tickhouse(client, tadmin):
    th_id = _create_tickhouse(client, tadmin, name="acme-fh-update")
    created = client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES"}, headers=tadmin).json()
    assert created["tickhouse_id"] is None

    r = client.put(f"/feedhandlers/{created['id']}", json={
        "provider": "COINBASE", "feed": "MATCHES", "tickhouse_id": th_id,
    }, headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["tickhouse_id"] == th_id

    # detach again by omitting tickhouse_id (defaults to None)
    r2 = client.put(f"/feedhandlers/{created['id']}", json={"provider": "COINBASE", "feed": "MATCHES"},
                    headers=tadmin)
    assert r2.json()["tickhouse_id"] is None


def test_list_filters_by_tickhouse_id(client, tadmin):
    th_id = _create_tickhouse(client, tadmin, name="acme-fh-filter")
    linked = client.post("/feedhandlers", json={
        "provider": "COINBASE", "feed": "MATCHES", "tickhouse_id": th_id,
    }, headers=tadmin).json()
    client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES"}, headers=tadmin)  # unlinked

    scoped = client.get(f"/feedhandlers?tickhouse_id={th_id}", headers=tadmin).json()
    assert [f["id"] for f in scoped] == [linked["id"]]


# ---- Catalog expansion: "add all exchanges/protocols for future
# integration" - covers both the honest "catalog_only" flag (venues with
# no working decoder yet) and that it's actually enforced, not just
# decorative text in the catalog response. --------------------------------

def test_catalog_includes_major_global_exchanges_and_vendors(client, tadmin):
    r = client.get("/feedhandlers/catalog", headers=tadmin)
    assert r.status_code == 200, r.text
    providers = {p["provider"] for p in r.json()["providers"]}
    # a representative sample across regions/asset classes/vendors, not
    # every single one - the catalog itself is the source of truth for the
    # full list.
    assert {
        "NYSE", "CBOE_US", "IEX", "CBOT", "NYMEX", "COMEX", "ICE", "EUREX",
        "DEUTSCHE_BOERSE", "LSE", "EURONEXT", "BORSA_ISTANBUL", "ASX",
        "HKEX", "JPX", "KRX", "B3", "LSEG_REFINITIV_RDP", "SP_GLOBAL", "FACTSET",
    } <= providers


def test_every_catalog_entry_has_consistent_engine_support_and_venue_adapter(client, tadmin):
    r = client.get("/feedhandlers/catalog", headers=tadmin)
    entries = r.json()["providers"]
    assert len(entries) > 20  # sanity - this really is an expanded catalog, not the original 4
    registered_keys = {"nasdaq_itch", "itch_style", "cme_mdp3", "sbe_generic", "generic_fix", "generic_wsjson"}
    for e in entries:
        adapter = e["default_config"]["venue_adapter"]
        if e["engine_support"] == "decoder_implemented":
            assert adapter in registered_keys, f"{e['provider']}/{e['feed']}: {adapter}"
        else:
            assert e["engine_support"] == "catalog_only", f"{e['provider']}/{e['feed']}"
            assert adapter is None, f"{e['provider']}/{e['feed']} is catalog_only but has a venue_adapter"


def test_bloomberg_bpipe_and_crims_are_not_in_this_catalog(client, tadmin):
    # those are handled by a separate, pre-existing simulated integration
    # (data-plane/feeds/bpipe_sim.py, crims_sim.py) - keeping them out of
    # this catalog avoids two competing "activate Bloomberg" paths.
    providers = {p["provider"] for p in client.get("/feedhandlers/catalog", headers=tadmin).json()["providers"]}
    assert not any("BLOOMBERG" in p or "BPIPE" in p or "CRIMS" in p for p in providers)


def test_cannot_create_an_enabled_catalog_only_feed_handler(client, tadmin):
    r = client.post("/feedhandlers", json={"provider": "NYSE", "feed": "PILLAR", "enabled": True}, headers=tadmin)
    assert r.status_code == 400
    assert "catalog-only" in r.json()["detail"]


def test_can_create_a_disabled_catalog_only_feed_handler_to_track_the_plan(client, tadmin):
    r = client.post("/feedhandlers", json={"provider": "NYSE", "feed": "PILLAR", "enabled": False}, headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["config"]["venue_adapter"] is None


def test_cannot_enable_a_catalog_only_feed_handler_via_update(client, tadmin):
    created = client.post("/feedhandlers", json={"provider": "CBOE_US", "feed": "PITCH"}, headers=tadmin).json()
    r = client.put(f"/feedhandlers/{created['id']}", json={
        "provider": "CBOE_US", "feed": "PITCH", "enabled": True,
    }, headers=tadmin)
    assert r.status_code == 400
    assert "catalog-only" in r.json()["detail"]


def test_new_decoder_implemented_entries_reuse_the_registered_venue_adapter_keys(client, tadmin):
    entries = {p["provider"]: p for p in client.get("/feedhandlers/catalog", headers=tadmin).json()["providers"]}
    assert entries["ASX"]["default_config"]["venue_adapter"] == "itch_style"
    assert entries["BORSA_ISTANBUL"]["default_config"]["venue_adapter"] == "itch_style"
    assert entries["CBOT"]["default_config"]["venue_adapter"] == "cme_mdp3"
    assert entries["EUREX"]["default_config"]["venue_adapter"] == "sbe_generic"
    assert entries["DEUTSCHE_BOERSE"]["default_config"]["venue_adapter"] == "sbe_generic"


def test_generic_protocol_family_catalog_entries_exist(client, tadmin):
    # any-venue entries mirroring GENERIC_FIX for the other three protocol
    # families the engine actually speaks.
    providers = {p["provider"] for p in client.get("/feedhandlers/catalog", headers=tadmin).json()["providers"]}
    assert {"GENERIC_ITCH_MOLDUDP64", "GENERIC_SBE", "GENERIC_WSJSON"} <= providers
