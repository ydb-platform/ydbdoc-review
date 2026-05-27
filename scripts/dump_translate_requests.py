#!/usr/bin/env python3
"""Dump LLM requests for annotated file-level translation (no API call).

Usage:
  PYTHONPATH=src python scripts/dump_translate_requests.py \\
    --source path/to/ru.md \\
    --source-path ydb/docs/ru/.../file.md \\
    --out debug/my-dump
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ydbdoc_review.annotated_translate import (
    build_annotated_chunks,
    refine_tab_regions,
    summarize_chunk_regions,
)
from ydbdoc_review.config import Settings
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.file_translate import _build_annotated_chunk_user_input
from ydbdoc_review.llm import load_annotated_chunk_instructions


def _dump(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument(
        "--source-path",
        default="ydb/docs/ru/core/devops/deployment-options/manual/initial-deployment.md",
    )
    ap.add_argument("--out", type=Path, default=Path("debug/translate-dump"))
    ap.add_argument("--source-lang", default="Russian")
    ap.add_argument("--target-lang", default="English")
    args = ap.parse_args()

    source_full = args.source.read_text(encoding="utf-8")
    settings = Settings.from_env()
    out_root = args.out
    source_path = args.source_path

    _dump(out_root / "00_source_ru.md", source_full)
    _dump(
        out_root / "README.txt",
        f"Source: {args.source.resolve()}\n"
        f"Logical path: {source_path}\n"
        f"Model (translate): {settings.model_translate}\n"
        f"Prompts dir: {settings.prompts_dir}\n",
    )

    instructions = load_annotated_chunk_instructions(
        settings, source_lang=args.source_lang, target_lang=args.target_lang
    )
    _dump(out_root / "01_system_instructions_annotated_chunk.txt", instructions)

    source_is_russian = args.source_lang.lower().startswith("rus")
    regions = refine_tab_regions(
        source_full,
        analyze_document_structure(source_full, source_is_russian=source_is_russian),
    )
    chunks = build_annotated_chunks(source_full, regions)

    region_lines = [
        f"  {r.start_line:5d}-{r.end_line:<5d}  {r.kind:6s}  {r.action}"
        for r in regions
    ]
    _dump(out_root / "02_full_region_map.txt", "\n".join(region_lines) + "\n")

    chunk_lines = []
    req_num = 0
    for ch in chunks:
        chunk_lines.append(
            f"chunk {ch.index}/{ch.total}: lines {ch.start_line}-{ch.end_line} "
            f"copy_only={ch.copy_only()} regions={len(ch.regions)}"
        )
        if ch.copy_only():
            continue
        req_num += 1
        user = _build_annotated_chunk_user_input(
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            source_path=source_path,
            chunk=ch,
            full_source=source_full,
            all_regions=regions,
        )
        prefix = (
            f"request_{req_num:02d}_chunk{ch.index}of{ch.total}_"
            f"L{ch.start_line}-{ch.end_line}"
        )
        _dump(out_root / f"{prefix}_INSTRUCTIONS.txt", instructions)
        _dump(out_root / f"{prefix}_USER.md", user)
        _dump(
            out_root / f"{prefix}_REGION_MAP.txt",
            summarize_chunk_regions(source_full, ch.regions),
        )

    _dump(out_root / "02_chunks_index.txt", "\n".join(chunk_lines) + "\n")

    llm_chunks = sum(1 for c in chunks if not c.copy_only())
    summary = (
        f"Total chunks: {len(chunks)}\n"
        f"LLM requests (would call): {llm_chunks}\n"
        f"Copy-only chunks (no LLM): {len(chunks) - llm_chunks}\n"
    )
    _dump(out_root / "03_summary.txt", summary)
    print(f"Wrote {req_num} request bundle(s) under {out_root.resolve()}")


if __name__ == "__main__":
    main()
