"""F-027: verification evidence stays bound to its frozen snapshots."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.pr import load_verify_pair_contents
from ydbdoc_review.github.provenance import RuAuthority, TranslationArtifactProvenance
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown


def _provenance() -> TranslationArtifactProvenance:
    return TranslationArtifactProvenance(
        authority=RuAuthority(
            source_repo="ydb-platform/ydb",
            source_pr=51079,
            source_base_sha="1" * 40,
            source_head_sha="2" * 40,
            baseline_sha="3" * 40,
            ru_sha="4" * 40,
            mode=RuAuthorityMode.SOURCE_PRESERVING,
        ),
        candidate_sha="5" * 40,
    )


def test_F027_repeat_verify() -> None:
    """A rerun reads the same frozen source R and checked candidate K."""
    reads: list[tuple[str, str]] = []

    def read_at_commit(_repo: str, ref: str, path: str) -> str:
        reads.append((ref, path))
        if ref == "4" * 40:
            return "source snapshot S\n"
        if ref == "k" * 40:
            return "checked translation K\n"
        if ref in {"1" * 40, "3" * 40}:
            return "publication baseline B\n"
        raise AssertionError(f"unexpected mutable ref: {ref}")

    pair = DocPair(
        ru_path="ydb/docs/ru/core/page.md",
        en_path="ydb/docs/en/core/page.md",
        en_changed=False,
    )
    gh = MagicMock()

    import ydbdoc_review.github.pr as pr_module

    original = pr_module.read_text_at_commit
    pr_module.read_text_at_commit = read_at_commit
    try:
        first = load_verify_pair_contents(
            "/repo",
            [pair],
            merge_base_with="main-now",
            gh=gh,
            owner="o",
            repo="r",
            source_pr=51079,
            target_ref="k" * 40,
            provenance=_provenance(),
        )
        second = load_verify_pair_contents(
            "/repo",
            [pair],
            merge_base_with="main-changed",
            gh=gh,
            owner="o",
            repo="r",
            source_pr=99999,
            target_ref="k" * 40,
            provenance=_provenance(),
        )
    finally:
        pr_module.read_text_at_commit = original

    assert first[0].ru_text == second[0].ru_text == "source snapshot S\n"
    assert first[0].en_text == second[0].en_text == "checked translation K\n"
    assert all(ref not in {"main-now", "main-changed", "HEAD"} for ref, _ in reads)
    gh.get_pull.assert_not_called()


def test_F027_map_not_enough() -> None:
    """An address map is insufficient until both locale declarations exist in K."""
    address_map = {"source-id": "target-id"}
    source_s = "## Заголовок {#source-id}\n"
    baseline_b = "## Старый заголовок {#old-id}\n"
    checked_k_without_target = "## Translated heading\n"
    checked_k_with_target = "## Translated heading {#target-id}\n"

    assert address_map["source-id"] == "target-id"
    assert fragment_declared_in_markdown(source_s, "source-id")
    assert fragment_declared_in_markdown(baseline_b, "old-id")
    assert not fragment_declared_in_markdown(checked_k_without_target, "target-id")
    assert fragment_declared_in_markdown(checked_k_with_target, "target-id")
    assert source_s == "## Заголовок {#source-id}\n"
