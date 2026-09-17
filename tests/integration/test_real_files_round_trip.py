"""Round-trip tests on real YDB documentation files.

These tests show our current YFM coverage. Failures here are expected
until all YFM plugins are implemented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "markdown_files"


def _collect_files() -> list[Path]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(FIXTURES_DIR.rglob("*.md"))


def _file_id(path: Path) -> str:
    return str(path.relative_to(FIXTURES_DIR))


REAL_FILES = _collect_files()


@pytest.mark.parametrize("path", REAL_FILES, ids=[_file_id(p) for p in REAL_FILES])
def test_parse_does_not_crash(path: Path) -> None:
    """Parser must not crash on any real file (may produce imperfect AST)."""
    text = path.read_text(encoding="utf-8")
    try:
        parse_markdown(text)
    except ValueError as e:
        pytest.fail(f"Parse failed: {e}\nFirst 200 chars:\n{text[:200]}")


@pytest.mark.parametrize("path", REAL_FILES, ids=[_file_id(p) for p in REAL_FILES])
def test_round_trip_stable(path: Path) -> None:
    """parse→render→parse→render must be stable (idempotent after first pass)."""
    text = path.read_text(encoding="utf-8")
    first = render_markdown(parse_markdown(text))
    second = render_markdown(parse_markdown(first))
    if first != second:
        # Show a diff snippet for debugging.
        from difflib import unified_diff

        diff = "\n".join(
            list(
                unified_diff(
                    first.splitlines(),
                    second.splitlines(),
                    lineterm="",
                    n=2,
                )
            )[:80]
        )
        pytest.fail(f"Round-trip not stable.\nDiff (first ~80 lines):\n{diff}")
