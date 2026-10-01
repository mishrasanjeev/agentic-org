# SPDX-License-Identifier: Apache-2.0
"""Evidence records for the Grantex evidence service, from a governed case's agent runs.

Off by default (``AGENTICORG_CASE_EVIDENCE_SERVICE=grantex`` turns it on). After each
agent run the case runtime hands the run's record (``UnderwritingOutcome.case_record()`` or
``DispositionOutcome.case_record()``), the memo or the proposed disposition, and the case's
earlier agent records to :func:`records_for_run`, which builds the records the service takes
(``spec/evidence-package.md``: ``run_context``, ``tool_call``, ``policy_evaluation``,
``recommendation``, ``disposition``) and posts them with :class:`GrantexEvidenceService`.

Every tool call the gateway recorded becomes a ``tool_call`` record naming the run grant it
was authorised under (the ``grant_id`` the authorizer resolved), so the service can check it
against the grant's validity and the case's delegation chain. Evidence references
(``call_id``, ``provider``, ``record_id``, ``retrieved_at``) are resolved from the case's own
tool calls: a memo citation or a disposition comparison that names a provider record is tied
to the call that retrieved it. A policy input is recorded ``unsourced`` because the policy
engine does not keep per-path provenance (FINDINGS); its value is kept.

Recording is best effort for the case: a failure is logged and counted and never changes the
case's state, because the evidence service is a sink, not an authority. The demo and the
tests read back what was recorded.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from prometheus_client import Counter

from core.config import external_keys, settings
from core.tool_gateway.provider_gateway import canonical_sha256

logger = structlog.get_logger()

RECORDS_PATH = "/v1/evidence/cases/{case}/records"
EXPORT_PATH = "/v1/evidence/cases/{case}/export"
BATCH_SIZE = 100
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[!-~]{1,256}$")

_SECTION_STATUS = {"complete": "complete", "partial": "issues_found", "issues_found": "issues_found"}
_COMPARISON_RESULT = {
    "match": "match",
    "partial_match": "partial",
    "partial": "partial",
    "mismatch": "mismatch",
    "not_comparable": "not_available",
    "not_available": "not_available",
}
_COMPARISON_IDENTIFIERS = frozenset(
    {"address", "associated_entities", "date_of_birth", "name", "nationality", "registration_number"}
)
_DISPOSITION_OUTCOMES = frozenset({"escalate", "false_positive", "inconclusive", "true_match"})
_RECOMMENDATION_OUTCOMES = frozenset({"approve", "decline", "refer", "request_information"})

_evidence_records_total = Counter(
    "agenticorg_case_evidence_records_total",
    "Evidence records posted to the evidence service, by outcome",
    ["outcome"],
)


class EvidenceServiceError(RuntimeError):
    """The evidence service refused or could not be reached; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "", *, status: int = 502) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.status = status


# ── timestamps and digests ──────────────────────────────────────────────────


