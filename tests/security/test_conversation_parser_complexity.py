# SPDX-License-Identifier: Apache-2.0
"""Replay hostile user turns in a subprocess so a parser regression cannot hang CI."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "expression",
    [
        "intents.parse_amount('9' * 100_000 + 'x')",
        "intents.parse_amount('rs ' + ' ' * 100_000 + '!')",
        "intents.parse_amounts('9' * 100_000 + 'x')",
        "intents.parse_amounts('500' + ' ' * 100_000 + '!')",
        "context.repeats_last('again' + ' ' * 100_000 + '?')",
        "feedback.rating_from_text('rating' + ' ' * 100_000 + '?')",
        "feedback.rating_from_text('4' + ' ' * 100_000 + '?')",
    ],
)
def test_hostile_turn_completes_without_backtracking(expression: str) -> None:
    subprocess.run(  # noqa: S603 -- expressions are fixed synthetic cases above, never user input
        [sys.executable, "-c", "from core.conversation import context, feedback, intents; " + expression],
        check=True,
        capture_output=True,
        timeout=5,
    )


@pytest.mark.parametrize("text", ["again", " do that again . \n", "repeat it!", "once more"])
def test_repeat_request_whitespace_and_punctuation(text: str) -> None:
    from core.conversation.context import repeats_last

    assert repeats_last(text)


@pytest.mark.parametrize("text", ["4", " rating : 4 / 5 . \n", "4 stars!", "rating=4 out of 5"])
def test_rating_whitespace_and_punctuation(text: str) -> None:
    from core.conversation.feedback import rating_from_text

    assert rating_from_text(text) == 4


@pytest.mark.parametrize("text", ["999x", "account 1234", "reference AB12345", "2026/10/08"])
def test_non_amount_tokens_are_not_salvaged_as_amounts(text: str) -> None:
    from core.conversation.intents import parse_amount, parse_amounts

    assert parse_amount(text) is None
    assert parse_amounts(text) == []
