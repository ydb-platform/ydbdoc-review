"""Frozen-B reader and independent final-link redirect contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.github.workflow import _docs_text_reader
from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets

DOCS_ROOT = "ydb/docs"
PAGE = f"{DOCS_ROOT}/en/core/guide/referrer.md"
OLD = f"{DOCS_ROOT}/en/core/old/target.md"
LIVE = f"{DOCS_ROOT}/en/core/live/target.md"
REDIRECTS = "common:\n  - from: /old/(.*)$\n    to: /live/$1\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def test_authority_reader_routes_ru_to_r_redirects_to_b_and_en_to_k(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    ru = f"{DOCS_ROOT}/ru/core/guide/source.md"
    en = f"{DOCS_ROOT}/en/core/guide/source.md"
    redirects = f"{DOCS_ROOT}/redirects.yaml"

    _write(repo, ru, "H0 RU\n")
    _write(repo, en, "H0 EN\n")
    h0 = _commit(repo, "H0")
    _write(repo, ru, "R RU\n")
    r_sha = _commit(repo, "R")
    _write(repo, redirects, REDIRECTS)
    _write(repo, en, "B EN\n")
    b_sha = _commit(repo, "B")
    _write(repo, redirects, "common:\n  - from: /old/(.*)$\n    to: /wrong/$1\n")
    _write(repo, en, "K EN\n")
    k_sha = _commit(repo, "K")

    authority = RuAuthority(
        source_repo="owner/repo",
        source_pr=1,
        source_base_sha=h0,
        source_head_sha=r_sha,
        baseline_sha=b_sha,
        ru_sha=r_sha,
        mode=RuAuthorityMode.SOURCE_PRESERVING,
    )
    read = _docs_text_reader(str(repo), k_sha, authority=authority, docs_root=DOCS_ROOT)

    assert read(ru) == "R RU\n"
    assert read(en) == "K EN\n"
    assert read(redirects) == REDIRECTS


def test_final_gate_uses_b_redirect_but_validates_actual_final_target() -> None:
    baseline = {f"{DOCS_ROOT}/redirects.yaml": REDIRECTS, LIVE: "# Live {#ok}\n"}
    final = {LIVE: "# Live {#ok}\n"}
    text = "See [target](../old/target.md#ok).\n"

    assert check_en_page_link_targets(
        PAGE,
        text,
        read_text=final.get,
        baseline_read_text=baseline.get,
    ) == []
    assert check_en_page_link_targets(
        PAGE,
        text,
        read_text=lambda _path: None,
        baseline_read_text=baseline.get,
    )


def test_rule_only_in_candidate_and_absent_b_target_remain_blocking() -> None:
    final = {LIVE: "# Live\n"}
    text = "See [target](../old/target.md).\n"
    assert check_en_page_link_targets(
        PAGE,
        text,
        read_text=final.get,
        baseline_read_text=lambda _path: None,
    )
    assert check_en_page_link_targets(
        PAGE,
        text,
        read_text=final.get,
        baseline_read_text=lambda path: REDIRECTS if path.endswith("redirects.yaml") else None,
    )
