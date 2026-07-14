#!/usr/bin/env python3
"""Download real ydb/docs navigation fixtures for nav scope planner tests.

Usage:
  python scripts/fetch_nav_fixtures.py case_45181
  python scripts/fetch_nav_fixtures.py --all
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "ydb-platform/ydb"
API = f"https://api.github.com/repos/{REPO}/contents"

CASES: dict[str, dict] = {
    "case_45181": {
        "description": "Source PR #45181 — topic+d diagnostics; sqs-api via reference toc include",
        "source_pr": 45181,
        "head_sha": "f24ddcca8bf41942f2d7f390778545cc87ed63c1",
        "base_ref": "main",
        "pr_diff_ru": [
            "ydb/docs/ru/core/devops/observability/diagnostics.md",
            "ydb/docs/ru/core/reference/ydb-sdk/topic.md",
        ],
        "files": [
            ("ydb/docs/ru/core/devops/observability/diagnostics.md", "head"),
            ("ydb/docs/ru/core/reference/ydb-sdk/topic.md", "head"),
            ("ydb/docs/ru/core/devops/observability/toc_p.yaml", "main"),
            ("ydb/docs/ru/core/reference/ydb-sdk/toc_p.yaml", "main"),
            ("ydb/docs/ru/core/reference/ydb-sdk/toc_i.yaml", "main"),
            ("ydb/docs/ru/core/reference/toc_p.yaml", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/toc_p.yaml", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/toc_i.yaml", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/index.md", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/auth.md", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/examples.md", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/_includes/limitations.md", "main"),
            (
                "ydb/docs/ru/core/reference/sqs-api/_includes/examples_prerequisites.md",
                "main",
            ),
        ],
    },
    "case_43530": {
        "description": "Source PR #43530 — OTel observability with explicit toc edits",
        "source_pr": 43530,
        "head_sha": "2e849cb25b73cace89e435b53ca172efca7cb215",
        "base_ref": "main",
        "pr_diff_ru": [
            "ydb/docs/ru/core/recipes/ydb-sdk/debug.md",
            "ydb/docs/ru/core/recipes/ydb-sdk/index.md",
            "ydb/docs/ru/core/recipes/ydb-sdk/toc_i.yaml",
            "ydb/docs/ru/core/reference/ydb-sdk/index.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/index.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/logging/logging.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/logging/opentelemetry.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/logging/toc_p.yaml",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/metrics/opentelemetry.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/metrics/prometheus.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/metrics/toc_p.yaml",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/toc_p.yaml",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/tracing/jaeger.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/tracing/opentelemetry.md",
            "ydb/docs/ru/core/reference/ydb-sdk/observability/tracing/toc_p.yaml",
            "ydb/docs/ru/core/reference/ydb-sdk/toc_i.yaml",
        ],
        "files": [
            ("ydb/docs/ru/core/reference/ydb-sdk/toc_i.yaml", "head"),
            ("ydb/docs/ru/core/reference/ydb-sdk/observability/toc_p.yaml", "head"),
            ("ydb/docs/ru/core/reference/ydb-sdk/observability/logging/toc_p.yaml", "head"),
            ("ydb/docs/ru/core/reference/ydb-sdk/observability/metrics/toc_p.yaml", "head"),
            ("ydb/docs/ru/core/reference/ydb-sdk/observability/tracing/toc_p.yaml", "head"),
            ("ydb/docs/ru/core/recipes/ydb-sdk/toc_i.yaml", "head"),
        ],
    },
    "case_44820": {
        "description": "Source PR #44820 — SQS API docs added in RU diff",
        "source_pr": 44820,
        "head_sha": "7d3f9db51edc93b661c379ca9059512deda63a9f",
        "base_ref": "main",
        "pr_diff_ru": [
            "ydb/docs/ru/core/reference/sqs-api/auth.md",
            "ydb/docs/ru/core/reference/sqs-api/examples.md",
            "ydb/docs/ru/core/reference/sqs-api/index.md",
            "ydb/docs/ru/core/reference/sqs-api/_includes/limitations.md",
            "ydb/docs/ru/core/reference/sqs-api/_includes/examples_prerequisites.md",
            "ydb/docs/ru/core/reference/toc_p.yaml",
        ],
        "files": [
            ("ydb/docs/ru/core/reference/toc_p.yaml", "head"),
            ("ydb/docs/ru/core/reference/sqs-api/toc_p.yaml", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/toc_i.yaml", "main"),
            ("ydb/docs/ru/core/reference/sqs-api/index.md", "head"),
            ("ydb/docs/ru/core/reference/sqs-api/auth.md", "head"),
            ("ydb/docs/ru/core/reference/sqs-api/examples.md", "head"),
            (
                "ydb/docs/ru/core/reference/sqs-api/_includes/limitations.md",
                "head",
            ),
            (
                "ydb/docs/ru/core/reference/sqs-api/_includes/examples_prerequisites.md",
                "head",
            ),
        ],
    },
    "case_44457": {
        "description": "Source PR #44457 — query_execution split; partial EN sidebar",
        "source_pr": 44457,
        "head_sha": "9aae21e0a72893f84fe9900771f12efa4b879d12",
        "base_ref": "82821309d53c77f372ec99b5d35574881f92a375",
        "pr_diff_ru": [
            "ydb/docs/ru/core/concepts/glossary.md",
            "ydb/docs/ru/core/concepts/query_execution/execution_process.md",
            "ydb/docs/ru/core/concepts/query_execution/index.md",
            "ydb/docs/ru/core/concepts/query_execution/toc_i.yaml",
        ],
        "en_present_at_base": [
            "ydb/docs/en/core/concepts/glossary.md",
            "ydb/docs/en/core/concepts/query_execution/index.md",
            "ydb/docs/en/core/concepts/query_execution/toc_i.yaml",
            "ydb/docs/en/core/concepts/toc_p.yaml",
            "ydb/docs/en/core/toc_p.yaml",
            "ydb/docs/en/core/postgresql/connect.md",
            "ydb/docs/en/core/concepts/secondary_indexes.md",
        ],
        "files": [
            ("ydb/docs/ru/core/concepts/glossary.md", "head"),
            ("ydb/docs/ru/core/concepts/query_execution/execution_process.md", "head"),
            ("ydb/docs/ru/core/concepts/query_execution/index.md", "head"),
            ("ydb/docs/ru/core/concepts/query_execution/toc_i.yaml", "head"),
            ("ydb/docs/ru/core/concepts/query_execution/toc_p.yaml", "base"),
            ("ydb/docs/ru/core/concepts/toc_p.yaml", "base"),
            ("ydb/docs/ru/core/toc_p.yaml", "base"),
        ],
        "ru_at_base": [
            ("ydb/docs/ru/core/concepts/query_execution/toc_i.yaml", "base"),
        ],
    },
}


def _fetch(path: str, ref: str) -> str | None:
    url = f"{API}/{path}?ref={ref}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ydbdoc-review-fixture-fetch",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    content = data.get("content")
    if not content:
        return None
    return base64.b64decode(content).decode("utf-8")


def fetch_case(case_id: str, out_root: Path) -> None:
    spec = CASES[case_id]
    case_dir = out_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    head_sha = spec["head_sha"]
    base_ref = spec["base_ref"]
    manifest: dict = {
        "case_id": case_id,
        "description": spec["description"],
        "source_pr": spec["source_pr"],
        "head_sha": head_sha,
        "base_ref": base_ref,
        "pr_diff_ru": spec["pr_diff_ru"],
        "files": {},
    }

    for repo_path, source in spec["files"]:
        ref = head_sha if source == "head" else (base_ref if source == "base" else source)
        text = _fetch(repo_path, ref)
        rel = repo_path.replace("ydb/docs/", "")
        dest = case_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            manifest["files"][repo_path] = {"ref": ref, "missing": True}
            continue
        dest.write_text(text, encoding="utf-8")
        manifest["files"][repo_path] = {"ref": ref, "path": str(dest.relative_to(out_root))}

    if spec.get("en_present_at_base"):
        manifest["en_present_at_base"] = spec["en_present_at_base"]

    ru_at_base: dict[str, dict] = {}
    for repo_path, source in spec.get("ru_at_base") or []:
        ref = base_ref if source == "base" else head_sha
        text = _fetch(repo_path, ref)
        rel = repo_path.replace("ydb/docs/", "")
        dest = case_dir / "ru_base" / rel.replace("ru/", "", 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            ru_at_base[repo_path] = {"ref": ref, "missing": True}
            continue
        dest.write_text(text, encoding="utf-8")
        ru_at_base[repo_path] = {"ref": ref, "path": str(dest.relative_to(out_root))}
    if ru_at_base:
        manifest["ru_at_base"] = ru_at_base

    (case_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {case_dir} ({len(manifest['files'])} entries)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", nargs="?", help="Case id, e.g. case_45181")
    parser.add_argument("--all", action="store_true", help="Fetch all cases")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tests/fixtures/nav_cases"),
        help="Output directory",
    )
    args = parser.parse_args(argv)

    if args.all:
        for case_id in CASES:
            fetch_case(case_id, args.out)
        return 0
    if not args.case_id or args.case_id not in CASES:
        parser.error(f"Unknown case. Choose from: {', '.join(CASES)}")
    fetch_case(args.case_id, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
