"""Tests for the TickHouse admin journey: preview -> create -> provision."""
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


# Sample non-secret cloud/k8s coordinates, one full set per cloud - the
# mandatory fields validate_spec now requires (see tickhouse.py CLOUD_CONFIG_FIELDS).
_SAMPLE_TARGET_CONFIG = {
    "aws": {"region": "eu-west-1", "vpc_id": "vpc-1", "subnet_ids": "subnet-1,subnet-2",
            "eks_cluster": "acme-prod", "namespace": "tick", "storage_class": "gp3",
            "ingress_class": "nginx"},
    "azure": {"subscription_id": "sub-1", "resource_group": "acme-rg", "location": "westeurope",
              "aks_cluster": "acme-aks", "namespace": "tick", "storage_class": "managed-premium",
              "ingress_class": "nginx"},
    "gcp": {"project_id": "acme-proj", "region": "europe-west1", "gke_cluster": "acme-gke",
            "namespace": "tick", "storage_class": "standard-rwo", "ingress_class": "nginx"},
    "onprem": {"compose_project_dir": "/srv/kdb"},
}


def _hi(name="acme-emea", location="aws", profile="low-latency", ranges="a-m, n-z", idb=False,
        target_config=None):
    return {"name": name, "location": location, "os": "ubuntu-22.04",
            "profile": profile, "shard_ranges": ranges, "idb": idb,
            "target_config": target_config if target_config is not None
                             else _SAMPLE_TARGET_CONFIG.get(location, {})}


def test_meta_lists_choices(client, tadmin):
    r = client.get("/tickhouses/meta", headers=tadmin)
    assert r.status_code == 200
    body = r.json()
    assert "aws" in body["clouds"] and "low-latency" in body["profiles"]


def test_preview_auto_tunes_and_validates(client, tadmin):
    r = client.post("/tickhouses/preview", json=_hi(), headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["problems"] == []
    comps = {c["type"]: c for c in body["spec"]["components"]}
    # auto-tuning filled hardware for every component (item 4)
    assert comps["tickerplant"]["hardware"]["instance_type"]      # non-empty for aws
    assert "cpu-pinning" in comps["tickerplant"]["hardware"]["tuning"]   # low-latency
    assert comps["hdb"]["hardware"]["disk_gb"] >= 2000


def test_preview_reports_bad_shard_ranges(client, tadmin):
    r = client.post("/tickhouses/preview", json=_hi(ranges="z-a"), headers=tadmin)
    assert r.status_code == 400


def test_suggest_returns_a_profile_and_shard_count_for_review(client, tadmin):
    r = client.post("/tickhouses/suggest",
                    json={"tick_to_trade_target_ms": 100, "peak_msgs_per_sec": 8000},
                    headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"] in ("low-latency", "balanced", "high-throughput")
    assert body["shard_count"] >= 1
    assert body["caveats"]  # never silent about these being estimates


def test_suggest_with_no_body_fields_still_returns_a_default(client, tadmin):
    r = client.post("/tickhouses/suggest", json={}, headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["profile"] == "balanced"


def test_suggest_output_feeds_straight_into_preview(client, tadmin):
    # the intended flow: suggest -> operator reviews/edits -> preview -> create
    # -> provision. Confirm the suggested profile is one preview actually accepts.
    suggestion = client.post("/tickhouses/suggest",
                             json={"tick_to_trade_target_ms": 50}, headers=tadmin).json()
    r = client.post("/tickhouses/preview",
                    json=_hi(profile=suggestion["profile"]), headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["problems"] == []


def test_create_list_get_delete(client, tadmin):
    created = client.post("/tickhouses", json=_hi(name="acme-apac"), headers=tadmin)
    assert created.status_code == 200, created.text
    th_id = created.json()["id"]

    listing = client.get("/tickhouses", headers=tadmin).json()
    assert any(t["id"] == th_id for t in listing)

    detail = client.get(f"/tickhouses/{th_id}", headers=tadmin).json()
    assert detail["spec"]["name"] == "acme-apac"
    assert detail["status"] == "defined"

    d = client.delete(f"/tickhouses/{th_id}", headers=tadmin)
    assert d.status_code == 200 and d.json()["deleted"] is True


def test_provision_queues_spec_to_matching_agent(client, tadmin):
    # a tickhouse targeting aws
    th_id = client.post("/tickhouses", json=_hi(name="acme-aws", location="aws"),
                        headers=tadmin).json()["id"]
    # an agent in aws
    agent = client.post("/fleet/agents", json={"name": "acme-aws-agent", "environment": "aws"},
                        headers=tadmin).json()

    r = client.post(f"/tickhouses/{th_id}/provision",
                    json={"agent_id": agent["agent_id"]}, headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "provisioning"

    # the queued command carries the full tickhouse spec
    cmds = client.get(f"/fleet/agents/{agent['agent_id']}/commands", headers=tadmin).json()
    prov = next(c for c in cmds if c["action"] == "provision")
    import json
    payload = json.loads(prov["payload"])
    assert payload["desired"]["tickhouse"] == "acme-aws"
    assert payload["desired"]["shardCount"] == 2
    assert payload["desired"]["components"][0]["hardware"] is not None


def test_create_with_feed_handler_attaches_it_to_the_new_tickhouse(client, tadmin):
    body = _hi(name="acme-coinbase")
    body["feed_handler"] = {"provider": "COINBASE", "feed": "MATCHES", "enabled": True}
    r = client.post("/tickhouses", json=body, headers=tadmin)
    assert r.status_code == 200, r.text
    result = r.json()
    th_id = result["id"]
    assert result["feed_handler"]["provider"] == "COINBASE"
    assert result["feed_handler"]["has_secrets"] is False
    fh_id = result["feed_handler"]["id"]

    # the created FeedHandlerInstance really is linked, both directions
    fh = client.get("/feedhandlers", headers=tadmin).json()
    linked = next(f for f in fh if f["id"] == fh_id)
    assert linked["tickhouse_id"] == th_id

    scoped = client.get(f"/feedhandlers?tickhouse_id={th_id}", headers=tadmin).json()
    assert [f["id"] for f in scoped] == [fh_id]


def test_create_with_feed_handler_missing_credentials_fails(client, tadmin):
    body = _hi(name="acme-fix")
    body["feed_handler"] = {"provider": "GENERIC_FIX", "feed": "MARKET_DATA"}  # no secrets supplied
    r = client.post("/tickhouses", json=body, headers=tadmin)
    assert r.status_code == 400
    assert "credential" in r.json()["detail"]


def test_create_without_feed_handler_omits_the_field(client, tadmin):
    r = client.post("/tickhouses", json=_hi(name="acme-no-feed"), headers=tadmin)
    assert r.status_code == 200, r.text
    assert "feed_handler" not in r.json()


def test_provision_rejects_agent_in_wrong_cloud(client, tadmin):
    th_id = client.post("/tickhouses", json=_hi(name="acme-gcp", location="gcp"),
                        headers=tadmin).json()["id"]
    agent = client.post("/fleet/agents", json={"name": "wrong-cloud", "environment": "aws"},
                        headers=tadmin).json()
    r = client.post(f"/tickhouses/{th_id}/provision",
                    json={"agent_id": agent["agent_id"]}, headers=tadmin)
    assert r.status_code == 400
    assert "targets" in r.json()["detail"]
