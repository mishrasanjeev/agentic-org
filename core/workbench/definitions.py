# SPDX-License-Identifier: Apache-2.0
"""The workbenches and their tabs.

A tab names the page it opens, the counter behind its badge, the roles that
may see it and whether it is sensitive (a sensitive tab is hidden from every
role not named, whatever workbench the caller holds). A tab names the roles
its page admits (the UI route guard), never more: the shell lists nothing a
person could not open. The catalogue is
fixed; which workbenches a person holds is decided by role and assignment
(``core/workbench/access.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ADMIN = "admin"
ALL_ROLES: tuple[str, ...] = ("admin", "cfo", "chro", "cmo", "coo", "auditor", "domain_lead", "analyst", "developer")
# The roles with something to search: the approval pages admit the first seven, the companies page the auditor too.
SEARCH_ROLES: tuple[str, ...] = ("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer", "auditor")


@dataclass(frozen=True)
class Tab:
    key: str
    title: str
    path: str
    source: str  # the page the shell renders and the counter behind the badge
    roles: tuple[str, ...] = ()  # empty: every holder of the workbench
    sensitive: bool = False
    actions: tuple[str, ...] = ()  # what the tab lets a person do; the backend authorises each anyway

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "path": self.path,
            "source": self.source,
            "sensitive": self.sensitive,
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class Workbench:
    name: str
    title: str
    description: str
    tabs: tuple[Tab, ...] = field(default_factory=tuple)
    default_roles: tuple[str, ...] = ()  # platform roles that hold it without an assignment

    def to_dict(self, tabs: list[Tab] | None = None) -> dict[str, Any]:
        shown = tabs if tabs is not None else list(self.tabs)
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tabs": [t.to_dict() for t in shown],
        }


CATALOGUE: tuple[Workbench, ...] = (
    Workbench(
        "review_officer",
        "Review officer",
        "Items that wait for a decision: approvals, documents in review, content drafts and governed cases.",
        (
            Tab(
                "queue",
                "Review queue",
                "/dashboard/workbench/review_officer/queue",
                "queue",
                actions=("decide", "edit"),
            ),
            Tab(
                "approvals",
                "Approvals",
                "/dashboard/approvals",
                "approvals",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("decide", "edit"),
            ),
            Tab(
                "documents",
                "Documents",
                "/dashboard/documents",
                "documents",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("correct", "decide"),
            ),
            Tab(
                "drafts", "Content drafts", "/dashboard/workbench/review_officer/drafts", "drafts", actions=("decide",)
            ),
            Tab(
                "cases",
                "Governed cases",
                "/dashboard/approvals/cases",
                "cases",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("review",),
            ),
            Tab(
                "search",
                "Search",
                "/dashboard/workbench/review_officer/search",
                "search",
                roles=SEARCH_ROLES,
                actions=("search",),
            ),
        ),
        default_roles=("admin", "cfo", "coo", "domain_lead"),
    ),
    Workbench(
        "relationship_manager",
        "Relationship manager",
        "Customers and their conversations, the knowledge base, and the agents that serve them.",
        (
            Tab(
                "conversations",
                "Conversations",
                "/dashboard/conversations",
                "conversations",
                roles=("admin",),
                actions=("watch",),
            ),
            Tab(
                "knowledge",
                "Knowledge base",
                "/dashboard/knowledge",
                "knowledge",
                roles=("admin", "cfo", "chro", "cmo", "coo"),
            ),
            Tab(
                "agents",
                "Agents",
                "/dashboard/agents",
                "agents",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
            ),
            Tab(
                "cases",
                "Governed cases",
                "/dashboard/approvals/cases",
                "cases",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("review",),
            ),
        ),
        default_roles=("admin", "cmo", "domain_lead"),
    ),
    Workbench(
        "investigator",
        "Investigator",
        "Documents and their analysis, governed cases, the audit trail and run timelines.",
        (
            Tab(
                "documents",
                "Documents",
                "/dashboard/documents",
                "documents",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("correct",),
            ),
            Tab(
                "cases",
                "Governed cases",
                "/dashboard/approvals/cases",
                "cases",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("review",),
            ),
            Tab(
                "audit",
                "Audit trail",
                "/dashboard/audit",
                "audit",
                roles=("admin", "cfo", "chro", "cmo", "coo", "auditor"),
                sensitive=True,
            ),
            Tab(
                "observability",
                "Run timelines",
                "/dashboard/observability",
                "observability",
                roles=("admin",),
                sensitive=True,
            ),
            Tab(
                "search",
                "Search",
                "/dashboard/workbench/investigator/search",
                "search",
                roles=SEARCH_ROLES,
                actions=("search",),
            ),
            Tab(
                "transactions",
                "Transactions",
                "/dashboard/transactions",
                "transactions",
                roles=("admin", "coo", "auditor", "cfo"),
                sensitive=True,
            ),
        ),
        default_roles=("admin", "auditor", "domain_lead"),
    ),
    Workbench(
        "supervisor",
        "Supervisor",
        "Live conversations with takeover, the approval queue, guardrail outcomes and costs.",
        (
            Tab(
                "live",
                "Live conversations",
                "/dashboard/conversations",
                "conversations",
                roles=("admin",),
                sensitive=True,
                actions=("takeover", "reply"),
            ),
            Tab(
                "queue",
                "Review queue",
                "/dashboard/workbench/supervisor/queue",
                "queue",
                roles=("admin", "coo"),
                actions=("decide", "edit"),
            ),
            Tab(
                "approvals",
                "Approvals",
                "/dashboard/approvals",
                "approvals",
                roles=("admin", "cfo", "chro", "cmo", "coo", "domain_lead", "developer"),
                actions=("decide",),
            ),
            Tab("calls", "Calls", "/dashboard/calls", "calls", roles=("admin", "coo"), sensitive=True),
            Tab("guardrails", "Guardrails", "/dashboard/settings/guardrails", "guardrails", roles=("admin",)),
            Tab(
                "console",
                "Business console",
                "/dashboard/workbench/supervisor/console",
                "console",
                roles=("admin",),
                actions=("configure",),
            ),
            Tab("costs", "Costs", "/dashboard/costs", "costs", roles=("admin", "cfo"), sensitive=True),
        ),
        default_roles=("admin", "coo"),
    ),
)

WORKBENCHES: dict[str, Workbench] = {item.name: item for item in CATALOGUE}
NAMES: tuple[str, ...] = tuple(WORKBENCHES)


def catalogue() -> list[dict[str, Any]]:
    return [
        {
            **item.to_dict(),
            "default_roles": list(item.default_roles),
            "tabs": [{**t.to_dict(), "roles": list(t.roles)} for t in item.tabs],
        }
        for item in CATALOGUE
    ]
