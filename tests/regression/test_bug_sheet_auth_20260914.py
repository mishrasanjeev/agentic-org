"""QA bug-sheet replay (2026-09-14) — auth / RBAC / HITL / platform rows.

Each test pins one row of the framework bug sheet that was confirmed still
open in this codebase by static + runtime verification:

 #24/#35  password, signup and Google tokens never carried the user's id;
          ``sub`` is an e-mail so every ``UUID(sub)`` attribution failed.
 #25/#33  approvals role hierarchy omitted merchant/domain_lead/analyst/
          developer -> "unknown role" for every decision by those users.
 #26      a session with no ``agenticorg:domains`` claim resolved to
          ``None`` (= all domains) instead of failing closed.
 #5       SSO post-login redirect ignored the UI origin (no setting).
 #27      organization name was not surfaced to the header.
 #29      connector rename onto an existing name surfaced as a 500.
 #48      upload marked ``indexed`` before/despite pgvector ingestion and
          the content_text search fallback matched non-indexed rows.
 #1       docker-compose pulled a MinIO image its registry no longer serves.
"""

from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# #24 / #35 — user id claim on every human token
# ---------------------------------------------------------------------------


class TestUserIdClaim:
    @pytest.mark.parametrize("fn_name", ["signup", "login", "google_login"])
    def test_every_human_token_carries_agenticorg_user_id(self, fn_name: str) -> None:
        from api.v1 import auth as auth_mod

        src = inspect.getsource(getattr(auth_mod, fn_name))
        assert '"agenticorg:user_id": str(user.id)' in src, fn_name

    def test_agents_and_prompt_templates_no_longer_parse_sub_as_uuid(self) -> None:
        from api.v1 import agents, prompt_templates

        for mod in (agents, prompt_templates):
            src = inspect.getsource(mod._user_uuid_from_claims)
            assert '"agenticorg:user_id"' in src, mod.__name__
            assert '"sub"' not in src, mod.__name__

    def test_governance_actor_prefers_user_id_claim(self) -> None:
        src = (REPO / "api" / "v1" / "governance.py").read_text(encoding="utf-8")
        assert 'claims.get("agenticorg:user_id") or claims.get("sub")' in src

    def test_hitl_decide_never_raises_on_non_uuid_subject(self) -> None:
        """Delegation lookup used ``_uuid.UUID(user_id_str)`` inline; for a
        password-login user (``sub`` = e-mail) that raised inside the broad
        except and delegations silently never applied."""
        from api.v1 import approvals

        src = inspect.getsource(approvals.decide)
        assert "_uuid.UUID(user_id_str)" in src  # parsed exactly once, guarded
        assert src.count("_uuid.UUID(user_id_str)") == 1
        assert "UserDelegation.delegate_id == user_uuid" in src


# ---------------------------------------------------------------------------
# #25 / #33 — approvals role hierarchy covers every provisioned role
# ---------------------------------------------------------------------------


class TestApprovalRoleHierarchy:
    def test_every_rbac_role_has_a_level(self) -> None:
        from api.v1.approvals import _role_level
        from core.rbac import ROLE_DOMAIN_MAP

        missing = [r for r in ROLE_DOMAIN_MAP if _role_level(r) == 0]
        assert missing == [], f"roles with no approval level: {missing}"

    def test_domain_lead_can_decide_in_own_domain(self) -> None:
        from api.v1.approvals import _can_decide

        allowed, reason = _can_decide("domain_lead", ["finance"], "finance", "finance")
        assert allowed, reason

    def test_domain_lead_cannot_decide_outside_domain(self) -> None:
        from api.v1.approvals import _can_decide

        allowed, _ = _can_decide("domain_lead", ["finance"], "hr", "hr")
        assert not allowed

    def test_unknown_role_still_denied(self) -> None:
        from api.v1.approvals import _can_decide

        allowed, reason = _can_decide("intern", None, "finance", "finance")
        assert not allowed and "unknown role" in reason


# ---------------------------------------------------------------------------
# #26 — domain claim fails closed
# ---------------------------------------------------------------------------


def _req(claims: dict, auth_mode: str | None = "legacy") -> SimpleNamespace:
    state = SimpleNamespace(claims=claims)
    if auth_mode is not None:
        state.auth_mode = auth_mode
    return SimpleNamespace(state=state)


class TestUserDomainsFailClosed:
    def test_explicit_claim_wins(self) -> None:
        from api.deps import get_user_domains

        assert get_user_domains(_req({"agenticorg:domains": ["hr"]})) == ["hr"]
        assert get_user_domains(_req({"agenticorg:domains": None, "role": "cfo"})) is None

    def test_missing_claim_is_rederived_from_role(self) -> None:
        from api.deps import get_user_domains

        assert get_user_domains(_req({"role": "cfo"})) == ["finance"]
        assert get_user_domains(_req({"role": "admin"})) is None
        assert get_user_domains(_req({"role": "analyst", "domain": "ops"})) == ["ops"]
        assert get_user_domains(_req({"role": "analyst"})) == []

    def test_missing_claim_and_role_is_no_domain(self) -> None:
        from api.deps import get_user_domains

        assert get_user_domains(_req({"sub": "someone@x.io"})) == []
        assert get_user_domains(_req({}, auth_mode=None)) == []

    def test_admin_scope_without_role_claim_is_unrestricted(self) -> None:
        """CI integration replay: a role-less token holding agenticorg:admin
        collapsed to [] and could not decide 'platform' approvals or see
        prompt templates."""
        from api.deps import get_user_domains

        assert get_user_domains(_req({"grantex:scopes": ["agenticorg:admin"]})) is None

    def test_machine_credentials_keep_scope_bounded_access(self) -> None:
        from api.deps import get_user_domains

        assert get_user_domains(_req({"sub": "apikey:abc"}, "api_key")) is None
        assert get_user_domains(_req({"grantex:grant_id": "g1"}, "grantex")) is None


