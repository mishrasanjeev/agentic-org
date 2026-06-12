"""TRACES connector - finance.

TRACES (TDS Reconciliation Analysis and Correction Enabling System) is
separate from the Income Tax 26AS flow. The live portal requires a
deductor login and captcha/DSC ceremony, so the production-safe capability
here is deterministic offline reconciliation of downloaded TRACES rows
against the firm's books/Tally/Zoho deductions. Live downloads remain a
connector method and must run with configured credentials.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from connectors.framework.base_connector import BaseConnector


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"amount must be numeric, got {value!r}") from exc


def _first(row: dict[str, Any], *keys: str) -> Any:
    lower_map = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        value = lower_map.get(key.lower())
        if value not in (None, ""):
            return value
    return ""


def _normalize_entry(row: dict[str, Any], index: int, source: str) -> dict[str, Any]:
    pan = _upper(_first(row, "deductee_pan", "pan", "vendor_pan", "party_pan"))
    section = _upper(_first(row, "section", "tds_section", "section_code")).replace("SECTION", "").strip()
    amount = _decimal(_first(row, "tds_amount", "tds", "tax_deducted", "amount"))
    date_value = _clean(_first(row, "transaction_date", "payment_date", "deduction_date", "date"))
    challan = _upper(_first(row, "challan_serial", "challan_no", "challan_number", "csi_number"))
    bsr = _upper(_first(row, "bsr_code", "bank_bsr_code", "bsr"))
    certificate = _upper(_first(row, "certificate_number", "form16a_number", "traces_certificate"))

    missing = []
    if not pan:
        missing.append("deductee_pan")
    if not section:
        missing.append("section")
    if amount <= 0:
        missing.append("tds_amount")
    if missing:
        raise ValueError(f"{source} row {index} missing/invalid: {', '.join(missing)}")

    return {
        "id": _clean(_first(row, "id", "reference", "invoice_number")) or f"{source}-{index}",
        "row_number": index,
        "deductee_pan": pan,
        "section": section,
        "tds_amount": amount,
        "transaction_date": date_value,
        "challan_serial": challan,
        "bsr_code": bsr,
        "certificate_number": certificate,
        "vendor_name": _clean(_first(row, "vendor_name", "deductee_name", "party_name")),
        "raw": row,
    }


def _entry_key(entry: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        entry["deductee_pan"],
        entry["section"],
        entry.get("transaction_date") or "",
        entry.get("challan_serial") or "",
        entry.get("bsr_code") or "",
    )


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        **entry,
        "tds_amount": float(entry["tds_amount"]),
    }


class TracesConnector(BaseConnector):
    name = "traces"
    category = "finance"
    auth_type = "deductor_portal"
    base_url = "https://www.tdscpc.gov.in"
    rate_limit_rpm = 10

    def _register_tools(self) -> None:
        self._tool_registry["download_traces_statement"] = self.download_traces_statement
        self._tool_registry["validate_traces_rows"] = self.validate_traces_rows
        self._tool_registry["reconcile_tds_with_traces"] = self.reconcile_tds_with_traces
        self._tool_registry["get_mismatch_report"] = self.get_mismatch_report

    async def _authenticate(self) -> None:
        username = self._get_secret("username")
        tan = self._get_secret("tan")
        token = self._get_secret("access_token") or self._get_secret("api_key")
        self._auth_headers = {
            "X-TRACES-Username": username,
            "X-TRACES-TAN": tan,
            "Authorization": f"Bearer {token}",
        }

    async def download_traces_statement(self, **params: Any) -> dict[str, Any]:
        """Download a statement via a configured TRACES automation bridge.

        AgenticOrg does not fake a government portal session. If no bridge or
        credentials are configured, callers should use the offline
        reconciliation endpoint with a downloaded TRACES CSV/XLSX export.
        """
        if not self._client:
            return {
                "status": "not_connected",
                "message": "TRACES download requires configured deductor portal credentials.",
            }
        return await self._get("/deductor/tds-statement", params)

    async def validate_traces_rows(self, **params: Any) -> dict[str, Any]:
        """Validate downloaded TRACES/book rows before reconciliation."""
        rows = params.get("rows") or params.get("traces_statement") or params.get("traces_rows") or []
        source = _clean(params.get("source") or "traces") or "traces"
        normalized: list[dict[str, Any]] = []
        row_errors: list[dict[str, Any]] = []

        if not isinstance(rows, list):
            return {
                "status": "invalid",
                "summary": {"rows": 0, "valid": 0, "failed": 1},
                "rows": [],
                "errors": [{"row_number": 0, "error": "rows must be a list of objects"}],
            }

        for idx, row in enumerate(rows, start=1):
            try:
                normalized.append(_normalize_entry(dict(row), idx, source))
            except (TypeError, ValueError) as exc:
                row_errors.append({"source": source, "row_number": idx, "error": str(exc)})

        return {
            "status": "valid" if not row_errors else "invalid",
            "summary": {
                "rows": len(rows),
                "valid": len(normalized),
                "failed": len(row_errors),
            },
            "rows": [_public_entry(row) for row in normalized],
            "errors": row_errors,
        }

    async def reconcile_tds_with_traces(self, **params: Any) -> dict[str, Any]:
        expected_rows = params.get("expected_deductions") or params.get("books_rows") or []
        traces_rows = params.get("traces_statement") or params.get("traces_rows") or []
        tolerance = _decimal(params.get("tolerance_rupees", 1))

        expected: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []
        row_errors: list[dict[str, Any]] = []

        for idx, row in enumerate(expected_rows, start=1):
            try:
                expected.append(_normalize_entry(dict(row), idx, "books"))
            except ValueError as exc:
                row_errors.append({"source": "books", "row_number": idx, "error": str(exc)})
        for idx, row in enumerate(traces_rows, start=1):
            try:
                traces.append(_normalize_entry(dict(row), idx, "traces"))
            except ValueError as exc:
                row_errors.append({"source": "traces", "row_number": idx, "error": str(exc)})

        unmatched_traces = set(range(len(traces)))
        matched: list[dict[str, Any]] = []
        amount_mismatches: list[dict[str, Any]] = []
        missing_in_traces: list[dict[str, Any]] = []

        for exp in expected:
            candidates = [
                i
                for i in unmatched_traces
                if traces[i]["deductee_pan"] == exp["deductee_pan"]
                and traces[i]["section"] == exp["section"]
            ]

            keyed = [
                i
                for i in candidates
                if _entry_key(traces[i]) == _entry_key(exp)
                or (
                    exp.get("challan_serial")
                    and traces[i].get("challan_serial") == exp.get("challan_serial")
                )
            ]
            pool = keyed or candidates
            exact_index: int | None = None
            mismatch_index: int | None = None
            smallest_delta: Decimal | None = None

            for i in pool:
                delta = abs(traces[i]["tds_amount"] - exp["tds_amount"])
                if delta <= tolerance:
                    exact_index = i
                    smallest_delta = delta
                    break
                if smallest_delta is None or delta < smallest_delta:
                    mismatch_index = i
                    smallest_delta = delta

            if exact_index is not None:
                actual = traces[exact_index]
                unmatched_traces.remove(exact_index)
                matched.append({
                    "books": _public_entry(exp),
                    "traces": _public_entry(actual),
                    "amount_delta": float(smallest_delta or Decimal("0")),
                })
            elif mismatch_index is not None:
                actual = traces[mismatch_index]
                unmatched_traces.remove(mismatch_index)
                amount_mismatches.append({
                    "books": _public_entry(exp),
                    "traces": _public_entry(actual),
                    "amount_delta": float(abs(actual["tds_amount"] - exp["tds_amount"])),
                })
            else:
                missing_in_traces.append(_public_entry(exp))

        extra_in_traces = [_public_entry(traces[i]) for i in sorted(unmatched_traces)]
        summary = {
            "books_rows": len(expected_rows),
            "traces_rows": len(traces_rows),
            "matched": len(matched),
            "missing_in_traces": len(missing_in_traces),
            "extra_in_traces": len(extra_in_traces),
            "amount_mismatches": len(amount_mismatches),
            "row_errors": len(row_errors),
        }
        has_reconciliation_issues = bool(
            missing_in_traces or extra_in_traces or amount_mismatches or row_errors
        )
        return {
            "status": "needs_review" if has_reconciliation_issues else "reconciled",
            "summary": summary,
            "matched": matched,
            "missing_in_traces": missing_in_traces,
            "extra_in_traces": extra_in_traces,
            "amount_mismatches": amount_mismatches,
            "row_errors": row_errors,
        }

    async def get_mismatch_report(self, **params: Any) -> dict[str, Any]:
        reconciliation = await self.reconcile_tds_with_traces(**params)
        rows: list[dict[str, Any]] = []
        for bucket in ("missing_in_traces", "extra_in_traces"):
            for row in reconciliation[bucket]:
                rows.append({"type": bucket, **row})
        for item in reconciliation["amount_mismatches"]:
            rows.append({
                "type": "amount_mismatch",
                "books_id": item["books"]["id"],
                "traces_id": item["traces"]["id"],
                "deductee_pan": item["books"]["deductee_pan"],
                "section": item["books"]["section"],
                "books_tds_amount": item["books"]["tds_amount"],
                "traces_tds_amount": item["traces"]["tds_amount"],
                "amount_delta": item["amount_delta"],
            })
        return {
            "status": reconciliation["status"],
            "summary": reconciliation["summary"],
            "rows": rows,
        }
