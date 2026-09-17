"""Execution contracts for proof-based source-preserving translation."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    parse_authority_evidence,
    render_authority_evidence,
)
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.navigation.scope_planner import plan_translation_scope
from ydbdoc_review.navigation.toc import merge_en_toc_yaml
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.builder import build_coverage_summary
from ydbdoc_review.reporting.provenance_drift import build_later_ru_drift_report
from ydbdoc_review.translation.coverage import (
    CoveragePlan,
    CoverageUnit,
    assemble_coverage,
    build_coverage_evidence,
    load_coverage_evidence,
    save_coverage_evidence,
    validate_coverage_evidence,
)
from ydbdoc_review.translation.glossary import load_glossary


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


def test_independent_validation_rejects_semantically_unrelated_required_definition() -> None:
    en_path = "ydb/docs/en/core/concepts/glossary.md"
    baseline = "# Existing\n\nAccepted glossary prose.\n"
    plan = _insertion_plan(baseline)
    unrelated = (
        baseline
        + "\n## User token {#user-token}\n\n"
        + "This paragraph discusses an unrelated backup schedule.\n"
    )
    evidence = build_coverage_evidence(
        authority=_authority(),
        candidate_sha="4" * 40,
        plans={en_path: plan},
        baseline_en={en_path: baseline},
        candidate_files={en_path: unrelated},
    )

    checked: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []

    def reject_unrelated(path, unit, segments, translations):
        checked.append(
            (
                path,
                unit.source,
                tuple(segment.text for segment in segments),
                tuple(translations.values()),
            )
        )
        return not any("backup schedule" in value for value in translations.values())

    with pytest.raises(ValueError, match="semantic coverage rejected"):
        validate_coverage_evidence(
            evidence,
            authority=_authority(),
            read_source=lambda _path: plan.units[0].source,
            read_baseline_en=lambda _path: baseline,
            read_candidate=lambda _path: unrelated,
            semantic_validator=reject_unrelated,
        )

    assert checked == [
        (
            en_path,
            plan.units[0].source,
            ("New", "Required source."),
            ("User token", "This paragraph discusses an unrelated backup schedule."),
        )
    ]


def test_real_protected_only_pair_reports_asset_without_paid_calls() -> None:
    source = "```mermaid\ngraph LR\n  A --> B\n```\n"
    old_target = "```mermaid\ngraph LR\n  OLD --> OLD\n```\n"
    pair = DocPair(
        ru_path="ydb/docs/ru/core/security/_assets/user-token.md",
        en_path="ydb/docs/en/core/security/_assets/user-token.md",
        ru_changed=True,
    )
    coverage_plan = CoveragePlan(
        source_path=pair.ru_path,
        source_hash=_hash(source),
        en_hash=_hash(old_target),
        units=(
            CoverageUnit(
                key="a" * 64,
                action="materialize_protected",
                source=source,
                en_span=None,
                target=source,
                reason="protected source structure",
            ),
        ),
        required_fragments=frozenset(),
        mode="full",
    )
    content = PairContent(
        pair=pair,
        ru_text=source,
        en_text=old_target,
        en_base_text=old_target,
        coverage_plan=coverage_plan,
    )
    pair_plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    client = MagicMock()
    client.usage_tracker.records = []
    client.usage_tracker.metrics_since.return_value = {
        "models_used": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    ctx = HarnessContext.from_options(
        client,
        glossary=load_glossary(),
        config=load_config(
            env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
        ),
    )

    with patch(
        "ydbdoc_review.harness.steps.translate_segments",
        side_effect=AssertionError("protected-only asset dispatched paid translation"),
    ):
        result = run_pair_plan(content, pair_plan, ctx, {})

    assert result.target_text == source
    assert result.file_result is not None
    assert result.file_result.differential_meta["protected"] == 1
    assert "reused 0, translated 0, protected 1" in build_coverage_summary(
        PRTranslationResult(pair_results=[result])
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(repo: Path, subject: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _read_at(repo: Path, sha: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout if result.returncode == 0 else None


def _coverage_pair(
    ru_path: str,
    en_path: str,
    source: str,
    baseline: str | None,
    plan: CoveragePlan,
) -> tuple[PairContent, PairPlan]:
    pair = DocPair(ru_path=ru_path, en_path=en_path, ru_changed=True)
    return (
        PairContent(
            pair=pair,
            ru_text=source,
            en_text=baseline,
            en_base_text=baseline,
            coverage_plan=plan,
        ),
        PairPlan(
            pair=pair,
            action="translate_to_en",
            source_path=ru_path,
            target_path=en_path,
            source_lang="ru",
            target_lang="en",
        ),
    )


@pytest.fixture
def frozen_pr51079_candidate(tmp_path: Path) -> SimpleNamespace:
    """Six docs + one navigation file over frozen R/B/K Git authorities."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Task 7 Test")
    _git(repo, "config", "user.email", "task7@example.test")

    root = "ydb/docs"
    rels = (
        "core/security/auth_config.md",
        "core/security/authentication.md",
        "core/concepts/glossary.md",
        "core/security/caching.md",
        "core/security/_assets/token-flow.md",
        "core/security/_assets/cache-flow.md",
    )
    ru_paths = tuple(f"{root}/ru/{rel}" for rel in rels)
    en_paths = tuple(f"{root}/en/{rel}" for rel in rels)
    ru_initial = {
        ru_paths[0]: "# Конфигурация\n\nСтарая конфигурация.\n",
        ru_paths[1]: "# Аутентификация\n\nСтарая аутентификация.\n",
        ru_paths[2]: "# Глоссарий\n\nСуществующее определение.\n",
        ru_paths[3]: "# Кэширование\n\nСтарое описание.\n",
        ru_paths[4]: "```mermaid\ngraph LR\n  O --> O\n```\n",
        ru_paths[5]: "```mermaid\ngraph LR\n  C --> C\n```\n",
    }
    en_initial = {
        en_paths[0]: "# Configuration\n\nAccepted configuration.\n",
        en_paths[1]: "# Authentication\n\nAccepted authentication.\n",
        en_paths[2]: "# Glossary\n\nUnrelated accepted definition.\n",
        en_paths[3]: "# Caching\n\nOld accepted description.\n",
    }
    for path, text_value in {**ru_initial, **en_initial}.items():
        _write(repo, path, text_value)
    ru_toc = f"{root}/ru/core/security/toc.yaml"
    en_toc = f"{root}/en/core/security/toc.yaml"
    _write(
        repo,
        ru_toc,
        "- name: Аутентификация\n  href: authentication.md\n  items:\n"
        "    - name: Конфигурация\n      href: auth_config.md\n",
    )
    _write(
        repo,
        en_toc,
        "- name: Authentication\n  href: authentication.md\n  items:\n"
        "    - name: Configuration\n      href: auth_config.md\n",
    )
    source_base = _commit(repo, "source base")

    ru_at_r = {
        ru_paths[0]: (
            "# Конфигурация\n\nИспользуйте [стабильную ссылку]"
            "(../../concepts/glossary.md#existing).\n"
        ),
        ru_paths[1]: "# Аутентификация\n\nНастройте аутентификацию.\n",
        ru_paths[2]: (
            "# Глоссарий\n\nСуществующее определение.\n\n"
            "## Токен пользователя {#user-token}\n\n"
            "Обязательное определение токена пользователя.\n"
        ),
        ru_paths[3]: "# Кэширование\n\nНастройте вложенное кэширование.\n",
        ru_paths[4]: "```mermaid\ngraph LR\n  User --> Token\n```\n",
        ru_paths[5]: "```mermaid\ngraph LR\n  Cache --> Hit\n```\n",
    }
    for path, text_value in ru_at_r.items():
        _write(repo, path, text_value)
    ru_toc_at_r = (
        "- name: Аутентификация\n  href: authentication.md\n  items:\n"
        "    - name: Конфигурация\n      href: auth_config.md\n"
        "    - name: Кэширование\n      href: caching.md\n      items:\n"
        "        - name: Поток токена\n          href: _assets/token-flow.md\n"
        "        - name: Поток кэша\n          href: _assets/cache-flow.md\n"
    )
    _write(repo, ru_toc, ru_toc_at_r)
    source_head = _commit(repo, "Frozen PR 51079 source")

    repaired_auth = (
        "# Configuration\n\nAccepted configuration with "
        "[stable repaired link](../../concepts/glossary.md#existing).\n"
    )
    _write(repo, en_paths[0], repaired_auth)
    _commit(repo, "Preserve accepted EN repair (#52330)")
    _write(
        repo,
        ru_paths[0],
        ru_at_r[ru_paths[0]] + "\nПоздняя правка вне frozen source.\n",
    )
    baseline_sha = _commit(repo, "Later RU change (#52355)")

    authority = RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=51079,
        source_base_sha=source_base,
        source_head_sha=source_head,
        baseline_sha=baseline_sha,
        ru_sha=source_head,
        mode=RuAuthorityMode.SOURCE_PRESERVING,
    )
    baseline_en = {path: _read_at(repo, baseline_sha, path) for path in en_paths}
    assert baseline_en[en_paths[0]] == repaired_auth

    plans: dict[str, CoveragePlan] = {}
    for index in (0, 1):
        source = ru_at_r[ru_paths[index]]
        target = baseline_en[en_paths[index]]
        assert target is not None
        plans[en_paths[index]] = CoveragePlan(
            source_path=ru_paths[index],
            source_hash=_hash(source),
            en_hash=_hash(target),
            units=(
                CoverageUnit(
                    key=f"{index + 1}" * 64,
                    action="reuse_verified",
                    source=source,
                    en_span=(0, len(target)),
                    target=target,
                    reason="exact Task 5 checkpoint receipt",
                ),
            ),
            required_fragments=frozenset(),
            mode="units",
        )

    glossary_baseline = baseline_en[en_paths[2]]
    assert glossary_baseline is not None
    glossary_unit_source = (
        "## Токен пользователя {#user-token}\n\n"
        "Обязательное определение токена пользователя.\n"
    )
    plans[en_paths[2]] = CoveragePlan(
        source_path=ru_paths[2],
        source_hash=_hash(ru_at_r[ru_paths[2]]),
        en_hash=_hash(glossary_baseline),
        units=(
            CoverageUnit(
                key="3" * 64,
                action="translate_required",
                source=glossary_unit_source,
                en_span=(len(glossary_baseline), len(glossary_baseline)),
                target=None,
                reason="exact fragment dependency: user-token",
            ),
        ),
        required_fragments=frozenset({"user-token"}),
        mode="units",
    )
    caching_baseline = baseline_en[en_paths[3]]
    assert caching_baseline is not None
    plans[en_paths[3]] = CoveragePlan(
        source_path=ru_paths[3],
        source_hash=_hash(ru_at_r[ru_paths[3]]),
        en_hash=_hash(caching_baseline),
        units=(
            CoverageUnit(
                key="4" * 64,
                action="translate_required",
                source=ru_at_r[ru_paths[3]],
                en_span=(0, len(caching_baseline)),
                target=None,
                reason="pending changed caching page",
            ),
        ),
        required_fragments=frozenset(),
        mode="units",
    )
    for index in (4, 5):
        source = ru_at_r[ru_paths[index]]
        plans[en_paths[index]] = CoveragePlan(
            source_path=ru_paths[index],
            source_hash=_hash(source),
            en_hash=None,
            units=(
                CoverageUnit(
                    key=f"{index + 1}" * 64,
                    action="materialize_protected",
                    source=source,
                    en_span=None,
                    target=source,
                    reason="protected source structure",
                ),
            ),
            required_fragments=frozenset(),
            mode="full",
        )

    client = MagicMock()
    client.usage_tracker.records = []
    client.usage_tracker.metrics_since.return_value = {
        "models_used": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    ctx = HarnessContext.from_options(
        client,
        glossary=load_glossary(),
        config=load_config(
            env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
        ),
    )
    paid_sources: list[str] = []
    translations = {
        "Токен пользователя": "User token",
        "Обязательное определение токена пользователя.": (
            "Required user-token definition."
        ),
        "Кэширование": "Caching",
        "Настройте вложенное кэширование.": "Configure nested caching.",
    }

    def translate_pending(segments, *_args, **_kwargs):
        paid_sources.extend(segment.text for segment in segments)
        return {segment.id: translations[segment.text] for segment in segments}

    pair_results = []
    with patch(
        "ydbdoc_review.harness.steps.translate_segments",
        side_effect=translate_pending,
    ):
        for ru_path, en_path in zip(ru_paths, en_paths, strict=True):
            content, pair_plan = _coverage_pair(
                ru_path,
                en_path,
                ru_at_r[ru_path],
                baseline_en[en_path],
                plans[en_path],
            )
            result = run_pair_plan(content, pair_plan, ctx, {})
            assert result.error is None
            assert result.target_text is not None
            pair_results.append(result)

    nav_candidate = merge_en_toc_yaml(
        _read_at(repo, baseline_sha, en_toc) or "",
        ru_toc_at_r,
        translate_hrefs={
            "caching.md",
            "_assets/token-flow.md",
            "_assets/cache-flow.md",
        },
        translate_name=lambda name: {
            "Кэширование": "Caching",
            "Поток токена": "Token flow",
            "Поток кэша": "Cache flow",
        }[name],
        restrict_gap_fill_to_scope=True,
    )
    for en_path, result in zip(en_paths, pair_results, strict=True):
        _write(repo, en_path, result.target_text or "")
    _write(repo, en_toc, nav_candidate)
    candidate_sha = _commit(repo, "Assembled frozen PR51079 candidate")
    candidate_files = {
        path: _read_at(repo, candidate_sha, path) or "" for path in en_paths
    }
    evidence = build_coverage_evidence(
        authority=authority,
        candidate_sha=candidate_sha,
        plans=plans,
        baseline_en=baseline_en,
        candidate_files=candidate_files,
    )
    store = InMemoryTranscriptStore()
    save_coverage_evidence(store, "run-51079", evidence)
    return SimpleNamespace(
        repo=repo,
        authority=authority,
        source_head=source_head,
        baseline_sha=baseline_sha,
        candidate_sha=candidate_sha,
        ru_paths=ru_paths,
        en_paths=en_paths,
        en_toc=en_toc,
        plans=plans,
        baseline_en=baseline_en,
        candidate_files=candidate_files,
        evidence=evidence,
        store=store,
        paid_sources=paid_sources,
        pair_results=pair_results,
        nav_candidate=nav_candidate,
        repaired_auth=repaired_auth,
    )


def test_composed_frozen_pr51079_candidate_is_source_preserving_and_verifiable(
    frozen_pr51079_candidate: SimpleNamespace,
) -> None:
    case = frozen_pr51079_candidate
    loaded = load_coverage_evidence(
        case.store,
        "run-51079",
        candidate_sha=case.candidate_sha,
        expected_digest=case.evidence.digest,
    )
    semantic_paths: list[str] = []

    def accept_required(path, _unit, _segments, translations):
        semantic_paths.append(path)
        values = "\n".join(translations.values())
        return (
            "Required user-token definition." in values
            or "Configure nested caching." in values
        )

    validate_coverage_evidence(
        loaded,
        authority=case.authority,
        read_source=lambda path: _read_at(case.repo, case.source_head, path),
        read_baseline_en=lambda path: _read_at(case.repo, case.baseline_sha, path),
        read_candidate=lambda path: _read_at(case.repo, case.candidate_sha, path),
        semantic_validator=accept_required,
    )

    assert len(case.pair_results) == 6
    assert len(case.plans) == 6
    assert case.candidate_files[case.en_paths[2]].count("{#user-token}") == 1
    assert case.candidate_files[case.en_paths[2]].startswith(case.baseline_en[case.en_paths[2]])
    assert "Unrelated accepted definition.\n" in case.candidate_files[case.en_paths[2]]
    assert case.candidate_files[case.en_paths[0]] == case.repaired_auth
    assert case.paid_sources == [
        "Токен пользователя",
        "Обязательное определение токена пользователя.",
        "Кэширование",
        "Настройте вложенное кэширование.",
    ]
    assert semantic_paths == [case.en_paths[2], case.en_paths[3]]
    assert "href: caching.md" in case.nav_candidate
    assert "href: _assets/token-flow.md" in case.nav_candidate
    assert "href: _assets/cache-flow.md" in case.nav_candidate
    assert case.nav_candidate.index("href: caching.md") < case.nav_candidate.index(
        "href: _assets/token-flow.md"
    )
    assert case.candidate_files[case.en_paths[4]] == _read_at(
        case.repo, case.source_head, case.ru_paths[4]
    )
    assert case.candidate_files[case.en_paths[5]] == _read_at(
        case.repo, case.source_head, case.ru_paths[5]
    )

    gh = SimpleNamespace(
        get_pull=lambda _owner, _repo, number: {
            "number": number,
            "merged": True,
            "merge_commit_sha": case.baseline_sha,
        }
    )
    later = build_later_ru_drift_report(
        str(case.repo),
        gh,
        owner="ydb-platform",
        repo="ydb",
        source_head_sha=case.source_head,
        baseline_sha=case.baseline_sha,
        source_paths=case.ru_paths,
        docs_root="ydb/docs",
    )
    assert "Confirmed merge association: PR #52355" in later
    assert "Поздняя правка" not in "\n".join(case.candidate_files.values())


def test_fresh_manifest_cannot_bless_changed_materialized_asset(
    frozen_pr51079_candidate: SimpleNamespace,
) -> None:
    case = frozen_pr51079_candidate
    changed = dict(case.candidate_files)
    changed[case.en_paths[4]] = "```mermaid\ngraph LR\n  Forged --> Asset\n```\n"
    forged = build_coverage_evidence(
        authority=case.authority,
        candidate_sha="f" * 40,
        plans=case.plans,
        baseline_en=case.baseline_en,
        candidate_files=changed,
    )

    with pytest.raises(ValueError, match="materialized target mismatch"):
        validate_coverage_evidence(
            forged,
            authority=case.authority,
            read_source=lambda path: _read_at(case.repo, case.source_head, path),
            read_baseline_en=lambda path: _read_at(case.repo, case.baseline_sha, path),
            read_candidate=changed.get,
            semantic_validator=lambda *_args: True,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "corrupt", "authority", "baseline", "source", "candidate", "stale-k"],
)
def test_composed_candidate_evidence_fails_closed(
    frozen_pr51079_candidate: SimpleNamespace,
    mutation: str,
) -> None:
    case = frozen_pr51079_candidate
    if mutation in {"missing", "corrupt", "stale-k"}:
        store = InMemoryTranscriptStore()
        key = f"translation/v1/coverage/{case.candidate_sha}.json"
        if mutation == "corrupt":
            store.put("run-51079", key, b"{not-json")
        elif mutation == "stale-k":
            stale = case.store.get("run-51079", key)
            assert stale is not None
            _write(case.repo, case.en_paths[3], "# Caching\n\nK2 repair.\n")
            k2 = _commit(case.repo, "K2 repair")
            store.put("run-51079", f"translation/v1/coverage/{k2}.json", stale)
            with pytest.raises(ValueError, match="coverage evidence candidate mismatch"):
                load_coverage_evidence(
                    store,
                    "run-51079",
                    candidate_sha=k2,
                    expected_digest=case.evidence.digest,
                )
            return
        with pytest.raises(ValueError, match="coverage evidence"):
            load_coverage_evidence(
                store,
                "run-51079",
                candidate_sha=case.candidate_sha,
                expected_digest=case.evidence.digest,
            )
        return

    authority = (
        replace(case.authority, source_pr=51080)
        if mutation == "authority"
        else case.authority
    )

    def source_reader(path):
        text_value = _read_at(case.repo, case.source_head, path)
        return (text_value or "") + "source drift" if mutation == "source" else text_value

    def baseline_reader(path):
        text_value = _read_at(case.repo, case.baseline_sha, path)
        return (text_value or "") + "baseline drift" if mutation == "baseline" else text_value

    def candidate_reader(path):
        text_value = _read_at(case.repo, case.candidate_sha, path)
        return (text_value or "") + "candidate drift" if mutation == "candidate" else text_value

    with pytest.raises(ValueError, match="coverage evidence"):
        validate_coverage_evidence(
            case.evidence,
            authority=authority,
            read_source=source_reader,
            read_baseline_en=baseline_reader,
            read_candidate=candidate_reader,
            semantic_validator=lambda *_args: True,
        )
