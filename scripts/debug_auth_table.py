"""Debug round-trip for auth.md table."""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


path = Path("tests/fixtures/markdown_files/ru/core/reference/ydb-sdk/_includes/auth.md")
text = path.read_text(encoding="utf-8")

doc1 = parse_markdown(text)
first = render_markdown(doc1)
doc2 = parse_markdown(first)
second = render_markdown(doc2)

# Find lines containing 'grant-type' and surrounding context.
def find_around(s: str, needle: str, before: int = 5, after: int = 10) -> str:
    lines = s.splitlines()
    out = []
    for i, line in enumerate(lines):
        if needle in line:
            lo = max(0, i - before)
            hi = min(len(lines), i + after + 1)
            out.append(f"--- around line {i + 1} ---")
            for j in range(lo, hi):
                marker = ">>" if j == i else "  "
                out.append(f"{marker} {j + 1:4d}: {lines[j]!r}")
            out.append("")
    return "\n".join(out)


print("=" * 70)
print("ORIGINAL around 'grant-type':")
print(find_around(text, "grant-type"))

print("=" * 70)
print("FIRST PASS around 'grant-type':")
print(find_around(first, "grant-type"))

print("=" * 70)
print("SECOND PASS around 'grant-type':")
print(find_around(second, "grant-type"))

# Also dump AST around the table for first pass.
print("=" * 70)
print("AST after first parse — tables:")
for i, block in enumerate(doc1.children):
    if block.kind == "table":
        print(f"  Block #{i}: table")
        print(f"    header cells: {len(block.header.cells)}")
        print(f"    aligns: {block.aligns}")
        print(f"    rows: {len(block.rows)}")
        for j, row in enumerate(block.rows):
            print(f"    row {j}: {len(row.cells)} cells")

