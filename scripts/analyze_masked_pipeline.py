#!/usr/bin/env python3
"""Verify mask/chunk pipeline does not drop content before the LLM."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.document_mask import MaskRegistry, unmask_text
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.masked_chunking import chunk_masked_text
from ydbdoc_review.masked_translate import (
    CopySegment,
    MaskedTranslateSegment,
    _max_chunk_chars,
    _prose_needs_translation,
    build_masked_segments,
    count_masked_stats,
)
from ydbdoc_review.placeholder_translate import _join_segments, _slice_lines

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")


def _line_set(start: int, end: int) -> set[int]:
    return set(range(start, end + 1))


def _cyrillic_count(s: str) -> int:
    return len(_CYRILLIC_RE.findall(s))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--source-path", default="ydb/docs/ru/core/reference/ydb-cli/parameterized-query-execution.md")
    ap.add_argument("--out", type=Path, default=None, help="Optional dump dir (runs dump_masked_requests too)")
    args = ap.parse_args()

    text = args.source.read_text(encoding="utf-8")
    n_lines = len(text.splitlines()) or 1
    regions = refine_tab_regions(
        text, analyze_document_structure(text, source_is_russian=True)
    )
    registry = MaskRegistry()
    segments = build_masked_segments(text, regions, registry, source_is_russian=True)
    copy_n, tr_n, _ = count_masked_stats(segments)
    max_chars = _max_chunk_chars()

    errors: list[str] = []
    warnings: list[str] = []

    # Region coverage
    covered: set[int] = set()
    for r in regions:
        covered |= _line_set(r.start_line, r.end_line)
    missing_lines = set(range(1, n_lines + 1)) - covered
    if missing_lines:
        errors.append(f"regions miss {len(missing_lines)} line(s): {sorted(missing_lines)[:20]}...")

    # Segment line coverage (must partition file)
    seg_lines: dict[int, list[int]] = {}
    for i, seg in enumerate(segments):
        for ln in range(seg.start_line, seg.end_line + 1):
            seg_lines.setdefault(ln, []).append(i)
    for ln, owners in sorted(seg_lines.items()):
        if len(owners) != 1:
            errors.append(f"line {ln} in {len(owners)} segments: {owners}")
    uncovered = set(range(1, n_lines + 1)) - set(seg_lines)
    if uncovered:
        errors.append(f"segments miss {len(uncovered)} line(s)")

    # Reconstruct source from segment bodies (pre-LLM)
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, CopySegment):
            parts.append(seg.text)
        else:
            parts.append(seg.source_text)
    reconstructed = _join_segments(parts)
    if reconstructed != text:
        # _join_segments may insert glue newlines; compare line-by-line
        if reconstructed.splitlines() != text.splitlines():
            errors.append(
                "segment bodies + _join_segments != source "
                f"(chars {len(reconstructed)} vs {len(text)}, "
                f"lines {len(reconstructed.splitlines())} vs {n_lines})"
            )
        else:
            warnings.append("_join_segments: same lines, different trailing newlines only")

    total_chunks = 0
    chunk_rows: list[str] = []

    print(f"Source: {args.source} ({len(text)} chars, {n_lines} lines)")
    print(f"Regions: {len(regions)} | Segments: copy={copy_n} translate={tr_n} | max_chunk={max_chars}")
    print(f"Placeholders in registry: {len(registry.atoms)}")
    print()

    for i, seg in enumerate(segments, start=1):
        tag = f"seg{i:02d} L{seg.start_line}-{seg.end_line}"
        if isinstance(seg, CopySegment):
            cy = _cyrillic_count(seg.text)
            print(f"{tag} COPY {len(seg.text)} chars cyrillic={cy}")
            if cy > 20:
                warnings.append(f"{tag} COPY has {cy} cyrillic chars (RU in EN output path)")
            continue

        assert isinstance(seg, MaskedTranslateSegment)
        body = seg.source_text
        masked = seg.masked_text
        if body != _slice_lines(text, seg.start_line, seg.end_line):
            errors.append(f"{tag} source_text != slice from file")

        unmasked = unmask_text(masked, registry)
        if unmasked != body:
            errors.append(
                f"{tag} unmask(masked) != source_text "
                f"({len(unmasked)} vs {len(body)} chars)"
            )

        needs = _prose_needs_translation(masked, source_is_russian=True)
        chunks = chunk_masked_text(masked, max_chars=max_chars)
        joined = "".join(chunks)
        if joined != masked:
            errors.append(
                f"{tag} join(chunks) != masked_text "
                f"({len(joined)} vs {len(masked)} chars, {len(chunks)} chunks)"
            )

        total_chunks += len(chunks) if needs else 0
        cy_src = _cyrillic_count(body)
        cy_mask = _cyrillic_count(masked)
        for ci, ch in enumerate(chunks, start=1):
            chunk_rows.append(
                f"{tag} chunk {ci}/{len(chunks)} | in={len(ch)} cy={_cyrillic_count(ch)} | "
                f"action={seg.action} needs_llm={needs}"
            )

        est_out_tokens = max(2048, min(len(masked) * 2 + 1024, 32_768))
        print(
            f"{tag} TRANSLATE action={seg.action} needs_llm={needs} "
            f"body={len(body)} masked={len(masked)} chunks={len(chunks)} "
            f"cyrillic body={cy_src} masked={cy_mask} est_max_out={est_out_tokens}"
        )
        if not needs and cy_src > 0:
            warnings.append(f"{tag} skipped LLM but body has cyrillic ({cy_src}) -> RU stays in output")

    print()
    print(f"Total LLM sub-chunks (needs translation): {total_chunks}")
    print()
    for row in chunk_rows:
        print(row)

    if args.out:
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "dump_masked_requests.py"),
                "--source",
                str(args.source),
                "--source-path",
                args.source_path,
                "--out",
                str(args.out),
            ],
            check=True,
        )

    print()
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print("ERRORS (pipeline loses or corrupts data BEFORE LLM):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: no pre-LLM data loss detected (chunk join, mask/unmask, line coverage).")
    if total_chunks > 1:
        print(
            f"Note: {total_chunks} separate LLM calls; content loss after this point "
            "is model/truncation/guard fallback, not chunking."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
