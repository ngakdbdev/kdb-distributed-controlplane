"""
Tests for the provisioning path added to the fleet router: a tenant admin
picks an environment (agent) + shard count, we queue a `provision` command
carrying the desired topology, the agent pulls it on heartbeat, runs it, and
reports the result. Mirrors the auth used by the tenant-scoped SSO/LDAP tests
(the seeded demo tenant admin).
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import topology


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    # seeded demo TENANT admin (fleet endpoints are tenant-scoped)
    r = client.post("/auth/login",
                    json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _register_agent(client, tadmin, env="aws", name="acme-aws-prod"):
    r = client.post("/fleet/agents", json={"name": name, "environment": env}, headers=tadmin)
    assert r.status_code == 200, r.text
    return r.json()


# ---- provision request ----------------------------------------------------

def test_provision_queues_command_with_desired_topology(client, tadmin):
    agent = _register_agent(client, tadmin)
    r = client.post(f"/fleet/agents/{agent['agent_id']}/provision",
                    json={"shard_count": 4, "note": "demo for prospect"}, headers=tadmin)
    assert r.status_code == 200, r.text
    cmd = r.json()
    assert cmd["action"] == "provision"
    assert cmd["service"] == "data-plane"
    assert cmd["status"] == "queued"

    payload = json.loads(cmd["payload"])
    assert payload["desired"]["shardCount"] == 4
    # the embedded topology is the canonical one, so agent + gateway can't drift
    assert payload["desired"]["topology"] == topology.shards_json(4)
    assert payload["note"] == "demo for prospect"
    # 4 shards => 4 tickerplants named in the managed-services list
    tps = [s for s in payload["desired"]["services"] if s.startswith("tp-")]
    assert len(tps) == 4


@pytest.mark.parametrize("bad", [0, -1, topology.MAX_SHARDS + 1])
def test_provision_rejects_out_of_range_shard_count(client, tadmin, bad):
    agent = _register_agent(client, tadmin)
    r = client.post(f"/fleet/agents/{agent['agent_id']}/provision",
                    json={"shard_count": bad}, headers=tadmin)
    assert r.status_code == 400


def test_provision_unknown_agent_404(client, tadmin):
    r = client.post("/fleet/agents/999999/provision",
                    json={"shard_count": 2}, headers=tadmin)
    assert r.status_code == 404


def test_deprovision_queues_command(client, tadmin):
    agent = _register_agent(client, tadmin)
    r = client.post(f"/fleet/agents/{agent['agent_id']}/deprovision", headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "deprovision"


# ---- full round trip through the agent side -------------------------------

def test_agent_pulls_provision_command_and_reports_result(client, tadmin):
    agent = _register_agent(client, tadmin, name="acme-gcp", env="gcp")
    agent_id = agent["agent_id"]

    # queue a provision
    prov = client.post(f"/fleet/agents/{agent_id}/provision",
                       json={"shard_count": 3}, headers=tadmin).json()

    # agent enrolls with its one-time token, gets its secret
    enr = client.post("/fleet/enroll",
                      json={"enrollment_token": agent["enrollment_token"]})
    assert enr.status_code == 200, enr.text
    secret = enr.json()["agent_secret"]

    # agent heartbeats -> pulls the queued provision command (now dispatched)
    hb = client.post(f"/fleet/agents/{agent_id}/heartbeat",
                     json={"service_status": {}},
                     headers={"x-agent-secret": secret})
    assert hb.status_code == 200, hb.text
    cmds = hb.json()["commands"]
    assert any(c["action"] == "provision" and c["id"] == prov["id"] for c in cmds)

    # agent runs it and reports success
    res = client.post(f"/fleet/agents/{agent_id}/commands/{prov['id']}/result",
                      json={"outcome": "success", "detail": "reconciled to 3 shards"},
                      headers={"x-agent-secret": secret})
    assert res.status_code == 200, res.text

    # tenant admin sees the job as succeeded in the command list
    listing = client.get(f"/fleet/agents/{agent_id}/commands", headers=tadmin).json()
    done = next(c for c in listing if c["id"] == prov["id"])
    assert done["status"] == "success"
    assert done["completed_at"] is not None
