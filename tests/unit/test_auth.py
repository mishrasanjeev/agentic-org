"""Auth tests — JWT, scopes, tenant isolation."""
import pytest
from auth.scopes import parse_scope

class TestScopeParsing:
    def test_read_scope(self):
        s = parse_scope("tool:oracle_fusion:read:purchase_order")
        assert s and s.connector == "oracle_fusion" and s.permission == "read"

    def test_capped_scope(self):
        s = parse_scope("tool:banking_api:write:queue_payment:capped:500000")
        assert s and s.cap == 500000

    def test_agentflow_scope(self):
        s = parse_scope("agentflow:agents:write")
        assert s and s.category == "agentflow"
