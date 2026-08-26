from ydbdoc_review.pipeline.analyze import PairPlan, PairProvenance
from ydbdoc_review.pipeline.completeness import CompletenessState, evaluate_completeness_states
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult


def _run(provenance: PairProvenance, **kwargs) -> PairRunResult:
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md")
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en", provenance=provenance)
    return PairRunResult(plan, **kwargs)


def test_typed_completeness_transitions():
    cases = (
        (_run(PairProvenance.CURRENT_PAIR, skipped=True, historical_disposition="existing_satisfied"), CompletenessState.EXISTING_SATISFIED),
        (_run(PairProvenance.CURRENT_RU_MISSING_EN, target_text="en"), CompletenessState.ADDED),
        (_run(PairProvenance.CURRENT_PAIR, target_text="en"), CompletenessState.UPDATED),
        (_run(PairProvenance.CURRENT_PAIR, deleted=True), CompletenessState.DELETED),
        (_run(PairProvenance.SUPERSEDED_ABSENT, skipped=True, historical_disposition="superseded_absent"), CompletenessState.SUPERSEDED_ABSENT),
    )
    for run, expected in cases:
        assert evaluate_completeness_states(PRTranslationResult(pair_results=[run]))[run.plan.target_path] is expected


def test_arbitrary_skip_is_blocked_but_proven_unchanged_is_satisfied():
    arbitrary = _run(PairProvenance.CURRENT_PAIR, skipped=True)
    proven = _run(
        PairProvenance.CURRENT_PAIR,
        skipped=True,
        historical_disposition="existing_satisfied",
    )

    assert evaluate_completeness_states(
        PRTranslationResult(pair_results=[arbitrary])
    )[arbitrary.plan.target_path] is CompletenessState.BLOCKED
    assert evaluate_completeness_states(
        PRTranslationResult(pair_results=[proven])
    )[proven.plan.target_path] is CompletenessState.EXISTING_SATISFIED


def test_required_unchanged_without_proof_remains_blocked():
    run = _run(PairProvenance.CURRENT_PAIR)
    assert evaluate_completeness_states(PRTranslationResult(pair_results=[run]))[
        run.plan.target_path
    ] is CompletenessState.BLOCKED
