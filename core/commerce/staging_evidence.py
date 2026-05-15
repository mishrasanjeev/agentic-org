"""Redacted evidence helpers for Commerce Sales Agent real-staging runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SENSITIVE_KEY_PARTS = (
    "authorization",
    "token",
    "jwt",
    "passport",
    "idempotency",
    "credential",
    "secret",
    "payload",
)


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_value(child)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def safe_error_code(result: dict[str, Any]) -> str | None:
    code = result.get("error") or result.get("code")
    return str(code) if code else None


@dataclass
class EvidenceCase:
    name: str
    status: str
    tool_alias: str | None = None
    http_status: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    blocker: str | None = None


@dataclass
class StagingEvidence:
    run_mode: str
    grantex_host: str
    auth_source_env_name: str
    cases: list[EvidenceCase] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    no_provider_call_confirmation: bool = True

    def add_case(
        self,
        *,
        name: str,
        status: str,
        tool_alias: str | None = None,
        result: dict[str, Any] | None = None,
        blocker: str | None = None,
    ) -> None:
        self.cases.append(
            EvidenceCase(
                name=name,
                status=status,
                tool_alias=tool_alias,
                error_code=safe_error_code(result or {}),
                blocker=blocker,
            )
        )
        if tool_alias:
            self.tool_sequence.append(tool_alias)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_mode": self.run_mode,
            "grantex_host": self.grantex_host,
            "auth_source_env_name": self.auth_source_env_name,
            "cases": [case.__dict__ for case in self.cases],
            "tool_sequence": self.tool_sequence,
            "no_provider_call_confirmation": self.no_provider_call_confirmation,
            "redaction": {
                "auth_values_recorded": False,
                "passport_values_recorded": False,
                "idempotency_values_recorded": False,
                "provider_material_recorded": False,
                "raw_payloads_recorded": False,
            },
        }

    def write_markdown(self, path: str | Path) -> None:
        report_path = Path(path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        data = redact_value(self.as_dict())
        rows = [
            "| Case | Status | Tool | HTTP | Latency ms | Error | Blocker |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for case in self.cases:
            rows.append(
                "| "
                + " | ".join(
                    [
                        case.name,
                        case.status,
                        case.tool_alias or "",
                        "" if case.http_status is None else str(case.http_status),
                        "" if case.latency_ms is None else str(case.latency_ms),
                        case.error_code or "",
                        case.blocker or "",
                    ]
                )
                + " |"
            )

        report_path.write_text(
            "\n".join(
                [
                    "# Commerce Sales Agent Real-Staging Evidence",
                    "",
                    f"- Run mode: `{self.run_mode}`",
                    f"- Grantex host: `{self.grantex_host}`",
                    f"- Auth source env name: `{self.auth_source_env_name}`",
                    "- Secret values recorded: false",
                    "- Raw passports/JWTs recorded: false",
                    "- Request correlation values recorded: false",
                    "- Provider material recorded: false",
                    "- Raw request/response bodies recorded: false",
                    "",
                    "## Case Results",
                    "",
                    *rows,
                    "",
                    "## Redacted Summary",
                    "",
                    "```json",
                    json.dumps(data, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )
