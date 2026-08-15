"""Tests for app/asset_metadata.py - the durable (asset_class, market,
currency) record symbol_discovery.py's poll loop persists, and
symbols.classify()'s fx/crypto/unknown distinction it depends on."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.main as m
from app import asset_metadata, symbols as symref
from app.db import engine
from app.models import AssetMetadata


def test_classify_recognizes_an_fx_pair_over_a_crypto_pair():
    market, currency, asset_class = symref.classify("EUR/USD")
    assert asset_class == "fx" and market == "FX" and currency == "USD"


def test_classify_recognizes_a_crypto_pair_with_a_coin_base():
    market, currency, asset_class = symref.classify("ZZBTC-USD")
    assert asset_class == "crypto" and market == "CRYPTO"


def test_classify_falls_back_to_unknown_for_an_unrecognized_shape():
    market, currency, asset_class = symref.classify("ZZFOOBARBAZ")
    assert asset_class == "unknown" and market == "LIVE"


@pytest.fixture()
def session():
    with Session(engine) as s:
        yield s


def test_record_seen_creates_a_new_row_with_first_and_last_seen(session):
    asset_metadata.record_seen(["ZZAM-USD"], session)
    row = session.get(AssetMetadata, "ZZAM-USD")
    assert row is not None
    assert row.asset_class == "crypto"
    assert row.first_seen_at == row.last_seen_at


def test_record_seen_bumps_last_seen_without_reclassifying_on_repeat(session):
    asset_metadata.record_seen(["ZZAM2-USD"], session)
    first = session.get(AssetMetadata, "ZZAM2-USD")
    first_seen = first.first_seen_at

    new_count = asset_metadata.record_seen(["ZZAM2-USD"], session)
    assert new_count == 0
    row = session.get(AssetMetadata, "ZZAM2-USD")
    assert row.first_seen_at == first_seen  # unchanged
    assert row.asset_class == "crypto"      # unchanged


def test_class_breakdown_counts_by_asset_class(session):
    asset_metadata.record_seen(["ZZAM3-USD", "EUR/GBP"], session)
    breakdown = {b["asset_class"]: b["count"] for b in asset_metadata.class_breakdown(session)}
    assert breakdown.get("crypto", 0) >= 1
    assert breakdown.get("fx", 0) >= 1


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_asset_classes_endpoint(client, tadmin, session):
    asset_metadata.record_seen(["ZZAM4-USD"], session)
    r = client.get("/symbols/asset-classes", headers=tadmin)
    assert r.status_code == 200, r.text
    assert any(b["asset_class"] == "crypto" for b in r.json()["asset_classes"])


def test_symbol_discovery_run_once_persists_asset_metadata(monkeypatch, session):
    from app import symbol_discovery
    monkeypatch.setattr(symbol_discovery.signal_engine, "fetch_universe_symbols",
                        lambda connect=None: ["ZZAM5-USD"])
    symbol_discovery.run_once()
    row = session.get(AssetMetadata, "ZZAM5-USD")
    assert row is not None and row.asset_class == "crypto"
