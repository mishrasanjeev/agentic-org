"""Unit tests for the knowledge_stats helper parsing logic.

Validates the shape-tolerant parsing in ``_ragflow_dataset_stats`` so
the stats card never regresses back to ``total_chunks=0`` when RAGFlow
ships either field-name convention.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.v1.knowledge import _ragflow_dataset_stats


def _mock_http_response(status_code: int, payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload)
    return resp


@pytest.mark.asyncio
async def test_parses_chunk_count_field() -> None:
    """Newer RAGFlow builds use `chunk_count`; `token_num` is token
    count, which we convert to a bytes estimate at 4 bytes/token."""
    payload = {
        "data": [{
            "name": "tenant_abc",
            "chunk_count": 42,
            "token_num": 1_000_000,
        }],
    }
    with patch("api.v1.knowledge._RAGFLOW_URL", "http://ragflow"), \
         patch("api.v1.knowledge._httpx") as mock_httpx:
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client_cm
        client_cm.__aexit__.return_value = False
        client_cm.get = AsyncMock(return_value=_mock_http_response(200, payload))
        mock_httpx.AsyncClient.return_value = client_cm
        stats = await _ragflow_dataset_stats("abc")
    assert stats == {"chunk_count": 42, "index_size_bytes": 4_000_000}


@pytest.mark.asyncio
async def test_parses_chunk_num_field() -> None:
    """Older RAGFlow builds use `chunk_num`."""
    payload = {
        "data": [{
            "name": "tenant_abc",
            "chunk_num": 7,
            "index_size_bytes": 2048,
        }],
    }
    with patch("api.v1.knowledge._RAGFLOW_URL", "http://ragflow"), \
         patch("api.v1.knowledge._httpx") as mock_httpx:
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client_cm
        client_cm.__aexit__.return_value = False
        client_cm.get = AsyncMock(return_value=_mock_http_response(200, payload))
        mock_httpx.AsyncClient.return_value = client_cm
        stats = await _ragflow_dataset_stats("abc")
    assert stats == {"chunk_count": 7, "index_size_bytes": 2048}


@pytest.mark.asyncio
async def test_token_num_converted_to_bytes_when_no_explicit_bytes_field() -> None:
    """If only token_num is exposed, convert at 4 bytes/token."""
    payload = {
        "data": [{
            "name": "tenant_abc",
            "chunk_count": 10,
            "token_num": 500,
        }],
    }
    with patch("api.v1.knowledge._RAGFLOW_URL", "http://ragflow"), \
         patch("api.v1.knowledge._httpx") as mock_httpx:
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client_cm
        client_cm.__aexit__.return_value = False
        client_cm.get = AsyncMock(return_value=_mock_http_response(200, payload))
        mock_httpx.AsyncClient.return_value = client_cm
        stats = await _ragflow_dataset_stats("abc")
    assert stats == {"chunk_count": 10, "index_size_bytes": 2000}  # 500*4


@pytest.mark.asyncio
async def test_returns_none_on_http_error() -> None:
    with patch("api.v1.knowledge._RAGFLOW_URL", "http://ragflow"), \
         patch("api.v1.knowledge._httpx") as mock_httpx:
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client_cm
        client_cm.__aexit__.return_value = False
        client_cm.get = AsyncMock(return_value=_mock_http_response(500, {}))
        mock_httpx.AsyncClient.return_value = client_cm
        stats = await _ragflow_dataset_stats("abc")
    assert stats is None


@pytest.mark.asyncio
async def test_returns_none_on_network_exception() -> None:
    with patch("api.v1.knowledge._RAGFLOW_URL", "http://ragflow"), \
         patch("api.v1.knowledge._httpx") as mock_httpx:
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client_cm
        client_cm.__aexit__.return_value = False
        client_cm.get = AsyncMock(side_effect=TimeoutError("nope"))
        mock_httpx.AsyncClient.return_value = client_cm
        stats = await _ragflow_dataset_stats("abc")
    assert stats is None


@pytest.mark.asyncio
async def test_returns_none_when_dataset_missing_from_response() -> None:
    """RAGFlow returns 200 but with an empty data list."""
    with patch("api.v1.knowledge._RAGFLOW_URL", "http://ragflow"), \
         patch("api.v1.knowledge._httpx") as mock_httpx:
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client_cm
        client_cm.__aexit__.return_value = False
        client_cm.get = AsyncMock(return_value=_mock_http_response(200, {"data": []}))
        mock_httpx.AsyncClient.return_value = client_cm
        stats = await _ragflow_dataset_stats("abc")
    assert stats is None
