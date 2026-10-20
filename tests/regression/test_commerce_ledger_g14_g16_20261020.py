# SPDX-License-Identifier: Apache-2.0
"""Phase 2 G14-G16 without a database: Grantex countersignature, witness checkpoints, offline export verification.

Each test replays a failure the work closes: an allow consumable without a
verified Grantex grant, a grant bound to another payload, a forked chain head,
an export whose edited, removed, reordered or truncated rows go unnoticed, and an
operator decision that could turn a refusal into an allow.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.commerce import arbiter_grantex
from core.commerce.arbiter import ArbiterContext, ConsentEvidence, MandateEvidence, decide
from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches
from core.governance import audit_witness
from core.governance.audit_chain_link import GENESIS, link_hash
from tests.grantex_service_fakes import FakeGrantexService
from tests.oacp_signing import ISSUER

TENANT = "22222222-2222-4222-8222-222222222222"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_trust_caches():
    reset_oacp_trust_caches()
    yield
    reset_oacp_trust_caches()


def _decision(outcome: str = "allow", *, buyer: str | None = "shopper-01", now: datetime | None = None) -> Any:
    moment = now or datetime.now(UTC)
    context = ArbiterContext(
        tenant_id=TENANT,
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        buyer_agent_id=buyer,
        now=moment,
        payload={"handoff_id": "h-1", "quantity": 1},
        amount_minor_units=None,
        currency=None,
        quantity=1,
        mandate=MandateEvidence(ref="mandate-1", hash="b" * 64, expires_at=moment + timedelta(hours=1)),
        consent=ConsentEvidence(ref="consent-1", status="active"),
    )
    decision = decide("purchase_handoff", context)
    if outcome == "allow":
        # The rule set refuses this sparse context; the countersignature path only needs an allow-shaped decision.
        decision = dataclasses.replace(decision, outcome="allow", failing_rule=None)
    return decision


@pytest.fixture()
def attached(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _attach(decision, *, jti, recorded_at):
        calls.append((decision.decision_id, jti))
        return True

    monkeypatch.setattr(arbiter_grantex, "attach_grant", _attach)
    return calls


# ---------------------------------------------------------------------------
# G16(a): arbiter decision countersignature
# ---------------------------------------------------------------------------


def test_contract_body_carries_six_evidence_keys_and_the_failing_rule_rules() -> None:
    refused = _decision("refuse")
    body = arbiter_grantex.arbiter_decision_body(refused)
    assert set(body) == {
        "decision_id",
        "tenant_id",
        "merchant_id",
        "seller_agent_id",
        "buyer_agent_id",
        "action",
        "action_hash",
        "outcome",
        "rules_evaluated",
        "failing_rule",
        "evidence",
        "decided_at",
        "policy_version",
    }
    assert set(body["evidence"]) == set(arbiter_grantex.EVIDENCE_KEYS)
    assert body["evidence"]["passport_ref"] is None
    assert body["outcome"] == "refuse" and body["failing_rule"] in body["rules_evaluated"]
    assert body["action_hash"] == refused.payload_hash
    allowed = arbiter_grantex.arbiter_decision_body(_decision("allow"))
    assert allowed["failing_rule"] is None
    assert allowed["decided_at"].endswith("Z")
    assert "buyer_authority_source" not in allowed["evidence"]


@pytest.mark.asyncio
async def test_allow_grant_is_verified_and_its_jti_attached(attached) -> None:
    fake = FakeGrantexService()
    decision = _decision("allow")
    result = await arbiter_grantex.countersign_decision(decision, client=fake.client(), post_enabled=True)
    assert result.status == "granted", result
    assert attached == [(decision.decision_id, result.jti)]
    [(path, body)] = fake.posted
    assert path == "/v1/commerce/oacp/arbiter-decisions" and body["decision_id"] == decision.decision_id


@pytest.mark.asyncio
async def test_refusal_is_recorded_and_never_gets_a_grant(attached) -> None:
    fake = FakeGrantexService()
    result = await arbiter_grantex.countersign_decision(_decision("refuse"), client=fake.client(), post_enabled=True)
    assert result.status == "recorded" and result.jti is None
    assert attached == []


@pytest.mark.parametrize(
    ("mode", "status", "reason"),
    [
        ("unreachable", "unavailable", "grantex_unreachable"),
        ("http_503", "unavailable", "grantex_http_503"),
        ("wrong_action_hash", "invalid", "action_hash_mismatch"),
        ("human_profile", "invalid", "approver_auth_not_policy_arbiter"),
        ("forged_signature", "invalid", "grant_signature_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_an_unusable_grant_leaves_the_allow_without_a_countersignature(attached, mode, status, reason) -> None:
    fake = FakeGrantexService(mode=mode)
    result = await arbiter_grantex.countersign_decision(_decision("allow"), client=fake.client(), post_enabled=True)
    assert (result.status, result.reason) == (status, reason)
    assert attached == []


@pytest.mark.asyncio
async def test_posting_off_or_without_a_buyer_is_skipped_without_a_call(attached) -> None:
    fake = FakeGrantexService()
    off = await arbiter_grantex.countersign_decision(_decision("allow"), client=fake.client(), post_enabled=False)
    no_buyer = await arbiter_grantex.countersign_decision(
        _decision("allow", buyer=None), client=fake.client(), post_enabled=True
    )
    assert (off.status, no_buyer.status) == ("skipped", "skipped")
    assert fake.posted == []


@pytest.mark.asyncio
async def test_unconfigured_grantex_is_unavailable(monkeypatch, attached) -> None:
    monkeypatch.delenv("GRANTEX_COMMERCE_BASE_URL", raising=False)
    monkeypatch.delenv("GRANTEX_COMMERCE_INTERNAL_TOKEN", raising=False)
    result = await arbiter_grantex.countersign_decision(_decision("allow"), post_enabled=True)
    assert (result.status, result.reason) == ("unavailable", "grantex_not_configured")


def test_grant_claims_must_bind_this_decision() -> None:
    decision = _decision("allow")
    now = datetime.now(UTC)
    good = {
        "iss": ISSUER,
        "aud": "urn:grantex:decision",
        "approver_auth": "policy_arbiter",
        "action_hash": decision.payload_hash,
        "decision_request": decision.decision_id,
        "sub": decision.buyer_agent_id,
        "jti": "dg_1",
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    assert arbiter_grantex.check_grant_claims(good, decision, issuer=ISSUER, now=now) is None
    cases = {
        "issuer_mismatch": {"iss": "https://other.example"},
        "audience_mismatch": {"aud": "urn:other"},
        "decision_request_mismatch": {"decision_request": "arbdec_other"},
        "subject_mismatch": {"sub": "someone-else"},
        "human_acr_present": {"acr": "urn:example:acr"},
        "grant_expired": {"exp": int((now - timedelta(seconds=1)).timestamp())},
        "grant_lifetime_exceeds_decision": {"exp": int((decision.decided_at + timedelta(hours=1)).timestamp())},
    }
    for reason, change in cases.items():
        assert arbiter_grantex.check_grant_claims({**good, **change}, decision, issuer=ISSUER, now=now) == reason


def test_guard_refuses_an_allow_without_a_grant_when_required() -> None:
    from core.commerce.arbiter_store import _refusal_for

    now = datetime.now(UTC)

    class Row:
        outcome = "allow"
        action = "pos_handoff"
        payload_hash = "a" * 64
        consumed_at = None
        expires_at = now + timedelta(minutes=5)
        grantex_grant_jti = None

    refusal = _refusal_for(Row(), action="pos_handoff", payload_hash="a" * 64, now=now, grant_required=True)
    assert refusal.code == "grantex_grant_missing"


@pytest.mark.parametrize(
    ("env", "explicit", "post_explicit", "require", "post"),
    [
        ("production", None, None, True, True),
        ("development", None, None, False, False),
        ("production", False, None, False, False),
        ("production", False, True, False, True),
        ("test", True, None, True, True),
    ],
)
def test_grant_flags_default_on_in_strict_runtimes(monkeypatch, env, explicit, post_explicit, require, post) -> None:
    from core import config

    monkeypatch.setattr(config.settings, "env", env)
    monkeypatch.setattr(config.settings, "arbiter_require_grantex_grant", explicit)
    monkeypatch.setattr(config.settings, "arbiter_grantex_post_enabled", post_explicit)
    assert config.arbiter_require_grantex_grant_enabled() is require
    assert config.arbiter_grantex_post_enabled() is post


# ---------------------------------------------------------------------------
# G16(b): witness checkpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def stored(monkeypatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def _store(tenant_id, body, **values):
        rows.append({"tenant_id": tenant_id, **body, **values})

    monkeypatch.setattr(audit_witness, "_store", _store)
    return rows


@pytest.mark.asyncio
async def test_witnessed_head_is_verified_and_stored(stored) -> None:
    fake = FakeGrantexService()
    tenant = uuid.UUID(TENANT)
    answer = await audit_witness.witness_head(
        tenant, head_seq=7, head_hash="c" * 64, anchored_at=datetime.now(UTC), client=fake.client(), enabled=True
    )
    assert answer["status"] == "witnessed"
    [row] = stored
    assert row["status"] == "witnessed" and row["witness_id"] == answer["witness_id"]
    assert row["countersignature"].count(".") == 2


@pytest.mark.asyncio
async def test_a_fork_is_an_irregularity_with_its_evidence_kept(stored, caplog) -> None:
    fake = FakeGrantexService()
    tenant = uuid.UUID(TENANT)
    client = fake.client()
    await audit_witness.witness_head(
        tenant, head_seq=7, head_hash="c" * 64, anchored_at=None, client=client, enabled=True
    )
    answer = await audit_witness.witness_head(
        tenant, head_seq=7, head_hash="d" * 64, anchored_at=None, client=client, enabled=True
    )
    assert answer["status"] == "fork"
    fork = stored[-1]
    assert fork["status"] == "fork"
    assert fork["fork_evidence"]["witnessed"]["head_hash"] == "c" * 64
    assert fork["fork_evidence"]["presented"]["head_hash"] == "d" * 64


@pytest.mark.asyncio
async def test_a_countersignature_for_another_head_is_refused(stored) -> None:
    fake = FakeGrantexService(mode="witness_wrong_hash")
    answer = await audit_witness.witness_head(
        uuid.UUID(TENANT), head_seq=3, head_hash="c" * 64, anchored_at=None, client=fake.client(), enabled=True
    )
    assert answer == {"status": "invalid", "reason": "head_hash_mismatch"}
    assert stored == []


@pytest.mark.asyncio
async def test_witness_is_off_by_default_and_never_raises_when_unreachable(stored) -> None:
    from core.config import settings

    assert settings.audit_witness_enabled is False
    off = await audit_witness.witness_head(uuid.UUID(TENANT), head_seq=1, head_hash="c" * 64, anchored_at=None)
    assert off["status"] == "skipped"
    down = await audit_witness.witness_head(
        uuid.UUID(TENANT),
        head_seq=1,
        head_hash="c" * 64,
        anchored_at=None,
        client=FakeGrantexService(mode="unreachable").client(),
        enabled=True,
    )
    assert down["status"] == "unavailable" and stored == []


# ---------------------------------------------------------------------------
# G15: export bundle and the offline verifier
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, seq: int, prev: str, *, resource_type: str = "commerce_ledger") -> None:
        self.id = uuid.UUID(int=seq)
        self.tenant_id = uuid.UUID(TENANT)
        self.event_type = "commerce.arbiter.decision"
        self.actor_type = "user"
        self.actor_id = "operator-1"
        self.agent_id = None
        self.workflow_run_id = None
        self.resource_type = resource_type
        self.resource_id = f"arbdec_{seq:040x}"
        self.action = "commerce.arbiter.decision"
        self.outcome = "allow"
        self.details = {"ledger": {"payload_hash": f"{seq:064x}", "note": "naïve ✓"}, "n": seq}
        self.trace_id = ""
        self.created_at = datetime(2026, 10, 20, 9, 0, tzinfo=UTC) + timedelta(seconds=seq)
        self.signature = f"{seq:064x}"
        self.chain_seq = seq
        self.chain_prev = prev
        self.chain_hash = link_hash(prev, self)
        self.sealed_at = self.created_at


def _bundle(count: int = 6, *, witness_seq: int | None = None) -> dict[str, Any]:
    from core.commerce.ledger_export import EXPORT_FORMAT, export_row

    rows: list[_Row] = []
    prev = GENESIS
    for seq in range(1, count + 1):
        row = _Row(seq, prev, resource_type="commerce_ledger" if seq % 2 else "tool_call")
        rows.append(row)
        prev = row.chain_hash
    exported = [export_row(row) for row in rows]
    witnesses = []
    if witness_seq is not None:
        witnesses.append(
            {
                "head_seq": witness_seq,
                "head_hash": rows[witness_seq - 1].chain_hash,
                "status": "witnessed",
                "witness_id": "awit_1",
                "countersignature": None,
                "anchored_at": None,
                "witnessed_at": None,
                "fork_evidence": None,
            }
        )
    bundle = {
        "format": EXPORT_FORMAT,
        "tenant_id": TENANT,
        "chain": {
            "version": "1",
            "from_seq": 1,
            "to_seq": count,
            "prev_link": GENESIS,
            "head": {"seq": count, "hash": prev},
            "row_count": count,
        },
        "rows": exported,
        "anchors": {"audit_chain_anchor": {"seq": count, "hash": prev}, "witness_checkpoints": witnesses},
        "signatures": {"algorithm": "HMAC-SHA256", "offline_verifiable": False},
        "grantex": {"issuer": None, "jwks": None},
    }
    return json.loads(json.dumps(bundle))  # exactly what a file round trip gives


def _verify(bundle: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import verify_ledger
    finally:
        sys.path.pop(0)
    return verify_ledger.verify_bundle(bundle, **kwargs).to_dict()


def test_export_rows_recompute_the_same_links_after_a_file_round_trip() -> None:
    from core.commerce.ledger_export import export_row

    row = _Row(1, GENESIS)
    exported = json.loads(json.dumps(export_row(row)))
    assert link_hash(GENESIS, exported) == row.chain_hash
    assert exported["commerce_ledger"] is True


def test_clean_export_verifies_offline() -> None:
    report = _verify(_bundle())
    assert report["status"] == "verified", report
    assert report["rows_checked"] == 6 and report["anchors_checked"] >= 1
    assert report["row_signatures"] == "not_verifiable_offline"


def test_edited_row_is_detected() -> None:
    bundle = _bundle()
    bundle["rows"][2]["details"]["n"] = 999
    assert _verify(bundle)["first_break"]["seq"] == 3
    assert _verify(bundle)["first_break"]["reason"] == "link_hash"


def test_removed_row_is_detected() -> None:
    bundle = _bundle()
    del bundle["rows"][3]
    bundle["chain"]["to_seq"] = 5
    assert _verify(bundle)["first_break"] == {
        "seq": 4,
        "reason": "sequence_gap",
        "detail": "expected sequence 4, found 5",
        "row_id": str(uuid.UUID(int=5)),
    }


def test_reordered_rows_are_detected() -> None:
    bundle = _bundle()
    bundle["rows"][1], bundle["rows"][2] = bundle["rows"][2], bundle["rows"][1]
    assert _verify(bundle)["first_break"]["reason"] == "sequence_gap"
    # Renumbering the swapped rows to hide the swap breaks the links instead.
    renumbered = _bundle()
    renumbered["rows"][1], renumbered["rows"][2] = renumbered["rows"][2], renumbered["rows"][1]
    renumbered["rows"][1]["chain_seq"], renumbered["rows"][2]["chain_seq"] = 2, 3
    assert _verify(renumbered)["first_break"]["reason"] in {"previous_link", "link_hash"}


def test_truncated_tail_is_detected_against_the_anchor() -> None:
    bundle = _bundle(witness_seq=6)
    cut = bundle["rows"][:4]
    bundle["rows"] = cut
    bundle["chain"]["to_seq"] = 4
    bundle["chain"]["head"] = {"seq": 4, "hash": cut[-1]["chain_hash"]}
    # The stored anchor is still the real head: rows were cut from the end.
    report = _verify(bundle)
    assert report["first_break"]["reason"] == "truncated" and report["first_break"]["seq"] == 5
    # Even with the stored anchor rewritten too, the witnessed head (and an external head) still catch it.
    bundle["anchors"]["audit_chain_anchor"] = {"seq": 4, "hash": cut[-1]["chain_hash"]}
    assert _verify(bundle)["first_break"]["reason"] == "truncated"
    bundle["anchors"]["witness_checkpoints"] = []
    full = _bundle()
    expected = (6, full["rows"][-1]["chain_hash"])
    assert _verify(bundle, expected_head=expected)["first_break"]["reason"] == "truncated"


def test_a_head_hash_that_disagrees_with_an_anchor_is_detected() -> None:
    bundle = _bundle()
    bundle["anchors"]["audit_chain_anchor"] = {"seq": 3, "hash": "0" * 63 + "1"}
    assert _verify(bundle)["first_break"] == {
        "seq": 3,
        "reason": "anchor_mismatch",
        "detail": "audit_chain_anchor names another link at this sequence",
        "row_id": None,
    }


def test_signatures_verify_with_the_secret_and_a_forged_one_is_detected() -> None:
    import hashlib
    import hmac as hmac_lib

    from core.governance.audit_chain_link import canonical_audit_payload

    secret = b"placeholder-audit-secret"
    rows = []
    prev = GENESIS
    for seq in (1, 2):
        row = _Row(seq, prev)
        row.signature = hmac_lib.new(secret, canonical_audit_payload(row).encode(), hashlib.sha256).hexdigest()
        row.chain_hash = link_hash(prev, row)
        rows.append(row)
        prev = row.chain_hash
    bundle = _bundle(2)
    from core.commerce.ledger_export import export_row

    bundle["rows"] = json.loads(json.dumps([export_row(r) for r in rows]))
    bundle["chain"]["head"] = {"seq": 2, "hash": prev}
    bundle["anchors"]["audit_chain_anchor"] = {"seq": 2, "hash": prev}
    assert _verify(bundle, secret=secret)["row_signatures"] == "verified"
    assert _verify(bundle, secret=b"another-secret")["first_break"]["reason"] == "signature"


def test_witness_countersignatures_verify_offline_with_the_bundled_jwks() -> None:
    from tests.oacp_signing import EcSigningKey, sign_compact_jwt

    key = EcSigningKey(kid="platform-test-1")
    bundle = _bundle(witness_seq=6)
    witness = bundle["anchors"]["witness_checkpoints"][0]
    claims = {
        "iss": ISSUER,
        "jti": "awit_1",
        "source": "agenticorg",
        "chain": "audit_log",
        "tenant_ref": TENANT,
        "head_seq": 6,
        "head_hash": witness["head_hash"],
        "anchored_at": "2026-10-20T09:00:00.000Z",
        "witnessed_at": "2026-10-20T09:00:01.000Z",
    }
    witness["countersignature"] = sign_compact_jwt(key, claims, typ="audit-witness+jwt")
    bundle["grantex"] = {"issuer": ISSUER, "jwks": {"keys": [key.public_jwk()]}}
    report = _verify(bundle)
    assert report["status"] == "verified" and report["witness_countersignatures_verified"] == 1
    forged = copy.deepcopy(bundle)
    forged["anchors"]["witness_checkpoints"][0]["countersignature"] = sign_compact_jwt(
        EcSigningKey(kid="platform-test-1"), claims, typ="audit-witness+jwt"
    )
    assert _verify(forged)["first_break"]["reason"] == "witness_countersignature"


def test_the_cli_verifies_from_a_file_without_importing_the_application(tmp_path) -> None:
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(_bundle()), encoding="utf-8")
    edited_bundle = _bundle()
    edited_bundle["rows"][0]["outcome"] = "refuse"
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(edited_bundle), encoding="utf-8")
    probe = (
        "import runpy, sys; sys.argv = ['verify_ledger.py', sys.argv[1]];\n"
        "try:\n    runpy.run_path(r'%s', run_name='__main__')\n"
        "except SystemExit as exc:\n"
        "    leaked = [m for m in sys.modules if m == 'core' or m.startswith(('core.', 'sqlalchemy', 'httpx'))]\n"
        "    print('LEAKED', leaked); raise\n" % (ROOT / "scripts" / "verify_ledger.py")
    )
    ok = subprocess.run(  # noqa: S603 - fixed interpreter, test-built probe and file
        [sys.executable, "-I", "-c", probe, str(clean)], capture_output=True, text=True, check=False
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "LEAKED []" in ok.stdout
    bad = subprocess.run(  # noqa: S603 - fixed interpreter, test-built probe and file
        [sys.executable, "-I", "-c", probe, str(edited)], capture_output=True, text=True, check=False
    )
    assert bad.returncode == 1 and '"reason": "link_hash"' in bad.stdout
    unreadable = tmp_path / "nope.json"
    unreadable.write_text("{", encoding="utf-8")
    assert (
        subprocess.run(  # noqa: S603 - fixed interpreter and repository script
            [sys.executable, str(ROOT / "scripts" / "verify_ledger.py"), str(unreadable)],
            capture_output=True,
            check=False,
        ).returncode
        == 2
    )


# ---------------------------------------------------------------------------
# G14: operator decisions annotate only; routes record their events; nothing waits
# ---------------------------------------------------------------------------


def test_operator_decision_body_has_no_outcome_or_execution_member() -> None:
    from pydantic import ValidationError

    from api.v1.commerce_ledger import OperatorDecisionCreate

    OperatorDecisionCreate(decision_kind="request_more_evidence")
    for forged in ({"outcome": "allow"}, {"allowed_to_execute": True}, {"consume": True}):
        with pytest.raises(ValidationError):
            OperatorDecisionCreate(decision_kind="request_more_evidence", **forged)
    for kind in ("approve_future_refresh_request", "block_unsafe_action", "allow"):
        with pytest.raises(ValidationError):
            OperatorDecisionCreate(decision_kind=kind)


def test_operator_annotation_record_is_storable_and_never_executable() -> None:
    from api.v1.commerce_ledger import build_operator_annotation
    from core.commerce.oacp_artifacts import _c6x8_decision_safe_for_storage

    class Row:
        decision_id = "arbdec_" + "a" * 40
        merchant_id = "merchant.example"
        seller_agent_id = "seller-01"
        buyer_agent_id = "shopper-01"
        failing_rule = "consent_active"
        payload_hash = "b" * 64
        decided_at = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

    record = build_operator_annotation(
        Row(),
        tenant_id=TENANT,
        decision_kind="escalate_to_human_support",
        reason_codes=["Needs Review"],
        reviewer_ref="operator_ref_" + "c" * 24,
        decided_at=datetime(2026, 10, 20, 9, 5, tzinfo=UTC),
    )
    stored = _c6x8_decision_safe_for_storage(record)
    assert stored["stored"] is True, stored
    assert record["review_packet_id"] == Row.decision_id
    assert record["allowed_to_execute"] is False and record["no_execution"] is True


def test_retention_guard_text_matches_the_migration() -> None:
    from core.commerce.oacp_retention import DELETE_DUE_MANIFEST_SQL, MANIFEST_RETENTION_GUARD_SQL

    migration = (ROOT / "migrations" / "versions" / "v6_z88_commerce_ledger.py").read_text(encoding="utf-8")
    assert MANIFEST_RETENTION_GUARD_SQL in migration
    assert DELETE_DUE_MANIFEST_SQL in migration
    assert (
        "SECURITY DEFINER" in DELETE_DUE_MANIFEST_SQL and "SET search_path = public, pg_temp" in DELETE_DUE_MANIFEST_SQL
    )


def test_v6z88_grants_the_app_role_least_privilege() -> None:
    """G13/G14: v6z88 closes its definer function to PUBLIC and its append-only tables to UPDATE/DELETE.

    Before, ``oacp_delete_due_manifest`` kept PostgreSQL's default PUBLIC EXECUTE (any role could
    call the owner-privileged delete), and the application role kept whatever row DML default
    privileges gave it on the append-only tables.
    """
    import importlib.util
    import re

    path = ROOT / "migrations" / "versions" / "v6_z88_commerce_ledger.py"
    spec = importlib.util.spec_from_file_location("v6z88_grants_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    grants = " ".join(module._APP_ROLE_GRANTS_SQL.split())
    fn = "oacp_delete_due_manifest(text, text)"
    revoke_public = f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC;"
    grant_app = f"GRANT EXECUTE ON FUNCTION {fn} TO agenticorg_app;"
    # PUBLIC loses EXECUTE on every database, before (outside) the role guard, so a database
    # migrated before the application role exists does not leave the function open to all roles.
    assert grants.startswith(f"DO $$ BEGIN {revoke_public} IF to_regrole('agenticorg_app') IS NOT NULL THEN")
    assert revoke_public in grants and grant_app in grants
    assert grants.index(revoke_public) < grants.index(grant_app)
    assert "TO PUBLIC" not in grants
    for table in ("audit_chain_witness_checkpoints", *module.OACP_APPEND_ONLY_TABLES, "commerce_arbiter_decisions"):
        assert f"REVOKE UPDATE, DELETE ON {table} FROM agenticorg_app;" in grants, table
        assert not re.search(rf"GRANT [^;]*\b(DELETE|UPDATE)\b[^(;]* ON {table} ", grants), table
    column_grant = (
        "GRANT UPDATE (consumed_at, grantex_grant_jti, grantex_recorded_at) "
        "ON commerce_arbiter_decisions TO agenticorg_app;"
    )
    assert column_grant in grants
    # Table-level REVOKE UPDATE also drops column grants, so the column grant must come after it.
    assert grants.index("REVOKE UPDATE, DELETE ON commerce_arbiter_decisions") < grants.index(column_grant)
    # The grants run last in upgrade(), after the function and the tables exist.
    upgrade = inspect.getsource(module.upgrade)
    assert upgrade.rstrip().endswith("op.execute(_APP_ROLE_GRANTS_SQL)")


def test_ledger_routes_record_their_declared_events() -> None:
    from fastapi.routing import APIRoute

    from api.main import app
    from api.route_enforcement import is_commerce_write_route
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import commerce_ledger

    source = inspect.getsource(commerce_ledger)
    writes = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/commerce/runtime/ledger"):
            continue
        for method in route.methods:
            if is_commerce_write_route(route.path, method):
                writes += 1
                event = getattr(route.endpoint, ROUTE_METADATA_ATTR)["audit_event"]
                assert source.count(f'"{event}"') >= 2, event
    assert writes == 3


def test_retention_task_is_registered_on_the_shared_celery_app() -> None:
    from core.tasks.celery_app import app

    assert "core.tasks.commerce_ledger_tasks" in app.conf.include
    entry = app.conf.beat_schedule["apply-oacp-retention"]
    assert entry["task"] == "core.tasks.commerce_ledger_tasks.apply_oacp_retention"


def test_no_g14_g16_path_waits_or_retries() -> None:
    from api.v1 import commerce_ledger
    from core.commerce import grantex_service, oacp_retention

    for module in (arbiter_grantex, grantex_service, audit_witness, oacp_retention, commerce_ledger):
        tree = ast.parse(inspect.getsource(module))
        calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        assert not any("sleep" in call or "wait_for" in call or call.endswith(".wait") for call in calls), module
        assert not any(isinstance(node, ast.While) for node in ast.walk(tree)), module
        assert "hitl" not in inspect.getsource(module).lower(), module
