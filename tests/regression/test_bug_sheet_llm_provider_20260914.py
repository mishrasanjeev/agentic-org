"""Bug sheet 2026-09-14 — per-agent LLM provider pin (#31, #38, #34).

#31  An agent's model could disagree with its provider: ``agents`` carried
     only ``llm_model``; the runtime guessed the provider by substring and
     mapped any unknown name to ``gemini-2.5-flash``. Now ``agents`` has a
     nullable ``llm_provider`` (migration ``v6z20_agent_llm_provider``),
     ``LLMConfig.provider`` is validated against the catalog at the API
     boundary (422), and ``create_chat_model`` dispatches by the explicit
     provider without inference or silent downgrade. ``provider=None``
     keeps the legacy inference for pre-existing rows.
#38  ``openai_compatible`` is in the catalog but had no dispatch branch.
     The factory now builds ``ChatOpenAI`` against the tenant credential's
     ``provider_config.base_url`` and fails closed when it is missing.
#34  ``api/v1/agents.py`` persists/returns ``llm_provider`` on every write
     path (create / replace / patch / clone / rollback) so the UI provider
     picker round-trips.

Tests replay the tester's inputs (the request payload / the agent's stored
llm config) and assert the observable outcome (422, built client, error).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.ai_providers.resolver import ResolvedCredential
from core.llm.router import LLMProviderConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations" / "versions" / "v6_z20_agent_llm_provider.py"

_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_TENANT = "11111111-1111-4111-8111-111111111111"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# ── #31 — API boundary: provider/model consistency ─────────────────────


def test_agent_create_rejects_provider_model_mismatch_with_422_semantics() -> None:
    from core.schemas.api import AgentCreate

    with pytest.raises(ValidationError) as exc:
        AgentCreate(
            name="Ledger",
            agent_type="bookkeeper",
            domain="finance",
            llm={"provider": "anthropic", "model": "gpt-4o"},
        )
    msg = str(exc.value)
    assert "not a 'anthropic' model" in msg
    assert "gpt-4o" in msg


def test_agent_update_rejects_provider_model_mismatch() -> None:
    from core.schemas.api import AgentUpdate

    with pytest.raises(ValidationError):
        AgentUpdate(llm={"provider": "gemini", "model": _ANTHROPIC_MODEL})


def test_llm_config_normalises_and_accepts_catalog_pairs() -> None:
    from core.schemas.api import LLMConfig

    cfg = LLMConfig(provider=" Anthropic ", model=f" {_ANTHROPIC_MODEL} ")
    assert cfg.provider == "anthropic"
    assert cfg.model == _ANTHROPIC_MODEL
    # openai_compatible is the wildcard entry: the admin's endpoint owns the list.
    assert LLMConfig(provider="openai_compatible", model="my-local-llm").provider == "openai_compatible"


def test_llm_config_rejects_unknown_provider_and_empty_model() -> None:
    from core.schemas.api import LLMConfig

    with pytest.raises(ValidationError):
        LLMConfig(provider="not-a-provider", model="gpt-4o")
    with pytest.raises(ValidationError):
        LLMConfig(provider="openai_compatible", model="   ")


def test_llm_config_rejects_provider_without_runtime_dispatch() -> None:
    """azure_openai is catalogued for tenant settings but the agent factory
    cannot build it — refuse at create time instead of on the first run."""
    from core.schemas.api import LLMConfig

    with pytest.raises(ValidationError) as exc:
        LLMConfig(provider="azure_openai", model="deployment:gpt-4o")
    assert "cannot be pinned" in str(exc.value)


def test_llm_config_provider_none_keeps_legacy_behaviour() -> None:
    from core.schemas.api import LLMConfig

    cfg = LLMConfig(model="anything-goes")
    assert cfg.provider is None
    assert cfg.model == "anything-goes"
    assert LLMConfig(provider="", model="x").provider is None
    assert "provider" in LLMConfig().model_dump()


# ── #31 — storage: model column + migration ───────────────────────────


def test_agent_model_declares_nullable_llm_provider_column() -> None:
    from sqlalchemy import String

    from core.models.agent import Agent

    col = Agent.__table__.c.llm_provider
    assert col.nullable is True
    assert isinstance(col.type, String)
    assert col.type.length == 50


def test_v6z20_migration_is_guarded_idempotent_and_single_head() -> None:
    spec = importlib.util.spec_from_file_location("v6z20", MIGRATION)
    mig = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mig)

    assert mig.revision == "v6z20_agent_llm_provider"
    assert len(mig.revision) <= 32
    assert mig.down_revision == "v6z19_repair_billing_cdc"

    executed: list[str] = []

    class _Op:
        @staticmethod
        def execute(sql: str) -> None:
            executed.append(" ".join(str(sql).split()))

    with patch.object(mig, "op", _Op):
        mig.upgrade()
        up = " ".join(executed)
        executed.clear()
        mig.downgrade()
        down = " ".join(executed)

    assert "to_regclass('public.agents')" in up
    assert "ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(50)" in up
    assert "ALTER TABLE agents DROP COLUMN IF EXISTS llm_provider" in down

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    heads = script.get_heads()
    # Later migrations (v6z21 ownership) chain on top; pin the single-head
    # invariant and that v6z20 is in the chain, not the current head's id.
    assert len(heads) == 1, heads
    assert "v6z20_agent_llm_provider" in {rev.revision for rev in script.walk_revisions()}


# ── #31 — runtime: explicit provider is honoured, never inferred ──────


@pytest.fixture
def cloud_mode(monkeypatch):
    monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")
    monkeypatch.delenv("AGENTICORG_LLM_ROUTING", raising=False)
    monkeypatch.delenv("VLLM_API_BASE", raising=False)
    monkeypatch.delenv("AGENTICORG_LLM_PRIMARY", raising=False)


def _stub_builders(monkeypatch) -> dict[str, list[tuple[str, str | None]]]:
    """Replace the per-provider builders with recorders (no network)."""
    import core.langgraph.llm_factory as factory

    calls: dict[str, list[tuple[str, str | None]]] = {
        "gemini": [],
        "anthropic": [],
        "openai": [],
        "openai_compatible": [],
    }

    def _make(name: str):
        def _builder(model_name, temperature, max_tokens, tenant_id):
            calls[name].append((model_name, tenant_id))
            return object()

        return _builder

    monkeypatch.setattr(factory, "_build_gemini_model", _make("gemini"))
    monkeypatch.setattr(factory, "_build_anthropic_model", _make("anthropic"))
    monkeypatch.setattr(factory, "_build_openai_model", _make("openai"))
    monkeypatch.setattr(factory, "_build_openai_compatible_model", _make("openai_compatible"))
    return calls


def test_explicit_provider_mismatch_raises_instead_of_downgrading(cloud_mode, monkeypatch) -> None:
    from core.langgraph.llm_factory import create_chat_model

    calls = _stub_builders(monkeypatch)
    with pytest.raises(ValueError, match="not a 'anthropic' model"):
        create_chat_model(model="gpt-4o", provider="anthropic")
    # Nothing was built — in particular no silent Gemini fallback.
    assert all(not v for v in calls.values())


def test_explicit_provider_unknown_model_is_not_mapped_to_gemini_flash(cloud_mode, monkeypatch) -> None:
    from core.langgraph.llm_factory import create_chat_model

    calls = _stub_builders(monkeypatch)
    with pytest.raises(ValueError, match="gemini-9-ultra"):
        create_chat_model(model="gemini-9-ultra", provider="gemini")
    assert calls["gemini"] == []


def test_explicit_provider_dispatches_by_provider_not_model_substring(cloud_mode, monkeypatch) -> None:
    from core.langgraph.llm_factory import create_chat_model

    calls = _stub_builders(monkeypatch)
    create_chat_model(model=_ANTHROPIC_MODEL, provider="anthropic", tenant_id=_TENANT)
    assert calls["anthropic"] == [(_ANTHROPIC_MODEL, _TENANT)]
    assert calls["gemini"] == [] and calls["openai"] == []


def test_provider_from_agent_llm_config_is_honoured(cloud_mode, monkeypatch) -> None:
    """The agent's stored llm_config (routing_config) carries ``provider``."""
    from core.langgraph.llm_factory import create_chat_model

    calls = _stub_builders(monkeypatch)
    create_chat_model(model="gpt-4o", routing_config={"provider": "OpenAI", "routing": "disabled"})
    assert calls["openai"] == [("gpt-4o", None)]


