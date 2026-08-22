"""
ldap_auth.py - LDAP / on-prem Active Directory authentication.

Verifies a username+password against a tenant's directory and returns the same
identity shape the OIDC path produces, so provisioning + role mapping are
shared. Two bind modes (see models.TenantLDAP):

  search  bind as a read-only service account, find the user by filter, then
          re-bind as the found DN with the user's password to verify it. Reads
          group membership from the entry. This is the robust AD pattern.
  direct  bind straight as bind_dn_template.format(username=...).

Connections are created through an injectable factory so the whole path is
unit-tested offline with ldap3's MOCK_SYNC strategy - no domain controller.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ldap3 import BASE, SUBTREE, SYNC, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from . import sso
from .crypto import decrypt_secret


class LDAPAuthError(Exception):
    """Raised for any authentication or configuration failure."""


@dataclass
class LDAPConfig:
    tenant_slug: str
    server_uri: str
    use_start_tls: bool
    bind_mode: str
    bind_dn: str
    bind_password: str
    user_search_base: str
    user_filter: str
    bind_dn_template: str
    attr_email: str
    attr_name: str
    group_attr: str
    group_role_map: dict
    default_role: str
    allowed_domains: list

    @classmethod
    def from_model(cls, tenant_slug: str, row) -> "LDAPConfig":
        try:
            gmap = json.loads(row.group_role_map or "{}")
        except json.JSONDecodeError:
            gmap = {}
        domains = [d.strip().lower() for d in (row.allowed_domains or "").split(",") if d.strip()]
        role = row.default_role.value if hasattr(row.default_role, "value") else str(row.default_role)
        return cls(
            tenant_slug, row.server_uri, row.use_start_tls, row.bind_mode, row.bind_dn,
            decrypt_secret(row.bind_password), row.user_search_base, row.user_filter, row.bind_dn_template,
            row.attr_email, row.attr_name, row.group_attr, gmap, role, domains,
        )


def _cn(dn: str) -> str:
    """First RDN value of a DN: 'CN=kdb-admins,OU=Groups,DC=..' -> 'kdb-admins'."""
    first = dn.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else first


def _default_factory(cfg: LDAPConfig):
    """Real connection factory: one Server, TLS as configured."""
    server = Server(cfg.server_uri, use_ssl=str(cfg.server_uri).lower().startswith("ldaps"))

    def make(user: str, password: str) -> Connection:
        conn = Connection(server, user=user, password=password, client_strategy=SYNC,
                          auto_bind=False, read_only=True, receive_timeout=10)
        conn.open()
        if cfg.use_start_tls:
            conn.start_tls()
        return conn

    return make


def _attrs(entry) -> dict:
    try:
        return entry.entry_attributes_as_dict
    except Exception:
        return {}


def _identity(cfg: LDAPConfig, entry, username: str) -> dict:
    a = _attrs(entry)

    def first(attr):
        v = a.get(attr) or []
        return v[0] if v else ""

    email = (first(cfg.attr_email) or first("userPrincipalName")
             or (username if "@" in username else "")).lower()
    memberships = []
    for g in a.get(cfg.group_attr, []) or []:
        memberships.append(str(g))          # full DN
        memberships.append(_cn(str(g)))     # and its CN, so the map can key on either
    return {
        "email": email,
        "name": first(cfg.attr_name),
        "external_id": str(getattr(entry, "entry_dn", "")) or username,
        "memberships": memberships,
    }


def authenticate(cfg: LDAPConfig, username: str, password: str, *,
                 connection_factory=None) -> dict:
    """Return the identity dict on success; raise LDAPAuthError otherwise."""
    if not password:
        raise LDAPAuthError("empty password")       # guard against unauthenticated bind
    make = connection_factory or _default_factory(cfg)
    attributes = [cfg.attr_email, cfg.attr_name, cfg.group_attr, "userPrincipalName"]

    try:
        if cfg.bind_mode == "search":
            svc = make(cfg.bind_dn, cfg.bind_password)
            if not svc.bind():
                raise LDAPAuthError("service-account bind failed (check bind_dn/bind_password)")
            flt = cfg.user_filter.format(username=escape_filter_chars(username))
            svc.search(cfg.user_search_base, flt, search_scope=SUBTREE, attributes=attributes)
            if not svc.entries:
                raise LDAPAuthError("invalid credentials")   # don't leak user-exists vs bad-password
            entry = svc.entries[0]
            user_conn = make(entry.entry_dn, password)
            if not user_conn.bind():
                raise LDAPAuthError("invalid credentials")
            return _identity(cfg, entry, username)

        if cfg.bind_mode == "direct":
            user_dn = cfg.bind_dn_template.format(username=username)
            user_conn = make(user_dn, password)
            if not user_conn.bind():
                raise LDAPAuthError("invalid credentials")
            user_conn.search(user_dn, "(objectClass=*)", search_scope=BASE, attributes=attributes)
            entry = user_conn.entries[0] if user_conn.entries else None
            if entry is None:
                return {"email": username.lower() if "@" in username else "",
                        "name": "", "external_id": user_dn, "memberships": []}
            return _identity(cfg, entry, username)

        raise LDAPAuthError(f"unknown bind_mode '{cfg.bind_mode}'")
    except LDAPException as exc:
        raise LDAPAuthError(f"LDAP error: {exc}")


# role mapping / domain allow reuse the OIDC helpers - identical semantics
resolve_role = sso.resolve_role
email_allowed = sso.email_allowed
