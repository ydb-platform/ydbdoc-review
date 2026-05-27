#!/usr/bin/env python3
"""Dump placeholder JSON translation batches (no API call)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.config import Settings
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.llm import load_placeholder_instructions
from ydbdoc_review.placeholder_translate import (
    _batch_units,
    _build_batch_user_input,
    _max_batch_chars,
    build_placeholder_segments,
    count_placeholder_stats,
)


def _dump(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--source-path", default="ydb/docs/ru/example.md")
    ap.add_argument("--out", type=Path, default=Path("debug/placeholder-dump"))
    args = ap.parse_args()

    text = args.source.read_text(encoding="utf-8")
    settings = Settings.from_env()
    regions = refine_tab_regions(
        text, analyze_document_structure(text, source_is_russian=True)
    )
    segments = build_placeholder_segments(text, regions, source_is_russian=True)
    copy_n, tr_n, units_n = count_placeholder_stats(segments)

    all_units = [u for s in segments if s.kind == "translate" for u in s.units]
    batches = _batch_units(all_units, max_chars=_max_batch_chars())

    out = args.out
    _dump(out / "00_source_ru.md", text)
    _dump(
        out / "01_instructions.txt",
        load_placeholder_instructions(settings),
    )
    lines = [f"regions={len(regions)} copy_segs={copy_n} translate_segs={tr_n} units={units_n}"]
    for i, s in enumerate(segments, 1):
        if s.kind == "copy":
            lines.append(f"seg {i}: COPY lines {s.start_line}-{s.end_line}")
        else:
            lines.append(
                f"seg {i}: TRANSLATE lines {s.start_line}-{s.end_line} "
                f"units={len(s.units)}"
            )
    _dump(out / "02_segments_index.txt", "\n".join(lines) + "\n")

    for bi, batch in enumerate(batches, 1):
        user = _build_batch_user_input(
            source_lang="Russian",
            target_lang="English",
            source_path=args.source_path,
            batch=batch,
            batch_index=bi,
            batch_total=len(batches),
        )
        _dump(out / f"batch_{bi:02d}_USER.json", user)
        _dump(
            out / f"batch_{bi:02d}_ids.txt",
            "\n".join(f"{u.unit_id} L{u.line_no}: {u.source_line[:80]!r}" for u in batch)
            + "\n",
        )

    _dump(
        out / "03_summary.txt",
        f"LLM batches: {len(batches)}\nCopy segments: {copy_n}\n",
    )
    print(f"Wrote {len(batches)} batch(es) to {out.resolve()}")


if __name__ == "__main__":
    main()
