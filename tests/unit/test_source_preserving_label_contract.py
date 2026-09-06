"""Behavioral contract for the trusted source-preserving selector label."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit import test_a05_authority_provenance_contract as a05
from ydbdoc_review.github import workflow

SELECTOR = "doc_translate_source_preserving"


def _history(tmp_path: Path) -> a05.AuthorityHistory:
    return a05.authority_history.__wrapped__(tmp_path)


def _source_github(
    history: a05.AuthorityHistory,
    *,
    labels: tuple[str, ...],
    title: str | None = None,
    body: str | None = None,
    head_ref: str | None = None,
) -> a05.ExactGitHub:
    gh = a05.ExactGitHub(history)
    pull = gh._source_pull(a05.SOURCE_PR)
    pull["labels"] = [{"name": label} for label in labels]
    if title is not None:
        pull["title"] = title
    if body is not None:
        pull["body"] = body
    if head_ref is not None:
        pull["head"]["ref"] = head_ref
    gh.pull_overrides[a05.SOURCE_PR] = pull
    return gh


def _assert_source_preserving_snapshot(
    job: workflow.DocJobResult,
    observed: a05.ObservedRun,
    history: a05.AuthorityHistory,
) -> None:
    a05._assert_translate_snapshot(
        job,
        observed,
        history,
        ref=history.h,
        expected_docs=a05.EXPECTED_H_DOC_SCOPE,
        expect_later_nav=False,
    )


def test_exact_selector_runs_real_merged_producer_with_source_preserving_artifact(
    tmp_path: Path,
) -> None:
    history = _history(tmp_path)
    gh = _source_github(
        history,
        labels=("documentation", "doc_translate", SELECTOR),
    )

    producer = a05._translate_candidate(
        history,
        keep_broken_dependency=True,
        move_refs=False,
        config=a05._config(),
        github=gh,
    )

    assert producer.observed.plans[0].doc_ru_paths == a05.EXPECTED_H_DOC_SCOPE
    a05._assert_h_model_inputs(producer.observed.translate_contents[0], history)
    assert producer.observed.prepare_calls == [history.baseline]
    candidate = history.candidate
    assert candidate is not None
    assert a05._git(history.repo, "rev-parse", f"{candidate}^") == history.baseline
    assert a05._authority_envelope(producer.gh.translation_body, history).payload == {
        "version": 1,
        "source": {
            "repo": a05.REPO_ID,
            "pr": a05.SOURCE_PR,
            "head_sha": history.h,
            "base_sha": history.h0,
        },
        "selection": {
            "kind": "source-preserving",
            "ru_sha": history.h,
            "baseline_sha": history.baseline,
        },
        "candidate_sha": candidate,
    }


@pytest.mark.parametrize(
    ("labels", "metadata"),
    [
        (("doc_translate",), {}),
        ((SELECTOR.upper(),), {}),
        ((f"{SELECTOR} ",), {}),
        (
            (),
            {
                "title": SELECTOR,
                "body": SELECTOR,
                "head_ref": SELECTOR,
            },
        ),
    ],
)
def test_absent_or_near_match_selector_keeps_current_authority(
    tmp_path: Path,
    labels: tuple[str, ...],
    metadata: dict[str, Any],
) -> None:
    history = _history(tmp_path)
    gh = _source_github(history, labels=labels, **metadata)

    job, observed = a05._dry_translate(history, config=a05._config(), github=gh)

    a05._assert_translate_snapshot(
        job,
        observed,
        history,
        ref=history.baseline,
        expected_docs=a05._expected_b_scope(),
        expect_later_nav=True,
    )


def test_explicit_source_preserving_config_still_works_without_selector(
    tmp_path: Path,
) -> None:
    history = _history(tmp_path)
    gh = _source_github(history, labels=("doc_translate",))

    job, observed = a05._dry_translate(
        history,
        config=a05._config(ru_authority_mode="source-preserving"),
        github=gh,
    )

    _assert_source_preserving_snapshot(job, observed, history)


def test_ordinary_doc_translate_changes_to_h_only_after_selector_is_added(
    tmp_path: Path,
) -> None:
    history = _history(tmp_path)
    ordinary = _source_github(history, labels=("documentation", "doc_translate"))
    selector = _source_github(
        history,
        labels=("documentation", "doc_translate", SELECTOR),
    )

    ordinary_job, ordinary_observed = a05._dry_translate(
        history,
        config=a05._config(),
        github=ordinary,
    )
    selector_job, selector_observed = a05._dry_translate(
        history,
        config=a05._config(),
        github=selector,
    )

    assert ordinary_observed.plans[0].doc_ru_paths == a05._expected_b_scope()
    _assert_source_preserving_snapshot(selector_job, selector_observed, history)
    assert ordinary_job.dry_run


def test_fresh_verify_keeps_artifact_authority_after_label_removal_and_ref_movement(
    tmp_path: Path,
) -> None:
    history = _history(tmp_path)
    producer_gh = _source_github(
        history,
        labels=("documentation", "doc_translate", SELECTOR),
    )
    producer = a05._translate_candidate(
        history,
        keep_broken_dependency=True,
        move_refs=True,
        config=a05._config(),
        github=producer_gh,
    )
    body = producer.gh.translation_body
    consumer = a05._clone_consumer(tmp_path, history, shallow=False)
    consumer_gh = a05.ExactGitHub(history, translation_body=body)
    moved_source = consumer_gh._source_pull(a05.SOURCE_PR)
    moved_source["labels"] = []
    moved_source["head"]["sha"] = history.later
    moved_source["merge_commit_sha"] = history.later
    consumer_gh.pull_overrides[a05.SOURCE_PR] = moved_source
    observed = a05.ObservedRun()

    with a05._runtime(
        history,
        consumer_gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=False,
    ):
        job = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=a05.REPO_ID,
            pr_number=a05.TRANSLATION_PR,
            merge_base_with="origin/main",
            dry_run=True,
            config=a05._config(),
            skip_ops_gates=True,
        )

    assert job.dry_run
    assert consumer_gh.body_reads == [body]
    assert observed.translate_contents == []
    assert len(observed.verify_contents) == 1
    a05._assert_h_model_inputs(observed.verify_contents[0], history)
    envelope = a05._authority_envelope(body, history)
    assert isinstance(envelope.payload, dict)
    assert envelope.payload["selection"] == {
        "kind": "source-preserving",
        "ru_sha": history.h,
        "baseline_sha": history.baseline,
    }
