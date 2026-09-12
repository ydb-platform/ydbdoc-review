"""Scan markdown files for YFM constructs and report their frequencies."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path


# Patterns for YFM constructs we want to inventory.
PATTERNS: dict[str, re.Pattern[str]] = {
    "note_open": re.compile(r"\{%\s*note\s+(\w+)(?:\s+\"[^\"]*\")?\s*%\}"),
    "note_close": re.compile(r"\{%\s*endnote\s*%\}"),
    "cut_open": re.compile(r"\{%\s*cut\s+\"[^\"]*\"\s*%\}"),
    "cut_close": re.compile(r"\{%\s*endcut\s*%\}"),
    "list_tabs": re.compile(r"\{%\s*list\s+tabs(?:\s+\w+)?\s*%\}"),
    "endlist": re.compile(r"\{%\s*endlist\s*%\}"),
    "include": re.compile(r"\{%\s*include\s+(?:notitle\s+)?\[[^\]]*\]\([^)]+\)\s*%\}"),
    "if_open": re.compile(r"\{%\s*if\s+[^%]+%\}"),
    "if_close": re.compile(r"\{%\s*endif\s*%\}"),
    "else": re.compile(r"\{%\s*else\s*%\}"),
    "variable": re.compile(r"\{\{\s*[\w\-\.]+\s*\}\}"),
    "heading_anchor": re.compile(r"^#{1,6}\s+.*\{#[A-Za-z0-9_\-]+\}\s*$", re.MULTILINE),
    "image_with_size": re.compile(r"!\[[^\]]*\]\([^)]+=\d+x\d*\)"),
    "term_definition": re.compile(r"^\[\*[\w\-]+\]:\s+.+$", re.MULTILINE),
    "raw_html_block": re.compile(r"^<[a-zA-Z][^>]*>", re.MULTILINE),
}


def scan_file(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    counts: dict[str, int] = {}
    for name, pat in PATTERNS.items():
        n = len(pat.findall(text))
        if n > 0:
            counts[name] = n
    counts["__chars__"] = len(text)
    counts["__lines__"] = text.count("\n") + 1
    return counts


def main() -> None:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    else:
        root = Path("tests/fixtures/markdown_files")

    files = sorted(root.rglob("*.md"))
    if not files:
        print(f"No .md files under {root}")
        sys.exit(1)

    total = Counter()
    per_file: dict[Path, dict[str, int]] = {}

    for f in files:
        c = scan_file(f)
        per_file[f] = c
        for k, v in c.items():
            if not k.startswith("__"):
                total[k] += v

    print(f"Scanned {len(files)} files\n")

    print("== Per-file summary ==")
    for f in files:
        c = per_file[f]
        chars = c.pop("__chars__", 0)
        lines = c.pop("__lines__", 0)
        rel = f.relative_to(root)
        constructs = ", ".join(f"{k}={v}" for k, v in sorted(c.items()) if not k.startswith("__"))
        print(f"  {rel} [{chars} chars, {lines} lines]")
        if constructs:
            print(f"    {constructs}")

    print("\n== Total YFM constructs ==")
    for k, v in total.most_common():
        print(f"  {k:25s} {v}")


if __name__ == "__main__":
    main()
