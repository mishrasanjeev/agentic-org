# SPDX-License-Identifier: Apache-2.0
"""OpenID Connect provider stub: protocol logic and HTTP server.

Implements, for development and tests only:

* discovery (``/.well-known/openid-configuration``) and ``/jwks``;
* the authorization-code flow with mandatory PKCE (S256) at ``/authorize``,
  with a sign-in page that lists the configured users;
* ``/token`` (``authorization_code``; single-use codes; ``client_secret_basic``
  or ``client_secret_post`` for confidential clients) issuing RS256 ID and
  access tokens;
* ``/userinfo`` for those access tokens;
* step-up: ``acr_values`` containing :data:`ACR_STEP_UP` requires a second
  factor (simulated security key) and yields ``amr: ["pwd", "hwk"]``;
  ``max_age`` and ``prompt=login`` force re-authentication. Tokens always carry
  ``acr``, ``amr`` and ``auth_time``; ``acr`` and ``amr`` describe how the
  browser session authenticated, which may be stronger than requested.
* browser sessions last at most :data:`SESSION_TTL_SECONDS` from sign-in.
* every query and form parameter may appear once; repeats are rejected.

Passwords are not checked - picking a configured user on the sign-in page is
the authentication - so ``amr`` describes what a real provider would have
done. Every request that cannot be satisfied exactly fails with an OAuth
error; nothing falls back to a weaker authentication.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ACR_BASIC = "urn:agenticorg:acr:basic"
ACR_STEP_UP = "urn:agenticorg:acr:step-up"
AMR_BASIC: tuple[str, ...] = ("pwd",)
AMR_STEP_UP: tuple[str, ...] = ("pwd", "hwk")
SECOND_FACTOR = "hwk"

ALLOWED_ENVS = frozenset({"development", "dev", "local", "test"})
PRODUCTION_MARKERS = frozenset({"production", "prod", "staging", "stage", "uat", "preprod", "live"})
ENV_VARIABLES = ("AGENTICORG_ENV", "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV")

CODE_TTL_SECONDS = 60
REQUEST_TTL_SECONDS = 600
TOKEN_TTL_SECONDS = 900
SESSION_COOKIE = "oidc_stub_session"
SESSION_TTL_SECONDS = 8 * 3600
_MAX_AGE_RE = re.compile(r"[0-9]{1,9}")
MAX_BODY_BYTES = 64 * 1024
SUPPORTED_SCOPES = ("openid", "profile", "email")
_PKCE_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")


class StartupRefusedError(RuntimeError):
    """The runtime is not a development or test environment."""


class ConfigError(ValueError):
    """The users/clients configuration is invalid."""


# ── Runtime guard ────────────────────────────────────────────────────────────


def assert_development_runtime(environ: Mapping[str, str]) -> str:
    """Return the runtime name, or raise unless this is clearly development or test."""
    for name in ENV_VARIABLES:
        value = environ.get(name, "").strip().lower()
        if value in PRODUCTION_MARKERS:
            raise StartupRefusedError(
                f"{name}={value} indicates a production-like runtime; the OIDC stub only runs in development and test"
            )
    runtime = environ.get("AGENTICORG_ENV", "").strip().lower()
    if runtime not in ALLOWED_ENVS:
        raise StartupRefusedError(
            f"AGENTICORG_ENV must be one of {', '.join(sorted(ALLOWED_ENVS))} to run the OIDC stub "
            f"(got {runtime or 'nothing'})"
        )
    return runtime


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class User:
    sub: str
    email: str
    name: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Client:
    client_id: str
    redirect_uris: tuple[str, ...]
    client_secret: str | None = None


@dataclass(frozen=True)
class StubConfig:
    users: dict[str, User]
    clients: dict[str, Client]


def _require_str(entry: Mapping[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: '{key}' must be a non-empty string")
    return value


def _check_redirect_uri(uri: str, where: str) -> str:
    parts = urlsplit(uri)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.fragment:
        raise ConfigError(f"{where}: redirect URI {uri!r} must be an absolute http(s) URL without a fragment")
    return uri


def parse_config(data: Any) -> StubConfig:
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    users: dict[str, User] = {}
    raw_users = data.get("users")
    if not isinstance(raw_users, list) or not raw_users:
        raise ConfigError("'users' must be a non-empty list")
    for index, entry in enumerate(raw_users):
        where = f"users[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: must be an object")
        roles = entry.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise ConfigError(f"{where}: 'roles' must be a list of strings")
        user = User(
            sub=_require_str(entry, "sub", where),
            email=_require_str(entry, "email", where),
            name=_require_str(entry, "name", where),
            roles=tuple(roles),
        )
        if user.sub in users:
            raise ConfigError(f"{where}: duplicate sub {user.sub!r}")
        users[user.sub] = user

    clients: dict[str, Client] = {}
    raw_clients = data.get("clients")
    if not isinstance(raw_clients, list) or not raw_clients:
        raise ConfigError("'clients' must be a non-empty list")
    for index, entry in enumerate(raw_clients):
        where = f"clients[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: must be an object")
        uris = entry.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            raise ConfigError(f"{where}: 'redirect_uris' must be a non-empty list of strings")
        secret = entry.get("client_secret")
        if secret is not None and (not isinstance(secret, str) or not secret):
            raise ConfigError(f"{where}: 'client_secret' must be a non-empty string when present")
        client = Client(
            client_id=_require_str(entry, "client_id", where),
            redirect_uris=tuple(_check_redirect_uri(u, where) for u in uris),
            client_secret=secret,
        )
        if client.client_id in clients:
            raise ConfigError(f"{where}: duplicate client_id {client.client_id!r}")
        clients[client.client_id] = client
    return StubConfig(users=users, clients=clients)


def with_extra_redirect_uris(config: StubConfig, extra: Sequence[str]) -> StubConfig:
    """Return a copy in which every client also accepts these redirect URIs.

    A relying party's callback carries its port, and the development stack's
    ports are configurable, so a callback cannot always be baked into the
    fixture file. Each URI is validated exactly as a configured one is.
    """
    checked = tuple(_check_redirect_uri(uri, "OIDC_STUB_EXTRA_REDIRECT_URIS") for uri in extra)
    if not checked:
        return config
    clients = {
        client_id: Client(
            client_id=client.client_id,
            redirect_uris=tuple(dict.fromkeys((*client.redirect_uris, *checked))),
            client_secret=client.client_secret,
        )
        for client_id, client in config.clients.items()
    }
    return StubConfig(users=config.users, clients=clients)


def load_config(path: Path) -> StubConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    return parse_config(data)


# ── Signing key ──────────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@dataclass(frozen=True)
class SigningKey:
    private_key: rsa.RSAPrivateKey
    kid: str

    @classmethod
    def from_private_key(cls, private_key: rsa.RSAPrivateKey) -> SigningKey:
        der = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return cls(private_key=private_key, kid=_b64url(hashlib.sha256(der).digest())[:16])

    @classmethod
    def generate(cls) -> SigningKey:
        return cls.from_private_key(rsa.generate_private_key(public_exponent=65537, key_size=2048))

    @classmethod
    def from_pem_file(cls, path: Path) -> SigningKey:
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise ConfigError(f"cannot load signing key {path}: {exc}") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ConfigError(f"signing key {path} is not an RSA private key")
        return cls.from_private_key(key)

    def public_jwk(self) -> dict[str, str]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _int_b64url(numbers.n),
            "e": _int_b64url(numbers.e),
        }


# ── Protocol state ───────────────────────────────────────────────────────────


@dataclass
class _PendingRequest:
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    state: str | None
    nonce: str | None
    code_challenge: str
    step_up: bool
    locked_sub: str | None
    created: float


@dataclass
class _Session:
    sub: str
    auth_time: int
    amr: tuple[str, ...]


@dataclass
class _IssuedCode:
    client_id: str
    redirect_uri: str
    sub: str
    scopes: tuple[str, ...]
    nonce: str | None
    code_challenge: str
    auth_time: int
    amr: tuple[str, ...]
    created: float


@dataclass
class Response:
    """What the HTTP layer should send."""

    status: int
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, status: int, payload: Mapping[str, Any], headers: dict[str, str] | None = None) -> Response:
        return cls(status, json.dumps(payload).encode("utf-8"), "application/json", dict(headers or {}))

    @classmethod
    def html(cls, status: int, markup: str) -> Response:
        return cls(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    @classmethod
    def redirect(cls, location: str, headers: dict[str, str] | None = None) -> Response:
        return cls(HTTPStatus.FOUND, b"", headers={"Location": location, **(headers or {})})


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def redirect_uri_registered(requested: str, registered: tuple[str, ...]) -> bool:
    """Exact match, except that plain-http loopback URIs may use any port (RFC 8252 section 7.3).

    The local stack's ports are configurable, so a callback registered on
    127.0.0.1 must keep working when the console or API moves port.
    """
    if requested in registered:
        return True
    try:
        req = urlsplit(requested)
        if req.scheme != "http" or req.hostname not in _LOOPBACK_HOSTS or req.fragment or req.username:
            return False
        req.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        return False
    for uri in registered:
        reg = urlsplit(uri)
        if (reg.scheme, reg.hostname, reg.path, reg.query) == ("http", req.hostname, req.path, req.query):
            return True
    return False


def _with_query(uri: str, params: Mapping[str, str]) -> str:
    parts = urlsplit(uri)
    query = parts.query + ("&" if parts.query else "") + urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _s256(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


class OIDCStub:
    """The provider. Thread-safe; the HTTP handler maps requests onto these methods."""

    def __init__(
        self,
        config: StubConfig,
        signing_key: SigningKey,
        *,
        public_url: str,
        internal_url: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.key = signing_key
        self.issuer = public_url.rstrip("/")
        self.internal_url = (internal_url or public_url).rstrip("/")
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._sessions: dict[str, _Session] = {}
        self._codes: dict[str, _IssuedCode] = {}

    # Discovery ------------------------------------------------------------

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.internal_url}/token",
            "userinfo_endpoint": f"{self.internal_url}/userinfo",
            "jwks_uri": f"{self.internal_url}/jwks",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "nonce", "acr", "amr", "azp",
                                 "email", "email_verified", "name", "roles"],
            "acr_values_supported": [ACR_BASIC, ACR_STEP_UP],
            "prompt_values_supported": ["none", "login", "consent", "select_account"],
            "authorization_response_iss_parameter_supported": True,
        }

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.key.public_jwk()]}

    # Authorization --------------------------------------------------------

    def _now(self) -> float:
        return self._clock()

    def _purge(self, now: float) -> None:
        for request_id in [k for k, v in self._pending.items() if now - v.created > REQUEST_TTL_SECONDS]:
            del self._pending[request_id]
        for code in [k for k, v in self._codes.items() if now - v.created > CODE_TTL_SECONDS]:
            del self._codes[code]
        for session_id in [k for k, v in self._sessions.items() if now - v.auth_time >= SESSION_TTL_SECONDS]:
            del self._sessions[session_id]

    def _error_redirect(self, redirect_uri: str, error: str, description: str, state: str | None) -> Response:
        params = {"error": error, "error_description": description, "iss": self.issuer}
        if state is not None:
            params["state"] = state
        return Response.redirect(_with_query(redirect_uri, params))

    @staticmethod
    def _error_page(status: int, message: str) -> Response:
        return Response.html(status, _page("Sign-in request rejected", f"<p>{html.escape(message)}</p>"))

    def authorize(self, query: Mapping[str, str], session_id: str | None) -> Response:
        client = self.config.clients.get(query.get("client_id", ""))
        if client is None:
            return self._error_page(HTTPStatus.BAD_REQUEST, "Unknown client_id.")
        redirect_uri = query.get("redirect_uri", "")
        if not redirect_uri_registered(redirect_uri, client.redirect_uris):
            return self._error_page(HTTPStatus.BAD_REQUEST, "redirect_uri is not registered for this client.")
        state = query.get("state")

        def fail(error: str, description: str) -> Response:
            return self._error_redirect(redirect_uri, error, description, state)

        if query.get("response_type") != "code":
            return fail("unsupported_response_type", "only response_type=code is supported")
        scopes = tuple(query.get("scope", "").split())
        if "openid" not in scopes:
            return fail("invalid_scope", "the openid scope is required")
        unknown_scopes = [s for s in scopes if s not in SUPPORTED_SCOPES]
        if unknown_scopes:
            return fail("invalid_scope", f"unsupported scope: {' '.join(unknown_scopes)}")
        challenge = query.get("code_challenge", "")
        if query.get("code_challenge_method") != "S256" or not _CHALLENGE_RE.fullmatch(challenge):
            return fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
        acr_values = query.get("acr_values", "").split()
        unknown_acr = [a for a in acr_values if a not in (ACR_BASIC, ACR_STEP_UP)]
        if unknown_acr:
            return fail("invalid_request", f"unsupported acr_values: {' '.join(unknown_acr)}")
        step_up = ACR_STEP_UP in acr_values
        max_age: int | None = None
        if "max_age" in query:
            # ASCII digits only: str.isdigit() also accepts characters int() rejects.
            if not _MAX_AGE_RE.fullmatch(query["max_age"]):
                return fail("invalid_request", "max_age must be a non-negative integer")
            max_age = int(query["max_age"])
        prompts = set(query.get("prompt", "").split())
        if prompts - {"none", "login", "consent", "select_account"}:
            return fail("invalid_request", "unsupported prompt value")
        if "none" in prompts and len(prompts) > 1:
            return fail("invalid_request", "prompt=none cannot be combined with other values")

        now = self._now()
        with self._lock:
            self._purge(now)
            session = self._sessions.get(session_id or "")
            satisfied = (
                session is not None
                and "login" not in prompts
                and "select_account" not in prompts
                and (max_age is None or now - session.auth_time <= max_age)
                and (not step_up or SECOND_FACTOR in session.amr)
            )
            if session is not None and satisfied:
                code = self._issue_code(client.client_id, redirect_uri, scopes, query.get("nonce"), challenge,
                                        session, now)
                return Response.redirect(_with_query(redirect_uri, _code_params(code, state, self.issuer)))
            if "none" in prompts:
                return fail("login_required", "the request needs an interactive sign-in")
            request_id = secrets.token_urlsafe(24)
            locked = session.sub if session is not None and "select_account" not in prompts else None
            self._pending[request_id] = _PendingRequest(
                client_id=client.client_id,
                redirect_uri=redirect_uri,
                scopes=scopes,
                state=state,
                nonce=query.get("nonce"),
                code_challenge=challenge,
                step_up=step_up,
                locked_sub=locked,
                created=now,
            )
        return Response.html(HTTPStatus.OK, self._sign_in_page(request_id, step_up, locked))

    def complete_sign_in(self, form: Mapping[str, str], session_id: str | None) -> Response:
        request_id = form.get("request_id", "")
        now = self._now()
        with self._lock:
            self._purge(now)
            pending = self._pending.get(request_id)
            if pending is None:
                return self._error_page(HTTPStatus.BAD_REQUEST, "The sign-in request is unknown or has expired.")
            user = self.config.users.get(form.get("sub", ""))
            if user is None:
                return self._error_page(HTTPStatus.BAD_REQUEST, "Unknown user.")
            if pending.locked_sub is not None and user.sub != pending.locked_sub:
                return self._error_page(HTTPStatus.BAD_REQUEST, "Re-authentication must be by the signed-in user.")
            second_factor = form.get("second_factor") == SECOND_FACTOR
            if pending.step_up and not second_factor:
                markup = self._sign_in_page(request_id, True, pending.locked_sub,
                                            error="This action requires your security key.")
                return Response.html(HTTPStatus.BAD_REQUEST, markup)
            del self._pending[request_id]
            if session_id:
                self._sessions.pop(session_id, None)
            new_session_id = secrets.token_urlsafe(32)
            session = _Session(sub=user.sub, auth_time=int(now), amr=AMR_STEP_UP if second_factor else AMR_BASIC)
            self._sessions[new_session_id] = session
            code = self._issue_code(pending.client_id, pending.redirect_uri, pending.scopes, pending.nonce,
                                    pending.code_challenge, session, now)
        cookie = f"{SESSION_COOKIE}={new_session_id}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax"
        location = _with_query(pending.redirect_uri, _code_params(code, pending.state, self.issuer))
        return Response.redirect(location, headers={"Set-Cookie": cookie})

    def _issue_code(
        self,
        client_id: str,
        redirect_uri: str,
        scopes: tuple[str, ...],
        nonce: str | None,
        challenge: str,
        session: _Session,
        now: float,
    ) -> str:
        code = secrets.token_urlsafe(32)
        self._codes[code] = _IssuedCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            sub=session.sub,
            scopes=scopes,
            nonce=nonce,
            code_challenge=challenge,
            auth_time=session.auth_time,
            amr=session.amr,
            created=now,
        )
        return code

    def _sign_in_page(self, request_id: str, step_up: bool, locked_sub: str | None, error: str = "") -> str:
        users = [u for u in self.config.users.values() if locked_sub is None or u.sub == locked_sub]
        factor = (
            '<p><label><input type="checkbox" name="second_factor" value="hwk" required> '
            "Confirm with security key (simulated)</label></p>"
            if step_up
            else ""
        )
        forms = "".join(
            f'<form method="post" action="/authorize">'
            f'<input type="hidden" name="request_id" value="{html.escape(request_id)}">'
            f'<input type="hidden" name="sub" value="{html.escape(u.sub)}">{factor}'
            f'<button type="submit" data-sub="{html.escape(u.sub)}">Sign in as {html.escape(u.name)}</button>'
            f" <small>{html.escape(u.email)}</small></form>"
            for u in users
        )
        notice = "<p><strong>Step-up required.</strong></p>" if step_up else ""
        alert = f'<p role="alert">{html.escape(error)}</p>' if error else ""
        return _page("Development sign-in", f"{alert}{notice}{forms}")

    # Token and userinfo -----------------------------------------------------

    def _authenticate_client(self, form: Mapping[str, str], authorization: str | None) -> Client | None:
        client_id = form.get("client_id")
        secret = form.get("client_secret")
        if authorization and authorization.lower().startswith("basic "):
            if secret:
                return None
            try:
                decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return None
            basic_id, sep, basic_secret = decoded.partition(":")
            if not sep or (client_id and client_id != basic_id):
                return None
            # RFC 6749 section 2.3.1: basic credentials are form-encoded first.
            client_id, secret = unquote_plus(basic_id), unquote_plus(basic_secret)
        client = self.config.clients.get(client_id or "")
        if client is None:
            return None
        if client.client_secret is None:
            return client if not secret else None
        if not secret or not hmac.compare_digest(secret.encode("utf-8"), client.client_secret.encode("utf-8")):
            return None
        return client

    def token(self, form: Mapping[str, str], authorization: str | None) -> Response:
        no_store = {"Cache-Control": "no-store", "Pragma": "no-cache"}

        def error(status: int, code: str, description: str) -> Response:
            return Response.json(status, {"error": code, "error_description": description}, no_store)

        if form.get("grant_type") != "authorization_code":
            return error(HTTPStatus.BAD_REQUEST, "unsupported_grant_type", "only authorization_code is supported")
        client = self._authenticate_client(form, authorization)
        if client is None:
            return error(HTTPStatus.UNAUTHORIZED, "invalid_client", "client authentication failed")
        now = self._now()
        with self._lock:
            self._purge(now)
            issued = self._codes.pop(form.get("code", ""), None)
        if issued is None:
            return error(HTTPStatus.BAD_REQUEST, "invalid_grant", "the code is unknown, expired or already used")
        if issued.client_id != client.client_id:
            return error(HTTPStatus.BAD_REQUEST, "invalid_grant", "the code was issued to another client")
        if form.get("redirect_uri") != issued.redirect_uri:
            return error(HTTPStatus.BAD_REQUEST, "invalid_grant", "redirect_uri does not match the authorization")
        verifier = form.get("code_verifier", "")
        if not _PKCE_RE.fullmatch(verifier) or not hmac.compare_digest(_s256(verifier), issued.code_challenge):
            return error(HTTPStatus.BAD_REQUEST, "invalid_grant", "code_verifier does not match the code_challenge")

        user = self.config.users[issued.sub]
        issued_at = int(now)
        acr = ACR_STEP_UP if SECOND_FACTOR in issued.amr else ACR_BASIC
        profile = _profile_claims(user, issued.scopes)
        id_claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": user.sub,
            "aud": client.client_id,
            "azp": client.client_id,
            "iat": issued_at,
            "exp": issued_at + TOKEN_TTL_SECONDS,
            "auth_time": issued.auth_time,
            "acr": acr,
            "amr": list(issued.amr),
            **profile,
        }
        if issued.nonce is not None:
            id_claims["nonce"] = issued.nonce
        access_claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": user.sub,
            "aud": f"{self.issuer}/userinfo",
            "client_id": client.client_id,
            "scope": " ".join(issued.scopes),
            "iat": issued_at,
            "exp": issued_at + TOKEN_TTL_SECONDS,
            "jti": secrets.token_urlsafe(16),
            "token_use": "access",
            "auth_time": issued.auth_time,
            "acr": acr,
            "amr": list(issued.amr),
        }
        return Response.json(
            HTTPStatus.OK,
            {
                "access_token": self._sign(access_claims),
                "token_type": "Bearer",
                "expires_in": TOKEN_TTL_SECONDS,
                "id_token": self._sign(id_claims),
                "scope": " ".join(issued.scopes),
            },
            no_store,
        )

    def userinfo(self, authorization: str | None) -> Response:
        challenge = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
        if not authorization or not authorization.startswith("Bearer "):
            return Response.json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_token"}, challenge)
        try:
            claims = jwt.decode(
                authorization[7:].strip(),
                self.key.private_key.public_key(),
                algorithms=["RS256"],
                audience=f"{self.issuer}/userinfo",
                issuer=self.issuer,
                # Expiry is checked below against this stub's clock, the one that issued the token.
                options={"require": ["exp", "iat", "sub", "aud", "iss"], "verify_exp": False, "verify_iat": False},
            )
        except jwt.PyJWTError:
            return Response.json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_token"}, challenge)
        user = self.config.users.get(str(claims.get("sub")))
        expired = not isinstance(claims.get("exp"), int) or claims["exp"] <= self._now()
        if expired or claims.get("token_use") != "access" or user is None:
            return Response.json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_token"}, challenge)
        scopes = tuple(str(claims.get("scope", "")).split())
        return Response.json(HTTPStatus.OK, {"sub": user.sub, **_profile_claims(user, scopes)})

    def _sign(self, claims: Mapping[str, Any]) -> str:
        return jwt.encode(dict(claims), self.key.private_key, algorithm="RS256", headers={"kid": self.key.kid})


def single_valued(pairs: list[tuple[str, str]]) -> tuple[dict[str, str], str | None]:
    """The parameters as a dict, and the first name that appears more than once (or None).

    RFC 6749 section 3.1: request parameters must not be included more than
    once. Taking the first or the last copy silently would let two parties
    read one request differently.
    """
    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params:
            return params, name
        params[name] = value
    return params, None


def _code_params(code: str, state: str | None, issuer: str) -> dict[str, str]:
    params = {"code": code, "iss": issuer}
    if state is not None:
        params["state"] = state
    return params


def _profile_claims(user: User, scopes: tuple[str, ...]) -> dict[str, Any]:
    claims: dict[str, Any] = {}
    if "email" in scopes:
        claims.update(email=user.email, email_verified=True)
    if "profile" in scopes:
        claims.update(name=user.name, roles=list(user.roles))
    return claims


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1><p>Development identity provider. Not for production use.</p>"
        f"{body}</body></html>"
    )


# ── HTTP server ──────────────────────────────────────────────────────────────


#: OAuth 2.0 error codes (RFC 6749 and RFC 6750), which are the only values a
#: refusal contributes to the request log. The accompanying description is not
#: logged: it is written per call site and could quote a request parameter.
OAUTH_ERROR_CODES = frozenset({
    "invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "unsupported_response_type", "invalid_scope",
    "access_denied", "server_error", "temporarily_unavailable", "invalid_token",
    "login_required", "interaction_required", "consent_required", "account_selection_required",
    "not_found",
})


def oauth_error_detail(response: Response) -> dict[str, str]:
    """The OAuth error code a refusal carried, for the request log.

    A development stub that refuses without saying why costs an afternoon, and the code is enough
    to say which check refused. Only codes from :data:`OAUTH_ERROR_CODES` are returned, so nothing
    a caller supplied can reach the log through here, and an unreadable body says nothing rather
    than failing the response that is already on its way out.
    """
    if int(response.status) < 400 or not response.content_type.startswith("application/json"):
        return {}
    try:
        body = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(body, dict):
        return {}
    code = body.get("error")
    return {"error": code} if isinstance(code, str) and code in OAUTH_ERROR_CODES else {}


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), file=sys.stderr, flush=True)


def make_handler(stub: OIDCStub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agenticorg-oidc-stub"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from the base class
            return  # request lines carry codes and states; _send logs a redacted line instead

        def _session_id(self) -> str | None:
            header = self.headers.get("Cookie")
            if not header:
                return None
            try:
                cookie = SimpleCookie(header)
            except CookieError:
                return None
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else None

        def _read_body(self) -> bytes | None:
            """The request body, whether it is framed by length or chunked.

            A client that does not know the length in advance sends
            ``Transfer-Encoding: chunked``, which is ordinary HTTP/1.1 and
            what Node's ``http.request`` does when no ``Content-Length`` is
            set. Reading only ``Content-Length`` bodies made such a request
            look like an empty form, which is an OAuth error about the wrong
            thing.
            """
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                chunks = bytearray()
                while True:
                    line = self.rfile.readline(64).strip().split(b";", 1)[0]
                    try:
                        size = int(line, 16)
                    except ValueError:
                        return None
                    if size < 0 or len(chunks) + size > MAX_BODY_BYTES:
                        return None
                    if size == 0:
                        # Consume the trailer section up to the blank line.
                        while self.rfile.readline(MAX_BODY_BYTES).strip():
                            pass
                        return bytes(chunks)
                    chunks += self.rfile.read(size)
                    if self.rfile.read(2) != b"\r\n":
                        return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if length < 0 or length > MAX_BODY_BYTES:
                return None
            return self.rfile.read(length)

        def _form(self) -> tuple[dict[str, str] | None, str | None]:
            if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                return None, None
            raw = self._read_body()
            if raw is None:
                return None, None
            return single_valued(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))

        def _repeated(self, name: str) -> Response:
            description = f"parameter {name!r} appears more than once"
            if urlsplit(self.path).path == "/authorize" and self.command == "GET":
                markup = _page("Sign-in request rejected", html.escape(description))
                return Response.html(HTTPStatus.BAD_REQUEST, markup)
            return Response.json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "error_description": description})

        def _send(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            if response.content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.send_header("Cache-Control", "no-store")
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)
            _log(
                "oidc_stub_request", method=self.command, path=urlsplit(self.path).path,
                status=int(response.status), **oauth_error_detail(response),
            )  # fmt: skip

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            parts = urlsplit(self.path)
            if parts.path == "/healthz":
                self._send(Response.json(HTTPStatus.OK, {"status": "ok"}))
            elif parts.path == "/.well-known/openid-configuration":
                self._send(Response.json(HTTPStatus.OK, stub.discovery()))
            elif parts.path == "/jwks":
                self._send(Response.json(HTTPStatus.OK, stub.jwks()))
            elif parts.path == "/authorize":
                query, repeated = single_valued(parse_qsl(parts.query, keep_blank_values=True))
                if repeated is not None:
                    self._send(self._repeated(repeated))
                else:
                    self._send(stub.authorize(query, self._session_id()))
            elif parts.path == "/userinfo":
                self._send(stub.userinfo(self.headers.get("Authorization")))
            else:
                self._send(Response.json(HTTPStatus.NOT_FOUND, {"error": "not_found"}))

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            path = urlsplit(self.path).path
            form, repeated = self._form()
            if path not in ("/authorize", "/token", "/userinfo"):
                self._send(Response.json(HTTPStatus.NOT_FOUND, {"error": "not_found"}))
            elif path == "/userinfo":
                self._send(stub.userinfo(self.headers.get("Authorization")))
            elif repeated is not None:
                self._send(self._repeated(repeated))
            elif form is None:
                self._send(Response.json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request",
                                                                   "error_description": "expected a form body"}))
            elif path == "/authorize":
                self._send(stub.complete_sign_in(form, self._session_id()))
            else:
                self._send(stub.token(form, self.headers.get("Authorization")))

    return Handler


def make_server(stub: OIDCStub, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(stub))
    server.daemon_threads = True
    return server
