"""
sso.py - OpenID Connect (Microsoft Entra) authentication.

Implements the authorization-code-with-PKCE flow against a tenant's own Entra
directory:

  1. /login builds an authorization URL and redirects the browser to Entra
  2. Entra authenticates the user and redirects back to /callback with a code
  3. we exchange the code for tokens, validate the id_token's signature against
     Entra's JWKS (plus issuer / audience / nonce / expiry), and read the
     user's identity + group/role claims
  4. the user is provisioned just-in-time and issued one of OUR session JWTs

The state/nonce/PKCE verifier are carried across the redirect in a short-lived
signed cookie (signed with the app's JWT secret), so this works across multiple
stateless API replicas with no shared session store.

Everything here except the live network round-trip to Entra is unit-tested with
a self-signed key acting as the IdP - see tests/test_sso.py.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import jwt
import requests

from .config import settings

DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
_STATE_PURPOSE = "sso_state"
_discovery_cache: dict[str, tuple[float, dict]] = {}
_DISCOVERY_TTL = 3600


@dataclass
class OIDCConfig:
    """The subset of a TenantIdP row the flow needs."""
    tenant_slug: str
    authority: str
    client_id: str
    client_secret: str
    allowed_domains: list[str]
    group_role_map: dict[str, str]
    default_role: str

    @classmethod
    def from_idp(cls, tenant_slug: str, idp) -> "OIDCConfig":
        domains = [d.strip().lower() for d in (idp.allowed_domains or "").split(",") if d.strip()]
        try:
            gmap = json.loads(idp.group_role_map or "{}")
        except json.JSONDecodeError:
            gmap = {}
        role = idp.default_role.value if hasattr(idp.default_role, "value") else str(idp.default_role)
        return cls(tenant_slug, idp.authority.rstrip("/"), idp.client_id, idp.client_secret,
                   domains, gmap, role)


# --------------------------------------------------------------------- discovery
def get_discovery(authority: str, *, fetcher=None) -> dict:
    """Fetch and cache the OIDC discovery document for an authority.
    `fetcher` is injectable for tests."""
    authority = authority.rstrip("/")
    now = time.time()
    cached = _discovery_cache.get(authority)
    if cached and now - cached[0] < _DISCOVERY_TTL:
        return cached[1]
    fetch = fetcher or (lambda url: requests.get(url, timeout=5).json())
    doc = fetch(authority + DISCOVERY_SUFFIX)
    _discovery_cache[authority] = (now, doc)
    return doc


# ------------------------------------------------------------------------- PKCE
def new_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(discovery: dict, cfg: OIDCConfig, redirect_uri: str,
                      state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": settings.sso_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return discovery["authorization_endpoint"] + "?" + urlencode(params)


def exchange_code(discovery: dict, cfg: OIDCConfig, redirect_uri: str,
                  code: str, code_verifier: str, *, poster=None) -> dict:
    data = {
        "client_id": cfg.client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if cfg.client_secret:  # confidential client
        data["client_secret"] = cfg.client_secret
    post = poster or (lambda url, d: requests.post(url, data=d, timeout=5).json())
    return post(discovery["token_endpoint"], data)


# ------------------------------------------------------------ id_token validation
def validate_id_token(discovery: dict, cfg: OIDCConfig, id_token: str, nonce: str,
                      *, signing_key=None) -> dict:
    """Verify the id_token and return its claims. Raises on any failure.

    `signing_key` (a public key / PEM) is injectable for tests; in production
    the RS256 signing key is resolved from Entra's JWKS by kid.
    """
    if signing_key is None:
        jwk_client = jwt.PyJWKClient(discovery["jwks_uri"])
        signing_key = jwk_client.get_signing_key_from_jwt(id_token).key

    claims = jwt.decode(
        id_token,
        signing_key,
        algorithms=["RS256"],
        audience=cfg.client_id,
        issuer=discovery["issuer"],
        options={"require": ["exp", "iss", "aud"]},
    )
    if nonce and claims.get("nonce") != nonce:
        raise jwt.InvalidTokenError("nonce mismatch")
    return claims


# ------------------------------------------------------------------ identity/role
def extract_identity(claims: dict) -> dict:
    """Pull the fields we care about out of Entra's claims, tolerant of the
    several places Entra can put an email."""
    email = (claims.get("email") or claims.get("preferred_username")
             or claims.get("upn") or "")
    # Entra puts group object-ids in "groups" and app-role values in "roles"
    memberships = list(claims.get("groups", []) or []) + list(claims.get("roles", []) or [])
    return {
        "email": email.lower(),
        "name": claims.get("name", ""),
        "external_id": claims.get("oid") or claims.get("sub", ""),
        "memberships": [str(m) for m in memberships],
    }


_ROLE_RANK = {"tenant_admin": 1, "platform_admin": 2}


def resolve_role(memberships: list[str], group_role_map: dict[str, str], default_role: str) -> str:
    """Highest-privilege role among the user's mapped memberships, else default.
    (platform_admin is intentionally NOT grantable via tenant SSO - a tenant's
    Entra groups must never be able to mint a platform operator; capped below.)"""
    best = default_role
    for m in memberships:
        mapped = group_role_map.get(m)
        if mapped and _ROLE_RANK.get(mapped, 0) > _ROLE_RANK.get(best, 0):
            best = mapped
    if best == "platform_admin":            # hard cap - never via tenant federation
        best = "tenant_admin"
    return best


def email_allowed(email: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in allowed_domains


# --------------------------------------------------------------- state cookie jwt
def sign_state(payload: dict) -> str:
    body = dict(payload)
    body["purpose"] = _STATE_PURPOSE
    body["exp"] = int(time.time()) + settings.sso_state_ttl_sec
    return jwt.encode(body, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_state(token: str) -> dict:
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if claims.get("purpose") != _STATE_PURPOSE:
        raise jwt.InvalidTokenError("not an sso state token")
    return claims
