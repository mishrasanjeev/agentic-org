"""Regression tests for the TEI embeddings dispatch path.

After PR #324 landed and the in-process FlagEmbedding flag flip OOM-
killed agenticorg-api at 2 GiB (autopsy:
``bge_m3_rollout_26apr_a_failed.md``), the request path now calls a
dedicated TEI Cloud Run service when ``AGENTICORG_TEI_URL`` is set.
The api container stays at 2 GiB; bge-m3 weights live in the
``agenticorg-embeddings`` service.

Pin the contracts:

  1. ``embed_bge_m3`` routes through TEI when ``AGENTICORG_TEI_URL``
     is set (no FlagEmbedding import).
  2. ``embed_bge_m3`` falls back to FlagEmbedding when the env var
     is unset (used by the backfill Cloud Run job).
  3. The HTTP payload uses TEI's ``inputs`` + ``normalize`` keys.
  4. Empty input short-circuits without an HTTP call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_empty_input_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty list returns ``[]`` without touching httpx."""
    monkeypatch.setenv("AGENTICORG_TEI_URL", "http://example/tei")
    from core.embeddings import embed_bge_m3

    with patch("httpx.Client") as client:
        out = embed_bge_m3([])
    assert out == []
    client.assert_not_called()


def test_routes_via_tei_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ``AGENTICORG_TEI_URL`` switches to the HTTP path."""
    monkeypatch.setenv("AGENTICORG_TEI_URL", "http://example/tei")
    from core import embeddings as emb

    expected = [[0.5] * 1024, [0.25] * 1024]

    mock_response = MagicMock()
    mock_response.json.return_value = expected
    mock_response.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = mock_response

    with (
        patch("httpx.Client", return_value=mock_client),
        # Mocking out auth so the test runs without GCP creds.
        patch("google.oauth2.id_token.fetch_id_token", return_value="fake-id-token"),
    ):
        out = emb.embed_bge_m3(["hello", "world"])
    assert out == expected
    # Verify the HTTP shape — keys TEI expects.
    post_call = mock_client.__enter__.return_value.post
    args, kwargs = post_call.call_args
    assert args[0].endswith("/embed")
    assert kwargs["json"]["inputs"] == ["hello", "world"]
    assert kwargs["json"]["normalize"] is True
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")


def test_falls_back_to_flagembedding_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``AGENTICORG_TEI_URL`` — must use the in-process loader path."""
    monkeypatch.delenv("AGENTICORG_TEI_URL", raising=False)
    from core import embeddings as emb

    fake_model = MagicMock()
    fake_model.encode.return_value = {"dense_vecs": [[0.1] * 1024]}

    with patch.object(emb, "_get_bge_m3_model", return_value=fake_model):
        out = emb.embed_bge_m3(["hello"])
    assert len(out) == 1
    assert len(out[0]) == 1024
    fake_model.encode.assert_called_once()


def test_anonymous_fallback_when_id_token_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ID-token fetch failure must not crash — TEI may run with
    --allow-unauthenticated in dev / test envs.
    """
    monkeypatch.setenv("AGENTICORG_TEI_URL", "http://example/tei")
    from core import embeddings as emb

    mock_response = MagicMock()
    mock_response.json.return_value = [[0.0] * 1024]
    mock_response.raise_for_status.return_value = None
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = mock_response

    with (
        patch("httpx.Client", return_value=mock_client),
        patch(
            "google.oauth2.id_token.fetch_id_token",
            side_effect=RuntimeError("no creds"),
        ),
    ):
        out = emb.embed_bge_m3(["hello"])
    assert len(out[0]) == 1024
    headers = mock_client.__enter__.return_value.post.call_args.kwargs["headers"]
    assert "Authorization" not in headers
