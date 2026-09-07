"""Execution contracts for proof-based source-preserving translation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    parse_authority_evidence,
    render_authority_evidence,
)
from ydbdoc_review.navigation.scope_planner import plan_translation_scope
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.reporting.builder import build_coverage_summary
from ydbdoc_review.translation.coverage import (
    CoveragePlan,
    CoverageUnit,
    assemble_coverage,
    build_coverage_evidence,
    load_coverage_evidence,
    save_coverage_evidence,
    validate_coverage_evidence,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _insertion_plan(en: str) -> CoveragePlan:
    source = "## New\n\nRequired source.\n"
    return CoveragePlan(
        source_path="ydb/docs/ru/core/concepts/glossary.md",
        source_hash=_hash(source),
        en_hash=_hash(en),
        units=(
            CoverageUnit(
                key="1" * 64,
                action="translate_required",
                source=source,
                en_span=(len(en), len(en)),
                target=None,
                reason="required missing section",
            ),
        ),
        required_fragments=frozenset({"user-token"}),
        mode="units",
    )


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


def test_insertion_preserves_all_unaffected_en_bytes() -> None:
    en = "# Existing\n\nCorrect accepted prose.\n"
    plan = _insertion_plan(en)
    addition = "\n## User token {#user-token}\n\nTranslated definition.\n"

    result = assemble_coverage(
        plan,
        existing_en=en,
        translated_units={"1" * 64: addition},
    )

    assert result == en + addition


def test_assembly_rejects_changed_baseline_en_hash() -> None:
    en = "Accepted EN.\n"
    with pytest.raises(ValueError, match="EN hash"):
        assemble_coverage(
            _insertion_plan(en),
            existing_en="Changed EN.\n",
            translated_units={"1" * 64: "Translated.\n"},
        )


def test_assembly_rejects_malformed_source_projection_hash() -> None:
    en = "Accepted EN.\n"
    plan = replace(_insertion_plan(en), source_hash="not-a-hash")
    with pytest.raises(ValueError, match="source_hash"):
        assemble_coverage(
            plan,
            existing_en=en,
            translated_units={"1" * 64: "Translated.\n"},
        )


@pytest.mark.parametrize(
    "span",
    [(-1, 0), (2, 1), (0, 999)],
    ids=["negative", "reversed", "out-of-bounds"],
)
def test_assembly_rejects_invalid_span(span: tuple[int, int]) -> None:
    en = "Accepted EN.\n"
    plan = _insertion_plan(en)
    unit = replace(plan.units[0], en_span=span)
    with pytest.raises(ValueError, match="span"):
        assemble_coverage(
            replace(plan, units=(unit,)),
            existing_en=en,
            translated_units={unit.key: "Translated.\n"},
        )


def test_assembly_rejects_overlapping_spans() -> None:
    en = "Accepted EN.\n"
    first = CoverageUnit(
        key="1" * 64,
        action="translate_required",
        source="A",
        en_span=(0, 8),
        target=None,
        reason="first",
    )
    second = CoverageUnit(
        key="2" * 64,
        action="translate_required",
        source="B",
        en_span=(5, 10),
        target=None,
        reason="second",
    )
    plan = CoveragePlan(
        source_path="ydb/docs/ru/a.md",
        source_hash=_hash("AB"),
        en_hash=_hash(en),
        units=(first, second),
        required_fragments=frozenset(),
        mode="units",
    )

    with pytest.raises(ValueError, match="overlap"):
        assemble_coverage(
            plan,
            existing_en=en,
            translated_units={first.key: "One", second.key: "Two"},
        )


def test_assembly_rejects_missing_translated_unit() -> None:
    en = "Accepted EN.\n"
    with pytest.raises(ValueError, match="missing translated unit"):
        assemble_coverage(_insertion_plan(en), existing_en=en, translated_units={})


def test_assembly_rejects_unresolved_unit() -> None:
    en = "Accepted EN.\n"
    plan = _insertion_plan(en)
    unresolved = replace(plan.units[0], action="unresolved")
    with pytest.raises(ValueError, match="unresolved"):
        assemble_coverage(
            replace(plan, units=(unresolved,)),
            existing_en=en,
            translated_units={unresolved.key: "Translated.\n"},
        )


def test_versioned_coverage_evidence_round_trips_and_binds_authority() -> None:
    en_path = "ydb/docs/en/core/concepts/glossary.md"
    baseline = "# Existing\n\nAccepted.\n"
    plan = _insertion_plan(baseline)
    candidate = baseline + "\n## User token {#user-token}\n\nDefinition.\n"
    evidence = build_coverage_evidence(
        authority=_authority(),
        candidate_sha="4" * 40,
        plans={en_path: plan},
        baseline_en={en_path: baseline},
        candidate_files={en_path: candidate},
    )
    store = InMemoryTranscriptStore()

    save_coverage_evidence(store, "run-1", evidence)
    loaded = load_coverage_evidence(
        store,
        "run-1",
        candidate_sha="4" * 40,
        expected_digest=evidence.digest,
    )

    assert loaded == evidence
    assert loaded.plans == ((en_path, plan),)
    assert loaded.baseline_en_hashes == ((en_path, _hash(baseline)),)
    assert loaded.candidate_file_hashes == ((en_path, _hash(candidate)),)

    provenance = TranslationArtifactProvenance(
        _authority(),
        "4" * 40,
        coverage_version=1,
        coverage_run_id="run-1",
        coverage_digest=evidence.digest,
    )
    assert parse_authority_evidence(render_authority_evidence(provenance)) == provenance


def test_old_full_mode_authority_envelope_remains_compatible() -> None:
    provenance = TranslationArtifactProvenance(_authority(), "4" * 40)
    assert parse_authority_evidence(render_authority_evidence(provenance)) == provenance


@pytest.mark.parametrize("mutation", ["missing", "tampered", "different-candidate"])
def test_units_evidence_missing_or_mismatched_fails_closed(mutation: str) -> None:
    en_path = "ydb/docs/en/core/concepts/glossary.md"
    baseline = "Accepted EN.\n"
    evidence = build_coverage_evidence(
        authority=_authority(),
        candidate_sha="4" * 40,
        plans={en_path: _insertion_plan(baseline)},
        baseline_en={en_path: baseline},
        candidate_files={en_path: baseline + "Translated.\n"},
    )
    store = InMemoryTranscriptStore()
    save_coverage_evidence(store, "run-1", evidence)
    if mutation == "missing":
        store = InMemoryTranscriptStore()
    elif mutation == "tampered":
        key = "translation/v1/coverage/" + "4" * 40 + ".json"
        raw = store.get("run-1", key)
        assert raw is not None
        store.put("run-1", key, raw[:-1] + b"x")

    with pytest.raises(ValueError, match="coverage evidence"):
        load_coverage_evidence(
            store,
            "run-1",
            candidate_sha=("5" * 40 if mutation == "different-candidate" else "4" * 40),
            expected_digest=evidence.digest,
        )


def test_scope_records_only_actual_exact_fragment_dependency_metadata() -> None:
    page = "ydb/docs/ru/core/page.md"
    glossary = "ydb/docs/ru/core/glossary.md"
    texts = {
        page: "[Токен](glossary.md#user-token) and [plain](glossary.md).\n",
        glossary: "# Глоссарий\n\n## Токен {#user-token}\n\nОпределение.\n",
    }
    en_texts = {
        "ydb/docs/en/core/page.md": "Old.\n",
        "ydb/docs/en/core/glossary.md": "# Glossary\n",
    }

    plan = plan_translation_scope(
        [(page, "modified")],
        read_ru=texts.get,
        read_en_base=en_texts.get,
        read_ru_base=lambda _path: None,
        docs_root="ydb/docs",
    )

    assert plan.required_fragments_for(glossary) == frozenset({"user-token"})
    assert plan.required_fragments_for(page) == frozenset()


def test_fresh_manifest_cannot_bless_candidate_drift_outside_authorized_span() -> None:
    en_path = "ydb/docs/en/core/concepts/glossary.md"
    baseline = "# Existing\n\nAccepted prose.\n"
    plan = _insertion_plan(baseline)
    source = plan.units[0].source
    candidate = "# Existing\n\nRegressed prose.\n\n## User token {#user-token}\n\nDefinition.\n"
    evidence = build_coverage_evidence(
        authority=_authority(),
        candidate_sha="4" * 40,
        plans={en_path: plan},
        baseline_en={en_path: baseline},
        candidate_files={en_path: candidate},
    )

    with pytest.raises(ValueError, match="changed unaffected EN"):
        validate_coverage_evidence(
            evidence,
            authority=_authority(),
            read_source=lambda _path: source,
            read_baseline_en=lambda _path: baseline,
            read_candidate=lambda _path: candidate,
        )


def test_reporting_counts_units_and_lists_real_fallback_reasons() -> None:
    result = SimpleNamespace(
        pair_results=[
            SimpleNamespace(
                file_result=SimpleNamespace(
                    differential_meta={
                        "mode": "units",
                        "seeded": 4,
                        "pending": 1,
                        "protected": 2,
                        "fallback_reasons": (),
                    }
                )
            ),
            SimpleNamespace(
                file_result=SimpleNamespace(
                    differential_meta={
                        "mode": "full",
                        "seeded": 0,
                        "pending": 3,
                        "protected": 0,
                        "fallback_reasons": ("changed include identity",),
                    }
                )
            ),
        ]
    )

    summary = build_coverage_summary(result)

    assert "reused 4, translated 4, protected 2" in summary
    assert "changed include identity" in summary


def test_k2_requires_a_fresh_candidate_key_and_digest() -> None:
    en_path = "ydb/docs/en/core/concepts/glossary.md"
    baseline = "# Existing\n\nAccepted.\n"
    plan = _insertion_plan(baseline)
    store = InMemoryTranscriptStore()
    first = build_coverage_evidence(
        authority=_authority(),
        candidate_sha="4" * 40,
        plans={en_path: plan},
        baseline_en={en_path: baseline},
        candidate_files={
            en_path: baseline + "\n## User token {#user-token}\n\nFirst.\n"
        },
    )
    save_coverage_evidence(store, "run-1", first)
    repaired = build_coverage_evidence(
        authority=_authority(),
        candidate_sha="5" * 40,
        plans={en_path: plan},
        baseline_en={en_path: baseline},
        candidate_files={
            en_path: baseline + "\n## User token {#user-token}\n\nRepaired.\n"
        },
    )
    save_coverage_evidence(store, "run-1", repaired)

    with pytest.raises(ValueError, match="digest mismatch"):
        load_coverage_evidence(
            store,
            "run-1",
            candidate_sha="5" * 40,
            expected_digest=first.digest,
        )
    assert load_coverage_evidence(
        store,
        "run-1",
        candidate_sha="5" * 40,
        expected_digest=repaired.digest,
    ) == repaired
