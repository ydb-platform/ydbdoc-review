"""F-110: verify reports stay bound to the exact checked snapshots."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.pr import load_verify_pair_contents
from ydbdoc_review.github.provenance import RuAuthority, TranslationArtifactProvenance
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def _config():
    return load_config(
        env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
    )


def _report_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/page.md",
        en_path="ydb/docs/en/page.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="verify current translation",
    )
    issue = CriticIssueOut(
        segment_id="s0001",
        severity="warning",
        category="terminology",
        comment="term needs review",
        suggested_text="preferred term",
    )
    file_result = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Current translation.\n",
        segments_count=1,
        verdict="warnings",
        prompt_version="v1",
        critic_unresolved=CriticResponse(verdict="warnings", issues=[issue]),
        segment_locations={"s0001": "Introduction"},
        segment_lines={"s0001": (7, 7)},
        segment_excerpts={"s0001": "Current translation."},
        segment_source_excerpts={"s0001": "Текущий перевод."},
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="Current translation.\n",
                file_result=file_result,
            )
        ]
    )


def test_F110_new_state() -> None:
    """A later manual head cannot retarget an old report; a new verify gets new K."""
    old_k = "1" * 40
    new_k = "2" * 40
    mutable_head = "ydbdoc-review/pr-110"
    link = ReportLinkContext(github_repo="ydb-platform/ydb", ref=mutable_head)

    old_report = build_full_report(
        _report_result(),
        meta=ReportMeta(
            mode="doc_verify",
            report_number=1,
            elapsed_s=1,
            checkout_ref=old_k,
        ),
        config=_config(),
        link=link,
    )
    new_report = build_full_report(
        _report_result(),
        meta=ReportMeta(
            mode="doc_verify",
            report_number=2,
            elapsed_s=1,
            checkout_ref=new_k,
        ),
        config=_config(),
        link=link,
    )

    assert f"/blob/{old_k}/ydb/docs/en/page.md:7" in old_report
    assert f"/blob/{new_k}/" not in old_report
    assert f"/blob/{new_k}/ydb/docs/en/page.md:7" in new_report
    assert f"/blob/{mutable_head}/" not in old_report + new_report


def test_F110_without_dialogue(monkeypatch) -> None:
    """Frozen S and current K suffice; an unavailable S never becomes latest main."""
    source_s = "3" * 40
    baseline_b = "4" * 40
    old_k = "5" * 40
    new_k = "6" * 40
    pair = DocPair(
        ru_path="ydb/docs/ru/page.md",
        en_path="ydb/docs/en/page.md",
    )
    provenance = TranslationArtifactProvenance(
        authority=RuAuthority(
            source_repo="ydb-platform/ydb",
            source_pr=110,
            source_base_sha=baseline_b,
            source_head_sha=source_s,
            baseline_sha=baseline_b,
            ru_sha=source_s,
            mode=RuAuthorityMode.SOURCE_PRESERVING,
        ),
        candidate_sha=old_k,
    )
    reads: list[tuple[str, str]] = []

    def read_at_commit(_repo: str, ref: str, path: str) -> str | None:
        reads.append((ref, path))
        if ref == source_s:
            return "Исходное задание S.\n"
        if ref == old_k:
            return "Old verified result.\n"
        if ref == new_k:
            return "New manually edited result.\n"
        if ref == baseline_b:
            return "Baseline.\n"
        raise AssertionError(f"unexpected ref {ref}")

    monkeypatch.setattr("ydbdoc_review.github.pr.read_text_at_commit", read_at_commit)
    gh = MagicMock()

    old = load_verify_pair_contents(
        "/repo",
        [pair],
        merge_base_with="ignored-main",
        gh=gh,
        owner="ydb-platform",
        repo="ydb",
        source_pr=110,
        target_ref=old_k,
        provenance=provenance,
    )
    new = load_verify_pair_contents(
        "/repo",
        [pair],
        merge_base_with="newer-main",
        gh=gh,
        owner="ydb-platform",
        repo="ydb",
        source_pr=110,
        target_ref=new_k,
        provenance=provenance,
    )

    assert old[0].ru_text == new[0].ru_text == "Исходное задание S.\n"
    assert old[0].en_text == "Old verified result.\n"
    assert new[0].en_text == "New manually edited result.\n"
    assert all(ref not in {"ignored-main", "newer-main", "HEAD"} for ref, _ in reads)
    gh.get_pull.assert_not_called()

    gh.get_pull.return_value = {
        "merged": False,
        "head": {
            "sha": source_s,
            "repo": {
                "owner": {"login": "ydb-platform"},
                "name": "ydb",
            },
        },
        "base": {"sha": baseline_b},
    }
    gh.get_file_text.return_value = None
    monkeypatch.setattr(
        "ydbdoc_review.github.pr.read_text_at_ref",
        lambda _repo, ref, _path: (
            "Current K.\n" if ref == new_k else "Baseline.\n" if ref == baseline_b else None
        ),
    )

    limited = load_verify_pair_contents(
        "/repo",
        [pair],
        merge_base_with=baseline_b,
        gh=gh,
        owner="ydb-platform",
        repo="ydb",
        source_pr=110,
        target_ref=new_k,
    )

    assert limited[0].ru_text is None
    assert limited[0].en_text == "Current K.\n"
    assert all(call.args[3] != "latest-main" for call in gh.get_file_text.call_args_list)
