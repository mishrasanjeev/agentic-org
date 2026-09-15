# SPDX-License-Identifier: Apache-2.0
"""Documentation code examples are extracted from tests that run in CI.

A fenced block in a checked document is preceded by a marker naming its source region::

    <!-- snippet: tests/contract/test_domain_schema_contracts.py#validate-document -->
    ```python
    ...
    ```

and the source file delimits that region with ``# docs-snippet: start <name>`` and
``# docs-snippet: end <name>``. The block must equal the dedented region. A checked document may
not contain a Python block without a marker, so an example cannot be added that no test runs.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Documents whose code examples must all come from tests.
CHECKED_DOCS: tuple[str, ...] = (
    "docs/schemas/domain-schemas.md",
    "docs/adr/0009-provider-seam.md",
    "docs/providers/mock-provider.md",
)

_MARKER = re.compile(r"^<!--\s*snippet:\s*(?P<path>[^#\s]+)#(?P<name>[a-z0-9][a-z0-9-]*)\s*-->\s*$")
_FENCE = re.compile(r"^```(?P<lang>[a-z]*)\s*$")


class SnippetError(AssertionError):
    pass


@dataclass(frozen=True)
class DocBlock:
    doc: str
    line: int
    lang: str
    body: str
    source: str | None
    name: str | None


def parse_blocks(doc: str, text: str) -> list[DocBlock]:
    blocks: list[DocBlock] = []
    lines = text.splitlines()
    pending: tuple[str, str] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = _MARKER.match(line)
        if marker:
            pending = (marker["path"], marker["name"])
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            start = index
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                body.append(lines[index])
                index += 1
            if index == len(lines):
                raise SnippetError(f"{doc}:{start + 1}: unterminated code block")
            source, name = pending if pending else (None, None)
            blocks.append(DocBlock(doc, start + 1, fence["lang"], "\n".join(body), source, name))
            pending = None
            index += 1
            continue
        if line.strip() and pending:
            raise SnippetError(f"{doc}:{index + 1}: snippet marker is not followed by a code block")
        index += 1
    return blocks


def extract_region(source_text: str, name: str, source: str) -> str:
    start = re.search(rf"^[ \t]*# docs-snippet: start {re.escape(name)}[ \t]*$", source_text, re.MULTILINE)
    end = re.search(rf"^[ \t]*# docs-snippet: end {re.escape(name)}[ \t]*$", source_text, re.MULTILINE)
    if not start or not end or end.start() < start.end():
        raise SnippetError(f"{source}: no region named {name!r}")
    region = source_text[start.end() : end.start()].strip("\n")
    return textwrap.dedent(region)


def check_document(doc: str, root: Path = REPO_ROOT) -> None:
    text = (root / doc).read_text(encoding="utf-8")
    for block in parse_blocks(doc, text):
        if block.source is None or block.name is None:
            if block.lang == "python":
                raise SnippetError(f"{doc}:{block.line}: python example has no snippet marker")
            continue
        source_path = root / block.source
        if not block.source.startswith("tests/") or not source_path.is_file():
            raise SnippetError(f"{doc}:{block.line}: snippet source {block.source} is not a test file")
        expected = extract_region(source_path.read_text(encoding="utf-8"), block.name, block.source)
        if block.body.strip("\n") != expected:
            raise SnippetError(
                f"{doc}:{block.line}: example differs from {block.source}#{block.name}; copy the region into the doc"
            )


@pytest.mark.parametrize("doc", CHECKED_DOCS)
def test_documentation_examples_match_their_tests(doc: str) -> None:
    check_document(doc)


def test_a_drifted_example_is_reported(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_x():\n    # docs-snippet: start hello\n    value = 1\n    # docs-snippet: end hello\n",
        encoding="utf-8",
    )
    (tmp_path / "doc.md").write_text(
        "<!-- snippet: tests/test_x.py#hello -->\n```python\nvalue = 2\n```\n", encoding="utf-8"
    )
    with pytest.raises(SnippetError, match="differs from tests/test_x.py#hello"):
        check_document("doc.md", root=tmp_path)


def test_a_matching_example_passes(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_x():\n    # docs-snippet: start hello\n    value = 1\n    # docs-snippet: end hello\n",
        encoding="utf-8",
    )
    (tmp_path / "doc.md").write_text(
        "Intro.\n\n<!-- snippet: tests/test_x.py#hello -->\n```python\nvalue = 1\n```\n", encoding="utf-8"
    )
    check_document("doc.md", root=tmp_path)


def test_an_unmarked_python_example_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("```python\nprint('untested')\n```\n", encoding="utf-8")
    with pytest.raises(SnippetError, match="has no snippet marker"):
        check_document("doc.md", root=tmp_path)


def test_a_missing_region_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (tmp_path / "doc.md").write_text("<!-- snippet: tests/test_x.py#gone -->\n```python\nx\n```\n", encoding="utf-8")
    with pytest.raises(SnippetError, match="no region named 'gone'"):
        check_document("doc.md", root=tmp_path)
