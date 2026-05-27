#!/usr/bin/env python3
"""Dump mask → translate → unmask LLM requests (no API call)."""

from __future__ import annotations

import argparse
from pathlib import Path

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.config import Settings
from ydbdoc_review.document_mask import MaskRegistry
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.llm import load_masked_document_instructions
from ydbdoc_review.masked_translate import (
    _build_masked_user_input,
    build_masked_segments,
    chunk_masked_text,
    count_masked_stats,
)


def _dump(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--source-path", default="ydb/docs/ru/example.md")
    ap.add_argument("--out", type=Path, default=Path("debug/masked-dump"))
    args = ap.parse_args()

    text = args.source.read_text(encoding="utf-8")
    settings = Settings.from_env()
    regions = refine_tab_regions(
        text, analyze_document_structure(text, source_is_russian=True)
    )
    registry = MaskRegistry()
    segments = build_masked_segments(text, regions, registry, source_is_russian=True)
    copy_n, tr_n, _ = count_masked_stats(segments)

    out = args.out
    _dump(out / "00_source_ru.md", text)
    _dump(
        out / "01_instructions.txt",
        load_masked_document_instructions(settings),
    )
    _dump(
        out / "02_segments_index.txt",
        "\n".join(
            f"{i + 1}. {s.kind} L{s.start_line}-{s.end_line}"
            for i, s in enumerate(segments)
        )
        + f"\n\ncopy={copy_n} translate={tr_n} placeholders={len(registry.atoms)}\n",
    )

    chunk_idx = 0
    for seg in segments:
        if seg.kind != "translate":
            _dump(
                out / f"copy_L{seg.start_line:05d}-{seg.end_line:05d}.md",
                seg.text,
            )
            continue
        chunks = chunk_masked_text(seg.masked_text)
        for ci, masked in enumerate(chunks, start=1):
            chunk_idx += 1
            _dump(
                out / f"03_chunk_{chunk_idx:02d}_masked_L{seg.start_line:05d}.md",
                masked,
            )
            _dump(
                out
                / f"04_chunk_{chunk_idx:02d}_USER_L{seg.start_line:05d}_{ci}of{len(chunks)}.md",
                _build_masked_user_input(
                    source_lang="Russian",
                    target_lang="English",
                    source_path=args.source_path,
                    masked=masked,
                    chunk_index=ci,
                    chunk_total=len(chunks),
                    start_line=seg.start_line,
                    end_line=seg.end_line,
                ),
            )

    print(f"Wrote {chunk_idx} masked chunk(s) to {out}")


if __name__ == "__main__":
    main()
