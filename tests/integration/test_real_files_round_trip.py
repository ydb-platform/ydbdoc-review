"""Round-trip tests on real YDB documentation files.

Real inputs must parse and survive production protection/restoration byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.roundtrip import roundtrip
from ydbdoc_review.parsing.markdown_parser import create_parser

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "markdown_files"


def _collect_files() -> list[Path]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(FIXTURES_DIR.rglob("*.md"))


def _file_id(path: Path) -> str:
    return str(path.relative_to(FIXTURES_DIR))


REAL_FILES = _collect_files()
assert REAL_FILES, "Real-document corpus must not be empty"


@pytest.mark.parametrize("path", REAL_FILES, ids=[_file_id(p) for p in REAL_FILES])
def test_parse_does_not_crash(path: Path) -> None:
    """Production grammar must parse every real file."""
    text = path.read_text(encoding="utf-8")
    try:
        create_parser(source_locations=True).parse(text)
    except ValueError as e:
        pytest.fail(f"Parse failed: {e}\nFirst 200 chars:\n{text[:200]}")


@pytest.mark.parametrize("path", REAL_FILES, ids=[_file_id(p) for p in REAL_FILES])
def test_round_trip_stable(path: Path) -> None:
    """Protection/restoration must preserve the source exactly on every pass."""
    text = path.read_text(encoding="utf-8")
    first = roundtrip(text)
    second = roundtrip(first)
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