def evidence_time(value: str | datetime | None, *, fallback: datetime | None = None) -> str:
    """``YYYY-MM-DDTHH:MM:SS.mmmZ``, the one form the service accepts."""
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = fallback or datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.") + f"{parsed.microsecond // 1000:03d}Z"


def digest_of(value: Any) -> str:
    """``sha256:<hex>`` over canonical JSON; a value that already is one is kept."""
    if isinstance(value, str) and _DIGEST.match(value):
        return value
    return canonical_sha256(value)


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def call_id_for(run_id: str, sequence: int) -> str:
    return f"{run_id}:{sequence}"


# ── evidence references from the case's own tool calls ──────────────────────


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    call_id: str
    provider: str
    record_id: str
    retrieved_at: str

    def to_dict(self, field_name: str | None = None) -> dict[str, Any]:
        ref = {
            "call_id": self.call_id,
            "provider": self.provider,
            "record_id": self.record_id,
            "retrieved_at": self.retrieved_at,
        }
        if field_name and _TOKEN.match(field_name):
            ref["field"] = field_name
        return ref


class RecordIndex:
    """Which tool call retrieved each provider record, across every run of the case."""

    def __init__(self) -> None:
        self._by_record: dict[tuple[str, str], EvidenceRef] = {}

    def add_run(self, run_id: str, tool_calls: Iterable[Mapping[str, Any]]) -> None:
        for call in tool_calls:
            if call.get("outcome") not in ("ok", "pending"):
                continue
            ref_base = (
                call_id_for(run_id, int(call["sequence"])),
                str(call["provider"]),
                evidence_time(call.get("started_at")),
            )
            for record_id in call.get("record_ids") or ():
                key = (ref_base[1], str(record_id))
                # The first retrieval is the one cited.
                self._by_record.setdefault(key, EvidenceRef(ref_base[0], ref_base[1], str(record_id), ref_base[2]))

    def resolve(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        provider = item.get("provider")
        record_id = item.get("record_id")
        if not isinstance(provider, str) or not isinstance(record_id, str):
            return None
        ref = self._by_record.get((provider, record_id))
        if ref is None:
            return None
        field_name = item.get("field")
        return ref.to_dict(field_name if isinstance(field_name, str) else None)

    def resolve_all(self, items: Iterable[Any]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            ref = self.resolve(item)
            if ref is None:
                continue
            key = (ref["call_id"], ref["record_id"], ref.get("field", ""))
            if key not in seen:
                seen.add(key)
                refs.append(ref)
        return refs


def index_for(agent_records: Iterable[Mapping[str, Any]]) -> RecordIndex:
    index = RecordIndex()
    for record in agent_records:
        run_id = record.get("run_id")
        if isinstance(run_id, str) and run_id:
            index.add_run(run_id, record.get("tool_calls") or ())
    return index


# ── record builders ─────────────────────────────────────────────────────────


def tool_call_records(run_id: str, purpose: str, tool_calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One ``tool_call`` per gateway record. Calls without a resolved grant id are skipped
    (the service requires one); allowed calls name their upstream records."""
    # A denial before the grant was resolved carries no grant id; the run's grant is used
    # for it when any call in the run resolved one.
    run_grant = next((str(c["grant_id"]) for c in tool_calls if c.get("grant_id")), "")
    records: list[dict[str, Any]] = []
    for call in tool_calls:
        grant_id = str(call.get("grant_id") or run_grant)
        if not grant_id:
            logger.warning("governed_case_evidence_call_skipped", run_id=run_id, tool=call.get("tool"))
            continue
        started = evidence_time(call.get("started_at"))
        gateway_outcome = str(call.get("outcome", ""))
        if gateway_outcome in ("ok", "pending"):
            outcome = "allowed"
        elif gateway_outcome == "denied":
            outcome = "denied"
        else:
            outcome = "error"
        data: dict[str, Any] = {
            "call_id": call_id_for(run_id, int(call["sequence"])),
            "connector": str(call["provider"]),
            "provider": str(call["provider"]),
            "tool": str(call["tool"]),
            "grant_id": grant_id,
            "purpose": purpose or None,
            "run_id": run_id,
            "started_at": started,
            "input_hash": digest_of(call.get("input_sha256") or {}),
            "output_hash": None,
            "outcome": outcome,
            "upstream_records": [],
        }
        if outcome == "allowed":
            data["output_hash"] = digest_of(call.get("output_sha256") or {})
            seen: set[str] = set()
            for record_id in call.get("record_ids") or ():
                if record_id in seen:
                    continue
                seen.add(record_id)
                data["upstream_records"].append({"record_id": str(record_id), "retrieved_at": started})
        elif outcome == "denied":
            data["denial"] = {"reason": str(call.get("reason") or "grant_denied")}
        records.append({"type": "tool_call", "at": started, "data": data})
    return records


def run_context_record(run_record: Mapping[str, Any], *, at: str) -> dict[str, Any]:
    policy = run_record.get("policy_result") or {}
    prompt = run_record.get("prompt")
    agent = str(run_record.get("agent") or "agent")
    data: dict[str, Any] = {
        "run_id": str(run_record["run_id"]),
        "agent_id": agent,
        "model": {
            "name": str(run_record.get("model_id") or "default"),
            "provider": "agenticorg",
            "version": str(run_record.get("agent_version") or "0"),
        },
        "prompts": [],
        "policies": [],
        "schemas": [],
    }
    if isinstance(prompt, Mapping) and prompt:
        data["prompts"].append(
            {"id": f"{agent}.prompt", "version": str(prompt.get("version") or "0"), "digest": digest_of(prompt)}
        )
    if isinstance(policy, Mapping) and policy.get("policy_id"):
        data["policies"].append(
            {
                "id": str(policy["policy_id"]),
                "version": str(policy.get("policy_version") or "0"),
                "digest": digest_of(policy.get("policy_hash") or policy),
            }
        )
    return {"type": "run_context", "at": at, "data": data}


def policy_evaluation_record(run_id: str, policy: Mapping[str, Any], *, at: str) -> dict[str, Any]:
    tier = str(policy.get("tier") or "low")
    inputs = [
        {"path": str(path), "value": _scalar(value), "evidence": [], "unsourced": True}
        for path, value in sorted((policy.get("inputs") or {}).items())
    ]
    fired = [
        {
            "rule_id": str(reason["rule_id"]),
            "reason_code": str(reason["rule_id"]),
            "tier": str(reason.get("tier") or tier),
        }
        for reason in policy.get("reasons") or ()
        if isinstance(reason, Mapping) and reason.get("rule_id")
    ]
    data = {
        "evaluation_id": f"{run_id}:policy",
        "run_id": run_id,
        "policy": {
            "id": str(policy.get("policy_id") or "policy"),
            "version": str(policy.get("policy_version") or "0"),
            "digest": digest_of(policy.get("policy_hash") or policy),
        },
        "score": policy.get("score") if isinstance(policy.get("score"), int | float) else 0,
        "tier": tier if tier in ("low", "medium", "high", "blocked") else "low",
        "fired_rules": fired,
        "inputs": inputs,
    }
    return {"type": "policy_evaluation", "at": at, "data": data}


def recommendation_record(
    run_id: str, memo: Mapping[str, Any], *, evaluation_id: str, index: RecordIndex, at: str
) -> dict[str, Any] | None:
    proposed = (memo.get("recommendation") or {}).get("proposed")
    if proposed not in _RECOMMENDATION_OUTCOMES:
        return None
    sections: list[dict[str, Any]] = []
    for section in memo.get("sections") or ():
        if not isinstance(section, Mapping) or not section.get("section_id"):
            continue
        refs = index.resolve_all(section.get("evidence") or ())
        status = _SECTION_STATUS.get(str(section.get("status")), "not_available")
        # A section that is not "not available" must cite what it read.
        if not refs:
            status = "not_available"
        sections.append({"section": str(section["section_id"]), "status": status, "evidence": refs})
    if not sections:
        return None
    data = {
        "recommendation_id": f"{run_id}:recommendation",
        "run_id": run_id,
        "evaluation_ids": [evaluation_id],
        "outcome": proposed,
        "memo_digest": digest_of(memo),
        "sections": sections[:256],
    }
    return {"type": "recommendation", "at": at, "data": data}


def disposition_record(
    run_id: str, disposition: Mapping[str, Any], *, index: RecordIndex, at: str
) -> dict[str, Any] | None:
    outcome = disposition.get("proposed_outcome")
    band = disposition.get("confidence_band")
    hit_refs = index.resolve_all(disposition.get("evidence") or ())
    if outcome not in _DISPOSITION_OUTCOMES or band not in ("high", "medium", "low") or not hit_refs:
        logger.warning(
            "governed_case_evidence_disposition_skipped",
            run_id=run_id,
            outcome=outcome,
            band=band,
            hit_refs=len(hit_refs),
        )
        return None
    comparisons: list[dict[str, Any]] = []
    for comparison in disposition.get("comparisons") or ():
        if not isinstance(comparison, Mapping):
            continue
        identifier = str(comparison.get("identifier") or "")
        if identifier not in _COMPARISON_IDENTIFIERS:
            continue
        comparisons.append(
            {
                "identifier": identifier,
                "result": _COMPARISON_RESULT.get(str(comparison.get("result")), "not_available"),
                "evidence": index.resolve_all(comparison.get("evidence") or ()),
            }
        )
    data = {
        "disposition_id": str(disposition.get("disposition_id") or f"{run_id}:disposition"),
        "run_id": run_id,
        "hit": hit_refs[0],
        "outcome": outcome,
        "confidence_band": band,
        "rationale_digest": digest_of(disposition.get("rationale") or ""),
        "comparisons": comparisons[:64],
    }
    return {"type": "disposition", "at": at, "data": data}


def records_for_run(
    run_record: Mapping[str, Any],
    *,
    purpose: str,
    prior_records: Iterable[Mapping[str, Any]] = (),
    memo: Mapping[str, Any] | None = None,
    disposition: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every evidence record for one agent run, in the order the service needs them."""
    run_id = str(run_record["run_id"])
    tool_calls = [c for c in (run_record.get("tool_calls") or ()) if isinstance(c, Mapping)]
    first_started = evidence_time(tool_calls[0].get("started_at") if tool_calls else None, fallback=now)
    last_started = evidence_time(tool_calls[-1].get("started_at") if tool_calls else None, fallback=now)
    index = index_for([*prior_records, run_record])
    records = [run_context_record(run_record, at=first_started)]
    records.extend(tool_call_records(run_id, purpose, tool_calls))
    policy = run_record.get("policy_result")
    if isinstance(policy, Mapping) and policy:
        evaluation = policy_evaluation_record(run_id, policy, at=last_started)
        records.append(evaluation)
        if memo:
            recommendation = recommendation_record(
                run_id, memo, evaluation_id=evaluation["data"]["evaluation_id"], index=index, at=last_started
            )
            if recommendation is not None:
                records.append(recommendation)
    if disposition:
        record = disposition_record(run_id, disposition, index=index, at=last_started)
        if record is not None:
            records.append(record)
    return records


# ── the service client ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExportedEvidence:
    data: bytes
    root: str
    anchor_hash: str


@dataclass
class GrantexEvidenceService:
    """``POST /v1/evidence/cases/{case}/records`` and ``/export`` on the Grantex service."""

    base_url: str
    api_key: str
    timeout_seconds: float = 10.0
    client_factory: Callable[[], httpx.AsyncClient] | None = None
    _clients: dict[int, httpx.AsyncClient] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.base_url or not self.api_key:
            raise EvidenceServiceError(
                "evidence_service_not_configured", "GRANTEX_BASE_URL and GRANTEX_API_KEY", status=503
            )

    def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory()
        loop_id = id(asyncio.get_running_loop())
        client = self._clients.get(loop_id)
        if client is None:
            client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                timeout=httpx.Timeout(self.timeout_seconds),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                follow_redirects=False,
            )
            self._clients[loop_id] = client
        return client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    async def record(self, case_ref: str, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Post the records in batches of at most 100, in order; returns the service's receipts."""
        receipts: list[dict[str, Any]] = []
        path = RECORDS_PATH.format(case=httpx.URL(path=case_ref).path.strip("/") or case_ref)
        for start in range(0, len(records), BATCH_SIZE):
            batch = list(records[start : start + BATCH_SIZE])
            response = await self._post(path, {"records": batch})
            if response.status_code not in (200, 201):
                raise _refusal(response)
            body = response.json()
            receipts.extend(body.get("records") or [])
        return receipts

    async def export(self, case_ref: str, *, sign: bool = True) -> ExportedEvidence:
        response = await self._post(EXPORT_PATH.format(case=case_ref), {"disclose": [], "sign": sign})
        if response.status_code != 200:
            raise _refusal(response)
        root = response.headers.get("grantex-evidence-root", "")
        anchor = response.headers.get("grantex-evidence-anchor", "")
        if not _DIGEST.match(root) or not re.match(r"^[0-9a-f]{64}$", anchor):
            raise EvidenceServiceError("evidence_export_headers_invalid", "root or anchor header missing")
        return ExportedEvidence(data=response.content, root=root, anchor_hash=anchor)

    async def _post(self, path: str, body: Mapping[str, Any]) -> httpx.Response:
        try:
            return await self._client().post(path, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise EvidenceServiceError("evidence_service_unavailable", type(exc).__name__, status=502) from exc


def _refusal(response: httpx.Response) -> EvidenceServiceError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("code") if isinstance(body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None
    detail = f"{message or response.text[:200]}"
    if isinstance(body, dict) and body.get("field_path"):
        detail += f" at {body['field_path']}"
    return EvidenceServiceError(str(code or "evidence_service_refused"), detail, status=response.status_code)


def evidence_service() -> GrantexEvidenceService | None:
    """The configured evidence sink, or ``None`` when ``AGENTICORG_CASE_EVIDENCE_SERVICE`` is off.

    As the decision service, it uses only an explicit ``GRANTEX_BASE_URL`` and the
    ``GRANTEX_API_KEY`` the platform already holds; it never falls back to the hosted service.
    """
    if settings.case_evidence_service in ("", "off", "none"):
        return None
    base_url = os.getenv("GRANTEX_BASE_URL", "").strip() or (
        external_keys.grantex_base_url if "grantex_base_url" in external_keys.model_fields_set else ""
    )
    api_key = os.getenv("GRANTEX_API_KEY", "").strip() or external_keys.grantex_api_key
    if not base_url:
        raise EvidenceServiceError(
            "evidence_service_not_configured", "GRANTEX_BASE_URL must be set explicitly", status=503
        )
    return GrantexEvidenceService(
        base_url=base_url, api_key=api_key, timeout_seconds=settings.case_evidence_timeout_seconds
    )


async def record_run_evidence(
    service_factory: Callable[[], GrantexEvidenceService | None],
    *,
    case_ref: str,
    purpose: str,
    run_record: Mapping[str, Any],
    prior_records: Iterable[Mapping[str, Any]] = (),
    memo: Mapping[str, Any] | None = None,
    disposition: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record one run's evidence; ``None`` when the sink is off. Never raises: the case's own
    state is not the sink's to change, so a failure is logged and counted."""
    try:
        service = service_factory()
    # enterprise-gate: broad-except-ok reason=evidence-sink-configuration-must-not-fail-the-case
    except Exception as exc:
        logger.error("governed_case_evidence_unavailable", case_ref=case_ref, error=type(exc).__name__)
        _evidence_records_total.labels(outcome="unavailable").inc()
        return None
    if service is None:
        return None
    records = records_for_run(
        run_record, purpose=purpose, prior_records=prior_records, memo=memo, disposition=disposition
    )
    try:
        receipts = await service.record(case_ref, records)
    except EvidenceServiceError as exc:
        logger.error(
            "governed_case_evidence_failed",
            case_ref=case_ref,
            run_id=run_record.get("run_id"),
            reason=exc.reason,
            detail=exc.detail,
        )
        _evidence_records_total.labels(outcome="failed").inc(len(records))
        return None
    _evidence_records_total.labels(outcome="recorded").inc(len(records))
    logger.info(
        "governed_case_evidence_recorded",
        case_ref=case_ref,
        run_id=run_record.get("run_id"),
        records=len(records),
    )
    return {"run_id": run_record.get("run_id"), "records": len(records), "receipts": receipts}


__all__ = [
    "EvidenceRef",
    "EvidenceServiceError",
    "ExportedEvidence",
    "GrantexEvidenceService",
    "RecordIndex",
    "digest_of",
    "evidence_service",
    "evidence_time",
    "index_for",
    "record_run_evidence",
    "records_for_run",
    "tool_call_records",
]
