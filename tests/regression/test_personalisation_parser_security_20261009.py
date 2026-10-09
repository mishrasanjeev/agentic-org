# SPDX-License-Identifier: Apache-2.0
"""Personalisation template parsing must reject hostile input within a bounded runtime."""

from __future__ import annotations

import multiprocessing
from multiprocessing.connection import Connection

import pytest

from core.personalisation import rules

ENTRIES = ("placeholders", "check_template", "render_template", "check_rule")


def _parse(entry: str, template: str):
    if entry == "render_template":
        return rules.render_template(template, {"a": "value"}, ["a"])
    if entry == "check_rule":
        return rules.check_rule(
            {
                "name": "synthetic-rule",
                "purpose": "service",
                "variant": {"template": template},
                "allowed_attributes": ["a"],
            }
        )
    return getattr(rules, entry)(template)


def _parse_child(entry: str, template: str, connection: Connection) -> None:
    try:
        _parse(entry, template)
    except rules.PersonalisationError as exc:
        connection.send({"status": exc.status, "code": exc.code})
    else:
        connection.send({"status": 200})
    finally:
        connection.close()


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize(
    ("template", "code"),
    [
        ("{{{{" + " " * (rules.MAX_TEMPLATE - 4), "template_invalid"),
        ("{{a" + " " * (rules.MAX_TEMPLATE - 3), "template_invalid"),
        ("{{" + " " * (rules.MAX_TEMPLATE - 4) + "}}", "placeholder_invalid"),
        ("{{" * (rules.MAX_TEMPLATE // 2), "template_invalid"),
        ("}}" * (rules.MAX_TEMPLATE // 2), "template_invalid"),
        ("{{{{" + " " * 100_000, "template_too_long"),
    ],
    ids=("alert-138-139-140", "unterminated-name", "empty-name", "opening-run", "closing-run", "oversized-hostile"),
)
def test_hostile_templates_fail_closed_with_a_process_deadline(entry, template, code):
    # A separate process can be killed even when a matcher holds the interpreter lock.
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_parse_child, args=(entry, template, writer), daemon=True)
    try:
        process.start()
        writer.close()
        process.join(timeout=2)
        if process.is_alive():
            pytest.fail(f"{entry} exceeded the two-second hostile-template deadline")
        assert process.exitcode == 0
        assert reader.poll(), "parser process returned no outcome"
        outcome = reader.recv()
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        reader.close()
        writer.close()
    assert outcome["status"] == 422
    assert outcome["code"] == code


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("template", ["x" * (rules.MAX_TEMPLATE + 1), "x" * 100_000])
def test_every_parser_entry_enforces_the_template_limit_before_scanning(entry, template):
    class UnscannableTemplate(str):
        def __iter__(self):
            raise AssertionError("oversized templates must not be scanned")

        def __getitem__(self, index):
            raise AssertionError("oversized templates must not be sliced")

        def strip(self, *args):
            raise AssertionError("oversized templates must not be stripped")

    with pytest.raises(rules.PersonalisationError) as caught:
        _parse(entry, UnscannableTemplate(template))
    assert caught.value.status == 422
    assert caught.value.code == "template_too_long"


@pytest.mark.parametrize(
    ("template", "attributes", "expected", "names"),
    [
        ("Hello {{ a }}, {{b}}/{{a}}!", {"a": "Asha", "b": 7.0}, "Hello Asha, 7/Asha!", ["a", "b"]),
        ("{{\t\na\r\v\f }}", {"a": False}, "no", ["a"]),
        ("{{\u00a0a\u2003}}", {"a": True}, "yes", ["a"]),
        ("{{a}}/{{b}}", {"a": 0, "b": 7.5}, "0/7.5", ["a", "b"]),
        ("{literal} {{{a}}}", {"a": "value"}, "{literal} {value}", ["a"]),
        ("{{a}}{{b}}", {"a": "{{b}}", "b": "\\value"}, "{{b}}\\value", ["a", "b"]),
        ("no placeholders {here}", {}, "no placeholders {here}", []),
        ("", {}, "", []),
        (" " * rules.MAX_TEMPLATE, {}, " " * rules.MAX_TEMPLATE, []),
        ("{{" + " " * (rules.MAX_TEMPLATE - 5) + "a}}", {"a": "value"}, "value", ["a"]),
        ("x" * rules.MAX_TEMPLATE, {}, "x" * rules.MAX_TEMPLATE, []),
        ("{{" + "a" * 64 + "}}", {"a" * 64: "value"}, "value", ["a" * 64]),
    ],
)
def test_valid_dsl_semantics_and_template_boundary_are_preserved(template, attributes, expected, names):
    assert rules.placeholders(template) == names
    assert rules.render_template(template, attributes, list(attributes)) == (expected, names)
    if template.strip():
        assert rules.check_template(template) == template


@pytest.mark.parametrize(
    ("template", "code"),
    [
        ("{{}}", "placeholder_invalid"),
        ("{{ }}", "placeholder_invalid"),
        ("{{ First-Name }}", "placeholder_invalid"),
        ("{{a b}}", "placeholder_invalid"),
        ("{{a.b}}", "placeholder_invalid"),
        ("{{_a}}", "placeholder_invalid"),
        ("{{" + "a" * 65 + "}}", "placeholder_invalid"),
        ("{{a", "template_invalid"),
        ("a}}", "template_invalid"),
        ("{{a}b}}", "template_invalid"),
        ("{{a{b}}", "template_invalid"),
        ("{{{{a}}}}", "template_invalid"),
        ("{{a}} {{", "template_invalid"),
        ("{{{a}}{", "template_invalid"),
        ("}{{a}}}", "template_invalid"),
        ("}} {{Bad}}", "placeholder_invalid"),
    ],
)
def test_malformed_placeholders_keep_the_existing_error_contract(template, code):
    for entry in ENTRIES:
        with pytest.raises(rules.PersonalisationError) as caught:
            _parse(entry, template)
        assert caught.value.status == 422
        assert caught.value.code == code


def test_authorisation_and_missing_value_checks_precede_rendering():
    with pytest.raises(rules.PersonalisationError) as caught:
        rules.render_template("{{a}}{{b}}", {}, ["a"])
    assert caught.value.code == "placeholder_not_allowed"
    for value in (None, ""):
        with pytest.raises(rules.PersonalisationError) as caught:
            rules.render_template("{{a}}", {"a": value}, ["a"])
        assert caught.value.code == "placeholder_unresolved"
    with pytest.raises(rules.PersonalisationError) as caught:
        rules.check_rule(
            {"name": "synthetic-rule", "purpose": "service", "variant": {"template": "{{a}}"}}
        )
    assert caught.value.code == "attribute_not_allowed"


def test_rendered_output_limit_includes_literals_and_repeated_substitutions():
    assert rules.render_template("x{{a}}{{a}}y", {"a": "v" * 3999}, ["a"])[0] == "x" + "v" * 7998 + "y"
    for template, value in (("x{{a}}{{a}}y", "v" * 4000), ("{{a}}", "v" * (rules.MAX_OUTPUT + 1))):
        with pytest.raises(rules.PersonalisationError) as caught:
            rules.render_template(template, {"a": value}, ["a"])
        assert caught.value.code == "output_too_long"


def test_rendering_stops_expanding_as_soon_as_the_output_limit_is_exceeded():
    class UnrenderableValue:
        def __str__(self):
            raise AssertionError("later values must not be expanded after the output limit")

    with pytest.raises(rules.PersonalisationError) as caught:
        rules.render_template("{{a}}{{b}}", {"a": "x" * (rules.MAX_OUTPUT + 1), "b": UnrenderableValue()}, ["a", "b"])
    assert caught.value.code == "output_too_long"


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("value", [None, 12, [], {}])
def test_nontext_templates_fail_closed_at_every_entry(entry, value):
    with pytest.raises(rules.PersonalisationError) as caught:
        _parse(entry, value)
    assert caught.value.status == 422
    assert caught.value.code == "template_invalid"
