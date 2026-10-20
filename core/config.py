"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_core import from_json
from pydantic_settings import BaseSettings

STRICT_ENVS = frozenset({"production", "prod", "staging", "stage", "preview"})
RELAXED_ENVS = frozenset({"local", "dev", "development", "test", "ci"})

# Secret values written in this repository (code defaults, .env.example,
# compose files, Makefile, CI and scripts). Anyone can read them, so a strict
# runtime refuses them as signing or vault keys. Compared case-insensitively
# after trimming whitespace.
PUBLISHED_PLACEHOLDER_SECRETS = frozenset(
    {
        "dev-only-vault-key",
        "dev-only-secret-key",
        "change-me-to-32-char-random-string",
        "agenticorg-dev-only-do-not-use-in-production",
        "ci-test-secret-key-minimum-16",
        "dev-secret-key-change-in-production-32chars",
    }
)


def normalize_env(env: str | None) -> str:
    """Return the canonical lowercase runtime environment label."""
    return (env or "").strip().lower()


def is_relaxed_env(env: str | None) -> bool:
    """Return True for explicitly local/test runtimes only."""
    return normalize_env(env) in RELAXED_ENVS


def is_strict_runtime_env(env: str | None) -> bool:
    """Return True for every non-local runtime, including unknown labels."""
    return not is_relaxed_env(env)


def is_published_placeholder_secret(value: str) -> bool:
    """Return True when *value* is a secret written in this repository."""
    return value.strip().casefold() in PUBLISHED_PLACEHOLDER_SECRETS


def _loadable_zone(name: object) -> bool:
    """True when ``name`` is an IANA time zone this runtime can load."""
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        ZoneInfo(name.strip())
    except (KeyError, ValueError, OSError):
        return False
    return True


# The SKU pattern of core/spend/vocab.py (``SKU_PATTERN``), repeated here because settings are
# checked before core.spend can be imported (it imports these settings); a test keeps them equal.
SPEND_SKU_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._:/@-]{0,127}$"
SPEND_GPU_PROVIDERS = ("ollama", "vllm")
SPEND_GPU_MAX_MODELS = 50
SPEND_GPU_MAX_NODES = Decimal("10000")
# The width of ``node_pool`` in spend_gpu_pool_hours and spend_gpu_allocations (VARCHAR(64)).
SPEND_GPU_POOL_MAX_CHARS = 64
_SPEND_GPU_POOL_KEYS = frozenset({"provider", "node_pool", "models", "nodes", "from", "to"})
_SPEND_SKU_RE = re.compile(SPEND_SKU_PATTERN)


@dataclass(frozen=True)
class SpendGpuPool:
    """One in-house GPU pool of ``spend_gpu_pools_json``, whose node hours core/spend/gpu.py spreads.

    ``models`` are the pool's model names as usage records carry them
    (lower-cased); ``nodes`` is the node count each hour of ``[start, end)``
    costs (``end`` ``None``: still running). Both bounds are whole UTC hours.
    """

    provider: str
    node_pool: str
    models: tuple[str, ...]
    nodes: Decimal
    start: datetime
    end: datetime | None


def _gpu_pool_sku(value: Any, field: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _SPEND_SKU_RE.fullmatch(text):
        raise ValueError(f"spend_gpu_pools_json: {field} is 1 to 128 of A-Z a-z 0-9 space . _ : / @ -")
    return text.lower()


def _gpu_pool_hour(value: Any, field: str) -> datetime:
    """A whole UTC hour from ISO text (an instant without an offset is read as UTC)."""
    try:
        moment = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"spend_gpu_pools_json: {field} is an ISO instant") from None
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    if moment.minute or moment.second or moment.microsecond:
        raise ValueError(f"spend_gpu_pools_json: {field} is a whole UTC hour")
    return moment


