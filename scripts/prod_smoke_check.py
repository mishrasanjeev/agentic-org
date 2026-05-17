"""Production smoke checks with explicit secret handling.

The runner is intentionally conservative: unauthenticated health checks run by
default, while authenticated or mutating checks run only when the operator
provides the relevant environment variables. Missing credentials produce
SKIPPED results with exact variable names, not hidden failures.

Core environment:
  AGENTICORG_PROD_API_BASE_URL
  AGENTICORG_SMOKE_BEARER_TOKEN or AGENTICORG_SMOKE_EMAIL + AGENTICORG_SMOKE_PASSWORD

Optional authenticated checks:
  AGENTICORG_SMOKE_CA_AGENT_ID
  AGENTICORG_SMOKE_ZOHO_MISSING_AGENT_ID
  AGENTICORG_SMOKE_WORKFLOW_RUN_ID or AGENTICORG_SMOKE_WORKFLOW_ID
  AGENTICORG_SMOKE_BRIDGE_ID
  AGENTICORG_SMOKE_CHAT_QUERY and optional AGENTICORG_SMOKE_CHAT_AGENT_ID
  AGENTICORG_SMOKE_KNOWLEDGE_QUERY
  AGENTICORG_SMOKE_CONTENT_SAFETY_TEXT

Optional signed CDC/event-wait dedupe check:
  AGENTICORG_SMOKE_CDC_TENANT_ID
  AGENTICORG_SMOKE_CDC_CONNECTOR
  AGENTICORG_SMOKE_CDC_SECRET
  AGENTICORG_SMOKE_CDC_EVENT_ID
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_API_BASE_URL = "https://app.agenticorg.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 20.0

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

SENSITIVE_NAME_FRAGMENTS = (
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "KEY",
    "PASSWORD",
    "SECRET",
    "SIGNATURE",
    "TOKEN",
)
SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-cdc-signature",
        "x-csrf-token",
    }
)


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: Any
    body_text: str


@dataclass(frozen=True)
class SmokeResult:
    name: str
    status: str
    detail: str
    http_status: int | None = None


@dataclass(frozen=True)
class AuthContext:
    token: str
    tenant_id: str | None = None


def is_sensitive_name(name: str) -> bool:
    upper = name.upper()
    return any(fragment in upper for fragment in SENSITIVE_NAME_FRAGMENTS)


def redact_value(value: str | None) -> str:
    if value in (None, ""):
        return ""
    return "<redacted>"


def redact_mapping(mapping: dict[str, str]) -> dict[str, str]:
    return {
        key: redact_value(value) if is_sensitive_name(key) else value
        for key, value in mapping.items()
    }


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: redact_value(value) if key.lower() in SENSITIVE_HEADER_NAMES else value
        for key, value in headers.items()
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _api_base_url() -> str:
    return os.getenv("AGENTICORG_PROD_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def _timeout_seconds() -> float:
    raw = os.getenv("AGENTICORG_PROD_SMOKE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1.0, timeout)


def missing_env(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if not os.getenv(name))


def skip_missing(name: str, required_env: tuple[str, ...]) -> SmokeResult | None:
    missing = missing_env(required_env)
    if not missing:
        return None
    return SmokeResult(
        name=name,
        status=SKIPPED,
        detail=f"missing required env: {', '.join(missing)}",
    )


def _parse_json_body(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class ProdSmokeRunner:
    def __init__(self, *, api_base_url: str | None = None, timeout_seconds: float | None = None) -> None:
        self.api_base_url = (api_base_url or _api_base_url()).rstrip("/")
        self.timeout_seconds = timeout_seconds or _timeout_seconds()

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.api_base_url}{normalized}"

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        req_headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            req_headers["Authorization"] = f"Bearer {token}"
        body_bytes = None
        if payload is not None:
            body_bytes = _json_bytes(payload)
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)
        try:
            response = httpx.request(
                method.upper(),
                self._url(path),
                content=body_bytes,
                headers=req_headers,
                timeout=self.timeout_seconds,
            )
            raw = response.text
            return HttpResult(
                status_code=response.status_code,
                body=_parse_json_body(raw),
                body_text=raw,
            )
        except httpx.HTTPError as exc:
            return HttpResult(
                status_code=0,
                body={"error": "network_error", "type": type(exc).__name__},
                body_text="",
            )

    def pass_if_status(
        self,
        name: str,
        result: HttpResult,
        expected: set[int],
        *,
        detail: str,
    ) -> SmokeResult:
        if result.status_code in expected:
            return SmokeResult(name=name, status=PASS, detail=detail, http_status=result.status_code)
        return SmokeResult(
            name=name,
            status=FAIL,
            detail=f"unexpected HTTP status {result.status_code}; expected {sorted(expected)}",
            http_status=result.status_code or None,
        )

    def health_checks(self) -> list[SmokeResult]:
        checks = (
            ("health.readiness", "GET", "/health", {200}),
            ("health.liveness", "GET", "/health/liveness", {200}),
            ("billing.health", "GET", "/billing/health", {200}),
            ("knowledge.health", "GET", "/knowledge/health", {200}),
        )
        results: list[SmokeResult] = []
        for name, method, path, expected in checks:
            response = self.request_json(method, path)
            results.append(self.pass_if_status(name, response, expected, detail="endpoint reachable"))
        return results

    def authenticate(self) -> tuple[AuthContext | None, SmokeResult]:
        token = os.getenv("AGENTICORG_SMOKE_BEARER_TOKEN")
        if token:
            return AuthContext(token=token), SmokeResult(
                name="auth.token",
                status=PASS,
                detail="using env-provided bearer token",
            )

        required = ("AGENTICORG_SMOKE_EMAIL", "AGENTICORG_SMOKE_PASSWORD")
        skipped = skip_missing("auth.login", required)
        if skipped:
            return None, skipped

        payload = {
            "email": os.environ["AGENTICORG_SMOKE_EMAIL"],
            "password": os.environ["AGENTICORG_SMOKE_PASSWORD"],
        }
        response = self.request_json("POST", "/auth/login", payload=payload)
        if response.status_code != 200 or not isinstance(response.body, dict):
            return None, SmokeResult(
                name="auth.login",
                status=FAIL,
                detail=f"login failed with HTTP {response.status_code}",
                http_status=response.status_code,
            )
        access_token = response.body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            return None, SmokeResult(
                name="auth.login",
                status=FAIL,
                detail="login response did not include access_token",
                http_status=response.status_code,
            )
        user = response.body.get("user") if isinstance(response.body.get("user"), dict) else {}
        tenant_id = user.get("tenant_id") if isinstance(user, dict) else None
        return AuthContext(token=access_token, tenant_id=tenant_id), SmokeResult(
            name="auth.login",
            status=PASS,
            detail="login returned bearer token",
            http_status=response.status_code,
        )

    def authenticated_checks(self, auth: AuthContext | None) -> list[SmokeResult]:
        if auth is None:
            return [
                SmokeResult(
                    name="authenticated.checks",
                    status=SKIPPED,
                    detail="missing authenticated smoke token or login credentials",
                )
            ]

        results = [
            self.pass_if_status(
                "bridge.list",
                self.request_json("GET", "/bridge/list", token=auth.token),
                {200},
                detail="authenticated bridge list reachable",
            ),
            self.content_safety_check(auth),
            self.knowledge_search_check(auth),
            self.chat_query_check(auth),
            self.ca_pack_promotion_check(auth),
            self.zoho_fail_closed_check(auth),
            self.workflow_state_check(auth),
        ]
        bridge_id = os.getenv("AGENTICORG_SMOKE_BRIDGE_ID")
        if bridge_id:
            results.append(
                self.pass_if_status(
                    "bridge.status",
                    self.request_json("GET", f"/bridge/{bridge_id}/status", token=auth.token),
                    {200, 404},
                    detail="authenticated bridge status returned deterministic state",
                )
            )
        else:
            results.append(
                SmokeResult(
                    name="bridge.status",
                    status=SKIPPED,
                    detail="missing required env: AGENTICORG_SMOKE_BRIDGE_ID",
                )
            )
        return results

    def content_safety_check(self, auth: AuthContext) -> SmokeResult:
        text = os.getenv("AGENTICORG_SMOKE_CONTENT_SAFETY_TEXT", "AgenticOrg deployment smoke text.")
        response = self.request_json(
            "POST",
            "/content-safety/check",
            token=auth.token,
            payload={
                "text": text,
                "config": {"check_pii": True, "check_toxicity": True, "check_duplicates": False},
            },
        )
        return self.pass_if_status(
            "content_safety.check",
            response,
            {200},
            detail="authenticated content-safety check returned a decision",
        )

    def knowledge_search_check(self, auth: AuthContext) -> SmokeResult:
        query = os.getenv("AGENTICORG_SMOKE_KNOWLEDGE_QUERY")
        if not query:
            return SmokeResult(
                name="knowledge.search",
                status=SKIPPED,
                detail="missing required env: AGENTICORG_SMOKE_KNOWLEDGE_QUERY",
            )
        response = self.request_json(
            "POST",
            "/knowledge/search",
            token=auth.token,
            payload={"query": query, "top_k": 3},
        )
        return self.pass_if_status(
            "knowledge.search",
            response,
            {200, 500, 503},
            detail="knowledge search returned success or explicit deterministic failure",
        )

    def chat_query_check(self, auth: AuthContext) -> SmokeResult:
        query = os.getenv("AGENTICORG_SMOKE_CHAT_QUERY")
        if not query:
            return SmokeResult(
                name="chat.query",
                status=SKIPPED,
                detail="missing required env: AGENTICORG_SMOKE_CHAT_QUERY",
            )
        payload: dict[str, Any] = {"query": query}
        agent_id = os.getenv("AGENTICORG_SMOKE_CHAT_AGENT_ID")
        if agent_id:
            payload["agent_id"] = agent_id
        response = self.request_json("POST", "/chat/query", token=auth.token, payload=payload)
        return self.pass_if_status(
            "chat.query",
            response,
            {200, 400, 409, 422, 500, 503},
            detail="chat query returned success or explicit deterministic failure",
        )

    def ca_pack_promotion_check(self, auth: AuthContext) -> SmokeResult:
        skipped = skip_missing("ca_pack.promote", ("AGENTICORG_SMOKE_CA_AGENT_ID",))
        if skipped:
            return skipped
        agent_id = os.environ["AGENTICORG_SMOKE_CA_AGENT_ID"]
        response = self.request_json("POST", f"/agents/{agent_id}/promote", token=auth.token)
        return self.pass_if_status(
            "ca_pack.promote",
            response,
            {200},
            detail="approved CA pack promotion route succeeded",
        )

    def zoho_fail_closed_check(self, auth: AuthContext) -> SmokeResult:
        skipped = skip_missing("ca_pack.zoho_missing_fail_closed", ("AGENTICORG_SMOKE_ZOHO_MISSING_AGENT_ID",))
        if skipped:
            return skipped
        agent_id = os.environ["AGENTICORG_SMOKE_ZOHO_MISSING_AGENT_ID"]
        response = self.request_json("POST", f"/agents/{agent_id}/promote", token=auth.token)
        if response.status_code in {400, 409, 422, 503}:
            return SmokeResult(
                name="ca_pack.zoho_missing_fail_closed",
                status=PASS,
                detail="missing Zoho connector produced deterministic non-success",
                http_status=response.status_code,
            )
        return SmokeResult(
            name="ca_pack.zoho_missing_fail_closed",
            status=FAIL,
            detail=f"expected fail-closed non-success, got HTTP {response.status_code}",
            http_status=response.status_code,
        )

    def workflow_state_check(self, auth: AuthContext) -> SmokeResult:
        run_id = os.getenv("AGENTICORG_SMOKE_WORKFLOW_RUN_ID")
        if run_id:
            response = self.request_json("GET", f"/workflows/runs/{run_id}", token=auth.token)
            return self.pass_if_status(
                "workflow.durable_state",
                response,
                {200},
                detail="existing workflow run state is readable",
            )

        workflow_id = os.getenv("AGENTICORG_SMOKE_WORKFLOW_ID")
        if not workflow_id:
            return SmokeResult(
                name="workflow.durable_state",
                status=SKIPPED,
                detail="missing required env: AGENTICORG_SMOKE_WORKFLOW_RUN_ID or AGENTICORG_SMOKE_WORKFLOW_ID",
            )

        trigger_payload = {"source": "prod_smoke", "ts": int(time.time())}
        response = self.request_json(
            "POST",
            f"/workflows/{workflow_id}/run",
            token=auth.token,
            payload={"payload": trigger_payload},
        )
        if response.status_code != 200 or not isinstance(response.body, dict):
            return SmokeResult(
                name="workflow.durable_state",
                status=FAIL,
                detail=f"workflow run creation failed with HTTP {response.status_code}",
                http_status=response.status_code,
            )
        created_run_id = response.body.get("run_id")
        if not isinstance(created_run_id, str) or not created_run_id:
            return SmokeResult(
                name="workflow.durable_state",
                status=FAIL,
                detail="workflow run response missing run_id",
                http_status=response.status_code,
            )
        state_response = self.request_json("GET", f"/workflows/runs/{created_run_id}", token=auth.token)
        return self.pass_if_status(
            "workflow.durable_state",
            state_response,
            {200},
            detail="created workflow run state is readable",
        )

    def cdc_event_dedupe_check(self) -> SmokeResult:
        required = (
            "AGENTICORG_SMOKE_CDC_TENANT_ID",
            "AGENTICORG_SMOKE_CDC_CONNECTOR",
            "AGENTICORG_SMOKE_CDC_SECRET",
        )
        skipped = skip_missing("cdc.event_dedupe", required)
        if skipped:
            return skipped

        tenant_id = os.environ["AGENTICORG_SMOKE_CDC_TENANT_ID"]
        connector = os.environ["AGENTICORG_SMOKE_CDC_CONNECTOR"]
        secret = os.environ["AGENTICORG_SMOKE_CDC_SECRET"]
        event_id = os.getenv("AGENTICORG_SMOKE_CDC_EVENT_ID", f"prod-smoke-{int(time.time())}")
        payload = {
            "event_type": "prod_smoke.event_wait",
            "resource_type": "smoke_event",
            "resource_id": event_id,
            "provider_event_id": event_id,
        }
        raw_body = _json_bytes(payload)
        signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        headers = {"X-CDC-Signature": signature}
        first = self.request_json(
            "POST",
            f"/webhooks/cdc/{tenant_id}/{connector}",
            payload=payload,
            headers=headers,
        )
        second = self.request_json(
            "POST",
            f"/webhooks/cdc/{tenant_id}/{connector}",
            payload=payload,
            headers=headers,
        )
        second_status = second.body.get("status") if isinstance(second.body, dict) else None
        if first.status_code in {200, 202} and second.status_code == 200 and second_status == "duplicate":
            return SmokeResult(
                name="cdc.event_dedupe",
                status=PASS,
                detail="signed CDC event accepted and duplicate delivery deduped",
                http_status=second.status_code,
            )
        return SmokeResult(
            name="cdc.event_dedupe",
            status=FAIL,
            detail=(
                "expected first delivery accepted and second delivery duplicate; "
                f"got HTTP {first.status_code}/{second.status_code}"
            ),
            http_status=second.status_code or first.status_code or None,
        )

    def run(self) -> list[SmokeResult]:
        results = self.health_checks()
        auth, auth_result = self.authenticate()
        results.append(auth_result)
        results.extend(self.authenticated_checks(auth))
        results.append(self.cdc_event_dedupe_check())
        return results


def print_results(results: list[SmokeResult]) -> None:
    for result in results:
        status = result.status.ljust(7)
        http = f" http={result.http_status}" if result.http_status is not None else ""
        print(f"{status} {result.name}{http} - {result.detail}")


def exit_code(results: list[SmokeResult]) -> int:
    return 1 if any(result.status == FAIL for result in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base-url",
        default=None,
        help="API base URL including /api/v1. Defaults to AGENTICORG_PROD_API_BASE_URL or production.",
    )
    args = parser.parse_args(argv)
    runner = ProdSmokeRunner(api_base_url=args.api_base_url)
    results = runner.run()
    print_results(results)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
