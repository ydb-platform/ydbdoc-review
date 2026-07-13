"""Tests for toc href supplementation when sidebars mirror RU-only pages."""

from __future__ import annotations

from unittest.mock import patch

from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
from ydbdoc_review.pipeline.toc_href_supplement import supplement_toc_href_pairs

RU_SQS_INDEX = "ydb/docs/ru/core/reference/sqs-api/index.md"
RU_SQS_AUTH = "ydb/docs/ru/core/reference/sqs-api/auth.md"
RU_SQS_EXAMPLES = "ydb/docs/ru/core/reference/sqs-api/examples.md"
EN_SQS_INDEX = "ydb/docs/en/core/reference/sqs-api/index.md"
EN_SQS_AUTH = "ydb/docs/en/core/reference/sqs-api/auth.md"
EN_SQS_EXAMPLES = "ydb/docs/en/core/reference/sqs-api/examples.md"
RU_SQS_TOC_P = "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml"
EN_SQS_TOC_P = "ydb/docs/en/core/reference/sqs-api/toc_p.yaml"
RU_SQS_TOC_I = "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml"
EN_SQS_TOC_I = "ydb/docs/en/core/reference/sqs-api/toc_i.yaml"

SQS_RU_TOC_P = """
items:
- name: Overview
  href: index.md
- include: { mode: link, path: toc_i.yaml }
""".strip()

SQS_RU_TOC_I = """
items:
- name: Auth
  href: auth.md
- name: Examples
  href: examples.md
""".strip()

RU_INDEX_BODY = "# SQS API\n"
RU_AUTH_BODY = "# Auth\n"
RU_EXAMPLES_BODY = "# Examples\n"


def test_supplement_toc_href_pairs_adds_missing_en_pages_from_mirrored_sidebars():
    ru_files = {
        RU_SQS_TOC_P: SQS_RU_TOC_P,
        RU_SQS_TOC_I: SQS_RU_TOC_I,
        RU_SQS_INDEX: RU_INDEX_BODY,
        RU_SQS_AUTH: RU_AUTH_BODY,
        RU_SQS_EXAMPLES: RU_EXAMPLES_BODY,
    }
    en_on_main: set[str] = set()

    def _read(repo: str, path: str) -> str | None:
        return ru_files.get(path)

    def _read_ref(repo: str, ref: str, path: str) -> str | None:
        if ref == "origin/main" and path in en_on_main:
            return "existing"
        if ref == "origin/main" and path in ru_files:
            return ru_files[path]
        return None

    nav_pairs = [
        NavigationPair(
            ru_path=RU_SQS_TOC_P,
            en_path=EN_SQS_TOC_P,
            ru_changed=True,
            supplement_only=True,
        ),
        NavigationPair(
            ru_path=RU_SQS_TOC_I,
            en_path=EN_SQS_TOC_I,
            ru_changed=True,
            supplement_only=True,
        ),
    ]
    seed = [
        DocPair(
            ru_path="ydb/docs/ru/core/reference/ydb-sdk/topic.md",
            en_path="ydb/docs/en/core/reference/ydb-sdk/topic.md",
            ru_changed=True,
        )
    ]

    with (
        patch("ydbdoc_review.pipeline.toc_href_supplement.read_text", side_effect=_read),
        patch(
            "ydbdoc_review.pipeline.toc_href_supplement.read_text_at_ref",
            side_effect=_read_ref,
        ),
    ):
        pairs, changes = supplement_toc_href_pairs(
            seed,
            nav_pairs,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
        )

    en_paths = {p.en_path for p in pairs}
    assert EN_SQS_INDEX in en_paths
    assert EN_SQS_AUTH in en_paths
    assert EN_SQS_EXAMPLES in en_paths
    assert {c[0] for c in changes} == {RU_SQS_INDEX, RU_SQS_AUTH, RU_SQS_EXAMPLES}


def test_supplement_toc_href_pairs_skips_pages_already_on_en_main():
    ru_files = {
        RU_SQS_TOC_P: SQS_RU_TOC_P,
        RU_SQS_INDEX: RU_INDEX_BODY,
    }
    en_on_main = {EN_SQS_INDEX}

    def _read(repo: str, path: str) -> str | None:
        return ru_files.get(path)

    def _read_ref(repo: str, ref: str, path: str) -> str | None:
        if ref == "origin/main" and path in en_on_main:
            return "existing"
        if ref == "origin/main" and path in ru_files:
            return ru_files[path]
        return None

    nav_pairs = [
        NavigationPair(
            ru_path=RU_SQS_TOC_P,
            en_path=EN_SQS_TOC_P,
            ru_changed=True,
            supplement_only=True,
        ),
    ]

    with (
        patch("ydbdoc_review.pipeline.toc_href_supplement.read_text", side_effect=_read),
        patch(
            "ydbdoc_review.pipeline.toc_href_supplement.read_text_at_ref",
            side_effect=_read_ref,
        ),
    ):
        pairs, changes = supplement_toc_href_pairs(
            [],
            nav_pairs,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
        )

    assert changes == []
    assert all(p.en_path != EN_SQS_INDEX for p in pairs)
