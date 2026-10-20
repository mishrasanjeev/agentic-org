#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify a commerce ledger export offline: no database, no network.

Usage:
    python scripts/verify_ledger.py export.json
    python scripts/verify_ledger.py export.json --expected-head 1042:<64-hex>
    AUDIT_SECRET=... python scripts/verify_ledger.py export.json --audit-secret-env AUDIT_SECRET

The export comes from ``GET /api/v1/commerce/runtime/ledger/export``
(``core/commerce/ledger_export.py``). The verifier:

1. walks ``rows`` in file order and recomputes every chain link from the
   link before the range: a sequence that is not the next one is
   ``sequence_gap`` (a removed or reordered row), a ``chain_prev`` that is not
   the previous link is ``previous_link``, a ``chain_hash`` that does not
   recompute is ``link_hash`` (an edited row);
2. holds the chain against every anchor in the bundle (the stored anchor and
   each witnessed head) and against ``--expected-head``: an anchor beyond the
   last exported row of a range that should reach it is ``truncated``, a row
   at an anchor's sequence with another hash is ``anchor_mismatch``;
3. verifies each Grantex witness countersignature against the JWKS in the
   bundle (when present) and checks it names the stored head
   (``witness_countersignature``);
4. with ``--audit-secret-env``, checks every row's HMAC signature
   (``signature``). Without the secret, signatures are reported as not
   verifiable offline: the platform signs rows with a symmetric key.

It prints a JSON report and exits 0 when no break is found, 1 on the first
break, 2 when the file cannot be read as an export. The link arithmetic is
``core/governance/audit_chain_link.py`` and the JWS check
``core/commerce/oacp_jws.py``, loaded by path so nothing else of the
application (settings, database drivers) is imported.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

