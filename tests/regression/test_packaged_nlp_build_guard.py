# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The production image must exercise packaged NLP, not just install its files."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
PROBES = (
    ("Mira Ellison is a customer.", "Mira Ellison", "PERSON"),
    ("Email: mira.ellison@example.com.", "mira.ellison@example.com", "EMAIL_ADDRESS"),
    ("PAN: ABCDE1234F.", "ABCDE1234F", "PAN"),
)


def test_final_runtime_requires_packaged_nlp_after_copy_and_nonroot_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile[dockerfile.rindex("\nFROM "):]
    gate = "RUN --network=none python -m scripts.validate_packaged_nlp"
    assert gate in runtime, "the final image must actually load its copied NLP runtime"
    assert runtime.index(gate) > runtime.index("COPY . .")
    assert runtime.index(gate) > runtime.index("USER agenticorg")
    assert "\nCOPY " not in runtime[runtime.index(gate):]
    assert "\nUSER " not in runtime[runtime.index(gate):]


def test_runtime_probe_discards_only_its_generated_suffix_cache_in_the_same_run():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "RUN --network=none python -m scripts.validate_packaged_nlp \\\n"
        "    && rm -rf /home/agenticorg/.cache/python-tldextract\n"
    ) in dockerfile


@pytest.mark.parametrize("install_set", ["v4", "dev", "requirements-v4.txt"])
def test_nlp_install_sets_pin_only_the_confirmed_python_314_compatibility(install_set):
    if install_set in {"v4", "dev"}:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        raw = project["project"]["optional-dependencies"][install_set]
    else:
        raw = [
            line.strip() for line in (ROOT / install_set).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    pins = [requirement for value in raw if (requirement := Requirement(value)).name == "preshed"]
    assert len(pins) == 1, f"{install_set}: require the known-working Python 3.14 token hash map"
    assert str(pins[0].specifier) == "==3.0.13"
    assert pins[0].marker is not None
    for version in ("3.12", "3.13", "3.14", "3.15"):
        assert pins[0].marker.evaluate({**default_environment(), "python_version": version}) == (version == "3.14")


@pytest.fixture
def dependencies(monkeypatch):
    class Analyzer(SimpleNamespace):
        pass

    def analyze(*, text, language, entities):
        assert language == "en"
        value, entity_type = next((value, kind) for probe, value, kind in PROBES if probe == text)
        assert entities == [entity_type]
        start = text.index(value)
        return [SimpleNamespace(
            start=start, end=start + len(value), entity_type=entity_type,
            recognition_metadata={
                "recognizer_name": "SpacyRecognizer" if entity_type == "PERSON" else "PatternRecognizer",
            },
        )]

    def redact(text):
        value, entity_type = next((value, kind) for probe, value, kind in PROBES if probe == text)
        token = f"<{entity_type}_1>"
        return text.replace(value, token), {token: value}

    analyzer = Analyzer(analyze=Mock(side_effect=analyze))
    redactor = SimpleNamespace(
        mode="before_llm",
        _analyzer=analyzer,
        redact=Mock(side_effect=redact),
        deanonymize=Mock(
            side_effect=lambda text, tokens: text.replace(next(iter(tokens)), next(iter(tokens.values())))
        ),
    )
    nlp = Mock(return_value=SimpleNamespace(ents=[SimpleNamespace(text="Mira Ellison", label_="PERSON")]))
    nlp.pipe_names = ["tok2vec", "ner"]
    spacy = SimpleNamespace(load=Mock(return_value=nlp))
    factory = Mock(return_value=redactor)
    monkeypatch.setitem(sys.modules, "spacy", spacy)
    monkeypatch.setitem(sys.modules, "presidio_analyzer", SimpleNamespace(AnalyzerEngine=Analyzer))
    monkeypatch.setitem(
        sys.modules, "core.pii.redactor", SimpleNamespace(PIIRedactor=factory, PII_MODE_BEFORE_LLM="before_llm")
    )
    return SimpleNamespace(spacy=spacy, nlp=nlp, redactor=redactor, analyzer=analyzer, factory=factory)


def _gate():
    return importlib.import_module("scripts.validate_packaged_nlp")


def test_guard_exercises_every_detector_redaction_and_restoration(dependencies, capsys):
    assert _gate().main() == 0
    dependencies.spacy.load.assert_called_once_with("en_core_web_sm")
    dependencies.nlp.assert_called_once_with(PROBES[0][0])
    assert dependencies.analyzer.analyze.call_count == len(PROBES)
    assert dependencies.redactor.redact.call_count == len(PROBES)
    assert dependencies.redactor.deanonymize.call_count == len(PROBES)
    assert "packaged NLP validation passed" in capsys.readouterr().out


@pytest.mark.parametrize("error", [OSError("missing model"), OverflowError("broken tokenizer"), SystemExit(0)])
def test_missing_broken_model_and_success_systemexit_cannot_pass(dependencies, error, capsys):
    dependencies.spacy.load.side_effect = error
    assert _gate().main() == 1
    dependencies.factory.assert_not_called()
    assert "packaged NLP validation failed" in capsys.readouterr().err


def test_missing_presidio_cannot_pass(dependencies, monkeypatch):
    monkeypatch.setitem(sys.modules, "presidio_analyzer", None)
    assert _gate().main() == 1


@pytest.mark.parametrize("pipes", [[], ["tok2vec"], ["unknown"]])
def test_missing_or_unknown_ner_pipeline_cannot_pass(dependencies, pipes):
    dependencies.nlp.pipe_names = pipes
    assert _gate().main() == 1


def test_empty_model_entities_cannot_pass(dependencies):
    dependencies.nlp.return_value.ents = []
    assert _gate().main() == 1


def test_person_result_must_come_from_the_spacy_recognizer(dependencies):
    original = dependencies.analyzer.analyze.side_effect

    def analyze(**kwargs):
        results = original(**kwargs)
        results[0].recognition_metadata = {"recognizer_name": "PatternRecognizer"}
        return results

    dependencies.analyzer.analyze.side_effect = analyze
    assert _gate().main() == 1


@pytest.mark.parametrize("mode", ["disabled", "logs_only", "unknown"])
def test_disabled_logs_only_or_unknown_mode_cannot_pass(dependencies, mode):
    dependencies.redactor.mode = mode
    assert _gate().main() == 1


@pytest.mark.parametrize("analyzer", [None, SimpleNamespace(analyze=Mock())])
def test_regex_fallback_or_non_analyzer_cannot_pass(dependencies, analyzer):
    dependencies.redactor._analyzer = analyzer
    assert _gate().main() == 1


@pytest.mark.parametrize("failed_entity", ["PERSON", "EMAIL_ADDRESS", "PAN"])
def test_each_real_analyzer_detector_is_required(dependencies, failed_entity):
    original = dependencies.analyzer.analyze.side_effect
    dependencies.analyzer.analyze.side_effect = lambda **kwargs: (
        [] if kwargs["entities"] == [failed_entity] else original(**kwargs)
    )
    assert _gate().main() == 1


@pytest.mark.parametrize("failed_entity", ["PERSON", "EMAIL_ADDRESS", "PAN"])
def test_each_value_must_actually_be_redacted(dependencies, failed_entity):
    original = dependencies.redactor.redact.side_effect
    dependencies.redactor.redact.side_effect = lambda text: (
        (text, {}) if text == next(probe for probe, _, kind in PROBES if kind == failed_entity) else original(text)
    )
    assert _gate().main() == 1


def test_incomplete_email_token_cannot_pass(dependencies):
    original = dependencies.redactor.redact.side_effect
    dependencies.redactor.redact.side_effect = lambda text: (
        ("Email: <EMAIL_ADDRESS_1>.com.", {"<EMAIL_ADDRESS_1>": "mira.ellison@example"})
        if text == PROBES[1][0] else original(text)
    )
    assert _gate().main() == 1


def test_wrong_entity_token_cannot_pass(dependencies):
    dependencies.redactor.redact.side_effect = lambda text: ("<OTHER_1>", {"<OTHER_1>": text})
    assert _gate().main() == 1


def test_broken_restoration_cannot_pass(dependencies):
    dependencies.redactor.deanonymize.side_effect = lambda text, tokens: text
    assert _gate().main() == 1


def test_guard_does_not_depend_on_optimizable_assert_statements():
    import ast

    source = (ROOT / "scripts" / "validate_packaged_nlp.py").read_text(encoding="utf-8")
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(source)))
