# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Contact validation keeps its accepted forms without ambiguous email regexes."""

import pytest

from core.onboarding_desk import agent_ready
from core.onboarding_desk.store import DeskError


@pytest.mark.parametrize(
    ("contact", "valid"),
    [
        ("avery@northwind.example", True),
        ("owner+desk@sub.northwind.example", True),
        ("a@b..c", True),
        ("a@.b.c", True),
        ("+1 (415) 555-0100", True),
        ("1234567", True),
        ("", False),
        ("not a contact", False),
        ("a@b", False),
        ("a@.b", False),
        ("a@b.", False),
        ("a@b.c@d", False),
        ("a@b.c\n", False),
        ("a b@c.d", False),
        ("123456", False),
        ("+" + "1" * 25, False),
    ],
)
def test_contact_forms(contact: str, valid: bool) -> None:
    assert agent_ready._valid_contact(contact) is valid


@pytest.mark.timeout(2)
@pytest.mark.parametrize("suffix", ["@", "\n", " "])
def test_repeated_dot_contact_is_linear(suffix: str) -> None:
    assert not agent_ready._valid_contact("!@!." + "!." * 100_000 + suffix)


def test_contact_length_bound_is_retained() -> None:
    with pytest.raises(DeskError) as caught:
        agent_ready._text("a@" + "b" * 251 + ".c", "contact", 254)
    assert caught.value.code == "representative_invalid"