def test_pinned_provider_is_not_crossed_by_tier_routing(cloud_mode, monkeypatch) -> None:
    import core.langgraph.llm_factory as factory

    calls = _stub_builders(monkeypatch)
    monkeypatch.setattr(factory.smart_router, "route", lambda **_: _ANTHROPIC_MODEL)
    factory.create_chat_model(
        model="gemini-2.5-flash",
        provider="gemini",
        query="draft a complex multi-entity reconciliation memo",
        routing_config={"routing": "auto"},
    )
    assert calls["gemini"] == [("gemini-2.5-flash", None)]
    assert calls["anthropic"] == []


def test_provider_none_keeps_legacy_inference_for_old_rows(cloud_mode, monkeypatch) -> None:
    from core.langgraph.llm_factory import _resolve_model, create_chat_model

    calls = _stub_builders(monkeypatch)
    assert _resolve_model("some-unknown-model") == "gemini-2.5-flash"
    create_chat_model(model="some-unknown-model")
    assert calls["gemini"] == [("gemini-2.5-flash", None)]
    create_chat_model(model="claude-3-5-sonnet-20241022")
    assert calls["anthropic"] == [("claude-3-5-sonnet-20241022", None)]


# ── #38 — openai_compatible dispatch ──────────────────────────────────


def _byo(base_url: str | None) -> ResolvedCredential:
    return ResolvedCredential(
        secret="sk-local",  # noqa: S106 — test fixture, not a credential
        provider="openai_compatible",
        kind="llm",
        source="tenant",
        provider_config={"base_url": base_url} if base_url is not None else None,
    )


