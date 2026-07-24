#!/usr/bin/env python3
"""List markdown pages not reachable from the Diplodoc toc graph (RU and/or EN).

Usage (from a ydb checkout or any repo with ydb/docs):

  python scripts/find_toc_orphans.py --repo-path /path/to/ydb
  python scripts/find_toc_orphans.py --repo-path /path/to/ydb --locale en
  python scripts/find_toc_orphans.py --repo-path /path/to/ydb --locale ru
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ydbdoc_review.validation.toc_targets import find_pages_missing_from_toc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Git checkout root that contains ydb/docs (default: .)",
    )
    parser.add_argument(
        "--docs-root",
        default="ydb/docs",
        help="Docs root relative to repo (default: ydb/docs)",
    )
    parser.add_argument(
        "--locale",
        choices=("en", "ru", "both"),
        default="both",
        help="Which locale tree to audit (default: both)",
    )
    args = parser.parse_args(argv)
    locales: tuple[str, ...] = (
        ("en", "ru") if args.locale == "both" else (args.locale,)
    )
    by_locale = find_pages_missing_from_toc(
        args.repo_path,
        locales=locales,
        docs_root=args.docs_root,
    )
    total = sum(len(v) for v in by_locale.values())
    if total == 0:
        labels = "+".join(locales)
        print(f"No toc orphans found ({labels}).")
        return 0
    for loc in locales:
        orphans = by_locale.get(loc, [])
        label = loc.upper()
        print(f"{label} pages missing from toc graph ({len(orphans)}):")
        for path in orphans:
            print(path)
        if loc != locales[-1]:
            print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
