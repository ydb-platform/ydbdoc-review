#!/usr/bin/env python3
"""
Translate one RU markdown file locally (same path as doc_translate / masked pipeline).

Use for A/B between translator models, e.g. yandexgpt-5.1 vs DeepSeek:

  export YANDEX_CLOUD_FOLDER_DOC_REVIEW=...
  export YANDEX_CLOUD_API_KEY_DOC_REVIEW=...
  PYTHONPATH=src python scripts/translate_one_file.py \\
    --source debug/pqe-source-ru.md \\
    --out debug/pqe-en-deepseek.md \\
    --model deepseek-v4-flash/latest
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

from ydbdoc_review.config import Settings
from ydbdoc_review.pipeline_v2 import translate_document
from ydbdoc_review.structural_resync import structural_report

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Translate one file (RU→EN) with configured FM model.")
    ap.add_argument("--source", type=Path, required=True, help="Local RU markdown file.")
    ap.add_argument("--out", type=Path, required=True, help="Write EN markdown here.")
    ap.add_argument(
        "--source-path",
        default="",
        help="Logical repo path for logs/prompts (default: --source name).",
    )
    ap.add_argument(
        "--model",
        default="",
        help="Override translator slug or gpt://<folder>/… URI.",
    )
    ap.add_argument(
        "--disable-self-check",
        action="store_true",
        help="Set YDBDOC_TRANSLATION_SELF_CHECK=false for this run only.",
    )
    args = ap.parse_args()

    if args.model:
        os.environ["YDBDOC_MODEL_TRANSLATE"] = args.model.strip()
    if args.disable_self_check:
        os.environ["YDBDOC_TRANSLATION_SELF_CHECK"] = "false"

    settings = Settings.from_env()
    if args.model:
        settings = replace(settings, model_translate=args.model.strip())
    try:
        settings.validate_yandex()
    except SystemExit as e:
        print(e, file=sys.stderr)
        print(
            "\nSet YANDEX_CLOUD_FOLDER_DOC_REVIEW + YANDEX_CLOUD_API_KEY_DOC_REVIEW "
            "(or OPENAI_* for another gateway). See .env.example.",
            file=sys.stderr,
        )
        return 1

    ru = args.source.read_text(encoding="utf-8")
    logical = args.source_path.strip() or args.source.name

    print(f"Translator model: {settings.model_translate}")
    print(f"Source: {args.source} ({len(ru)} chars, {len(ru.splitlines())} lines)")
    print(f"Logical path: {logical}")
    print("Calling translate_document (masked + postprocess) …")

    en, mode = translate_document(
        settings,
        source_path=logical,
        source_full=ru,
        source_lang="Russian",
        target_lang="English",
        en_on_main=None,
        ru_pr_diff=None,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(en, encoding="utf-8")

    rep = structural_report(ru, en)
    cy_en = len(_CYRILLIC_RE.findall(en))
    print()
    print(f"Mode: {mode}")
    print(f"Wrote: {args.out} ({len(en)} chars, {len(en.splitlines())} lines)")
    print(
        f"Structure: fences {rep.en_fence_blocks}/{rep.ru_fence_blocks}, "
        f"H2 {rep.en_h2}/{rep.ru_h2}, lines {rep.en_lines}/{rep.ru_lines} "
        f"({'OK' if rep.ok else 'MISMATCH'})"
    )
    print(f"Cyrillic chars left in EN: {cy_en}")
    if not rep.ok:
        print("Warning: structural parity failed — compare with analyze_masked_pipeline.py first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
