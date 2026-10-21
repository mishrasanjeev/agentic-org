# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The PDF documentation generators state the Orchestrum Personal Use licence (2026-10-21).

They used to print "Apache 2.0 License", "Open Source" and "Self-Host Free Forever"; after the
relicensing those statements are false. Each generator is built in memory and its text checked.
"""

from __future__ import annotations

import importlib
import io
import re

import pytest
from pypdf import PdfReader

GENERATORS = (
    "scripts.generate_architecture_doc",
    "scripts.generate_complete_user_guide",
    "scripts.generate_user_guide",
)


def _pdf_text(module_name: str) -> str:
    module = importlib.import_module(module_name)
    document = module.build()
    reader = PdfReader(io.BytesIO(bytes(document.output())))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return " ".join(text.split())


@pytest.mark.parametrize("module_name", GENERATORS)
def test_generated_pdf_states_the_orchestrum_personal_use_licence(module_name: str) -> None:
    text = _pdf_text(module_name)

    assert "Orchestrum Personal Use" in text
    assert "PolyForm" not in text
    assert re.search(r"[Oo]rganisations?[^.]{0,80}written agreement", text)
    assert not re.search(r"Apache 2\.0 (?:License|license|\(open source\))|Self-Host(?:ed)? \(?Free Forever", text)
    assert "completely free" not in text
