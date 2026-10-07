# SPDX-License-Identifier: Apache-2.0
"""Workbenches: the catalogue, who holds which, the tabs a role sees, the counts, assignments and the routes."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.workbench import access, assignments, definitions

TENANT = uuid.uuid4()
UI_ROUTES = set(re.findall(r'path="(/dashboard/[^"]*)"', Path("ui/src/App.tsx").read_text(encoding="utf-8")))


class TestDefinitions:
    def test_every_workbench_has_tabs_with_distinct_keys_and_known_roles(self):
        assert set(definitions.NAMES) == {"review_officer", "relationship_manager", "investigator", "supervisor"}
        for bench in definitions.CATALOGUE:
            keys = [tab.key for tab in bench.tabs]
            assert keys and len(keys) == len(set(keys))
            assert bench.default_roles and set(bench.default_roles) <= set(definitions.ALL_ROLES)
            for tab in bench.tabs:
                assert set(tab.roles) <= set(definitions.ALL_ROLES)
                assert tab.path.startswith("/dashboard/")
                assert tab.path in UI_ROUTES or tab.path.startswith("/dashboard/workbench/"), tab.path
                if tab.sensitive:
                    assert tab.roles, "a sensitive tab names the roles that may see it"

    def test_the_catalogue_lists_roles_per_tab_and_default_roles(self):
        found = definitions.catalogue()
        assert [item["name"] for item in found] == list(definitions.NAMES)
        review = next(item for item in found if item["name"] == "review_officer")
        assert "cfo" in review["default_roles"] and review["tabs"][0]["roles"] == []
        assert definitions.WORKBENCHES["supervisor"].to_dict()["tabs"][0]["sensitive"] is True


class TestAccess:
    def test_a_role_holds_its_default_workbenches_and_assigned_ones_and_admin_all(self):
        assert [b["name"] for b in access.workbenches_for("cfo")] == ["review_officer"]
        assert [b["name"] for b in access.workbenches_for("cfo", {"investigator"})] == [
            "review_officer",
            "investigator",
        ]
        assigned = next(b for b in access.workbenches_for("cfo", {"investigator"}) if b["name"] == "investigator")
        assert assigned["held_by"] == "assignment"
        assert [b["name"] for b in access.workbenches_for("admin")] == list(definitions.NAMES)
        assert access.workbenches_for("merchant") == []

    def test_sensitive_and_role_bound_tabs_are_hidden_from_other_roles(self):
        investigator = definitions.WORKBENCHES["investigator"]
        assert [t.key for t in access.tabs_for(investigator, "analyst")] == ["documents", "cases"]
        assert [t.key for t in access.tabs_for(investigator, "auditor")] == [
            "documents",
            "cases",
            "audit",
            "observability",
        ]
        supervisor = next(b for b in access.workbenches_for("cfo", {"supervisor"}) if b["name"] == "supervisor")
        assert [t["key"] for t in supervisor["tabs"]] == ["approvals", "costs"]
        assert access.may_open("supervisor", "live", "coo") is True
        assert access.may_open("supervisor", "live", "cfo", {"supervisor"}) is False
        assert access.may_open("supervisor", "costs", "cfo") is False  # not held without an assignment
        assert access.may_open("nowhere", "x", "admin") is False

    def test_enabled_follows_the_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        assert access.enabled() is False
        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        assert access.enabled() is True

    @pytest.mark.asyncio
    async def test_counts_come_from_the_stores_and_a_missing_counter_is_none(self, monkeypatch):
        seen: list[str] = []

        async def scalar(statement):
            seen.append(str(statement.get_final_froms()[0].name))
            return len(seen)

        class Session:
            async def __aenter__(self):
                return SimpleNamespace(scalar=scalar)

            async def __aexit__(self, *args):
                return False

        import core.database

        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: Session())
        found = await access.counts(TENANT, {"approvals", "documents", "drafts", "cases", "conversations", "knowledge"})
        assert set(seen) == {"hitl_queue", "idp_documents", "content_drafts", "governed_cases", "conversation_sessions"}
        assert found["knowledge"] is None
        assert {k for k, v in found.items() if isinstance(v, int)} == {
            "approvals",
            "documents",
            "drafts",
            "cases",
            "conversations",
        }

    @pytest.mark.asyncio
    async def test_a_summary_holds_the_tabs_counts_and_total_and_refuses_an_unheld_workbench(self, monkeypatch):
        monkeypatch.setattr(
            access,
            "counts",
            AsyncMock(return_value={"queue": 7, "approvals": 2, "documents": 5, "drafts": None, "cases": None}),
        )
        found = await access.summary(TENANT, "review_officer", "cfo")
        assert (
            found is not None
            and found["waiting"] == 7
            and found["counts"] == {"queue": 7, "approvals": 2, "documents": 5, "drafts": None, "cases": None}
        )
        assert found["held_by"] == "role" and [t["key"] for t in found["tabs"]] == [
            "queue",
            "approvals",
            "documents",
            "drafts",
            "cases",
        ]
        assert await access.summary(TENANT, "supervisor", "cfo") is None
        assert await access.summary(TENANT, "unknown", "admin") is None

    @pytest.mark.asyncio
    async def test_unreadable_stores_leave_every_count_unknown(self, monkeypatch):
        class Session:
            async def __aenter__(self):
                raise RuntimeError("no database")

            async def __aexit__(self, *args):
                return False

        import core.database

        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: Session())
        assert await access.counts(TENANT, {"approvals", "drafts"}) == {"approvals": None, "drafts": None}


class TestAssignments:
    def test_names_are_checked_and_deduplicated(self):
        assert assignments.check_names(["investigator", "investigator", "supervisor"]) == ["investigator", "supervisor"]
        assert assignments.check_names([]) == []
        for bad in ("investigator", ["nowhere"], [1], ["a"] * 9):
            with pytest.raises(assignments.AssignmentError) as info:
                assignments.check_names(bad)
            assert info.value.status == 422

    @pytest.mark.asyncio
    async def test_setting_assignments_adds_and_removes_rows_and_reading_them_groups_by_user(self, monkeypatch):
        from core.models.workbench_assignment import WorkbenchAssignment

        existing = [
            WorkbenchAssignment(tenant_id=TENANT, user_id="u1", workbench="investigator", assigned_by="a"),
            WorkbenchAssignment(tenant_id=TENANT, user_id="u1", workbench="supervisor", assigned_by="a"),
        ]
        added: list[WorkbenchAssignment] = []
        deleted: list[WorkbenchAssignment] = []

        class Result:
            def __init__(self, rows):
                self._rows = rows

            def scalars(self):
                return self

            def all(self):
                return list(self._rows)

        class Session:
            async def execute(self, statement):
                text = str(statement)
                return Result(
                    ["investigator", "supervisor"]
                    if "workbench_assignments.workbench \n" in text
                    or text.startswith("SELECT workbench_assignments.workbench")
                    else existing
                )

            async def delete(self, row):
                deleted.append(row)

            def add(self, row):
                added.append(row)

            async def flush(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        import core.database

        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: Session())
        out = await assignments.set_assignments(TENANT, "u1", ["supervisor", "review_officer"], assigned_by="admin-1")
        assert out == {"user_id": "u1", "workbenches": ["supervisor", "review_officer"], "assigned_by": "admin-1"}
        assert [r.workbench for r in deleted] == ["investigator"] and [r.workbench for r in added] == ["review_officer"]
        assert added[0].assigned_by == "admin-1" and added[0].user_id == "u1"
        assert await assignments.assigned_to(TENANT, "u1") == {"investigator", "supervisor"}
        grouped = await assignments.list_assignments(TENANT)
        assert grouped == [
            {"user_id": "u1", "workbenches": ["investigator", "supervisor"], "assigned_by": "a", "updated_at": None}
        ]


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_index_answers_off_and_the_other_routes_are_not_found(self, monkeypatch):
        from api.v1 import workbench as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        assert await api.list_workbenches(request, role="cfo", tenant_id=str(TENANT)) == {
            "enabled": False,
            "workbenches": [],
        }
        for call in (
            api.catalogue(tenant_id=str(TENANT)),
            api.list_assignments(tenant_id=str(TENANT)),
            api.set_assignments("u1", api.AssignmentIn(workbenches=[]), request, tenant_id=str(TENANT)),
            api.summary("review_officer", request, role="cfo", tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "workbench_disabled"

    @pytest.mark.asyncio
    async def test_the_routes_serve_the_callers_workbenches_and_assignments(self, monkeypatch):
        from api.v1 import workbench as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        monkeypatch.setattr(assignments, "assigned_to", AsyncMock(return_value={"investigator"}))
        monkeypatch.setattr(access, "counts", AsyncMock(return_value={"documents": 1, "cases": None}))
        monkeypatch.setattr(
            assignments,
            "list_assignments",
            AsyncMock(return_value=[{"user_id": "u1", "workbenches": ["investigator"]}]),
        )
        monkeypatch.setattr(
            assignments, "set_assignments", AsyncMock(return_value={"user_id": "u1", "workbenches": ["supervisor"]})
        )
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))

        listed = await api.list_workbenches(request, role="cfo", tenant_id=str(TENANT))
        assert listed["enabled"] is True and [b["name"] for b in listed["workbenches"]] == [
            "review_officer",
            "investigator",
        ]
        assert assignments.assigned_to.call_args.args == (TENANT, "u1")

        found = await api.summary("investigator", request, role="analyst", tenant_id=str(TENANT))
        assert found["counts"] == {"documents": 1, "cases": None} and found["waiting"] == 1
        with pytest.raises(HTTPException) as info:
            await api.summary("supervisor", request, role="cfo", tenant_id=str(TENANT))
        assert info.value.status_code == 404
        with pytest.raises(HTTPException) as info:
            await api.summary("nowhere", request, role="admin", tenant_id=str(TENANT))
        assert info.value.status_code == 404

        assert [b["name"] for b in (await api.catalogue(tenant_id=str(TENANT)))["workbenches"]] == list(
            definitions.NAMES
        )
        assert (await api.list_assignments(tenant_id=str(TENANT)))["total"] == 1
        out = await api.set_assignments(
            "u1", api.AssignmentIn(workbenches=["supervisor"]), request, tenant_id=str(TENANT)
        )
        assert out["workbenches"] == ["supervisor"]
        assert assignments.set_assignments.call_args.kwargs["assigned_by"] == "u1"
        with pytest.raises(HTTPException) as info:
            await api.set_assignments("u1", api.AssignmentIn(workbenches=["nowhere"]), request, tenant_id=str(TENANT))
        assert info.value.status_code == 422 and info.value.detail["error"] == "workbench_unknown"
        with pytest.raises(HTTPException) as info:
            await api.set_assignments("   ", api.AssignmentIn(workbenches=[]), request, tenant_id=str(TENANT))
        assert info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_a_caller_without_an_identity_holds_only_role_workbenches(self, monkeypatch):
        from api.v1 import workbench as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        monkeypatch.setattr(assignments, "assigned_to", AsyncMock(side_effect=AssertionError("not consulted")))
        request = SimpleNamespace(state=SimpleNamespace(claims={}))
        listed = await api.list_workbenches(request, role="coo", tenant_id=str(TENANT))
        assert [b["name"] for b in listed["workbenches"]] == ["review_officer", "supervisor"]
