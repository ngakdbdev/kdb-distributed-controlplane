"""
LDAP / Active Directory tests.

Uses ldap3's MOCK_SYNC strategy with a seeded in-memory directory - a service
account, an admin user (in the kdb-admins group), and a plain user - so the
whole bind/search/verify path is exercised with no domain controller.
"""
import json

import pytest
from ldap3 import MOCK_SYNC, Connection, Server
from fastapi.testclient import TestClient

import app.main as m
from app import ldap_auth
from app.ldap_auth import LDAPConfig, LDAPAuthError
from app.security import decode_access_token

# seeded directory
SVC_DN = "CN=svc,DC=bank,DC=com"
ALICE_DN = "CN=alice,OU=People,DC=bank,DC=com"
BOB_DN = "CN=bob,OU=People,DC=bank,DC=com"
ADMIN_GROUP = "CN=kdb-admins,OU=Groups,DC=bank,DC=com"

ENTRIES = {
    SVC_DN: {"userPassword": "svcpw", "objectClass": "person"},
    ALICE_DN: {"userPassword": "alicepw", "sAMAccountName": "alice", "mail": "alice@bank.com",
               "displayName": "Alice A", "memberOf": [ADMIN_GROUP], "objectClass": "user"},
    BOB_DN: {"userPassword": "bobpw", "sAMAccountName": "bob", "mail": "bob@bank.com",
             "displayName": "Bob B", "memberOf": [], "objectClass": "user"},
}


def make_factory(entries=ENTRIES):
    """A connection_factory that returns a fresh MOCK_SYNC connection seeded
    with the same directory each time (mock data is per-connection)."""
    def factory(user, password):
        conn = Connection(Server("fake"), user=user, password=password, client_strategy=MOCK_SYNC)
        for dn, attrs in entries.items():
            conn.strategy.add_entry(dn, attrs)
        return conn
    return factory


def search_cfg(**over):
    base = dict(
        tenant_slug="demo-bank", server_uri="ldaps://dc.bank.com", use_start_tls=False,
        bind_mode="search", bind_dn=SVC_DN, bind_password="svcpw",
        user_search_base="OU=People,DC=bank,DC=com", user_filter="(sAMAccountName={username})",
        bind_dn_template="{username}", attr_email="mail", attr_name="displayName",
        group_attr="memberOf", group_role_map={ADMIN_GROUP: "tenant_admin"},
        default_role="tenant_admin", allowed_domains=["bank.com"],
    )
    base.update(over)
    return LDAPConfig(**base)


# ------------------------------------------------------------------ authenticate
def test_search_bind_success_extracts_identity():
    ident = ldap_auth.authenticate(search_cfg(), "alice", "alicepw", connection_factory=make_factory())
    assert ident["email"] == "alice@bank.com"
    assert ident["name"] == "Alice A"
    # memberships include both the full group DN and its CN
    assert ADMIN_GROUP in ident["memberships"]
    assert "kdb-admins" in ident["memberships"]


def test_search_bind_wrong_password_fails():
    with pytest.raises(LDAPAuthError):
        ldap_auth.authenticate(search_cfg(), "alice", "WRONG", connection_factory=make_factory())


def test_search_bind_unknown_user_fails():
    with pytest.raises(LDAPAuthError):
        ldap_auth.authenticate(search_cfg(), "nobody", "x", connection_factory=make_factory())


def test_service_account_bad_password_fails():
    with pytest.raises(LDAPAuthError):
        ldap_auth.authenticate(search_cfg(bind_password="nope"), "alice", "alicepw",
                               connection_factory=make_factory())


def test_empty_password_rejected():
    with pytest.raises(LDAPAuthError):
        ldap_auth.authenticate(search_cfg(), "alice", "", connection_factory=make_factory())


def test_direct_bind_success():
    # direct bind as the full DN (bind_dn_template renders the DN from username)
    cfg = search_cfg(bind_mode="direct", bind_dn_template="CN={username},OU=People,DC=bank,DC=com")
    ident = ldap_auth.authenticate(cfg, "alice", "alicepw", connection_factory=make_factory())
    assert ident["email"] == "alice@bank.com"


# ------------------------------------------------------------------ role mapping
def test_role_from_group_cn_or_dn():
    # map keyed by CN also works because memberships carry both
    cfg = search_cfg(group_role_map={"kdb-admins": "tenant_admin"})
    ident = ldap_auth.authenticate(cfg, "alice", "alicepw", connection_factory=make_factory())
    role = ldap_auth.resolve_role(ident["memberships"], cfg.group_role_map, cfg.default_role)
    assert role == "tenant_admin"


def test_non_member_gets_default_role():
    ident = ldap_auth.authenticate(search_cfg(), "bob", "bobpw", connection_factory=make_factory())
    role = ldap_auth.resolve_role(ident["memberships"], {ADMIN_GROUP: "tenant_admin"}, "tenant_admin")
    assert role == "tenant_admin"      # default (only role available); no platform escalation


# ------------------------------------------------------- full router flow via API
@pytest.fixture
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture
def admin_token(client):
    r = client.post("/auth/login", json={"email": "admin@platform.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _configure(client, admin_token, **over):
    body = dict(server_uri="ldaps://dc.bank.com", bind_mode="search", bind_dn=SVC_DN,
                bind_password="svcpw", user_search_base="OU=People,DC=bank,DC=com",
                user_filter="(sAMAccountName={username})", attr_email="mail",
                group_attr="memberOf", group_role_map={ADMIN_GROUP: "tenant_admin"},
                default_role="tenant_admin", allowed_domains="bank.com", enabled=True)
    body.update(over)
    return client.put("/auth/ldap/demo-bank/config", json=body,
                      headers={"Authorization": f"Bearer {admin_token}"})


def test_config_put_get_redacts_password(client, admin_token):
    r = _configure(client, admin_token)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["bind_password_set"] is True
    assert "bind_password" not in out
    g = client.get("/auth/ldap/demo-bank/config", headers={"Authorization": f"Bearer {admin_token}"})
    assert g.json()["enabled"] is True and g.json()["bind_password_set"] is True


def test_ldap_login_provisions_and_issues_token(client, admin_token, monkeypatch):
    _configure(client, admin_token)
    # route authenticate through the seeded mock directory
    monkeypatch.setattr(ldap_auth, "authenticate", lambda cfg, u, p, **k: _real_auth(cfg, u, p))
    r = client.post("/auth/ldap/demo-bank/login", json={"username": "alice", "password": "alicepw"})
    assert r.status_code == 200, r.text
    payload = decode_access_token(r.json()["access_token"])
    assert payload["email"] == "alice@bank.com"
    assert payload["role"] == "tenant_admin"
    assert payload["tenant_id"] is not None


def test_ldap_login_bad_password_401(client, admin_token, monkeypatch):
    _configure(client, admin_token)
    monkeypatch.setattr(ldap_auth, "authenticate", lambda cfg, u, p, **k: _real_auth(cfg, u, p))
    r = client.post("/auth/ldap/demo-bank/login", json={"username": "alice", "password": "nope"})
    assert r.status_code == 401


# real authenticate bound to the mock directory, used by the router tests.
# Captured at import time so it survives monkeypatching of ldap_auth.authenticate.
_ORIG_AUTHENTICATE = ldap_auth.authenticate


def _real_auth(cfg, u, p):
    return _ORIG_AUTHENTICATE(cfg, u, p, connection_factory=make_factory())
