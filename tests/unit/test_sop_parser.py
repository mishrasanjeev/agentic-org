"""Tests for SOP parser and API endpoints."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDocumentExtraction:
    def test_extract_from_markdown(self):
        from core.langgraph.sop_parser import extract_text_from_document

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
            f.write("# Invoice Processing SOP\n\nStep 1: Extract invoice data")
            f.flush()
            text = extract_text_from_document(f.name)
        os.unlink(f.name)
        assert "Invoice Processing" in text
        assert "Step 1" in text

    def test_extract_from_text(self):
        from core.langgraph.sop_parser import extract_text_from_document

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("Standard Operating Procedure for Payroll")
            f.flush()
            text = extract_text_from_document(f.name)
        os.unlink(f.name)
        assert "Payroll" in text

    def test_extract_from_pdf(self):
        from core.langgraph.sop_parser import extract_text_from_document

        # Create a minimal PDF for testing
        try:
            from pypdf import PdfWriter

            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                writer.write(f)
                f.flush()
                # pypdf blank page has no text — just verify no crash
                text = extract_text_from_document(f.name)
            os.unlink(f.name)
            assert isinstance(text, str)
        except ImportError:
            pytest.skip("pypdf not installed")


class TestSOPParserPrompt:
    def test_parser_system_prompt_has_placeholders(self):
        from core.langgraph.sop_parser import SOP_PARSER_SYSTEM_PROMPT

        assert "{available_tools}" in SOP_PARSER_SYSTEM_PROMPT
        assert "agent_name" in SOP_PARSER_SYSTEM_PROMPT
        assert "required_tools" in SOP_PARSER_SYSTEM_PROMPT
        assert "hitl_conditions" in SOP_PARSER_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_parse_sop_returns_draft(self):
        from core.langgraph.sop_parser import parse_sop_document

        mock_response = MagicMock()
        import json

        mock_response.content = json.dumps({
            "agent_name": "Test Agent", "agent_type": "test_agent",
            "domain": "finance", "description": "Test", "steps": [],
            "required_tools": ["fetch_bank_statement"],
            "hitl_conditions": [], "confidence_floor": 0.9,
            "escalation_chain": [], "suggested_prompt": "test prompt",
        })

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("core.langgraph.sop_parser.create_chat_model", return_value=mock_llm):
            result = await parse_sop_document("Process invoices daily", domain_hint="finance")

        assert result["_parse_status"] == "draft"
        assert result["agent_name"] == "Test Agent"
        assert result["domain"] == "finance"
        assert "fetch_bank_statement" in result["required_tools"]

    @pytest.mark.asyncio
    async def test_parse_sop_handles_invalid_json(self):
        from core.langgraph.sop_parser import parse_sop_document

        mock_response = MagicMock()
        mock_response.content = "This is not JSON at all"

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("core.langgraph.sop_parser.create_chat_model", return_value=mock_llm):
            result = await parse_sop_document("Some SOP text")

        assert result["_parse_status"] == "draft"
        assert "parse_error" in result
        assert result["agent_type"] == "custom_agent"

    @pytest.mark.asyncio
    async def test_parse_sop_flags_unknown_tools(self):
        from core.langgraph.sop_parser import parse_sop_document

        mock_response = MagicMock()
        import json

        mock_response.content = json.dumps({
            "agent_name": "Test", "agent_type": "test",
            "domain": "ops", "description": "Test", "steps": [],
            "required_tools": ["nonexistent_tool", "fetch_bank_statement"],
            "hitl_conditions": [], "confidence_floor": 0.88,
            "escalation_chain": [], "suggested_prompt": "test",
        })

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("core.langgraph.sop_parser.create_chat_model", return_value=mock_llm):
            result = await parse_sop_document("Some SOP")

        assert "nonexistent_tool" in result.get("_unknown_tools", [])
        assert "fetch_bank_statement" not in result.get("_unknown_tools", [])


class TestSOPEndpoints:
    @pytest.mark.asyncio
    async def test_parse_text_empty_rejected(self):
        from fastapi import HTTPException

        from api.v1.sop import SOPParseRequest, parse_text_sop

        with pytest.raises(HTTPException) as exc:
            await parse_text_sop(
                SOPParseRequest(text=""),
                tenant_id="00000000-0000-0000-0000-000000000001",
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_parse_text_too_long_rejected(self):
        from fastapi import HTTPException

        from api.v1.sop import SOPParseRequest, parse_text_sop

        with pytest.raises(HTTPException) as exc:
            await parse_text_sop(
                SOPParseRequest(text="x" * 60000),
                tenant_id="00000000-0000-0000-0000-000000000001",
            )
        assert exc.value.status_code == 400