EXPORT_FORMAT = "agenticorg-commerce-ledger-export-v1"
WITNESS_TYP = "audit-witness+jwt"
ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise ImportError(relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


link = _load("_ledger_chain_link", "core/governance/audit_chain_link.py")


def _jws() -> ModuleType | None:
    try:
        return _load("_ledger_oacp_jws", "core/commerce/oacp_jws.py")
    except ImportError:
        return None


class Report:
    def __init__(self) -> None:
        self.first_break: dict[str, Any] | None = None
        self.checked = 0
        self.anchors_checked = 0
        self.witnesses_verified = 0
        self.signatures = "not_verifiable_offline"
        self.notes: list[str] = []

    def brk(self, seq: int | None, reason: str, detail: str = "", row_id: Any = None) -> None:
        if self.first_break is None:
            self.first_break = {"seq": seq, "reason": reason, "detail": detail, "row_id": row_id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "broken" if self.first_break else "verified",
            "rows_checked": self.checked,
            "anchors_checked": self.anchors_checked,
            "witness_countersignatures_verified": self.witnesses_verified,
            "row_signatures": self.signatures,
            "first_break": self.first_break,
            "notes": self.notes,
        }


def verify_bundle(bundle: Any, *, expected_head: tuple[int, str] | None = None, secret: bytes | None = None) -> Report:
    report = Report()
    if not isinstance(bundle, dict) or bundle.get("format") != EXPORT_FORMAT:
        report.brk(None, "format", "not an agenticorg commerce ledger export")
        return report
    chain = bundle.get("chain") or {}
    rows = bundle.get("rows") or []
    if chain.get("version") != link.CHAIN_VERSION:
        report.brk(None, "format", f"unsupported chain version {chain.get('version')!r}")
        return report
    from_seq = int(chain.get("from_seq") or 1)
    prev = chain.get("prev_link")
    if from_seq == 1:
        prev = link.GENESIS
    elif not isinstance(prev, str):
        report.brk(from_seq - 1, "sequence_gap", "the link before the range is missing")
        return report
    expected = from_seq
    by_seq: dict[int, str] = {}
    for row in rows:
        seq = row.get("chain_seq") if isinstance(row, dict) else None
        row_id = row.get("id") if isinstance(row, dict) else None
        if not isinstance(seq, int) or seq != expected:
            report.brk(expected, "sequence_gap", f"expected sequence {expected}, found {seq!r}", row_id)
            return report
        if row.get("chain_prev") != prev:
            report.brk(seq, "previous_link", "chain_prev is not the previous link", row_id)
            return report
        recomputed = link.link_hash(prev, row)
        if row.get("chain_hash") != recomputed:
            report.brk(seq, "link_hash", "the row or its signature changed after sealing", row_id)
            return report
        if secret is not None:
            signed = hmac.new(secret, link.canonical_audit_payload(row).encode(), hashlib.sha256).hexdigest()
            if not row.get("signature") or not hmac.compare_digest(signed, str(row.get("signature"))):
                report.brk(seq, "signature", "the row signature does not verify under the given secret", row_id)
                return report
        by_seq[seq] = recomputed
        prev = recomputed
        expected = seq + 1
        report.checked += 1
    last_seq = expected - 1
    if chain.get("to_seq") != last_seq:
        report.brk(last_seq + 1, "truncated", f"range claims to_seq {chain.get('to_seq')!r}, rows end at {last_seq}")
        return report
    if secret is not None:
        report.signatures = "verified"
    else:
        report.notes.append(
            "Row signatures are HMAC-SHA256 under the platform audit secret; they were not checked "
            "(pass --audit-secret-env). Every chain link was recomputed."
        )

    anchors: list[tuple[str, int, str]] = []
    anchor = (bundle.get("anchors") or {}).get("audit_chain_anchor")
    if isinstance(anchor, dict) and isinstance(anchor.get("seq"), int) and anchor.get("seq", 0) > 0:
        anchors.append(("audit_chain_anchor", int(anchor["seq"]), str(anchor.get("hash"))))
    witnesses = (bundle.get("anchors") or {}).get("witness_checkpoints") or []
    jwks = (bundle.get("grantex") or {}).get("jwks")
    issuer = (bundle.get("grantex") or {}).get("issuer")
    jws = _jws() if jwks else None
    for witness in witnesses:
        if not isinstance(witness, dict) or witness.get("status") != "witnessed":
            continue
        seq, head_hash = witness.get("head_seq"), witness.get("head_hash")
        if not isinstance(seq, int) or not isinstance(head_hash, str):
            continue
        if jws is not None:
            problem = _witness_problem(jws, jwks, issuer, witness, bundle.get("tenant_id"))
            if problem:
                report.brk(seq, "witness_countersignature", problem)
                return report
            report.witnesses_verified += 1
        anchors.append(("witness_checkpoint", seq, head_hash))
    if witnesses and jws is None:
        report.notes.append("Witness countersignatures were not checked: the bundle carries no Grantex JWKS.")
    if expected_head is not None:
        anchors.append(("expected_head", expected_head[0], expected_head[1]))

    head = chain.get("head") or {}
    head_seq = head.get("seq") if isinstance(head.get("seq"), int) else last_seq
    if last_seq > head_seq:
        report.brk(head_seq + 1, "anchor_mismatch", "rows run past the head the export names")
        return report
    if last_seq == head_seq and head_seq >= from_seq:
        anchors.append(("export_head", head_seq, str(head.get("hash"))))
    elif last_seq < head_seq:
        report.notes.append(f"Partial range: rows end at {last_seq}, the chain head was {head_seq}.")
    for name, seq, head_hash in anchors:
        if seq > head_seq:
            # A head someone else holds (stored anchor, Grantex witness, external log) is newer than
            # the chain this export claims: rows were cut from the end.
            report.brk(head_seq + 1, "truncated", f"{name} at {seq} is beyond the exported head {head_seq}")
            return report
        if seq < from_seq or seq > last_seq:
            continue
        report.anchors_checked += 1
        if by_seq.get(seq) != head_hash:
            report.brk(seq, "anchor_mismatch", f"{name} names another link at this sequence")
            return report
    return report


def _witness_problem(jws: ModuleType, jwks: Any, issuer: Any, witness: dict[str, Any], tenant_id: Any) -> str | None:
    try:
        claims = jws.verify_compact_jwt(witness.get("countersignature"), jwks, expected_typ=WITNESS_TYP)
    except Exception as exc:  # noqa: BLE001 - any refusal of the countersignature is the finding
        return f"countersignature does not verify: {exc}"
    expected = {
        "source": "agenticorg",
        "chain": "audit_log",
        "tenant_ref": str(tenant_id),
        "head_seq": witness.get("head_seq"),
        "head_hash": witness.get("head_hash"),
        "jti": witness.get("witness_id"),
    }
    for key, value in expected.items():
        if claims.get(key) != value:
            return f"countersignature claim {key} does not match the stored witness"
    if issuer and claims.get("iss") != issuer:
        return "countersignature issuer is not the pinned Grantex issuer"
    return None


def _expected_head(text: str | None) -> tuple[int, str] | None:
    if not text:
        return None
    seq, _, head_hash = text.partition(":")
    if not seq.isdigit() or len(head_hash) != 64:
        raise SystemExit("--expected-head must be SEQ:<64 hex characters>")
    return int(seq), head_hash.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a commerce ledger export offline.")
    parser.add_argument("export", help="path to the export JSON")
    parser.add_argument("--expected-head", help="a head SEQ:HASH taken from a log kept outside the platform")
    parser.add_argument("--audit-secret-env", help="name of an environment variable holding the audit secret")
    args = parser.parse_args(argv)
    try:
        bundle = json.loads(Path(args.export).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "unreadable", "error": str(exc)}))
        return 2
    secret = None
    if args.audit_secret_env:
        value = os.environ.get(args.audit_secret_env)
        if not value:
            print(json.dumps({"status": "unreadable", "error": "the variable named by --audit-secret-env is not set"}))
            return 2
        secret = value.encode()
    report = verify_bundle(bundle, expected_head=_expected_head(args.expected_head), secret=secret)
    print(json.dumps(report.to_dict(), indent=2))
    if report.first_break is not None and report.first_break.get("reason") == "format":
        return 2
    return 1 if report.first_break else 0


if __name__ == "__main__":
    sys.exit(main())