def _gpu_pool_nodes(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("spend_gpu_pools_json: nodes is a number")
    try:
        nodes = Decimal(str(value).strip())
    except (DecimalException, ValueError):
        raise ValueError("spend_gpu_pools_json: nodes is a number") from None
    if not nodes.is_finite() or nodes <= 0 or nodes > SPEND_GPU_MAX_NODES:
        raise ValueError("spend_gpu_pools_json: nodes is above 0 and at most 10000")
    if nodes != nodes.quantize(Decimal("0.0001")):
        raise ValueError("spend_gpu_pools_json: nodes has at most 4 decimals")
    return nodes


def _gpu_pool(item: Any) -> SpendGpuPool:
    if not isinstance(item, dict):
        raise ValueError("spend_gpu_pools_json: every pool is a JSON object")
    unknown = set(item) - _SPEND_GPU_POOL_KEYS
    if unknown:
        raise ValueError(f"spend_gpu_pools_json: unknown key {sorted(unknown)[0]!r}")
    provider = str(item.get("provider") or "").strip().lower()
    if provider not in SPEND_GPU_PROVIDERS:
        raise ValueError("spend_gpu_pools_json: provider is ollama or vllm")
    models = item.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= SPEND_GPU_MAX_MODELS:
        raise ValueError("spend_gpu_pools_json: models is a list of 1 to 50 model names")
    start = _gpu_pool_hour(item.get("from"), "from")
    end = _gpu_pool_hour(item["to"], "to") if item.get("to") is not None else None
    if end is not None and end <= start:
        raise ValueError("spend_gpu_pools_json: to is after from")
    node_pool = _gpu_pool_sku(item.get("node_pool"), "node_pool")
    if len(node_pool) > SPEND_GPU_POOL_MAX_CHARS:
        # The pool-hour tables hold 64 characters; a longer name would fail every hourly insert.
        raise ValueError(f"spend_gpu_pools_json: node_pool is at most {SPEND_GPU_POOL_MAX_CHARS} characters")
    return SpendGpuPool(
        provider=provider,
        node_pool=node_pool,
        models=tuple(dict.fromkeys(_gpu_pool_sku(m, "every model") for m in models)),
        nodes=_gpu_pool_nodes(item.get("nodes")),
        start=start,
        end=end,
    )


def parse_spend_gpu_pools(raw: str | None) -> tuple[SpendGpuPool, ...]:
    """The pools of ``spend_gpu_pools_json`` (none for an empty setting); ``ValueError`` naming the problem.

    Two entries for one pool may not cover the same hour.
    """
    text = (raw or "").strip()
    if not text:
        return ()
    try:
        # Keep nesting bounded even when another library raises Python's recursion limit.
        data = from_json(text)
    except (ValueError, RecursionError) as exc:
        raise ValueError("spend_gpu_pools_json: not valid JSON") from exc
    if not isinstance(data, list):
        raise ValueError("spend_gpu_pools_json: must be a JSON list of pools")
    pools = tuple(_gpu_pool(item) for item in data)
    ordered = sorted(pools, key=lambda p: (p.provider, p.node_pool, p.start))
    for before, after in zip(ordered, ordered[1:], strict=False):
        same = (before.provider, before.node_pool) == (after.provider, after.node_pool)
        if same and (before.end is None or before.end > after.start):
            raise ValueError(f"spend_gpu_pools_json: two entries for pool {after.node_pool!r} cover the same hour")
    return pools


def _redis_url_with_default_db(url: str, default_db: int) -> str:
    """Return *url* with ``default_db`` applied without changing hosts.

    ``redis_url_from_env(default_db=1)`` must not jump back to localhost
    when the configured settings URL points at a production Redis host.
    Explicit environment URLs stay authoritative; this only adjusts the
    settings fallback path.
    """
    if default_db == 0:
        return url
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if parsed.scheme not in {"redis", "rediss"}:
        return url
    return urlunsplit(parsed._replace(path=f"/{default_db}"))


def redis_url_from_env(default_db: int = 0) -> str:
    """Resolve the Redis URL using the app's canonical env var first."""
    default_url = f"redis://localhost:6379/{default_db}"
    env_url = os.getenv("AGENTICORG_REDIS_URL") or os.getenv("REDIS_URL")
    if env_url:
        return env_url
    configured_settings = globals().get("settings")
    settings_url = getattr(configured_settings, "redis_url", default_url)
    return _redis_url_with_default_db(settings_url, default_db)


def redis_socket_timeout_kwargs() -> dict[str, float]:
    """Return bounded Redis socket timeouts, configurable per deploy.

    The defaults intentionally allow more room than the previous 500ms
    cap so real production p99 latency and brief cross-zone jitter do not
    force avoidable in-memory fallback. Deploys can tune these with
    AGENTICORG_REDIS_SOCKET_* env vars.
    """
    configured_settings = globals().get("settings")
    return {
        "socket_connect_timeout": getattr(configured_settings, "redis_socket_connect_timeout_seconds", 2.0),
        "socket_timeout": getattr(configured_settings, "redis_socket_timeout_seconds", 2.0),
    }


class Settings(BaseSettings):
    """Central configuration for AgenticOrg."""

    model_config = {"env_prefix": "AGENTICORG_", "env_file": ".env", "extra": "ignore"}

    # Environment
    env: str = "development"
    log_level: str = "INFO"
    # Log record rendering (core/logging_config.py): "json" emits one JSON
    # object per line for container log collectors; "console" is the
    # human-readable renderer. ``env=test`` defaults to console so pytest
    # output stays readable unless AGENTICORG_LOG_FORMAT is set explicitly.
    log_format: str = "json"
    secret_key: str = Field(default="dev-only-secret-key", min_length=16)

    # Database
    db_url: str = "postgresql+asyncpg://agenticorg:agenticorg_dev@localhost:5432/agenticorg"
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    # SQLAlchemy statement echo. Opt-in only (AGENTICORG_DB_ECHO=1): it was
    # previously tied to env=development, so every dev container emitted
    # multi-line SQL on stdout.
    db_echo: bool = False

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_connect_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    redis_socket_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)

    # Object Storage (GCS / S3-compatible)
    storage_bucket: str = "agenticorg-docs-dev"
    storage_region: str = "asia-south1"
    storage_endpoint: str | None = None  # Set for MinIO/S3-compatible; leave empty for GCS

    # Reasoning defaults are shared with the model registry; explicit operator overrides remain supported.
    llm_primary: str = "gemini-3.5-flash-lite"
    llm_fallback: str = "gemini-3.5-flash-lite"
    llm_temperature: float = 0.2
    llm_complete_timeout_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    llm_primary_timeout_fraction: float = Field(default=0.7, ge=0.1, le=0.9)
    llm_routing: str = "auto"  # auto | tier1 | tier2 | tier3 | disabled
    llm_mode: str = "cloud"  # cloud | local | auto

    # Auth
    auth_provider: str = "grantex"
    # Grantex bearer tokens are accepted only when ``iss`` matches this issuer
    # and ``aud`` matches this audience (auth/grantex_middleware.py). Audience
    # is mandatory in strict runtimes. Env: AGENTICORG_GRANTEX_ISSUER/_AUDIENCE.
    grantex_issuer: str = ""
    grantex_audience: str = ""
    # OACP artifact verification on POST /commerce/runtime/artifacts/cache
    # (core/commerce/oacp_grantex_trust.py). Unset, it is on in strict
    # runtimes (is_strict_runtime_env) and off in local/dev/test/ci. On: the
    # whole issuance is refused when any artifact fails detached-JWS, scope,
    # TTL or Token Status List verification. Off: verification still runs,
    # failures are logged as irregularities and the artifacts are cached
    # without a verifier_result_ref. Env: AGENTICORG_OACP_VERIFY_STRICT.
    oacp_verify_strict: bool | None = None
    # Commerce arbiter (core/commerce/arbiter.py), rule buyer_authority_verified:
    # refuse buyer authority (mandate, consent, passport) that carries no
    # server-side verification ref. Unset, on in strict runtimes, off in
    # local/dev/test/ci (the decision then records buyer_authority_source:
    # caller_asserted). Env: AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY.
    arbiter_require_verified_authority: bool | None = None
    # An allow decision is consumable only once Grantex countersigned it
    # (core/commerce/arbiter_grantex.py); without the grant require_allow_decision
    # refuses grantex_grant_missing. Unset, on in strict runtimes.
    # Env: AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT.
    arbiter_require_grantex_grant: bool | None = None
    # Post every arbiter decision to Grantex POST /v1/commerce/oacp/arbiter-decisions.
    # Unset, follows arbiter_require_grantex_grant. Env: AGENTICORG_ARBITER_GRANTEX_POST_ENABLED.
    arbiter_grantex_post_enabled: bool | None = None
    # route_meta enforcement (api/route_enforcement.py): "enforce" | "log"
    route_enforcement_mode: str = "enforce"
    # Route scope checks for the A2A and MCP families (FINDINGS A-68). Off, they
    # stay unmapped and any authenticated credential reaches POST /a2a/tasks,
    # GET /a2a/tasks/{id} and POST /mcp/call; on, those routes need a2a:read,
    # a2a:write or mcp:write (or agenticorg:admin) like every mapped family.
    # Env: AGENTICORG_ROUTE_SCOPE_A2A_MCP.
    route_scope_a2a_mcp: bool = False
    # Operator override (halt or throttle a model, agent, workflow or the tool
    # pipeline). Off by default; the authority flag ``operator_override.enabled``
    # turns it on per tenant, this switch for the whole deployment.
    operator_override_enabled: bool = False
    # Model gateway: tenant routing policies decide the provider and model of
    # every tenant-scoped model call. Off by default; the authority flag
    # ``model_gateway.enabled`` turns it on per tenant, this switch for the
    # whole deployment.
    model_gateway_enabled: bool = False
    # Runtime guardrails enforce (transform or block) instead of only flagging.
    # Off by default; the authority flag ``guardrails.enforce`` turns it on per
    # tenant, this switch for the whole deployment.
    guardrails_enforce: bool = False
    # The call-site hooks (every model call's input and output, retrieved
    # documents, tool arguments). Off by default: on, every stage is evaluated
    # in flag-only mode until guardrails.enforce is on for the tenant.
    guardrails_hooks_enabled: bool = False
    # A detector runs off the event loop under this budget; past it the rule
    # cannot say what it found (an enforced rule then fails closed in a strict
    # runtime). Pattern rules scan at most this many characters.
    guardrails_detector_timeout_seconds: float = 2.0
    guardrails_pattern_max_chars: int = 50_000
    # Distributed tracing. Off by default: on, every API request, task, agent
    # run, model call, tool call and knowledge search opens an OpenTelemetry
    # span, the log context carries the trace id and signed audit rows record
    # it. Spans go to OTEL_EXPORTER_OTLP_ENDPOINT over http/protobuf or grpc,
    # sampled at the ratio; a strict runtime refuses to start with tracing on
    # and no endpoint.
    tracing_enabled: bool = False
    tracing_protocol: str = "http/protobuf"
    tracing_sample_ratio: float = 1.0
    # Run timelines for the console's waterfall: with tracing on, the spans of
    # each agent run are stored in run_spans when the run ends and pruned
    # after the retention period. Off by default.
    tracing_timeline_enabled: bool = False
    # Streaming latency: the reasoning node reads each model answer as a
    # stream to time its first token (observability/streaming.py) and puts
    # the chunks back together into the same message. Off by default: off,
    # the call is made exactly as before.
    model_stream_timing_enabled: bool = False
    # Typed prompt parameters (core/prompts/parameters.py): creating or
    # changing a prompt template checks its declared parameters (type,
    # required, default, bounds) and refuses a placeholder the template does
    # not declare. Off by default: off, templates are stored as before.
    prompt_typed_parameters_enabled: bool = False
    # Maker-checker for prompt templates (core/prompts/change_requests.py):
    # a create, change, rollback or delete waits as a change request for a
    # second person's approval. Off by default; the authority flag
    # prompts.maker_checker turns it on for one tenant.
    prompts_maker_checker: bool = False
    # Prompt comparison and dataset evaluation (core/prompts/compare.py): one
    # prompt against several models side by side, and prompt variants scored
    # against a reference dataset. Both make real, billed model calls. Off by
    # default.
    prompt_compare_enabled: bool = False
    # Context-window management (core/prompts/context_window.py): before a
    # model call in an agent run, a conversation that does not fit the
    # model's window has its older, less relevant tool results omitted from
    # the copy that is sent. Off by default: off, the conversation is sent
    # whole and the provider's own limit applies.
    context_window_managed: bool = False
    # Structured-output enforcement (core/prompts/output_schema.py): an
    # agent's final answer is validated against the output schema it
    # declares; an invalid answer is sent back to the model to correct, and
    # one that is still invalid is escalated to a human instead of being
    # returned as completed. Off by default: off, nothing is validated.
    output_schema_enforced: bool = False
    # Provider content blocks in a model answer (core/llm/content.py): the
    # agent graph's output parser, the SOP parser, the run explainer and the
    # feedback analyser read each block's answer text (reasoning, signature
    # and tool-use blocks carry none), so a fenced JSON answer in a text block
    # parses. On by default, a deliberate default recorded in CHANGELOG.md:
    # false restores the previous parsing, where an agent run's output kept
    # the str() of the blocks in raw_output. The guardrail hooks read block
    # answers for their text whatever this says.
    # Env: AGENTICORG_AGENT_OUTPUT_BLOCK_PARSING.
    agent_output_block_parsing: bool = True
    # Evaluation framework v2 (core/evals/): tenant evaluation datasets, kept
    # as immutable versions, with an API and console to manage and run them.
    # Off by default: off, the endpoints answer 409 and nothing is stored.
    evals_v2_enabled: bool = False
    # Evaluation promotion gate (core/evals/gates.py): an agent that declares
    # a gate is not promoted or resumed to active while its prompt has no
    # stored run on the gate's dataset version, falls below the gate's pass
    # rate, or regresses beyond its allowance. Off by default: off, a gate is
    # kept and reported but not applied.
    eval_promotion_gate_enabled: bool = False
    # Agent registry (core/agent_registry/): each agent's card and its
    # governance lifecycle (draft, review, approved, published, deprecated,
    # retired) with recorded transitions. Off by default: off, the registry
    # endpoints answer 409 and nothing is written; the runtime is not
    # affected by the registry either way in this release.
    agent_registry_enabled: bool = False
    # Registry approval workflow (core/agent_registry/approval.py): with the
    # registry on, promotion and resume to active need an approved or
    # published registry entry, promotion publishes an approved entry and
    # retirement retires it. Off by default: off, the registry neither gates
    # nor follows the runtime.
    agent_registry_gates_promotion: bool = False
    # Traffic split (core/agent_registry/traffic.py): a share of the runs
    # asked of an agent through the agents API are served by another active
    # agent of the tenant. Off by default: off, a stored split is kept and
    # reported and no run is redirected.
    agent_traffic_split_enabled: bool = False
    # Task queue wait: a published background task is stamped with its publish
    # time and the worker meters how long it waited. Off by default: off, no
    # header is added and nothing is measured.
    task_queue_timing_enabled: bool = False
    tracing_timeline_retention_days: int = 30
    # Read direct agent runs from existing tenant audit rows in the explanation panel.
    agent_explanation_audit_enabled: bool = False
    # Tamper-evident audit: the sealing task links each tenant's audit rows
    # into a hash chain (chain_seq, chain_prev, chain_hash) so a removed,
    # reordered or edited row breaks verification. Off by default; the status
    # and verification read whatever is sealed either way.
    audit_chain_enabled: bool = False
    audit_chain_seal_batch: int = 5000
    # With audit_chain_enabled off, still seal the chain of every tenant that
    # has a commerce merchant config (commerce_c6z_merchant_configs), so the
    # commerce ledger (core/commerce/ledger.py) is tamper-evident. Off by
    # default. Env: AGENTICORG_AUDIT_CHAIN_DEFAULT_ON.
    audit_chain_default_on: bool = False
    # After each sealing, post the tenant's chain head to Grantex
    # POST /v1/audit/witness-checkpoints and store the verified countersignature
    # (core/governance/audit_witness.py). Off by default.
    # Env: AGENTICORG_AUDIT_WITNESS_ENABLED.
    audit_witness_enabled: bool = False
    # The retention task (core/tasks/commerce_ledger_tasks.py) deletes OACP review
    # manifests whose retention disposition is due (core/commerce/oacp_retention.py).
    # Off by default. Env: AGENTICORG_OACP_RETENTION_APPLY_ENABLED.
    oacp_retention_apply_enabled: bool = False
    # UCP transactions (api/v1/commerce_ucp.py): checkout sessions, carts and
    # orders at https://<merchant serving host>/ucp. Off by default: every /ucp
    # route answers 404 while off. Env: AGENTICORG_UCP_CHECKOUT_ENABLED.
    ucp_checkout_enabled: bool = False
    # UCP business profile (core/commerce/ucp, api/v1/ucp_discovery.py): off,
    # GET /.well-known/ucp and the /ucp catalog and location routes answer 404
    # and merchant onboarding generates no UCP signing key. On, a published
    # merchant's profile is served on its verified serving host.
    # Env: AGENTICORG_UCP_PROFILE_ENABLED.
    ucp_profile_enabled: bool = False
    # UCP releases served (comma list of YYYY-MM-DD). The newest is the
    # profile at /.well-known/ucp; older ones are leaf profiles advertised in
    # ucp.supported_versions. Env: AGENTICORG_UCP_VERSIONS.
    ucp_versions: str = "2026-04-08"
    # Days a rotated-out merchant UCP signing key stays in the profile so
    # verifiers keep accepting signatures made before the rotation (UCP
    # signatures, Key Rotation: at least 7). Env: AGENTICORG_UCP_KEY_ROTATION_GRACE_DAYS.
    ucp_key_rotation_grace_days: int = 7
    # RBI e-mandate framework 2026 (s.8): a subsequent UPI AutoPay / e-mandate
    # debit above these amounts (minor units, paise) needs the additional factor
    # of authentication; registration and the first debit always do
    # (core/commerce/india_rails.py). Defaults are INR 15,000 and INR 1,00,000
    # (insurance premiums, mutual fund subscriptions, credit-card bills).
    # Env: AGENTICORG_RBI_EMANDATE_AFA_LIMIT_MINOR_UNITS,
    # AGENTICORG_RBI_EMANDATE_AFA_HIGHER_LIMIT_MINOR_UNITS.
    rbi_emandate_afa_limit_minor_units: int = 1_500_000
    # Send the latest Plural capability verifier result as
    # mandate_capability_evidence (oacp.mandate_capability_evidence.v1) on the
    # Grantex C6Z authority request (core/commerce/mandate_capability_evidence.py).
    # Off by default: turn on after the Grantex release that accepts the member.
    # Env: AGENTICORG_GRANTEX_MANDATE_CAPABILITY_EVIDENCE_ENABLED.
    grantex_mandate_capability_evidence_enabled: bool = False
    # Server-requested OACP mandates (core/commerce/mandates_client.py): a UCP Complete
    # carrying ai.agenticorg.commerce.mandate_request asks Grantex for Intent, Cart and
    # Payment mandates and verifies them locally before the arbiter decides. Off by
    # default: turn on after the Grantex mandate release (Phase 4). While off, a
    # mandate_request is answered as unavailable and the authority stays caller-asserted.
    # Env: AGENTICORG_GRANTEX_MANDATES_ENABLED.
    grantex_mandates_enabled: bool = False
    rbi_emandate_afa_higher_limit_minor_units: int = 10_000_000

    # ACP product feed (core/commerce/acp/feed.py): the tenant-admin feed export
    # under /api/v1/commerce/runtime/acp/feed answers 404 while off. The feed is
    # generated only from verified, fresh OACP artifacts.
    # Env: AGENTICORG_ACP_FEED_ENABLED.
    acp_feed_enabled: bool = False
    # ACP checkout sessions (api/v1/commerce_acp.py) at
    # https://<merchant serving host>/acp/checkout_sessions, over the UCP session
    # table. Off by default: every /acp route answers 404 while off. Independent
    # of AGENTICORG_UCP_CHECKOUT_ENABLED so each protocol is enabled on its own.
    # Env: AGENTICORG_ACP_CHECKOUT_ENABLED.
    acp_checkout_enabled: bool = False
    # ACP request signatures (Signature + Timestamp headers): "log" records what
    # arrived and never refuses; "enforce" refuses a request that a verifier
    # registered for the calling platform does not accept (none ships: ACP
    # publishes no signing algorithm). Env: AGENTICORG_ACP_SIGNATURE_MODE.
    acp_signature_mode: str = "log"
    # Inbound RFC 9421 agent request signatures (auth/httpsig.py) on the public
    # commerce reads and the A2A routes, under the Visa TAP and Web Bot Auth
    # profiles. "log" (default) verifies what arrives, records it and never
    # refuses; "enforce" refuses an unsigned or invalid request with 401 and a
    # request whose replay check cannot run (Redis down) with 503.
    # Env: AGENTICORG_HTTPSIG_VERIFY_MODE.
    httpsig_verify_mode: str = "log"
    # Comma list of the authorities (host[:port], default port omitted) a signed
    # request may cover as @authority. Empty: the request's Host header.
    # Env: AGENTICORG_HTTPSIG_AUTHORITIES.
    httpsig_authorities: str = ""
    # Comma list of https URL templates of TAP key stores, each holding
    # "{keyid}" (for example https://keys.example/keys/{keyid}). Empty: no TAP
    # key resolves. Env: AGENTICORG_HTTPSIG_TAP_KEY_STORE_URLS.
    httpsig_tap_key_store_urls: str = ""
    # Comma list of https origins whose Web Bot Auth key directory
    # (/.well-known/http-message-signatures-directory) may be fetched for a
    # Signature-Agent. Empty: no Web Bot Auth key resolves.
    # Env: AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS.
    httpsig_signature_agent_origins: str = ""
    # Look a verified agent up in the Grantex registry to record its scheme
    # agent ids (G51). Off by default. Env: AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED.
    httpsig_registry_lookup_enabled: bool = False
    # Sign the Grantex Commerce buyer connector's outbound requests with the
    # tenant agent key under the TAP profile (G48). Off by default.
    # Env: AGENTICORG_HTTPSIG_SIGN_OUTBOUND.
    httpsig_sign_outbound: bool = False
    # AgenticOrg's own scheme agent ids, published in the A2A agent card (G51):
    # a JSON list of {"scheme": "visa_tap"|"mastercard_agent_pay", "agent_id": "..."}.
    # Empty: nothing is published. Env: AGENTICORG_A2A_SCHEME_AGENT_IDS.
    a2a_scheme_agent_ids: str = ""
    # Synthetic checks (observability/synthetic.py): the scheduled sweep runs
    # each tenant's due probes (model, knowledge, guardrail, audit chain) and
    # stores a result per run. Off by default; a check can be run by hand
    # through the API either way. Results are pruned after the retention period.
    synthetic_checks_enabled: bool = False
    synthetic_checks_retention_days: int = 30
    # A concurrency slot the model gateway hands out expires on its own after
    # this many seconds when the holder never releases it (a process that died).
    model_gateway_lease_seconds: int = 600
    # When enabled, a routed call refuses if the tenant's limit rows or Redis
    # admission state cannot be read. Off by default for existing tenants.
    model_gateway_limits_fail_closed: bool = False
    # Routing records: one signed row per model call while the gateway is on
    # for the tenant, pruned after the retention period by the daily task.
    # Off by default: turning it on adds a write to every routed model call.
    model_gateway_records_enabled: bool = False
    model_gateway_records_retention_days: int = 90
    # Cost-aware routing reads each model's observed failure rate over this
    # window from the routing records (cached briefly) and skips a model above
    # the failure rate a policy sets, or this default.
    model_gateway_quality_window_hours: int = 24
    model_gateway_max_failure_rate: float = 0.05
    model_gateway_health_cache_seconds: int = 60
    # Native dense + full-text rank fusion. Off until tenant retrieval quality
    # and index rollout have been verified; RAGFlow precedence is unchanged.
    knowledge_hybrid_search: bool = False
    # Re-ranking stage over the fused candidates of a knowledge search
    # (core/rag/rerank.py): the query's own terms decide the final order.
    # Off by default: off, the fused order is returned as it was.
    knowledge_rerank_enabled: bool = False
    # Query transformation and agentic retrieval (core/rag/query.py): a query
    # is normalised, decomposed and rewritten, a weak first pass is expanded
    # to the variants and fused, and the response can carry the trace.
    # Off by default: off, a search runs exactly as before.
    knowledge_query_transform_enabled: bool = False
    # A model (provider/model) that proposes alternative queries for the plan;
    # empty, the plan is deterministic only.
    knowledge_query_rewrite_model: str = ""
    # Graph retrieval (core/rag/entities.py): ingestion records the entities
    # each chunk mentions and a search fuses in the chunks the graph reaches
    # from the query. Off by default: off, ingestion writes no entity rows
    # and a search runs exactly as before.
    knowledge_graph_retrieval_enabled: bool = False
    # Retrieval quality metrics (core/rag/metrics.py): one figures-only row
    # and the Prometheus series per knowledge search, GET /knowledge/metrics
    # and the grounding indicator. Off by default: off, nothing is recorded.
    knowledge_metrics_enabled: bool = False
    # Incremental re-indexing (core/rag/reindex.py): POST /knowledge/reindex
    # re-embeds stale chunks and records missing entities, bounded per call.
    # Off by default: off, the endpoint is not found.
    knowledge_reindex_enabled: bool = False
    # Durable bounded recovery of transient upload-index failures on the existing worker.
    knowledge_upload_recovery_enabled: bool = False
    # AI asset inventory (core/governance/inventory.py): GET /governance/inventory
    # and its bill-of-materials export, tenant-admin only. Off by default: off,
    # the endpoints are not found and nothing is read.
    governance_inventory_enabled: bool = False
    # Model cards (core/governance/model_cards.py): one standard card per model
    # the tenant uses, with the administrator's part written and approved by a
    # second person. Off by default: off, the endpoints are not found.
    governance_model_cards_enabled: bool = False
    # Regulatory risk tiers (core/governance/risk_tiers.py): the controls a
    # tier forces at promotion, resume, tier change, update and gate removal,
    # and GET /governance/risk-tiers. Off by default: off, nothing runs.
    governance_risk_tiers_enabled: bool = False
    # Policy console (core/governance/policy_console.py): every policy in one
    # list, written through its own store and dry-run across the enforcement
    # points. Off by default: off, the endpoints are not found.
    governance_policy_console_enabled: bool = False
    # FinOps attribution (core/finops/attribution.py): a run binds its use
    # case, application, business unit, department and cost centre; the cost
    # write adds a row to finops_cost_ledger and the model call records carry
    # the labels. Off by default: off, nothing is written or tagged.
    finops_attribution_enabled: bool = False
    # FinOps thresholds (core/finops/thresholds.py): a breached organisation,
    # application, use-case or business-unit threshold alerts, throttles or
    # suspends runs through the agents API. Off by default: off, no run is
    # checked, delayed or refused. Thresholds read the attributed ledger, so
    # this needs finops_attribution_enabled; on without it, settings refuse
    # to load (validate_finops_flags).
    finops_thresholds_enabled: bool = False
    # FinOps forecast and comparison (core/finops/forecast.py): a projection
    # per use case from the attributed ledger and a model cost comparison from
    # the call records. Off by default: off, the endpoints are not found.
    finops_forecast_enabled: bool = False
    # Per-agent execution limits and loop detection (core/langgraph/limits.py):
    # an agent's own step, duration and tool-call limits and its loop rule are
    # enforced in the graph and the runner. Off by default: off, the platform
    # maxima alone apply, as today.
    runtime_limits_enabled: bool = False
    # Visual workflow builder (core/workflows/graph.py): POST /workflows refuses
    # a definition with problems (missing paths, checkpoints without options,
    # a failure directive outside the grammar). Off by default: off, creation
    # accepts what it accepted before; validate and graph answer regardless.
    workflow_builder_v2_enabled: bool = False
    # Long-term memory (core/memory/long_term.py): a run that names a subject
    # recalls what is remembered about it and stores what it asks to keep;
    # entries expire by their kind's retention and can be erased per subject.
    # Off by default: off, no run reads or writes memory.
    runtime_memory_enabled: bool = False
    # Tool registry (core/tool_gateway/registry.py): a registered tool's inputs
    # are checked against its schema before a call leaves the gateway, and the
    # call is held to its envelope. Off by default: off, no call is checked.
    tool_registry_enabled: bool = False
    # With the registry on, refuse calls to tools no registration covers.
    tool_registry_require_registration: bool = False
    # Debugging console (core/langgraph/debugger.py): an agent's breakpoints
    # pause its runs before a node; the console steps a paused run and reads
    # a run's checkpoints back step by step. Off by default: off, no run
    # pauses and the console endpoints are not found.
    runtime_debug_console_enabled: bool = False
    # Conversational services (core/conversation/): a chat message that names
    # a banking intent is handled as a dialogue turn (slots, clarification,
    # confirmation) and a confirmed action runs the bound tool under the
    # grant. Off by default: off, every message reaches the agent as before.
    conversation_v2_enabled: bool = False
    # Content services (core/content/): governed drafting, structured
    # summarisation and obligation extraction as APIs, each with a schema, a
    # guardrail profile and an evaluation dataset. Off by default: off, the
    # catalogue answers and every other route is not found.
    content_services_enabled: bool = False
    # The model the content services call through the direct router; empty
    # means the router's default.
    content_services_model: str = ""
    # Intelligent document processing (core/idp/): a PDF or image split into
    # typed documents with fields, tables and boxes, routed to review by
    # confidence. Off by default: off, the document-type catalogue answers and
    # every other route is not found.
    idp_enabled: bool = False
    # Workbenches (core/workbench/): role-shaped consoles over the review,
    # conversation, document and governance pages, with per-user
    # assignments. Off by default: off, GET /workbench answers
    # ``enabled: false`` and every other workbench route is not found.
    workbench_v2_enabled: bool = False
    # Speech and conversation intelligence (core/speech/): recordings split
    # by speaker, transcribed and kept with the transcript encrypted. Off by
    # default: off, GET /speech/status answers ``enabled: false`` and every
    # other speech route is not found.
    speech_intelligence_enabled: bool = False
    # Transaction intelligence (core/txn/): movements aggregated by entity,
    # structuring and pass-through detectors, findings under human
    # disposition. Off by default: off, GET /txn/status answers
    # ``enabled: false`` and every other transaction route is not found.
    transaction_intelligence_enabled: bool = False
    # Provenance and lineage (core/lineage/): every kept thing traced to its
    # source, version and processing steps; ingestion notes its chain. Off by
    # default: off, GET /lineage/status answers ``enabled: false``, every other
    # lineage route is not found and ingestion notes nothing.
    lineage_enabled: bool = False
    # The periodic sweep that runs every tenant's due sync sources
    # (core/tasks/lineage_tasks.py). Off by default: a manual run and the
    # cron trigger still work while lineage itself is on.
    lineage_sync_sweep_enabled: bool = False
    # Personalisation (core/personalisation/): content rendered from a
    # subject's profile only under a valid consent for the purpose, with the
    # attributes used recorded. Off by default: off, GET /personalisation/status
    # answers ``enabled: false`` and every other personalisation route is not found.
    personalisation_enabled: bool = False
    # AI spend intelligence (core/spend/): an organisation tree, rate cards,
    # commitments and FX rates; a usage record with its attribution for every
    # model call and metered service, priced in the card's currency and in INR;
    # invoice reconciliation and the Gate 1 status. Off by default: off,
    # GET /spend/status answers ``enabled: false``, every other spend route is
    # not found and nothing is metered or written.
    spend_intelligence_enabled: bool = False
    # The calendar spend is reported in (an IANA zone): a record's event date,
    # the rollup day, the FX date and the Gate 1 attribution month.
    spend_reporting_timezone: str = "Asia/Kolkata"
    # JSON object provider -> IANA zone the provider closes its billing day and
    # month in. Unlisted providers use core/spend/vocab.py defaults, then UTC.
    spend_provider_billing_timezones_json: str = ""
    # The spend beat jobs (partitions, FX settlement, commitment recompute,
    # storage samples, GPU allocation). Only effective while spend is on.
    spend_sweeps_enabled: bool = True
    # In-house GPU pools whose node hours spend spreads over the pool's calls
    # (core/spend/gpu.py): a JSON list of {provider, node_pool, models, nodes,
    # from, to}. Empty: no standing node hours (an operator can still record
    # metered hours with python -m core.spend.gpu_cli).
    spend_gpu_pools_json: str = ""
    # GET /audit hides the commercial spend audit rows (rate cards,
    # commitments, invoices, reconciliations and spend jobs: contract prices,
    # committed amounts, invoice figures, job parameters and the amounts they
    # moved) from every reader who is not a human administrator or auditor,
    # as the spend routes refuse those readers. It applies whatever
    # spend_intelligence_enabled says, because audit rows are never deleted
    # and outlive the flag, and only for a tenant that keeps such rows. On by
    # default, a deliberate default recorded in CHANGELOG.md; false restores
    # the previous GET /audit behaviour for every reader, commercial rows
    # included. Env: AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER.
    spend_audit_commercial_filter: bool = True
    # JSON object keyed provider/model with input and output USD per million
    # tokens; a negotiated rate replaces the list price.
    model_price_overrides_json: str = ""
    # The direct router costs its OpenAI and Anthropic calls at the model's
    # list price instead of the historical flat rates. Off by default: the
    # figures feed the cost counters and budget controls (FINDINGS A-116).
    model_pricing_for_router_costs: bool = False
    # A workflow halted by an override is re-driven by the ``resume_halted_workflow``
    # task on this countdown until the override is released or the run is cancelled.
    operator_halt_retry_seconds: int = 30
    # The beat sweep that re-queues a halted run whose retrying process went
    # away (a broker outage followed by a restart). Off by default.
    operator_halt_recovery_sweep_enabled: bool = False
    # Residency enforcement: refuse providers and destinations outside the
    # tenant's data region unless attested. Off by default; the authority
    # flag ``residency.enforce`` turns it on per tenant, this switch for the
    # whole deployment. The deployment facts below feed the compliance report.
    residency_enforce: bool = False
    tenancy_profile: Literal["shared", "dedicated"] = "shared"
    dr_standby_region: str | None = None
    dr_last_drill_at: str | None = None
    # Operator-recorded verifications of the infrastructure controls the
    # compliance report lists (core/governance/infrastructure.py).
    infrastructure_attestations_file: str | None = None
    # A paused agent is refused by the run endpoint, chat and workflow agent
    # steps, not only left out of routing (FINDINGS A-110). Off by default
    # until the next minor.
    paused_agents_refused: bool = False
    # The bank-operated customer agent (core/customer_agent/, api/v1/customer_agent.py).
    # Off by default: off, every customer-agent route answers 404. On, the
    # bank's backend opens customer sessions with assertions signed by the
    # key at ``customer_agent_assertion_jwks_uri`` (issuer and audience must
    # match), and confirmed actions run through the configured agent of the
    # configured tenant and company under the autonomy ladder, whose highest
    # level any customer may set is ``customer_agent_max_autonomy`` (2 =
    # prepare and wait for one tap, until the bank raises it).
    customer_agent_enabled: bool = False
    customer_agent_tenant_id: str = ""
    customer_agent_company_id: str = ""
    customer_agent_agent_id: str = ""
    customer_agent_assertion_issuer: str = ""
    customer_agent_assertion_audience: str = ""
    customer_agent_assertion_jwks_uri: str = ""
    customer_agent_session_ttl_minutes: int = 30
    customer_agent_max_autonomy: int = 2
    customer_agent_disclosure: str = ""
    customer_agent_bank_earns_text: str = ""
    # Whether the permissions page opens a principal session on the authority
    # service to show the customer's outside agents (needs GRANTEX_API_KEY).
    customer_agent_outside_agents: bool = False
    # Whether the authentication middleware consults the authority service's
    # revocation feed before honouring a grant token: "off" (default) or
    # "feed". Off by default because it changes an existing authentication
    # path: on, an unknown state (no feed, stale feed, no GRANTEX_API_KEY)
    # refuses the token in a strict runtime and is allowed with a warning
    # elsewhere (auth/revocation.py). Agent status is checked either way.
    grantex_revocation_check: str = "off"
    # Authenticated routes reached with an auth_mode the auth middleware does
    # not set (FINDINGS A-95). Off, such a request is logged as
    # route_enforcement_unknown_auth_mode and then checked on whatever scopes
    # it carries, agenticorg:admin included, as before; on, it gets 403 before
    # any scope is read, in enforce and log mode alike.
    # Env: AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE.
    route_refuse_unknown_auth_mode: bool = False
    # Governed business cases (core/cases, flag governed_cases.enabled per tenant).
    # Verification provider new cases use, by registered name.
    case_provider: str = "mock"
    # Directory of case policy files; empty uses the shipped examples, which a
    # strict runtime refuses because they are not reviewed for production.
    case_policy_dir: str = ""
    # Model the reference agents use for prose (memo summaries, rationales).
    case_llm_model: str = ""
    # Decision grants for governed cases (PRD G-3): "" (off) or "grantex". Off by
    # default, and the case decision route then keeps refusing with
    # decision_required. The human approval always happens on the Grantex auth
    # service's own approval page, never in this console.
    case_decision_service: str = ""
    # Connector name the decision request is made under at the issuer.
    case_decision_connector: str = "governed_cases"
    # Decisions that need two different approvers (comma separated, from the
    # tool manifest's four_eyes_on).
    case_decision_four_eyes_on: str = "decline"
    # Consume a case decision at the issuer by its request id
    # (POST /v1/decisions/requests/{id}/consume), so this server never presents,
    # stores or forwards a decision grant, and read readiness from
    # decisionGrantsReady. Until the issuer turns DECISION_GRANT_AGENT_BINDING on,
    # its status answer still carries the grants, which are dropped; once it is
    # on, none is sent here at all. Off keeps reading the grants from the
    # request's status and presenting them, which stops working once the binding
    # is on: turn this on first (docs/governance/decision-requests.md).
    # Env: AGENTICORG_CASE_DECISION_GRANT_RELEASE.
    case_decision_grant_release: bool = False
    # Periodic delivery sweep for the case push outbox (core/tasks/case_push_tasks.py).
    case_push_sweep_enabled: bool = False
    jwt_public_key_url: str = ""
    jwt_issuer: str = ""  # Grantex token server issuer URI (AGENTICORG_JWT_ISSUER)
    token_ttl_minutes: int = 60
    # Per-IP throttles key on the first X-Forwarded-For hop instead of the
    # socket peer (api/client_ip.py). Enable ONLY behind a trusted reverse
    # proxy (Cloud Run / nginx) that overwrites the header; otherwise the
    # throttle bucket is client-spoofable. Env: AGENTICORG_TRUST_PROXY_HEADERS.
    trust_proxy_headers: bool = False
    # Email event webhooks (api/v1/webhooks.py). Every tenant has its own
    # SendGrid, Mailchimp and MoEngage URL whose path token binds the tenant
    # (GET /api/v1/email-webhook-inbox). On, the shared URLs
    # /webhooks/email/{provider} refuse with 409 any delivery with an event
    # that names a tenant in its payload, so a signed event can no longer pick
    # the tenant whose workflow waits it resumes. Off keeps the shared URLs as
    # they were. Turn it on once every tenant's provider posts to its own URL
    # (docs/RUNBOOKS.md). Env: AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS.
    webhooks_tenant_bound_paths: bool = False

    # Google OAuth
    google_oauth_client_id: str = ""  # Google Cloud Console OAuth 2.0 Client ID

    # CORS
    cors_allowed_origins: str = ""  # Comma-separated origins; empty = allow all in dev

    # Public-facing API base URL — single source of truth for OAuth redirect
    # URIs. Required for every non-relaxed env because Cloud Run terminates
    # TLS at the edge, so ``request.url_for`` inside the container returns
    # ``http://`` and the resulting redirect_uri never matches the
    # ``https://`` value registered on Zoho / Google / etc.
    # Must include scheme + host (and optionally a path prefix).
    public_api_base_url: str = ""

    # Public URL of the web UI (scheme + host, optional path prefix, no
    # trailing slash). Used to build browser redirects after SSO login when
    # the UI is served from a different origin than the API (Cloud Run splits
    # them). Empty means "same origin as the API" (local docker / dev proxy).
    # Env: AGENTICORG_UI_BASE_URL.
    ui_base_url: str = ""

    # Entry-point plugins (connectors/plugins.py). Off by default: loading a
    # plugin runs third-party code, so only allowlisted distributions load.
    # Env: AGENTICORG_PLUGIN_LOADING, AGENTICORG_PLUGIN_ALLOWLIST (comma list).
    plugin_loading: bool = False
    plugin_allowlist: str = ""

    # API base URL of the screening service behind the deprecated ``sanctions_api``
    # connector (connectors/ops/sanctions_api.py), used when a tenant's connector
    # config has no ``base_url`` of its own. Empty by default: the address is
    # deployment configuration, never code, and with neither set the connector
    # refuses every call. Env: AGENTICORG_SANCTIONS_API_BASE_URL.
    sanctions_api_base_url: str = ""

    # Grant enforcement on agent tool calls (auth/grant_enforcement.py,
    # docs/operations/grant-enforcement.md). Deployment default for
    # ``grants.enforce_closed``: ``off`` keeps the legacy behaviour, ``warn``
    # allows and records every call that would be denied, ``deny`` refuses it.
    # Tenants override it with the ``grants.enforce_closed.warn`` and
    # ``grants.enforce_closed.deny`` feature flags. An unknown value fails
    # startup. Env: AGENTICORG_GRANTS_ENFORCE_CLOSED.
    grants_enforce_closed: Literal["off", "warn", "deny"] = "off"
    # Opt-in separate delegated grant per server-created run authorization.
    # Enabling this also forces deny mode and refuses legacy static agent tokens.
    grants_isolate_run_tokens: bool = False
    # Lifetime requested for a per-run grant delegated from the root grant
    # (auth/token_pool.py). At least 300 s: a grant is handed out only with
    # max(120 s, 10%) left, so a shorter lifetime would mint on nearly every
    # call. Env: AGENTICORG_GRANTS_RUN_TOKEN_TTL_SECONDS.
    grants_run_token_ttl_seconds: int = Field(default=900, ge=300, le=86_400)

    # Evidence sink for governed cases: ``off`` records nothing; ``grantex`` posts each agent
    # run's run context, tool calls (with the grant they were authorised under), policy
    # evaluation, recommendation and dispositions to the Grantex evidence service
    # (``POST /v1/evidence/cases/{case}/records``), which exports the signed package. Uses the
    # explicit ``GRANTEX_BASE_URL`` and ``GRANTEX_API_KEY`` only; off by default. A failure to
    # record is logged and counted and never changes the case. Env:
    # AGENTICORG_CASE_EVIDENCE_SERVICE, AGENTICORG_CASE_EVIDENCE_TIMEOUT_SECONDS.
    case_evidence_service: Literal["off", "grantex"] = "off"
    case_evidence_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    # HITL conditions outside the grammar (core/langgraph/hitl_condition.py)
    # when an agent or SOP config is saved: "off" accepts them as before,
    # "warn" accepts them but logs and counts them, "reject" answers 422 with
    # the parse reason. Any other value fails startup.
    # Env: AGENTICORG_HITL_CONDITION_VALIDATION.
    hitl_condition_validation: Literal["off", "warn", "reject"] = "off"

    # LangGraph checkpoint store (core/langgraph/checkpointer.py). "memory"
    # keeps paused runs in process memory, lost on restart; "postgres" stores
    # them encrypted in the Alembic-managed checkpoint tables and refuses to
    # run agents when that store is unreachable (no fallback to memory).
    # Env: AGENTICORG_LANGGRAPH_CHECKPOINTER, AGENTICORG_LANGGRAPH_CHECKPOINT_*.
    langgraph_checkpointer: Literal["memory", "postgres"] = "memory"
    # Empty derives the libpq URL from db_url.
    langgraph_checkpoint_db_url: str = ""
    langgraph_checkpoint_pool_max_size: int = Field(default=5, ge=1, le=100)
    langgraph_checkpoint_connect_timeout_seconds: float = Field(default=10.0, ge=0.5, le=120.0)

    # Platform behaviour
    pii_masking: bool = True
    data_region: str = "IN"
    audit_retention_years: int = 7
    max_concurrent_workflows: int = 500
    default_hitl_threshold_inr: int = 500_000
    default_confidence_floor: float = 0.88
    max_agent_retries: int = 3

    # Optional semantic decision layer. ``off`` preserves current behavior;
    # ``shadow`` records comparison data without changing execution; ``active``
    # is reserved for a later, explicitly reviewed integration.
    jev_mode: Literal["off", "shadow", "active"] = "off"
    jev_timeout_seconds: float = Field(default=0.8, ge=0.1, le=10.0)
    jev_shadow_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    jev_shadow_max_calls_per_process: int = Field(default=100, ge=0, le=100_000)
    jev_shadow_failure_threshold: int = Field(default=3, ge=1, le=20)
    jev_shadow_cooldown_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)

    # Resource-intensive local runtimes. These per-worker caps prevent one API
    # process from spawning unbounded Tesseract and Chromium processes.
    document_extraction_max_concurrency: int = Field(default=2, ge=1, le=32)
    document_extraction_queue_timeout_seconds: float = Field(default=30.0, ge=0.1, le=300.0)
    rpa_max_concurrency: int = Field(default=2, ge=1, le=16)
    rpa_queue_timeout_seconds: float = Field(default=5.0, ge=0.1, le=120.0)

    # SEC-012: every environment except local / dev / test is treated
    # as strict. Staging is internet-accessible, used for demos, pilots,
    # and enterprise security review — weak staging secrets become real
    # incidents when staging has production-like integrations.
    _STRICT_ENVS = STRICT_ENVS
    _RELAXED_ENVS = RELAXED_ENVS

    @model_validator(mode="after")
    def validate_finops_flags(self) -> Settings:
        """Refuse thresholds without attribution: the ledger they read would never be written.

        With only ``AGENTICORG_FINOPS_THRESHOLDS_ENABLED`` on, every spend
        query reads zero and a suspend threshold would never refuse a run;
        the deployment fails closed at start instead, in every environment.
        """
        if self.finops_thresholds_enabled and not self.finops_attribution_enabled:
            raise ValueError(
                "AGENTICORG_FINOPS_THRESHOLDS_ENABLED needs AGENTICORG_FINOPS_ATTRIBUTION_ENABLED: "
                "thresholds compare the attributed cost ledger, which is written only while attribution is on"
            )
        return self

    @model_validator(mode="after")
    def validate_spend_settings(self) -> Settings:
        """Refuse spend calendars that cannot load and GPU pools that do not parse, only while spend is on.

        Off, nothing here is read, so the settings are not checked and nothing
        about start-up changes.
        """
        if not self.spend_intelligence_enabled:
            return self
        if not _loadable_zone(self.spend_reporting_timezone):
            raise ValueError("spend_reporting_timezone: not a loadable IANA time zone")
        raw = (self.spend_provider_billing_timezones_json or "").strip()
        if raw:
            try:
                zones = from_json(raw)
            except (ValueError, RecursionError) as exc:
                raise ValueError("spend_provider_billing_timezones_json: not valid JSON") from exc
            if not isinstance(zones, dict):
                raise ValueError("spend_provider_billing_timezones_json: must be a JSON object of provider to zone")
            for provider, zone in zones.items():
                if not isinstance(provider, str) or not provider.strip() or not _loadable_zone(zone):
                    raise ValueError(
                        "spend_provider_billing_timezones_json: every provider maps to a loadable IANA time zone"
                    )
        parse_spend_gpu_pools(self.spend_gpu_pools_json)
        return self

    @model_validator(mode="after")
    def validate_production_secret(self) -> Settings:
        """Reject development fallbacks in any non-relaxed environment.

        Strict envs (production, staging, preview) MUST set
        AGENTICORG_SECRET_KEY, AGENTICORG_DB_URL, AGENTICORG_REDIS_URL
        to explicit non-default values. Default placeholders, dev
        credentials, and localhost-pinned URLs all fail closed.

        Secret length is also enforced: minimum 32 chars (≈192 bits of
        entropy) so JWT/HMAC operations don't operate over weak keys.
        """
        is_strict = is_strict_runtime_env(self.env)
        if not is_strict:
            return self

        env_label = self.env  # preserve original casing in error msg
        if is_published_placeholder_secret(self.secret_key):
            raise ValueError(
                f"AGENTICORG_SECRET_KEY must be explicitly set in env={env_label!r} "
                "(placeholders published in this repository, including the default "
                "'dev-only-secret-key', are rejected outside local/dev/test)"
            )
        if len(self.secret_key) < 32:
            raise ValueError(
                f"AGENTICORG_SECRET_KEY must be at least 32 chars in env={env_label!r} "
                f"(got {len(self.secret_key)}); JWT/HMAC need ≥192 bits of entropy"
            )
        # Refuse to start with the dev DB URL — would silently use localhost
        # with default credentials, causing data loss or auth bypass.
        if "agenticorg_dev@localhost" in self.db_url:
            raise ValueError(
                f"AGENTICORG_DB_URL must be explicitly set in env={env_label!r} "
                "(detected dev fallback with localhost credentials)"
            )
        if "localhost" in self.redis_url or "127.0.0.1" in self.redis_url:
            raise ValueError(
                f"AGENTICORG_REDIS_URL must be explicitly set in env={env_label!r} (detected localhost fallback)"
            )
        # Uday CA-Firms 2026-05-14: OAuth redirect_uri was being computed
        # from ``request.url_for`` which returns ``http://`` on Cloud Run
        # (TLS terminated at the edge). When set, the value MUST be https
        # so the redirect URI we send to Zoho / Google / etc. matches what
        # the provider has registered. We do NOT block strict-env startup
        # on a missing value — the OAuth handler has a proxy-aware
        # fallback path that upgrades to https — but we DO refuse a
        # half-configured non-https value, which would silently downgrade.
        if self.public_api_base_url:
            parsed = urlsplit(self.public_api_base_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(
                    "AGENTICORG_PUBLIC_API_BASE_URL must start with https:// "
                    "and include a host. Got: "
                    f"{self.public_api_base_url!r}"
                )
        return self


class ExternalKeys(BaseSettings):
    """External API keys — separate class to avoid prefix collision."""

    model_config = {"env_file": ".env", "extra": "ignore"}

    google_gemini_api_key: str = ""  # Free at aistudio.google.com
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    grantex_client_id: str = ""
    grantex_client_secret: str = ""
    grantex_token_server: str = ""
    grantex_api_key: str = ""  # Grantex SDK API key
    grantex_base_url: str = "https://api.grantex.dev"  # Configurable for self-hosted
    # Root grant the platform delegates per-run agent grants from
    # (auth/token_pool.py). A credential: set it from a secret manager.
    # Env: GRANTEX_ROOT_GRANT_TOKEN.
    grantex_root_grant_token: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "agenticorg-production"
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "agenticorg-core"
    slack_bot_token: str = ""
    slack_hitl_channel: str = "#hitl-approvals"
    sendgrid_api_key: str = ""
    hitl_notification_email: str = ""
    pinelabs_plural_api_key: str = ""
    pinelabs_plural_api_secret: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_contact_email: str = "mailto:push@agenticorg.ai"
    typesafe_api_base_url: str = "https://api.typesafe.ai"
    typesafe_model: str = "jev-latest"


settings = Settings()
external_keys = ExternalKeys()


GRANTEX_PRODUCTION_BASE_URL = "https://api.grantex.dev"
GRANTEX_STAGING_BASE_URL = "https://api-staging.grantex.dev"
GRANTEX_PRODUCTION_ENVS = frozenset({"production", "prod"})
# Hosted non-production runtimes verify grant tokens against the Grantex
# staging issuer: every STRICT_ENVS label that is not production, plus
# ``uat`` (not listed in STRICT_ENVS, but ``is_strict_runtime_env`` treats
# it as a hosted runtime). Production, local/dev/test and unknown labels use
# the production issuer, so an unrecognised env never accepts staging tokens.
GRANTEX_STAGING_ENVS = (STRICT_ENVS - GRANTEX_PRODUCTION_ENVS) | frozenset({"uat"})


def grantex_base_url_for_env(env: str | None = None) -> str:
    """Return the Grantex API origin (SDK base URL and JWKS host).

    An explicit ``GRANTEX_BASE_URL`` (env var or ``.env``) wins; otherwise
    staging-like environments use the Grantex staging origin and everything
    else the production origin. Before bug sheet 2026-09-14 #13 every
    environment silently resolved to the production issuer.
    """
    explicit = os.getenv("GRANTEX_BASE_URL", "").strip() or (
        external_keys.grantex_base_url if "grantex_base_url" in external_keys.model_fields_set else ""
    )
    if explicit:
        return explicit.strip().rstrip("/")
    label = normalize_env(env if env is not None else settings.env)
    if label in GRANTEX_STAGING_ENVS and is_strict_runtime_env(label):
        return GRANTEX_STAGING_BASE_URL
    return GRANTEX_PRODUCTION_BASE_URL


def grantex_jwks_uri_for_env(env: str | None = None) -> str:
    """Return the JWKS URI grant tokens are verified against."""
    return f"{grantex_base_url_for_env(env)}/.well-known/jwks.json"


def grantex_issuer_for_env(env: str | None = None) -> str:
    """Return the expected ``iss`` claim for Grantex grant tokens.

    Precedence: ``AGENTICORG_GRANTEX_ISSUER`` (settings or raw env var), then
    the issuer the Grantex SDK derives from the per-environment JWKS URI
    (``GRANTEX_BASE_URL`` if set, else the env default above). Returns ``""``
    when the SDK is unavailable so callers fail closed.
    """
    configured_settings = globals().get("settings")
    configured = (
        str(getattr(configured_settings, "grantex_issuer", "") or "").strip()
        or os.getenv("AGENTICORG_GRANTEX_ISSUER", "").strip()
    )
    if configured:
        return configured.rstrip("/")
    try:
        from grantex._verify import _derive_issuer_from_jwks_uri
    # enterprise-gate: broad-except-ok reason=missing-grantex-sdk-does-not-enable-grantex-mode-empty-issuer-fails-closed
    except Exception:
        return ""
    return str(_derive_issuer_from_jwks_uri(grantex_jwks_uri_for_env(env))).rstrip("/")


def _strict_default_flag(name: str, env: str | None) -> bool:
    """A tri-state flag: the explicit setting wins; unset, on in strict runtimes (``is_strict_runtime_env``)."""
    configured_settings = globals().get("settings")
    explicit = getattr(configured_settings, name, None)
    if isinstance(explicit, bool):
        return explicit
    return is_strict_runtime_env(env if env is not None else getattr(configured_settings, "env", None))


def arbiter_require_verified_authority_enabled(env: str | None = None) -> bool:
    """Whether the arbiter refuses caller-asserted buyer authority (``..._REQUIRE_VERIFIED_AUTHORITY``)."""
    return _strict_default_flag("arbiter_require_verified_authority", env)


def arbiter_require_grantex_grant_enabled(env: str | None = None) -> bool:
    """Whether an allow needs a verified Grantex grant to be consumed (``..._REQUIRE_GRANTEX_GRANT``)."""
    return _strict_default_flag("arbiter_require_grantex_grant", env)


def arbiter_grantex_post_enabled(env: str | None = None) -> bool:
    """Whether arbiter decisions are posted to Grantex (``..._GRANTEX_POST_ENABLED``; unset: the grant flag)."""
    configured_settings = globals().get("settings")
    explicit = getattr(configured_settings, "arbiter_grantex_post_enabled", None)
    if isinstance(explicit, bool):
        return explicit
    return arbiter_require_grantex_grant_enabled(env)


def oacp_verify_strict_enabled(env: str | None = None) -> bool:
    """Return whether OACP artifact verification failures refuse caching.

    ``AGENTICORG_OACP_VERIFY_STRICT`` wins when set; otherwise strict runtimes
    (production, staging, preview and unknown labels) refuse and relaxed
    runtimes log the irregularity and continue.
    """
    configured_settings = globals().get("settings")
    explicit = getattr(configured_settings, "oacp_verify_strict", None)
    if isinstance(explicit, bool):
        return explicit
    return is_strict_runtime_env(env if env is not None else getattr(configured_settings, "env", None))
