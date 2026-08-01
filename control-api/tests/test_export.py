"""Tests for the data-export sink catalog endpoint."""
import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login",
                    json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_export_sinks_catalog(client, tadmin):
    r = client.get("/export/sinks", headers=tadmin)
    assert r.status_code == 200, r.text
    cat = {s["name"]: s for s in r.json()}
    assert set(cat) == {"parquet", "snowflake", "databricks", "fabric"}
    assert cat["parquet"]["offline"] is True
    assert cat["snowflake"]["offline"] is False
    assert all(s["requires"] for s in cat.values())


def test_export_sinks_requires_auth(client):
    assert client.get("/export/sinks").status_code in (401, 403)
