"""Reports must render the same typed link blockers that stop publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import DocJobResult, job_requires_nonzero_exit
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import classify_publication_blockers
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_full_report,
    result_has_blocking_findings,
)
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.validation.href_parity import restore_md_link_hrefs
from ydbdoc_review.validation.link_contract import LinkContractIssue

PATH = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
RU_PATH = PATH.replace("/en/", "/ru/", 1)
HREF = (
    "../../devops/deployment-options/manual/node-authorization.md"
    "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
)
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/pr52330-wrapper-repair-snapshots.json"
)


def _cfg():
    return load_config(
        env={"YDBDOC_YC_FOLDER_ID": "typed-report", "YDBDOC_YC_API_KEY": "test"}
    )


def _plan() -> PairPlan:
    pair = DocPair(ru_path=RU_PATH, en_path=PATH, ru_changed=True)
    return PairPlan(
        pair=pair,
        action="critic_only",
        source_path=RU_PATH,
        target_path=PATH,
        source_lang="ru",
        target_lang="en",
        summary="typed report contract",
    )


def _issue(*, file_path: str = "") -> LinkContractIssue:
    return LinkContractIssue(
        code="missing_link_wrapper",
        message="no unique translated label in aligned LinkSlot span",
        file_path=file_path,
        slot=0,
        href=HREF,
    )


def _file_result(
    *,
    issues: tuple[LinkContractIssue, ...] = (),
    verdict: str = "ok",
    critic: CriticResponse | None = None,
) -> FileTranslationResult:
    return FileTranslationResult(
        file_path=PATH,
        final_text="Clean English text.\n",
        segments_count=1,
        verdict=verdict,
        prompt_version="typed-report",
        critic_initial=critic,
        critic_unresolved=critic,
        link_contract_issues=issues,
    )


def _report(result: PRTranslationResult) -> str:
    return build_full_report(
        result,
        meta=ReportMeta(
            mode="doc_verify",
            report_number=1,
            elapsed_s=1,
            checkout_ref="f" * 40,
        ),
        config=_cfg(),
    )


def _assert_red_visible_once(result: PRTranslationResult) -> None:
    body = _report(result)
    recommendation = body.split("Рекомендация:", 1)[1].split("\n", 1)[0]
    assert "🔴" in recommendation
    assert "можно мержить" not in recommendation
    assert PATH in body
    assert body.count("missing_link_wrapper") == 1
    assert body.count(HREF) == 1
    assert "slot 0" in body
    assert f"- 🟢 `{PATH}`" not in body
    assert result_has_blocking_findings(result)
    assert classify_publication_blockers(result).unsafe
    job = DocJobResult(mode="doc_verify", pr_number=52330, pr_result=result)
    assert job_requires_nonzero_exit(job)


@pytest.mark.parametrize("container", ["run", "file", "both"])
def test_typed_link_issue_is_red_visible_and_deduplicated(container: str) -> None:
    issue = _issue()
    fr_issues = (issue,) if container in {"file", "both"} else ()
    run_issues = (issue,) if container in {"run", "both"} else ()
    run = PairRunResult(
        plan=_plan(),
        target_text="Clean English text.\n",
        source_text="Чистый русский текст.\n",
        file_result=_file_result(issues=fr_issues),
        validation_issues=run_issues,
    )
    _assert_red_visible_once(PRTranslationResult(pair_results=[run]))


def test_typed_only_run_without_file_result_is_still_visible_and_red() -> None:
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=_plan(),
                target_text="Clean English text.\n",
                source_text="Чистый русский текст.\n",
                validation_issues=(_issue(),),
            )
        ]
    )
    _assert_red_visible_once(result)


def test_pinned_current_issue_is_red_and_repaired_result_is_green() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    trees = payload["trees"]
    paths = payload["paths"]
    h0 = trees["H0"][paths["ru_clientcert"]]["text"]
    h = trees["H"][paths["ru_clientcert"]]["text"]
    k = trees["K"][paths["en_clientcert"]]["text"]
    contract = restore_md_link_hrefs(
        k,
        h,
        source_ru_base=h0,
        target_baseline=k,
    )
    assert [issue.code for issue in contract.issues] == ["missing_link_wrapper"]
    current = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=_plan(),
                target_text=k,
                source_text=h,
                file_result=_file_result(),
                validation_issues=contract.issues,
            )
        ]
    )
    _assert_red_visible_once(current)

    repaired_text = k.replace(
        "registering dynamic nodes",
        "[registering dynamic nodes](../../devops/concepts/node-authorization.md"
        "#enabling-the-node-authentication-and-authorization-mode)",
        1,
    )
    repaired_contract = restore_md_link_hrefs(
        repaired_text,
        h,
        source_ru_base=h0,
        target_baseline=repaired_text,
    )
    assert repaired_contract.issues == ()
    repaired = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=_plan(),
                target_text=repaired_text,
                source_text=h,
                file_result=_file_result(),
            )
        ]
    )
    body = _report(repaired)
    recommendation = body.split("Рекомендация:", 1)[1].split("\n", 1)[0]
    assert "🟢" in recommendation
    assert not classify_publication_blockers(repaired).any
    assert not result_has_blocking_findings(repaired)


def test_stale_blocked_verdict_and_resolved_refusal_stay_nonblocking() -> None:
    for critic in (
        CriticResponse(verdict="blocked", issues=[]),
        CriticResponse(verdict="ok", issues=[]),
    ):
        run = PairRunResult(
            plan=_plan(),
            target_text="Clean English text.\n",
            source_text="Чистый русский текст.\n",
            file_result=_file_result(verdict="blocked", critic=critic),
        )
        body = _report(PRTranslationResult(pair_results=[run]))
        recommendation = body.split("Рекомендация:", 1)[1].split("\n", 1)[0]
        assert "🟢" in recommendation


def test_actual_blockers_never_yield_green_with_other_green_file() -> None:
    green_plan = _plan()
    green = PairRunResult(
        plan=green_plan,
        target_text="Green.\n",
        source_text="Green.\n",
        file_result=_file_result(),
    )
    blocked_critic = CriticResponse(
        verdict="blocked",
        issues=[
            CriticIssueOut(
                segment_id="s0001",
                severity="blocked",
                category="meaning",
                comment="meaning is missing",
                suggested_text=None,
            )
        ],
    )
    cases = [
        PRTranslationResult(
            pair_results=[
                green,
                PairRunResult(
                    plan=_plan(),
                    target_text="Bad.\n",
                    source_text="Плохо.\n",
                    file_result=_file_result(
                        verdict="blocked", critic=blocked_critic
                    ),
                ),
            ]
        ),
        PRTranslationResult(
            pair_results=[green],
            final_tree_blockers=[
                FinalTreeBlocker(
                    path=PATH,
                    code="unsupported",  # type: ignore[arg-type]
                    message="unsupported final-tree blocker",
                )
            ],
        ),
        PRTranslationResult(
            pair_results=[
                green,
                PairRunResult(
                    plan=_plan(),
                    target_text="Bad.\n",
                    source_text="Плохо.\n",
                    file_result=_file_result(issues=(_issue(),)),
                ),
            ]
        ),
    ]
    for result in cases:
        recommendation = _report(result).split("Рекомендация:", 1)[1].split(
            "\n", 1
        )[0]
        assert "🟢" not in recommendation
