"""Scope parsing and enforcement per PRD naming convention."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedScope:
    """Parsed scope components."""
    category: str        # tool | agentflow
    connector: str       # oracle_fusion, etc.
    permission: str      # read | write | admin
    resource: str        # purchase_order, journal_entry, etc.
    cap: Optional[int] = None  # Capped amount for write scopes

# Pattern: tool:{connector}:{perm}:{resource}[:capped:{N}]
SCOPE_PATTERN = re.compile(
    r"^(tool|agentflow):([\w]+):([\w]+)(?::([\w]+))?(?::capped:(\d+))?$"
)


def parse_scope(scope_str: str) -> ParsedScope | None:
    """Parse a scope string into components."""
    m = SCOPE_PATTERN.match(scope_str)
    if not m:
        return None
    return ParsedScope(
        category=m.group(1),
        connector=m.group(2),
        permission=m.group(3),
        resource=m.group(4) or "",
        cap=int(m.group(5)) if m.group(5) else None,
    )


def check_scope(
    granted_scopes: list[str],
    required_connector: str,
    required_permission: str,
    required_resource: str,
    amount: float | None = None,
) -> tuple[bool, str]:
    """Check if granted scopes allow the requested operation.

    Returns (allowed: bool, reason: str).
    """
    for scope_str in granted_scopes:
        parsed = parse_scope(scope_str)
        if not parsed:
            continue

        # Admin scope covers everything for that connector
        if parsed.connector == required_connector and parsed.permission == "admin":
            return True, "admin_scope"

        # Exact match
        if (
            parsed.connector == required_connector
            and parsed.permission == required_permission
            and parsed.resource == required_resource
        ):
            # Check cap if applicable
            if parsed.cap is not None and amount is not None:
                if amount > parsed.cap:
                    return False, f"cap_exceeded:{parsed.cap}"
            return True, "scope_match"

    return False, "no_matching_scope"


def validate_clone_scopes(parent_scopes: list[str], child_scopes: list[str]) -> list[str]:
    """Ensure child scopes do not exceed parent scopes (scope ceiling).

    Returns list of violations (empty = valid).
    """
    violations = []
    for child_scope in child_scopes:
        child_parsed = parse_scope(child_scope)
        if not child_parsed:
            continue

        # Find matching parent scope
        found_parent = False
        for parent_scope in parent_scopes:
            parent_parsed = parse_scope(parent_scope)
            if not parent_parsed:
                continue

            if (
                parent_parsed.connector == child_parsed.connector
                and parent_parsed.permission == child_parsed.permission
                and parent_parsed.resource == child_parsed.resource
            ):
                # Check cap elevation
                if child_parsed.cap and parent_parsed.cap:
                    if child_parsed.cap > parent_parsed.cap:
                        violations.append(
                            f"Cap elevation: {child_scope} exceeds parent {parent_scope}"
                        )
                found_parent = True
                break

            # Admin covers all
            if parent_parsed.connector == child_parsed.connector and parent_parsed.permission == "admin":
                found_parent = True
                break

        if not found_parent:
            violations.append(f"Scope not in parent: {child_scope}")

    return violations
