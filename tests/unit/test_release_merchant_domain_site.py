# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""scripts/release_merchant_domain_site.py: create, finalize and release the agent-only site version
through the Firebase Hosting REST API (a fake session; no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from core.commerce.custom_domains import FIREBASE_API, firebase_site_config
from scripts import release_merchant_domain_site as release

SITE = "merchant-domains-example"


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body


class _Session:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_on = fail_on

    def _answer(self, step: str, body: dict[str, Any]) -> _Response:
        if self.fail_on == step:
            return _Response(403, {"error": {"message": "The caller does not have permission"}})
        return _Response(200, body)

    def post(self, url: str, *, json: Any, timeout: int) -> _Response:
        self.calls.append(("POST", url, json))
        if url.endswith("/versions"):
            return self._answer("create", {"name": f"sites/{SITE}/versions/v1", "status": "CREATED"})
        return self._answer("release", {"name": f"sites/{SITE}/releases/r1"})

    def patch(self, url: str, *, json: Any, timeout: int) -> _Response:
        self.calls.append(("PATCH", url, json))
        return self._answer("finalize", {"name": f"sites/{SITE}/versions/v1", "status": "FINALIZED"})


def test_release_creates_finalizes_and_releases_the_agent_only_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _Session()
    asked: list[str] = []

    def fake_session(project: str) -> _Session:
        asked.append(project)
        return session

    monkeypatch.setattr(release, "_session", fake_session)
    assert release.main(["--project", "example-project", "--site", SITE]) == 0
    assert asked == ["example-project"]
    base = f"{FIREBASE_API}/sites/{SITE}"
    assert session.calls == [
        ("POST", f"{base}/versions", {"config": firebase_site_config()}),
        ("PATCH", f"{FIREBASE_API}/sites/{SITE}/versions/v1?update_mask=status", {"status": "FINALIZED"}),
        ("POST", f"{base}/releases?versionName=sites/{SITE}/versions/v1", {}),
    ]
    assert f"released sites/{SITE}/releases/r1" in capsys.readouterr().out


def _answer(status: int, content_type: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(status_code=status, headers={"content-type": content_type}, text=text)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (_answer(200, "text/plain; charset=utf-8", "Sitemap: https://x/s.xml\n"), "shop site live"),
        (_answer(404, "application/json", '{"detail":"Not Found"}'), "reaches AgenticOrg, not a listed network shop"),
        (_answer(404, "text/html", "<h1>Page Not Found</h1>"), "not reaching AgenticOrg yet (HTTP 404, text/html)"),
    ],
)
def test_check_host_reports_what_robots_txt_answers(answer: SimpleNamespace, expected: str) -> None:
    seen: list[tuple[str, Any]] = []

    def get(url: str, **kwargs: Any) -> SimpleNamespace:
        seen.append((url, kwargs))
        return answer

    line = release.check_host("tea-corner.agenticorg.ai", get=get)
    assert line.startswith("tea-corner.agenticorg.ai: ") and expected in line
    assert seen == [("https://tea-corner.agenticorg.ai/robots.txt", {"timeout": 15, "follow_redirects": False})]


def test_check_host_reports_a_host_that_does_not_answer() -> None:
    def get(_url: str, **_kwargs: Any) -> SimpleNamespace:
        raise TimeoutError

    assert release.check_host("down.example", get=get) == "down.example: no answer (TimeoutError)"


def test_release_then_checks_each_named_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release, "_session", lambda _project: _Session())
    checked: list[str] = []
    monkeypatch.setattr(release, "check_host", lambda host: checked.append(host) or f"{host}: ok")
    argv = ["--project", "example-project", "--site", SITE, "--check-host", "a.example", "--check-host", "b.example"]
    assert release.main(argv) == 0
    assert checked == ["a.example", "b.example"]
    assert "a.example: ok" in capsys.readouterr().out
    checked.clear()
    assert release.main([*argv, "--dry-run"]) == 0
    assert checked == []


@pytest.mark.parametrize(("step", "label"), [("create", "create version"), ("finalize", "finalize version")])
def test_a_refused_step_stops_with_its_name(monkeypatch: pytest.MonkeyPatch, step: str, label: str) -> None:
    session = _Session(fail_on=step)
    monkeypatch.setattr(release, "_session", lambda _project: session)
    with pytest.raises(SystemExit) as stopped:
        release.release(
            project="example-project", site=SITE, service="agenticorg-api", region="asia-southeast1", dry_run=False
        )
    assert str(stopped.value).startswith(f"{label} failed: HTTP 403")
    assert "release" not in [call[1].split("/")[-1] for call in session.calls]


def test_session_uses_application_default_credentials_and_bills_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.auth
    import google.auth.transport.requests as transport

    scopes_asked: list[Any] = []

    def fake_default(*, scopes: Any) -> tuple[Any, str]:
        scopes_asked.append(scopes)
        return SimpleNamespace(token="synthetic"), "example-project"

    class FakeAuthorizedSession:
        def __init__(self, credentials: Any) -> None:
            self.credentials = credentials
            self.headers: dict[str, str] = {}

    monkeypatch.setattr(google.auth, "default", fake_default)
    monkeypatch.setattr(transport, "AuthorizedSession", FakeAuthorizedSession)
    session = release._session("example-project")
    assert scopes_asked == [[release.SCOPE]]
    assert session.headers == {"x-goog-user-project": "example-project"}
