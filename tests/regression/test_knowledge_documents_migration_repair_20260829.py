"""Pins the fresh/legacy knowledge index migration repair."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_knowledge_documents_repair_is_idempotent_tenant_safe_and_current_shape() -> None:
    source = (ROOT / "migrations" / "versions" / "v6_z13_knowledge_documents_repair.py").read_text(encoding="utf-8")

    assert 'revision = "v6z13_knowledge_docs"' in source
    assert 'down_revision = "v6z12_voice_runtime"' in source
    assert "CREATE TABLE IF NOT EXISTS knowledge_documents" in source
    for column in (
        "title VARCHAR(500)",
        "content TEXT",
        "embedding vector(384)",
        "embedding_bge_m3 vector(1024)",
        "source_object_id VARCHAR(128)",
        "source_object_type VARCHAR(32)",
    ):
        assert column in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "knowledge_documents_tenant_isolation" in source
    assert "ix_knowledge_documents_tenant_source_object" in source
    assert "WITH CHECK" in source
    assert "DROP TABLE" not in source


def test_native_knowledge_chunks_follow_document_deletion_lifecycle() -> None:
    source = (ROOT / "api" / "v1" / "knowledge.py").read_text(encoding="utf-8")

    assert "UPDATE knowledge_documents SET status = 'deleted'" in source
    assert source.count("status = 'ready'") >= 4
    assert "if result.rowcount == 0 and not ragflow_deleted" in source
