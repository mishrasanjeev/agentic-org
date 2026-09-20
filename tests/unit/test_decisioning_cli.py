from __future__ import annotations

import json

from scripts.run_jev_shadow_evaluation import main


def test_cli_dry_run_is_provider_free_and_writes_redacted_plan(tmp_path, capsys) -> None:
    output = tmp_path / "jev-plan.json"

    assert main(["--dry-run", "--output", str(output)]) == 0

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["document_kind"] == "jev_shadow_evaluation_plan"
    assert document["plan"]["reporting"]["status"] == "not_run"
    assert document["plan"]["active_routing_enabled"] is False
    assert "TYPESAFE_API_KEY" not in output.read_text(encoding="utf-8")
    assert "Redacted Jev evaluation written to:" in capsys.readouterr().out


def test_cli_live_refuses_without_printing_or_using_a_missing_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    assert main(["--live"]) == 2

    captured = capsys.readouterr()
    assert "TYPESAFE_API_KEY is required" in captured.err
    assert "api.typesafe.ai" not in captured.out
