#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Fail the image build unless its baked model and full PII path actually work.

Run with ``python -m scripts.validate_packaged_nlp``. No downloads, environment
overrides, or fallback modes are used; every probe is synthetic and local.
"""

from __future__ import annotations

import sys


def validate() -> None:
    import spacy
    from presidio_analyzer import AnalyzerEngine

    from core.pii.redactor import PII_MODE_BEFORE_LLM, PIIRedactor

    probes = (
        ("Mira Ellison is a customer.", "Mira Ellison", "PERSON"),
        ("Email: mira.ellison@example.com.", "mira.ellison@example.com", "EMAIL_ADDRESS"),
        ("PAN: ABCDE1234F.", "ABCDE1234F", "PAN"),
    )
    nlp = spacy.load("en_core_web_sm")
    if "ner" not in nlp.pipe_names:
        raise RuntimeError("packaged English model has no NER pipeline")
    if not any(entity.text == probes[0][1] and entity.label_ == "PERSON" for entity in nlp(probes[0][0]).ents):
        raise RuntimeError("packaged English NER did not recognize the synthetic person")

    redactor = PIIRedactor()
    if redactor.mode != PII_MODE_BEFORE_LLM:
        raise RuntimeError("packaged PII redaction must run before the LLM")
    if not isinstance(redactor._analyzer, AnalyzerEngine):
        raise RuntimeError("packaged PII requires the full analyzer, not regex fallback")
    for text, value, entity_type in probes:
        results = redactor._analyzer.analyze(text=text, language="en", entities=[entity_type])
        if not any(
            result.entity_type == entity_type and text[result.start:result.end] == value
            and (
                entity_type != "PERSON"
                or (result.recognition_metadata or {}).get("recognizer_name") == "SpacyRecognizer"
            )
            for result in results
        ):
            raise RuntimeError(f"packaged analyzer did not detect the complete synthetic {entity_type}")
        masked, tokens = redactor.redact(text)
        if value in masked or not any(
            original == value and token.startswith(f"<{entity_type}_") and token in masked
            for token, original in tokens.items()
        ):
            raise RuntimeError(f"packaged redaction did not mask the complete synthetic {entity_type}")
        if redactor.deanonymize(masked, tokens) != text:
            raise RuntimeError(f"packaged redaction did not restore the synthetic {entity_type}")


def main() -> int:
    try:
        validate()
    except (Exception, SystemExit) as exc:  # enterprise-gate: broad-except-ok reason=build gate fails closed on every NLP error
        print(f"packaged NLP validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("packaged NLP validation passed: baked NER, full PII detection, redaction and restoration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
