"""Foundation #7 PR-D — fake connectors hermetic seam regressions.

Pinned behaviors:

- ``is_active()`` reflects the env var.
- ``register()`` accepts json/text/headers and most-recent wins.
- Empty ``url_contains`` is rejected.
- The MockTransport routes by case-insensitive substring +
  method (``*`` = any).
- Unmatched requests return 404 with a clear "no_rule_matched"
  body so tests fail loudly on missing mocks (NOT silently
  succeed with a generic 200 — Foundation #8 false-green
  prevention).
- ``request_log()`` records every call attempt including
  unmatched ones.
- ``reset()`` clears rules + log.
- The conftest enables the seam by default; autouse fixture
  resets between tests.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from core.test_doubles import fake_connectors


def test_is_active_reflects_env_var(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_CONNECTORS", raising=False)
    assert fake_connectors.is_active() is False
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "1")
    assert fake_connectors.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "true")
    assert fake_connectors.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "no")
    assert fake_connectors.is_active() is False


def test_register_empty_url_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        fake_connectors.register(method="GET", url_contains="   ")


def test_register_json_response_round_trips() -> None:
    fake_connectors.register(
        method="GET",
        url_contains="/api/customers",
        status=200,
        json={"customers": [{"id": 1, "name": "Acme"}]},
    )
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(
            base_url="https://example.com", transport=transport
        ) as client:
            return await client.get("/api/customers")

    resp = asyncio.run(_go())
    assert resp.status_code == 200
    assert resp.json() == {"customers": [{"id": 1, "name": "Acme"}]}
    assert fake_connectors.count() == 1


def test_unmatched_request_returns_404_with_clear_marker() -> None:
    """Foundation #8 false-green prevention — an unmocked URL
    must NOT silently succeed. The 404 + no_rule_matched body
    forces the test author to register a stub explicitly."""
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(
            base_url="https://example.com", transport=transport
        ) as client:
            return await client.get("/api/never-mocked")

    resp = asyncio.run(_go())
    assert resp.status_code == 404
    body = resp.json()
    assert body["fake_connectors_error"] == "no_rule_matched"
    assert "/api/never-mocked" in body["url"]
    assert "register" in body["hint"]


def test_method_wildcard_matches_any_verb() -> None:
    fake_connectors.register(
        method="*", url_contains="api.gmail.com", status=200, json={"ok": True}
    )
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            r1 = await client.get("https://api.gmail.com/messages")
            r2 = await client.post("https://api.gmail.com/messages", json={})
            r3 = await client.delete("https://api.gmail.com/messages/1")
            return r1, r2, r3

    r1, r2, r3 = asyncio.run(_go())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200


def test_method_specific_rule_does_not_match_other_verbs() -> None:
    fake_connectors.register(
        method="GET", url_contains="/api/x", status=200, json={"got": True}
    )
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(
            base_url="https://example.com", transport=transport
        ) as client:
            return await client.post("/api/x", json={})

    resp = asyncio.run(_go())
    assert resp.status_code == 404


def test_most_recently_registered_rule_wins() -> None:
    fake_connectors.register(
        method="GET", url_contains="/api/x", status=200, json={"v": 1}
    )
    fake_connectors.register(
        method="GET", url_contains="/api/x", status=200, json={"v": 2}
    )
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(
            base_url="https://example.com", transport=transport
        ) as client:
            return await client.get("/api/x")

    resp = asyncio.run(_go())
    assert resp.json() == {"v": 2}


def test_text_response_overrides_json() -> None:
    fake_connectors.register(
        method="GET",
        url_contains="/csv",
        status=200,
        json={"ignored": True},
        text="a,b,c\n1,2,3\n",
        headers={"Content-Type": "text/csv"},
    )
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get("https://example.com/csv")

    resp = asyncio.run(_go())
    assert resp.text.startswith("a,b,c")
    assert resp.headers["content-type"].startswith("text/csv")


def test_request_log_records_unmatched_too() -> None:
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(
            base_url="https://example.com", transport=transport
        ) as client:
            await client.get("/api/missing")

    asyncio.run(_go())
    log = fake_connectors.request_log()
    assert len(log) == 1
    assert log[0].matched_rule_index is None
    assert log[0].method == "GET"
    assert "/api/missing" in log[0].url


def test_reset_clears_both_rules_and_log() -> None:
    fake_connectors.register(method="GET", url_contains="/x", status=200)
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.com/x")

    asyncio.run(_go())
    assert fake_connectors.count() == 1
    fake_connectors.reset()
    assert fake_connectors.count() == 0
    # Re-running with no rules → 404 (not the prior 200).
    asyncio.run(_go())
    assert fake_connectors.request_log()[0].matched_rule_index is None


def test_conftest_default_makes_fake_connectors_active() -> None:
    assert os.environ.get("AGENTICORG_TEST_FAKE_CONNECTORS") == "1"
    assert fake_connectors.is_active() is True


def test_autouse_fixture_resets_part_1() -> None:
    fake_connectors.register(method="GET", url_contains="/bleed", status=200)
    transport = fake_connectors.build_transport()

    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.com/bleed")

    asyncio.run(_go())
    assert fake_connectors.count() == 1


def test_autouse_fixture_resets_part_2() -> None:
    """Bleed-check second half — must see clean state."""
    assert fake_connectors.count() == 0