def test_openai_compatible_builds_chat_openai_against_tenant_base_url(cloud_mode) -> None:
    from langchain_openai import ChatOpenAI

    from core.langgraph.llm_factory import create_chat_model

    with patch(
        "core.langgraph.llm_factory._resolve_cloud_credential",
        return_value=_byo("https://llm.internal.example.com/"),
    ) as resolve:
        llm = create_chat_model(model="my-local-llm", provider="openai_compatible", tenant_id=_TENANT)

    resolve.assert_called_once_with("openai_compatible", _TENANT)
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "my-local-llm"
    assert llm.openai_api_base == "https://llm.internal.example.com/v1"
    assert llm.openai_api_key.get_secret_value() == "sk-local"


def test_openai_compatible_keeps_explicit_v1_suffix(cloud_mode) -> None:
    from core.langgraph.llm_factory import create_chat_model

    with patch(
        "core.langgraph.llm_factory._resolve_cloud_credential",
        return_value=_byo("https://llm.internal.example.com/v1"),
    ):
        llm = create_chat_model(model="my-local-llm", provider="openai_compatible", tenant_id=_TENANT)
    assert llm.openai_api_base == "https://llm.internal.example.com/v1"


def test_openai_compatible_fails_closed_without_base_url(cloud_mode) -> None:
    from core.langgraph.llm_factory import create_chat_model

    with patch("core.langgraph.llm_factory._resolve_cloud_credential", return_value=_byo(None)):
        with pytest.raises(LLMProviderConfigurationError, match="provider_config.base_url"):
            create_chat_model(model="my-local-llm", provider="openai_compatible", tenant_id=_TENANT)
    with patch("core.langgraph.llm_factory._resolve_cloud_credential", return_value=_byo("  ")):
        with pytest.raises(LLMProviderConfigurationError, match="provider_config.base_url"):
            create_chat_model(model="my-local-llm", provider="openai_compatible", tenant_id=_TENANT)


def test_openai_compatible_rejects_non_https_base_url(cloud_mode) -> None:
    from core.langgraph.llm_factory import create_chat_model

    with patch(
        "core.langgraph.llm_factory._resolve_cloud_credential",
        return_value=_byo("http://llm.internal.example.com"),
    ):
        with pytest.raises(LLMProviderConfigurationError, match="https://"):
            create_chat_model(model="my-local-llm", provider="openai_compatible", tenant_id=_TENANT)


def test_openai_compatible_has_no_platform_env_fallback(cloud_mode, monkeypatch) -> None:
    """Real resolver path with no tenant context: there is no env var for
    openai_compatible, so the factory must raise a clear config error."""
    from core.ai_providers.resolver import _PLATFORM_ENV_VARS
    from core.langgraph.llm_factory import create_chat_model

    assert ("openai_compatible", "llm") not in _PLATFORM_ENV_VARS
    with pytest.raises(LLMProviderConfigurationError, match="openai_compatible provider is not configured"):
        create_chat_model(model="my-local-llm", provider="openai_compatible", tenant_id=None)


# ── #34 — agents.py persists and returns llm_provider ────────────────


def test_agents_api_persists_and_returns_llm_provider() -> None:
    src = _read("api/v1/agents.py")
    to_dict = src[src.index("def _agent_to_dict(") : src.index("def _agent_to_dict(") + 4000]
    assert '"llm_provider": getattr(agent, "llm_provider", None)' in to_dict
    # create + clone
    assert src.count("llm_provider=body.llm.provider,") == 1
    assert "llm_provider=parent.llm_provider," in src
    # replace + patch + rollback
    assert "agent.llm_provider = body.llm.provider" in src
    assert 'agent.llm_provider = update_data["llm"].get("provider")' in src
    assert 'agent.llm_provider = prev_version.llm_config.get("provider")' in src
    # generate_agent pins the provider it hardcodes
    assert re.search(r'llm_model="gemini-2\.5-flash",\s*llm_provider="gemini",', src)