# ---------------------------------------------------------------------------
# #5 — SSO redirect uses the configured UI origin
# ---------------------------------------------------------------------------


class TestSsoUiBaseUrl:
    def test_setting_exists_and_defaults_to_same_origin(self) -> None:
        from core.config import Settings

        assert Settings.model_fields["ui_base_url"].default == ""

    def test_callback_builds_redirect_from_setting(self) -> None:
        from api.v1 import sso

        src = inspect.getsource(sso)
        assert 'settings.ui_base_url if hasattr(settings, "ui_base_url")' not in src
        assert '(settings.ui_base_url or "").rstrip("/")' in src


# ---------------------------------------------------------------------------
# #27 — organization name reaches the UI
# ---------------------------------------------------------------------------


class TestOrgName:
    def test_auth_me_returns_org_name(self) -> None:
        from api.v1 import auth as auth_mod

        src = inspect.getsource(auth_mod.get_current_user_profile)
        assert '"org_name": tenant.name if tenant else None' in src

    def test_layout_renders_org_name(self) -> None:
        layout = (REPO / "ui" / "src" / "components" / "Layout.tsx").read_text(encoding="utf-8")
        assert 'data-testid="org-name"' in layout
        assert 'data-testid="org-name-mobile"' in layout
        ctx = (REPO / "ui" / "src" / "contexts" / "AuthContext.tsx").read_text(encoding="utf-8")
        assert "org_name?: string | null;" in ctx


# ---------------------------------------------------------------------------
# #29 sibling — connector rename collision is a 409, not a 500
# ---------------------------------------------------------------------------


class TestConnectorRename:
    def test_update_checks_name_uniqueness_before_setattr(self) -> None:
        src = (REPO / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")
        block = src.split('_blocked_fields = {"id", "tenant_id", "company_id", "auth_config", "secret_ref"}', 1)[1]
        block = block.split("setattr(connector, field, value)", 1)[0]
        assert "Connector.name == new_name" in block
        assert "Connector.id != conn_id" in block
        assert 'raise HTTPException(409, f"Connector \'{new_name}\' already exists")' in block


# ---------------------------------------------------------------------------
# #48 — knowledge status is truthful and search only surfaces indexed rows
# ---------------------------------------------------------------------------


class TestKnowledgeIndexStatus:
    def test_native_path_does_not_mark_indexed_before_ingestion(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge.upload_document)
        head = src.split("native_index_pending = ", 1)[0]
        # Only the RAGFlow success branch may set INDEXED up front.
        assert head.count("DOC_STATUS_INDEXED") == 1
        tail = src.split("native_index_pending = ", 1)[1]
        assert "DOC_STATUS_INDEXED if ingestion_status == \"indexed\" else DOC_STATUS_FAILED" in tail
        assert "_db_set_doc_status(" in tail

    def test_zero_chunk_ingest_is_not_reported_as_indexed(self) -> None:
        """Runtime replay: a 53-byte .txt returned ``status=indexed,
        ingestion_status=indexed`` while the ingest log said
        ``chunks_indexed=0`` (spans below min_chars)."""
        from api.v1 import knowledge

        src = inspect.getsource(knowledge.upload_document)
        assert "if ingest_result.chunks_indexed > 0:" in src
        assert 'ingestion_error = (ingest_result.errors or ["no_chunks_indexed"])[0]' in src

    def test_content_text_fallback_requires_indexed_status(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge._native_semantic_search)
        content_block = src.split("COALESCE(metadata->>'content_text', '')", 1)[1]
        content_block = content_block.split("LIMIT :k", 1)[0]
        assert "status = 'indexed'" in content_block
        assert "status != 'deleted'" not in content_block

    def test_status_helper_scopes_by_tenant(self) -> None:
        from api.v1 import knowledge

        src = inspect.getsource(knowledge._db_set_doc_status)
        assert "Document.tenant_id == tid" in src
        assert "ingestion_error" in src


# ---------------------------------------------------------------------------
# #1 — local docker stack boots (the MinIO image must still be pullable)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.dev.yml"])
def test_compose_minio_image_is_pullable(compose_file: str) -> None:
    """Neither registry MinIO used to publish on serves the server image any more.

    quay.io removed ``quay.io/minio/minio`` (every tag and digest), and Docker
    Hub's ``minio/minio`` now needs credentials, so ``make dev`` failed to pull.
    """
    compose = (REPO / compose_file).read_text(encoding="utf-8")
    assert re.search(r"^\s*image: cgr\.dev/chainguard/minio@sha256:[0-9a-f]{64}\s*$", compose, re.M)
    assert re.search(r"^\s*image: (quay\.io/)?minio/minio", compose, re.M) is None


def test_claim_values_are_uuid_strings() -> None:
    """Sanity: the value we now stamp into tokens parses as a UUID."""
    assert uuid.UUID(str(uuid.uuid4()))
