"""Coverage evidence is scoped to successful pair execution results."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.github.workflow import _persist_candidate_coverage_evidence
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.ops.translation_checkpoint import TranslationCheckpointError
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult
from ydbdoc_review.translation.coverage import (
    CoveragePlan,
    CoverageUnit,
    load_coverage_evidence,
)

CANDIDATE_SHA = "4" * 40
ELIGIBLE_EN = "ydb/docs/en/core/concepts/glossary.md"
SKIPPED_EN = "ydb/docs/en/core/security/_assets/user-token-lifecycle.md"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _authority() -> RuAuthority:
    return RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=51079,
        source_base_sha="1" * 40,
        source_head_sha="2" * 40,
        baseline_sha="3" * 40,
        ru_sha="2" * 40,
        mode=RuAuthorityMode.SOURCE_PRESERVING,
    )


def _fixture() -> tuple[list[PairContent], list[PairRunResult], str]:
    baseline = "# Existing\n\nAccepted prose.\n"
    translated = baseline + "\n## New\n\nRequired translation.\n"
    source = "## New\n\nRequired source.\n"
    eligible_pair = DocPair(
        ru_path="ydb/docs/ru/core/concepts/glossary.md",
        en_path=ELIGIBLE_EN,
        ru_changed=True,
    )
    eligible_plan = CoveragePlan(
        source_path=eligible_pair.ru_path,
        source_hash=_hash(source),
        en_hash=_hash(baseline),
        units=(
            CoverageUnit(
                key="1" * 64,
                action="translate_required",
                source=source,
                en_span=(len(baseline), len(baseline)),
                target=None,
                reason="required missing section",
            ),
        ),
        required_fragments=frozenset(),
        mode="units",
    )
    eligible_content = PairContent(
        pair=eligible_pair,
        ru_text=source,
        en_text=baseline,
        coverage_plan=eligible_plan,
    )
    eligible_result = PairRunResult(
        plan=PairPlan(
            pair=eligible_pair,
            action="translate_to_en",
            source_path=eligible_pair.ru_path,
            target_path=ELIGIBLE_EN,
            source_lang="ru",
            target_lang="en",
        ),
        source_text=source,
        target_text=translated,
    )

    skipped_source = "```mermaid\ngraph LR\n  User --> Token\n```\n"
    skipped_pair = DocPair(
        ru_path="ydb/docs/ru/core/security/_assets/user-token-lifecycle.md",
        en_path=SKIPPED_EN,
    )
    skipped_content = PairContent(
        pair=skipped_pair,
        ru_text=skipped_source,
        coverage_plan=CoveragePlan(
            source_path=skipped_pair.ru_path,
            source_hash=_hash(skipped_source),
            en_hash=None,
            units=(
                CoverageUnit(
                    key="2" * 64,
                    action="materialize_protected",
                    source=skipped_source,
                    en_span=None,
                    target=skipped_source,
                    reason="protected source structure",
                ),
            ),
            required_fragments=frozenset(),
            mode="full",
        ),
    )
    skipped_result = PairRunResult(
        plan=PairPlan(
            pair=skipped_pair,
            action="skip",
            source_path=skipped_pair.ru_path,
            target_path=SKIPPED_EN,
            source_lang="ru",
            target_lang="en",
        ),
        skipped=True,
    )
    return [eligible_content, skipped_content], [eligible_result, skipped_result], translated


def test_candidate_evidence_uses_only_successfully_materialized_pairs() -> None:
    contents, pair_results, translated = _fixture()
    store = InMemoryTranscriptStore()
    with patch(
        "ydbdoc_review.github.workflow.read_text_at_commit",
        return_value=translated,
    ) as read_candidate:
        evidence = _persist_candidate_coverage_evidence(
            repo_path="/repo",
            candidate_sha=CANDIDATE_SHA,
            authority=_authority(),
            contents=contents,
            pair_results=pair_results,
            store=store,
            run_id="run-filtered",
        )

    assert evidence is not None
    assert dict(evidence.plans) == {ELIGIBLE_EN: contents[0].coverage_plan}
    assert set(dict(evidence.candidate_file_hashes)) == {ELIGIBLE_EN}
    read_candidate.assert_called_once_with("/repo", CANDIDATE_SHA, ELIGIBLE_EN)
    assert load_coverage_evidence(
        store,
        "run-filtered",
        candidate_sha=CANDIDATE_SHA,
        expected_digest=evidence.digest,
    ) == evidence


def test_candidate_evidence_still_fails_for_missing_eligible_target() -> None:
    contents, pair_results, _translated = _fixture()
    with (
        patch(
            "ydbdoc_review.github.workflow.read_text_at_commit",
            return_value=None,
        ) as read_candidate,
        pytest.raises(
            TranslationCheckpointError,
            match=f"coverage evidence candidate file is missing: {ELIGIBLE_EN}",
        ),
    ):
        _persist_candidate_coverage_evidence(
            repo_path="/repo",
            candidate_sha=CANDIDATE_SHA,
            authority=_authority(),
            contents=contents,
            pair_results=pair_results,
            store=InMemoryTranscriptStore(),
            run_id="run-missing-eligible",
        )

    read_candidate.assert_called_once_with("/repo", CANDIDATE_SHA, ELIGIBLE_EN)
