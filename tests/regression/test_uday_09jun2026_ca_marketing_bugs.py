"""Regression pins for Uday CA/Marketing bug sheet dated 2026-06-09."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _FakeResponse:
    def __init__(self, body: dict[str, object]):
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._body


class _RecordingAsyncClient:
    def __init__(self, body: dict[str, object]):
        self.body = body
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> _RecordingAsyncClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.posts.append((url, kwargs))
        return _FakeResponse(self.body)


def _client_factory(client: _RecordingAsyncClient):
    def _factory(*_args: object, **_kwargs: object) -> _RecordingAsyncClient:
        return client

    return _factory


@pytest.mark.asyncio
async def test_gstn_uses_current_adaequare_gsp_auth_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import connectors.finance.gstn as gstn_module
    from connectors.finance.gstn import GstnConnector

    client = _RecordingAsyncClient({"access_token": "jwt-token", "token_type": "bearer"})
    monkeypatch.setattr(gstn_module.httpx, "AsyncClient", _client_factory(client))

    connector = GstnConnector(
        {
            "client_id": "gsp-app-id",
            "client_secret": "gsp-app-secret",
            "gstin": "29AADCB2230M1ZT",
        }
    )
    await connector._authenticate()

    url, kwargs = client.posts[0]
    assert url == f"{gstn_module.GSTN_API_BASE_URL}/authenticate?grant_type=token"
    assert kwargs["headers"]["gspappid"] == "gsp-app-id"
    assert kwargs["headers"]["gspappsecret"] == "gsp-app-secret"
    assert "clientid" not in kwargs["headers"]
    assert "client-secret" not in kwargs["headers"]
    assert connector._auth_headers["Authorization"] == "Bearer jwt-token"
    assert "auth-token" not in connector._auth_headers


@pytest.mark.asyncio
async def test_gstn_legacy_auth_token_response_still_becomes_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import connectors.finance.gstn as gstn_module
    from connectors.finance.gstn import GstnConnector

    client = _RecordingAsyncClient({"auth-token": "legacy-token"})
    monkeypatch.setattr(gstn_module.httpx, "AsyncClient", _client_factory(client))

    connector = GstnConnector({"client_id": "gsp-app-id", "client_secret": "gsp-app-secret"})
    await connector._authenticate()

    assert connector._auth_headers["Authorization"] == "Bearer legacy-token"
    assert connector._auth_headers["auth-token"] == "legacy-token"


def test_chat_output_formatter_unwraps_raw_output_and_hides_internal_fields() -> None:
    from api.v1.chat import _format_agent_output

    output = _format_agent_output(
        {
            "raw_output": {
                "text": "Invoice Number: INV-000001\nAmount: INR 5,000",
                "metadata": {"trace_id": "trace-1"},
            },
            "status": "completed",
            "signature": "sig",
            "extras": {"debug": True},
        }
    )

    assert output == "Invoice Number: INV-000001\nAmount: INR 5,000"
    assert "raw_output" not in output
    assert "metadata" not in output
    assert "signature" not in output
    assert "status" not in output.lower()


def test_chat_output_formatter_parses_nested_json_answer_envelope() -> None:
    from api.v1.chat import _format_agent_output

    output = _format_agent_output(
        {
            "answer": (
                '{"raw_output":{"text":"Clean assistant answer"},'
                '"status":"completed","metadata":{"debug":true}}'
            )
        }
    )

    assert output == "Clean assistant answer"
    assert "metadata" not in output
    assert "status" not in output


def test_connector_auth_type_custom_is_shared_create_and_edit_option() -> None:
    constants = (ROOT / "ui" / "src" / "lib" / "connector-constants.ts").read_text(
        encoding="utf-8"
    )
    create_page = (ROOT / "ui" / "src" / "pages" / "ConnectorCreate.tsx").read_text(
        encoding="utf-8"
    )
    detail_page = (ROOT / "ui" / "src" / "pages" / "ConnectorDetail.tsx").read_text(
        encoding="utf-8"
    )

    assert '"custom"' in constants
    assert "AUTH_TYPES.map" in create_page
    assert "AUTH_TYPES.map" in detail_page
    assert "custom: []" in create_page
