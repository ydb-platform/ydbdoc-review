#!/usr/bin/env python3
"""List EN markdown pages not reachable from the EN Diplodoc toc graph.

Prefer the bilingual script::

  python scripts/find_toc_orphans.py --repo-path /path/to/ydb --locale en
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ydbdoc_review.validation.toc_targets import find_en_pages_missing_from_toc  # noqa: E402


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
    args = parser.parse_args(argv)
    orphans = find_en_pages_missing_from_toc(
        args.repo_path, docs_root=args.docs_root
    )
    if not orphans:
        print("No EN toc orphans found.")
        return 0
    print(f"EN pages missing from toc graph ({len(orphans)}):")
    for path in orphans:
        print(path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
