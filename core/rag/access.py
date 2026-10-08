# SPDX-License-Identifier: Apache-2.0
"""Document-level access control for knowledge retrieval.

A knowledge document may belong to a domain (``finance``, ``hr``, ``ops``
and so on, the same domains that scope agents and users) or to the tenant as
a whole (no domain). A caller whose session is limited to some domains
(``agenticorg:domains``) is shown chunks of documents in those domains and
of shared documents, and nothing else: not in search results, not in
citations, not in an excerpt, not in the document list. A caller with no
limit (an administrator, or a machine credential bounded by scopes) sees the
tenant's documents as before.

The rule is one SQL clause the search, excerpt and list paths share, with
bound parameters; a document that is withheld is absent from the result,
never marked.
"""

from __future__ import annotations

from typing import Any

MAX_DOMAINS = 50


def normalise_domains(domains: Any) -> list[str] | None:
    """The caller's domains as a bounded list, or None when the caller is unrestricted."""
    if domains is None:
        return None
    cleaned = [str(d).strip().lower() for d in domains if isinstance(d, str) and str(d).strip()]
    return cleaned[:MAX_DOMAINS]


def sql_clause(domains: list[str] | None, *, alias: str = "d", prefix: str = "acl") -> tuple[str, dict[str, Any]]:
    """``AND (d.domain IS NULL OR d.domain IN (...))`` for a limited caller; empty for an unrestricted one.

    A limited caller with no domain at all sees shared documents only.
    """
    if domains is None:
        return "", {}
    if not domains:
        return f" AND {alias}.domain IS NULL", {}
    names = []
    params: dict[str, Any] = {}
    for index, domain in enumerate(domains):
        key = f"{prefix}_{index}"
        params[key] = domain
        names.append(f":{key}")
    return f" AND ({alias}.domain IS NULL OR {alias}.domain IN ({', '.join(names)}))", params


def may_see(document_domain: str | None, domains: list[str] | None) -> bool:
    """The same rule in Python, for rows that were read before the clause existed."""
    if domains is None or document_domain is None:
        return True
    return document_domain.lower() in domains


def metadata_clause(domains: list[str] | None, *, prefix: str = "acl") -> tuple[str, dict[str, Any]]:
    """The same rule for the uploads table, whose domain lives in ``metadata->>'domain'``."""
    if domains is None:
        return "", {}
    if not domains:
        return " AND metadata->>'domain' IS NULL", {}
    names = []
    params: dict[str, Any] = {}
    for index, domain in enumerate(domains):
        key = f"{prefix}_{index}"
        params[key] = domain
        names.append(f":{key}")
    return f" AND (metadata->>'domain' IS NULL OR metadata->>'domain' IN ({', '.join(names)}))", params
