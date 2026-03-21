"""Append-only audit log writer."""

from core.tool_gateway.audit_logger import AuditLogger

# Re-export the main audit logger
__all__ = ["AuditLogger"]
