"""Inspect how the current parser treats YFM constructs."""

from __future__ import annotations

import json
from pathlib import Path

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


SAMPLES = {
    "variable_in_paragraph": "Use {{ ydb-short-name }} CLI to run queries.\n",
    "note_block": (
        "{% note warning %}\n"
        "\n"
        "Be careful here.\n"
        "\n"
        "{% endnote %}\n"
    ),
    "note_inline_one_line": "{% note info %}\nSimple note text.\n{% endnote %}\n",
    "list_tabs": (
        "{% list tabs %}\n"
        "\n"
        "- Python\n"
        "\n"
        "  Python content here.\n"
        "\n"
        "- Go\n"
        "\n"
        "  Go content.\n"
        "\n"
        "{% endlist %}\n"
    ),
    "include": "{% include [text](../_includes/foo.md) %}\n",
    "if_block": (
        "{% if oss %}\n"
        "\n"
        "Open source content.\n"
        "\n"
        "{% endif %}\n"
    ),
    "variable_in_link": "See [glossary]({{ link-glossary }}).\n",
    "variable_in_heading": "## Run {{ ydb-short-name }} CLI\n",
}


def inspect(name: str, text: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"SAMPLE: {name}")
    print(f"INPUT:\n{text!r}")
    doc = parse_markdown(text)
    # Print AST as JSON-ish.
    print(f"\nAST:")
    print(json.dumps(doc.model_dump(), indent=2, ensure_ascii=False))
    rendered = render_markdown(doc)
    print(f"\nRENDERED:\n{rendered!r}")
    print(f"ROUND-TRIP IDENTICAL: {rendered == text}")


if __name__ == "__main__":
    for name, text in SAMPLES.items():
        inspect(name, text)

    # Also inspect a fragment from a real file.
    real = Path("tests/fixtures/markdown_files/ru/core/devops/backup-and-recovery/system-tablet-backup.md")
    if real.exists():
        text = real.read_text(encoding="utf-8")
        # Find a {% note %} block.
        import re
        m = re.search(r"\{%\s*note[^%]+%\}.*?\{%\s*endnote\s*%\}", text, re.DOTALL)
        if m:
            print(f"\n{'=' * 70}")
            print("SAMPLE: real_note_from_system_tablet_backup")
            print(f"FRAGMENT:\n{m.group(0)[:500]}")
            doc = parse_markdown(m.group(0))
            print(f"\nAST kinds:")
            for child in doc.children:
                print(f"  - {child.kind}: {child.model_dump_json()[:200]}")

