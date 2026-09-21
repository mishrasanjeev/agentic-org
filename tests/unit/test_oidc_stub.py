# SPDX-License-Identifier: Apache-2.0
"""Development OIDC stub (tools/oidc_stub): discovery, PKCE code flow, tokens, step-up, runtime guard."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import socket
import threading
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from tools.oidc_stub import __main__ as stub_main
from tools.oidc_stub import server as oidc
from tools.oidc_stub.server import ACR_BASIC, ACR_STEP_UP, OIDCStub, Response

PUBLIC = "http://127.0.0.1:58390"
INTERNAL = "http://oidc-stub:9400"
APP_REDIRECT = "http://127.0.0.1:3000/api/v1/auth/sso/dev-oidc/callback"
APP_SECRET = "unit-test-placeholder-secret"
REPO_ROOT = Path(__file__).resolve().parents[2]

CONFIG = oidc.parse_config(
    {
        "users": [
            {"sub": "dev-approver-a", "email": "approver.a@example.com", "name": "Approver A", "roles": ["approver"]},
            {"sub": "dev-approver-b", "email": "approver.b@example.com", "name": "Approver B", "roles": ["approver"]},
        ],
        "clients": [
            {"client_id": "app", "client_secret": APP_SECRET, "redirect_uris": [APP_REDIRECT]},
            {"client_id": "spa", "redirect_uris": ["https://console.example.com/callback"]},
        ],
    }
)


@pytest.fixture(scope="module")
def key() -> oidc.SigningKey:
    return oidc.SigningKey.generate()


class Clock:
    def __init__(self) -> None:
        self.now = 1_760_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def stub(key: oidc.SigningKey, clock: Clock) -> OIDCStub:
    return OIDCStub(CONFIG, key, public_url=PUBLIC, internal_url=INTERNAL, clock=clock)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def query_of(response: Response) -> dict[str, str]:
    assert response.status == HTTPStatus.FOUND, response.body[:300]
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["Location"]).query).items()}


def auth_params(challenge: str, **overrides: str) -> dict[str, str]:
    params = {
        "client_id": "app",
        "redirect_uri": APP_REDIRECT,
        "response_type": "code",
        "scope": "openid profile email",
        "state": "state-123",
        "nonce": "nonce-456",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(overrides)
    return {k: v for k, v in params.items() if v != "<omit>"}


def request_id_of(page: Response) -> str:
    match = re.search(r'name="request_id" value="([^"]+)"', page.body.decode())
    assert match, page.body[:500]
    return match.group(1)


def session_of(response: Response) -> str:
    match = re.match(rf"{oidc.SESSION_COOKIE}=([^;]+);", response.headers["Set-Cookie"])
    assert match
    return match.group(1)


def sign_in(
    stub: OIDCStub,
    challenge: str,
    sub: str = "dev-approver-a",
    *,
    second_factor: bool = False,
    session: str | None = None,
    **overrides: str,
) -> tuple[Response, Response]:
    page = stub.authorize(auth_params(challenge, **overrides), session)
    assert page.status == HTTPStatus.OK, page.body[:300]
    form = {"request_id": request_id_of(page), "sub": sub}
    if second_factor:
        form["second_factor"] = "hwk"
    return page, stub.complete_sign_in(form, session)


def exchange(
    stub: OIDCStub, code: str, verifier: str, *, client_id: str = "app", secret: str | None = APP_SECRET, **extra: str
) -> Response:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": APP_REDIRECT, "code_verifier": verifier,
            "client_id": client_id}
    if secret is not None:
        form["client_secret"] = secret
    form.update(extra)
    return stub.token(form, None)


def body(response: Response) -> dict[str, Any]:
    return json.loads(response.body)


def verify_id_token(token: str, key: oidc.SigningKey, audience: str = "app") -> dict[str, Any]:
    jwk = oidc.SigningKey.from_private_key(key.private_key).public_jwk()
    public = jwt.PyJWK(jwk).key
    return jwt.decode(token, public, algorithms=["RS256"], audience=audience, issuer=PUBLIC,
                      options={"verify_exp": False, "verify_iat": False})


# ── Discovery and keys ──────────────────────────────────────────────────────


def test_discovery_advertises_pkce_step_up_and_split_endpoints(stub: OIDCStub) -> None:
    doc = stub.discovery()
    assert doc["issuer"] == PUBLIC
    assert doc["authorization_endpoint"] == f"{PUBLIC}/authorize"
    assert doc["token_endpoint"] == f"{INTERNAL}/token"
    assert doc["jwks_uri"] == f"{INTERNAL}/jwks"
    assert doc["userinfo_endpoint"] == f"{INTERNAL}/userinfo"
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert set(doc["acr_values_supported"]) == {ACR_BASIC, ACR_STEP_UP}


def test_jwks_publishes_only_the_public_key(stub: OIDCStub) -> None:
    [jwk] = stub.jwks()["keys"]
    assert jwk["kty"] == "RSA" and jwk["alg"] == "RS256" and jwk["kid"] == stub.key.kid
    assert "d" not in jwk and "p" not in jwk


# ── Authorization code flow ─────────────────────────────────────────────────


def test_code_flow_with_pkce_issues_verifiable_basic_tokens(stub: OIDCStub, key: oidc.SigningKey, clock: Clock) -> None:
    verifier, challenge = pkce()
    _, done = sign_in(stub, challenge)
    params = query_of(done)
    assert params["state"] == "state-123" and params["iss"] == PUBLIC
    tokens = exchange(stub, params["code"], verifier)
    assert tokens.status == HTTPStatus.OK
    assert tokens.headers["Cache-Control"] == "no-store"
    claims = verify_id_token(body(tokens)["id_token"], key)
    assert claims["sub"] == "dev-approver-a"
    assert claims["nonce"] == "nonce-456"
    assert claims["email"] == "approver.a@example.com" and claims["name"] == "Approver A"
    assert claims["acr"] == ACR_BASIC
    assert claims["amr"] == ["pwd"]
    assert claims["auth_time"] == int(clock.now)


def test_scope_limits_profile_claims(stub: OIDCStub, key: oidc.SigningKey) -> None:
    verifier, challenge = pkce()
    _, done = sign_in(stub, challenge, scope="openid")
    claims = verify_id_token(body(exchange(stub, query_of(done)["code"], verifier))["id_token"], key)
    assert "email" not in claims and "name" not in claims


def test_existing_session_signs_in_without_prompting(stub: OIDCStub, clock: Clock) -> None:
    _, challenge = pkce()
    _, first = sign_in(stub, challenge)
    session = session_of(first)
    clock.now += 30
    again = stub.authorize(auth_params(challenge), session)
    assert "code" in query_of(again)


def test_prompt_none_without_a_session_returns_login_required(stub: OIDCStub) -> None:
    _, challenge = pkce()
    params = query_of(stub.authorize(auth_params(challenge, prompt="none"), None))
    assert params["error"] == "login_required"
    assert params["state"] == "state-123"


def test_prompt_login_forces_reauthentication(stub: OIDCStub) -> None:
    _, challenge = pkce()
    _, first = sign_in(stub, challenge)
    page = stub.authorize(auth_params(challenge, prompt="login"), session_of(first))
    assert page.status == HTTPStatus.OK
    assert b"Sign in as Approver A" in page.body and b"Approver B" not in page.body


# ── Step-up ─────────────────────────────────────────────────────────────────


def test_step_up_acr_yields_hardware_key_amr(stub: OIDCStub, key: oidc.SigningKey) -> None:
    verifier, challenge = pkce()
    page, done = sign_in(stub, challenge, second_factor=True, acr_values=ACR_STEP_UP)
    assert b"security key" in page.body
    claims = verify_id_token(body(exchange(stub, query_of(done)["code"], verifier))["id_token"], key)
    assert claims["acr"] == ACR_STEP_UP
    assert claims["amr"] == ["pwd", "hwk"]


def test_step_up_without_the_second_factor_is_refused(stub: OIDCStub) -> None:
    _, challenge = pkce()
    page, done = sign_in(stub, challenge, second_factor=False, acr_values=ACR_STEP_UP)
    assert done.status == HTTPStatus.BAD_REQUEST
    assert "Location" not in done.headers
    assert b"requires your security key" in done.body


def test_basic_session_is_stepped_up_by_the_same_user_only(
    stub: OIDCStub, key: oidc.SigningKey, clock: Clock
) -> None:
    verifier, challenge = pkce()
    _, first = sign_in(stub, challenge)
    session = session_of(first)
    clock.now += 120

    page = stub.authorize(auth_params(challenge, acr_values=ACR_STEP_UP), session)
    assert page.status == HTTPStatus.OK, "a basic session must not satisfy a step-up request"
    request_id = request_id_of(page)
    other = stub.complete_sign_in({"request_id": request_id, "sub": "dev-approver-b", "second_factor": "hwk"}, session)
    assert other.status == HTTPStatus.BAD_REQUEST

    form = {"request_id": request_id, "sub": "dev-approver-a", "second_factor": "hwk"}
    stepped = stub.complete_sign_in(form, session)
    claims = verify_id_token(body(exchange(stub, query_of(stepped)["code"], verifier))["id_token"], key)
    assert claims["amr"] == ["pwd", "hwk"]
    assert claims["auth_time"] == int(clock.now)
    assert session_of(stepped) != session, "the session id rotates on re-authentication"


def test_stepped_up_session_satisfies_later_step_up_requests(stub: OIDCStub) -> None:
    _, challenge = pkce()
    _, done = sign_in(stub, challenge, second_factor=True, acr_values=ACR_STEP_UP)
    again = stub.authorize(auth_params(challenge, acr_values=ACR_STEP_UP), session_of(done))
    assert "code" in query_of(again)


def test_max_age_forces_reauthentication_once_exceeded(stub: OIDCStub, clock: Clock) -> None:
    _, challenge = pkce()
    _, done = sign_in(stub, challenge)
    session = session_of(done)
    clock.now += 300
    assert "code" in query_of(stub.authorize(auth_params(challenge, max_age="300"), session))
    clock.now += 1
    assert stub.authorize(auth_params(challenge, max_age="300"), session).status == HTTPStatus.OK
    assert stub.authorize(auth_params(challenge, max_age="0"), session).status == HTTPStatus.OK


# ── Authorization request validation ────────────────────────────────────────


def test_unknown_client_or_unregistered_redirect_is_not_redirected(stub: OIDCStub) -> None:
    _, challenge = pkce()
    for overrides in ({"client_id": "nobody"}, {"redirect_uri": "https://attacker.example.com/cb"}):
        response = stub.authorize(auth_params(challenge, **overrides), None)
        assert response.status == HTTPStatus.BAD_REQUEST
        assert "Location" not in response.headers


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"code_challenge": "<omit>"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"scope": "profile email"}, "invalid_scope"),
        ({"scope": "openid admin"}, "invalid_scope"),
        ({"acr_values": "urn:example:gold"}, "invalid_request"),
        ({"max_age": "-5"}, "invalid_request"),
        ({"prompt": "none login"}, "invalid_request"),
    ],
)
def test_invalid_authorization_requests_fail_with_an_oauth_error(
    stub: OIDCStub, overrides: dict[str, str], error: str
) -> None:
    _, challenge = pkce()
    params = query_of(stub.authorize(auth_params(challenge, **overrides), None))
    assert params["error"] == error


def test_loopback_redirect_uris_may_change_port_only() -> None:
    registered = ("http://127.0.0.1:3000/api/cb",)
    assert oidc.redirect_uri_registered("http://127.0.0.1:58313/api/cb", registered)
    assert not oidc.redirect_uri_registered("http://127.0.0.1:58313/api/other", registered)
    assert not oidc.redirect_uri_registered("https://127.0.0.1:58313/api/cb", registered)
    assert not oidc.redirect_uri_registered("http://192.0.2.10:3000/api/cb", registered)
    assert not oidc.redirect_uri_registered("http://127.0.0.1:bad/api/cb", registered)
    assert not oidc.redirect_uri_registered("https://console.example.com:444/cb", ("https://console.example.com/cb",))


def test_expired_sign_in_request_is_refused(stub: OIDCStub, clock: Clock) -> None:
    _, challenge = pkce()
    page = stub.authorize(auth_params(challenge), None)
    clock.now += oidc.REQUEST_TTL_SECONDS + 1
    response = stub.complete_sign_in({"request_id": request_id_of(page), "sub": "dev-approver-a"}, None)
    assert response.status == HTTPStatus.BAD_REQUEST


# ── Token endpoint ──────────────────────────────────────────────────────────


def _code(stub: OIDCStub) -> tuple[str, str]:
    verifier, challenge = pkce()
    _, done = sign_in(stub, challenge)
    return query_of(done)["code"], verifier


def test_code_is_single_use(stub: OIDCStub) -> None:
    code, verifier = _code(stub)
    assert exchange(stub, code, verifier).status == HTTPStatus.OK
    replay = exchange(stub, code, verifier)
    assert replay.status == HTTPStatus.BAD_REQUEST and body(replay)["error"] == "invalid_grant"


def test_wrong_code_verifier_is_invalid_grant(stub: OIDCStub) -> None:
    code, verifier = _code(stub)
    wrong, _ = pkce()
    assert body(exchange(stub, code, wrong))["error"] == "invalid_grant"
    assert body(exchange(stub, code, verifier))["error"] == "invalid_grant", "a failed attempt burns the code"


def test_expired_code_is_invalid_grant(stub: OIDCStub, clock: Clock) -> None:
    code, verifier = _code(stub)
    clock.now += oidc.CODE_TTL_SECONDS + 1
    assert body(exchange(stub, code, verifier))["error"] == "invalid_grant"


def test_redirect_uri_must_match_the_authorization(stub: OIDCStub) -> None:
    code, verifier = _code(stub)
    response = exchange(stub, code, verifier, redirect_uri="http://127.0.0.1:3000/elsewhere")
    assert body(response)["error"] == "invalid_grant"


@pytest.mark.parametrize(
    ("client_id", "secret"),
    [("app", "wrong-secret"), ("app", None), ("spa", "unexpected-secret"), ("nobody", "x")],
)
def test_client_authentication_failures_are_invalid_client(
    stub: OIDCStub, client_id: str, secret: str | None
) -> None:
    code, verifier = _code(stub)
    response = exchange(stub, code, verifier, client_id=client_id, secret=secret)
    assert response.status == HTTPStatus.UNAUTHORIZED
    assert body(response)["error"] == "invalid_client"


def test_client_secret_basic_is_accepted(stub: OIDCStub) -> None:
    code, verifier = _code(stub)
    basic = "Basic " + base64.b64encode(f"app:{APP_SECRET}".encode()).decode()
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": APP_REDIRECT, "code_verifier": verifier}
    assert stub.token(form, basic).status == HTTPStatus.OK


def test_code_issued_to_another_client_is_invalid_grant(key: oidc.SigningKey, clock: Clock) -> None:
    config = oidc.parse_config(
        {
            "users": [{"sub": "u", "email": "u@example.com", "name": "U"}],
            "clients": [
                {"client_id": "one", "redirect_uris": [APP_REDIRECT]},
                {"client_id": "two", "redirect_uris": [APP_REDIRECT]},
            ],
        }
    )
    stub = OIDCStub(config, key, public_url=PUBLIC, clock=clock)
    verifier, challenge = pkce()
    page = stub.authorize(auth_params(challenge, client_id="one"), None)
    done = stub.complete_sign_in({"request_id": request_id_of(page), "sub": "u"}, None)
    response = exchange(stub, query_of(done)["code"], verifier, client_id="two", secret=None)
    assert body(response)["error"] == "invalid_grant"


def test_unsupported_grant_type_is_rejected(stub: OIDCStub) -> None:
    response = stub.token({"grant_type": "password", "client_id": "app", "client_secret": APP_SECRET}, None)
    assert body(response)["error"] == "unsupported_grant_type"


# ── Userinfo ────────────────────────────────────────────────────────────────


def test_userinfo_accepts_only_this_stubs_access_tokens(stub: OIDCStub) -> None:
    code, verifier = _code(stub)
    tokens = body(exchange(stub, code, verifier))
    ok = stub.userinfo(f"Bearer {tokens['access_token']}")
    assert ok.status == HTTPStatus.OK
    assert body(ok) == {"sub": "dev-approver-a", "email": "approver.a@example.com", "email_verified": True,
                        "name": "Approver A", "roles": ["approver"]}

    for header in (None, "Bearer ", f"Bearer {tokens['id_token']}", f"Bearer {tokens['access_token'][:-4]}AAAA"):
        denied = stub.userinfo(header)
        assert denied.status == HTTPStatus.UNAUTHORIZED
        assert denied.headers["WWW-Authenticate"].startswith("Bearer")


def test_userinfo_rejects_an_expired_access_token(stub: OIDCStub, clock: Clock) -> None:
    code, verifier = _code(stub)
    token = body(exchange(stub, code, verifier))["access_token"]
    clock.now += oidc.TOKEN_TTL_SECONDS - 1
    assert stub.userinfo(f"Bearer {token}").status == HTTPStatus.OK
    clock.now += 1
    assert stub.userinfo(f"Bearer {token}").status == HTTPStatus.UNAUTHORIZED


# ── Runtime guard and configuration ─────────────────────────────────────────


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"AGENTICORG_ENV": "production"},
        {"AGENTICORG_ENV": "staging"},
        {"AGENTICORG_ENV": "development", "ENVIRONMENT": "prod"},
        {"AGENTICORG_ENV": "development", "NODE_ENV": "production"},
        {"AGENTICORG_ENV": "qa"},
    ],
)
def test_refuses_to_start_outside_development(environ: dict[str, str]) -> None:
    with pytest.raises(oidc.StartupRefusedError):
        oidc.assert_development_runtime(environ)


@pytest.mark.parametrize("runtime", ["development", "local", "test"])
def test_starts_in_development_and_test(runtime: str) -> None:
    assert oidc.assert_development_runtime({"AGENTICORG_ENV": runtime}) == runtime


def test_main_exits_without_serving_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_server(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not bind a port in production")

    monkeypatch.setattr(stub_main, "make_server", no_server)
    monkeypatch.setenv("AGENTICORG_ENV", "production")
    assert stub_main.main() == stub_main.EXIT_REFUSED
    assert "refusing to start" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ({"users": [], "clients": []}, "'users'"),
        (
            {"users": [{"sub": "a", "email": "a@example.com", "name": "A"}] * 2,
             "clients": [{"client_id": "c", "redirect_uris": [APP_REDIRECT]}]},
            "duplicate sub",
        ),
        (
            {"users": [{"sub": "a", "email": "a@example.com", "name": "A"}],
             "clients": [{"client_id": "c", "redirect_uris": ["/relative"]}]},
            "absolute http",
        ),
        (
            {"users": [{"sub": "a", "email": "a@example.com", "name": "A"}],
             "clients": [{"client_id": "c", "client_secret": "", "redirect_uris": [APP_REDIRECT]}]},
            "client_secret",
        ),
        ({"users": [{"sub": "a", "name": "A"}], "clients": []}, "'email'"),
    ],
)
def test_invalid_config_is_rejected(data: dict[str, Any], reason: str) -> None:
    with pytest.raises(oidc.ConfigError, match=reason):
        oidc.parse_config(data)


def test_committed_dev_config_seeds_two_distinct_approvers() -> None:
    config = oidc.load_config(REPO_ROOT / "tools" / "oidc_stub" / "config.dev.json")
    assert {"dev-approver-a", "dev-approver-b"} <= set(config.users)
    emails = {u.email for u in config.users.values()}
    assert len(emails) == len(config.users)
    assert all(email.endswith("@example.com") for email in emails)


# ── Over HTTP ───────────────────────────────────────────────────────────────


@pytest.fixture()
def running(key: oidc.SigningKey) -> Iterator[str]:
    stub = OIDCStub(CONFIG, key, public_url=PUBLIC)
    server = oidc.make_server(stub, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_full_flow_over_http(running: str, key: oidc.SigningKey) -> None:
    verifier, challenge = pkce()
    with httpx.Client(base_url=running, follow_redirects=False, timeout=10) as client:
        assert client.get("/.well-known/openid-configuration").json()["issuer"] == PUBLIC
        assert client.get("/jwks").json()["keys"][0]["kid"] == key.kid

        page = client.get("/authorize", params=auth_params(challenge, acr_values=ACR_STEP_UP))
        assert page.status_code == 200
        assert page.headers["content-security-policy"] == "default-src 'none'"
        assert page.headers["x-frame-options"] == "DENY"
        request_id = re.search(r'name="request_id" value="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]

        form = {"request_id": request_id, "sub": "dev-approver-b", "second_factor": "hwk"}
        done = client.post("/authorize", data=form)
        assert done.status_code == 302
        assert oidc.SESSION_COOKIE in done.headers["set-cookie"] and "HttpOnly" in done.headers["set-cookie"]
        code = parse_qs(urlsplit(done.headers["location"]).query)["code"][0]

        tokens = client.post(
            "/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": APP_REDIRECT,
                  "code_verifier": verifier},
            auth=("app", APP_SECRET),
        )
        assert tokens.status_code == 200, tokens.text
        claims = verify_id_token(tokens.json()["id_token"], key)
        assert (claims["sub"], claims["acr"], claims["amr"]) == ("dev-approver-b", ACR_STEP_UP, ["pwd", "hwk"])

        info = client.get("/userinfo", headers={"Authorization": f"Bearer {tokens.json()['access_token']}"})
        assert info.json()["email"] == "approver.b@example.com"

        assert client.post("/token", content=b"{}", headers={"Content-Type": "application/json"}).status_code == 400
        assert client.get("/nothing-here").status_code == 404


# ── Review follow-ups: session lifetime, auth_time, max_age, repeated parameters ──


def test_session_expires_after_its_lifetime(stub: OIDCStub, clock: Clock) -> None:
    _, challenge = pkce()
    _, done = sign_in(stub, challenge)
    session = session_of(done)
    assert f"Max-Age={oidc.SESSION_TTL_SECONDS}" in done.headers["Set-Cookie"]
    clock.now += oidc.SESSION_TTL_SECONDS - 1
    assert "code" in query_of(stub.authorize(auth_params(challenge), session))
    clock.now += 1
    page = stub.authorize(auth_params(challenge), session)
    assert page.status == HTTPStatus.OK, "an expired session must sign in again"
    assert b"Approver B" in page.body, "an expired session no longer pins the user"


def test_reused_session_keeps_the_original_auth_time_in_both_tokens(
    stub: OIDCStub, key: oidc.SigningKey, clock: Clock
) -> None:
    verifier, challenge = pkce()
    _, first = sign_in(stub, challenge)
    signed_in_at = int(clock.now)
    clock.now += 600
    again = stub.authorize(auth_params(challenge), session_of(first))
    tokens = body(exchange(stub, query_of(again)["code"], verifier))
    assert verify_id_token(tokens["id_token"], key)["auth_time"] == signed_in_at
    access = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    assert access["auth_time"] == signed_in_at


def test_stepped_up_session_answers_a_basic_request_with_step_up_claims(
    stub: OIDCStub, key: oidc.SigningKey
) -> None:
    verifier, challenge = pkce()
    _, done = sign_in(stub, challenge, second_factor=True, acr_values=ACR_STEP_UP)
    basic = stub.authorize(auth_params(challenge), session_of(done))
    claims = verify_id_token(body(exchange(stub, query_of(basic)["code"], verifier))["id_token"], key)
    assert (claims["acr"], claims["amr"]) == (ACR_STEP_UP, ["pwd", "hwk"])


@pytest.mark.parametrize("value", ["²", "٣", "1e3", " 5", "5 ", "+5", "9999999999"])
def test_max_age_accepts_ascii_digits_only(stub: OIDCStub, value: str) -> None:
    _, challenge = pkce()
    params = query_of(stub.authorize(auth_params(challenge, max_age=value), None))
    assert params["error"] == "invalid_request"


def test_single_valued_reports_the_first_repeated_parameter() -> None:
    assert oidc.single_valued([("a", "1"), ("b", "2")]) == ({"a": "1", "b": "2"}, None)
    assert oidc.single_valued([("a", "1"), ("b", "2"), ("a", "3")])[1] == "a"


def test_repeated_parameters_are_rejected_over_http(running: str) -> None:
    _, challenge = pkce()
    base = auth_params(challenge)
    pairs = [*base.items(), ("redirect_uri", "https://attacker.example.com/cb")]
    with httpx.Client(base_url=running, follow_redirects=False, timeout=10) as client:
        page = client.get("/authorize", params=pairs)
        assert page.status_code == 400
        assert "location" not in page.headers
        assert "redirect_uri" in page.text

        token = client.post(
            "/token",
            content="grant_type=authorization_code&code=x&code=y",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert token.status_code == 400
        assert token.json()["error"] == "invalid_request"


# --- a body framed by chunks, not by length -------------------------------------------------------


def _chunked(payload: str) -> Iterator[bytes]:
    """The payload in two chunks, as an HTTP client with no known length sends it."""
    half = len(payload) // 2
    for part in (payload[:half], payload[half:]):
        if part:
            yield part.encode()


def test_a_chunked_form_body_is_read(running: str) -> None:
    """Clients that do not know the length in advance send Transfer-Encoding: chunked.

    Reading only Content-Length bodies made such a request look like an empty form, and the stub
    then answered with an OAuth error about the wrong thing - `unsupported_grant_type` for a
    request whose grant type was there all along.
    """
    with httpx.Client(base_url=running, follow_redirects=False, timeout=10) as client:
        chunked = client.post(
            "/token",
            content=_chunked("grant_type=authorization_code&code=unknown&client_id=spa&code_verifier=" + "a" * 43),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # The client framed it by chunks, which is the point of this test.
        assert chunked.request.headers.get("transfer-encoding") == "chunked"
        assert chunked.status_code == 400
        # The grant type was read; the code is what is unknown.
        assert chunked.json()["error"] == "invalid_grant"

        repeated = client.post(
            "/token",
            content=_chunked("grant_type=authorization_code&code=x&code=y"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert repeated.status_code == 400
        assert repeated.json()["error"] == "invalid_request"


def test_an_oversized_chunked_body_is_refused(running: str) -> None:
    with httpx.Client(base_url=running, follow_redirects=False, timeout=10) as client:
        response = client.post(
            "/token",
            content=_chunked("grant_type=authorization_code&code=" + "a" * (oidc.MAX_BODY_BYTES + 1)),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"


def test_a_refusal_is_logged_with_its_oauth_error(running: str, capfd: pytest.CaptureFixture[str]) -> None:
    """A development stub that refuses without saying why costs an afternoon."""
    with httpx.Client(base_url=running, follow_redirects=False, timeout=10) as client:
        assert client.post(
            "/token",
            content="grant_type=client_credentials",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code == 400
    logged = [json.loads(line) for line in capfd.readouterr().err.splitlines() if line.startswith("{")]
    refusals = [entry for entry in logged if entry.get("path") == "/token" and entry.get("status") == 400]
    assert refusals and refusals[-1]["error"] == "unsupported_grant_type"
    # Only the code: the description is written per call site and could quote a request parameter.
    assert "error_description" not in refusals[-1]


# --- redirect URIs the stack chooses at run time --------------------------------------------------


def test_extra_redirect_uris_are_added_to_every_client_and_validated() -> None:
    extra = "http://127.0.0.1:58391/decisions/callback"
    widened = oidc.with_extra_redirect_uris(CONFIG, [extra])
    assert all(extra in client.redirect_uris for client in widened.clients.values())
    # The configured ones are kept, and the originals are untouched.
    assert APP_REDIRECT in widened.clients["app"].redirect_uris
    assert widened.clients["app"].client_secret == APP_SECRET
    assert extra not in CONFIG.clients["app"].redirect_uris

    assert oidc.with_extra_redirect_uris(CONFIG, []) is CONFIG
    # Added twice is added once.
    assert list(oidc.with_extra_redirect_uris(CONFIG, [extra, extra]).clients["app"].redirect_uris).count(extra) == 1
    with pytest.raises(oidc.ConfigError):
        oidc.with_extra_redirect_uris(CONFIG, ["not-a-url"])


def _raw_post(origin: str, body: bytes, headers: str) -> str:
    """Send a request the HTTP client libraries will not send, and return the status line."""
    parts = urlsplit(origin)
    with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80), timeout=10) as sock:
        sock.sendall(
            f"POST /token HTTP/1.1\r\nHost: {parts.netloc}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n{headers}\r\n".encode()
            + body
        )
        received = b""
        while b"\r\n\r\n" not in received:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
    return received.split(b"\r\n", 1)[0].decode()


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        # A chunk size that is not hexadecimal.
        (b"zz\r\ngrant_type=x\r\n0\r\n\r\n", "Transfer-Encoding: chunked\r\n"),
        # A chunk not terminated by CRLF.
        (b"0d\r\ngrant_type=xxx!!\r\n\r\n", "Transfer-Encoding: chunked\r\n"),
        # A Content-Length that is not a number, and one past the limit.
        (b"grant_type=authorization_code", "Content-Length: nine\r\n"),
        (b"grant_type=authorization_code", f"Content-Length: {oidc.MAX_BODY_BYTES + 1}\r\n"),
    ],
)
def test_an_unreadable_body_is_refused_rather_than_read_as_an_empty_form(
    running: str, body: bytes, headers: str
) -> None:
    assert "400" in _raw_post(running, body, headers)


def test_a_chunked_body_with_trailers_is_read(running: str) -> None:
    status = _raw_post(
        running,
        b"1d\r\ngrant_type=client_credentials\r\n0\r\nX-Trailer: ignored\r\n\r\n",
        "Transfer-Encoding: chunked\r\n",
    )
    # The form was read: the refusal is about the grant type, not a missing body.
    assert "400" in status


def test_the_logged_detail_of_a_refusal_ignores_anything_it_cannot_read() -> None:
    refusal = Response.json(HTTPStatus.BAD_REQUEST, {"error": "invalid_grant", "error_description": "no", "x": 1})
    assert oidc.oauth_error_detail(refusal) == {"error": "invalid_grant"}
    assert oidc.oauth_error_detail(Response.json(HTTPStatus.OK, {"error": "invalid_grant"})) == {}
    # Anything that is not a known OAuth error code is not logged.
    assert oidc.oauth_error_detail(Response.json(HTTPStatus.BAD_REQUEST, {"error": "secret=hunter2"})) == {}
    assert oidc.oauth_error_detail(Response.html(HTTPStatus.BAD_REQUEST, "<p>error</p>")) == {}
    assert oidc.oauth_error_detail(oidc.Response(400, b"\xff\xfe", "application/json")) == {}
    assert oidc.oauth_error_detail(oidc.Response(400, b"[1, 2]", "application/json")) == {}


def test_extra_redirect_uris_from_the_environment_reach_the_clients(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`main` widens the configured clients, so a callback whose port the stack picks still works."""
    config = tmp_path / "approvers.json"
    config.write_text(
        json.dumps(
            {
                "users": [{"sub": "a", "email": "a@example.com", "name": "A"}],
                "clients": [{"client_id": "c", "redirect_uris": ["http://127.0.0.1:58392/cb"]}],
            }
        ),
        encoding="utf-8",
    )
    extra = "http://127.0.0.1:58393/decisions/callback"
    monkeypatch.setenv("AGENTICORG_ENV", "development")
    monkeypatch.setenv("OIDC_STUB_CONFIG", str(config))
    monkeypatch.setenv("OIDC_STUB_EXTRA_REDIRECT_URIS", f" {extra} , ")
    built: list[OIDCStub] = []

    def no_server(stub: OIDCStub, host: str, port: int) -> Any:
        built.append(stub)
        raise KeyboardInterrupt

    monkeypatch.setattr(stub_main, "make_server", no_server)
    with pytest.raises(KeyboardInterrupt):
        stub_main.main()
    assert extra in built[0].config.clients["c"].redirect_uris
    assert "http://127.0.0.1:58392/cb" in built[0].config.clients["c"].redirect_uris


def test_only_a_known_method_and_route_are_logged() -> None:
    """Nothing a caller chose reaches the log: the request line is mapped to a fixed set."""
    assert oidc.request_label("POST", "/token?client_secret=x") == ("POST", "/token")
    discovery = "/.well-known/openid-configuration"
    assert oidc.request_label("GET", discovery) == ("GET", discovery)
    assert oidc.request_label("PATCH", "/token") == ("other", "/token")
    assert oidc.request_label("GET", "/admin?password=x") == ("GET", "other")
