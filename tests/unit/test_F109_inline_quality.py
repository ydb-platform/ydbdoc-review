"""F-109: translate/continue finish one inline quality cycle before publication."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.ops.translation_checkpoint import CheckpointIdentity
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_full_report,
    build_translation_pr_body,
)
from ydbdoc_review.translation.coverage import (
    CoveragePlan,
    CoverageUnit,
    plan_source_coverage,
)
from ydbdoc_review.translation.glossary import load_glossary


def _completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _client(responses: list[str]) -> tuple[YandexLLMClient, MagicMock]:
    transport = MagicMock()
    transport.chat.completions.create.side_effect = [
        _completion(response) for response in responses
    ]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    # Distinct role models expose critic dispatch even though the current
    # critic transport selects its model explicitly and leaves usage.role unset.
    cfg.llm.models.translate.primary = "deepseek-v32"
    cfg.llm.models.translate.fallbacks = ["yandexgpt-5-pro"]
    cfg.llm.models.critic.primary = "yandexgpt-5.1"
    cfg.llm.models.critic.fallbacks = ["yandexgpt-5-lite"]
    return (
        YandexLLMClient(folder_id="b1", api_key="k", llm=cfg.llm, client=transport),
        transport,
    )


def _translate(text: str) -> str:
    return json.dumps({"segments": [{"id": "s0001", "text": text}]})


def _critic(*, suggestion: str | None = None) -> str:
    issues = []
    verdict = "ok"
    if suggestion is not None:
        verdict = "warnings"
        issues = [
            {
                "segment_id": "s0001",
                "severity": "warning",
                "category": "terminology",
                "comment": "use the glossary term",
                "suggested_text": suggestion,
            }
        ]
    return json.dumps({"verdict": verdict, "issues": issues})


def _full_coverage_content(source: str, existing: str | None = None) -> PairContent:
    pair = DocPair(
        ru_path="ydb/docs/ru/concepts/connection.md",
        en_path="ydb/docs/en/concepts/connection.md",
        ru_changed=True,
    )
    authority = RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=1,
        source_base_sha="1" * 40,
        source_head_sha="2" * 40,
        baseline_sha="3" * 40,
        ru_sha="3" * 40,
        mode=RuAuthorityMode.CURRENT,
    )
    coverage = plan_source_coverage(
        source_path=pair.ru_path,
        source_text=source,
        existing_en=existing,
        authority=authority,
        checkpoint_identity=CheckpointIdentity(authority, "4" * 64),
    )
    assert coverage.mode == "full"
    return PairContent(
        pair=pair, ru_text=source, en_text=existing, coverage_plan=coverage,
    )


def _semantic_issue(suggestion: str | None = None) -> str:
    return json.dumps({
        "verdict": "blocked",
        "issues": [{
            "segment_id": "s0001",
            "severity": "blocked",
            "category": "meaning_drift",
            "comment": "Both the name and the password are required, not alternatives.",
            "suggested_text": suggestion,
        }],
    })


@pytest.mark.parametrize("coverage_mode", [None, "full"])
def test_F109_one_cycle(coverage_mode: str | None) -> None:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    client, transport = _client([_translate("Draft."), _critic()])
    content = (
        _full_coverage_content("Текст.\n")
        if coverage_mode == "full"
        else PairContent(pair=pair, ru_text="Текст.\n")
    )

    result = run_pr_translation(
        [content],
        client,
        load_glossary(),
        use_analyze_llm=False,
    )

    file_result = result.pair_results[0].file_result
    assert file_result is not None
    assert file_result.critic_initial is not None
    assert file_result.verdict == "ok"
    assert transport.chat.completions.create.call_count == 2
    assert "run_doc_verify(" not in inspect.getsource(workflow.run_doc_translate)


@pytest.mark.parametrize(
    ("existing", "fallback_reason"),
    [
        (None, "target does not exist; full translation required"),
        (
            "# Old page\n\nOld draft.\n",
            "missing or ambiguous exact verified-unit receipt",
        ),
    ],
    ids=["missing-target", "existing-target-full-fallback"],
)
def test_F109_full_coverage_plan_runs_inline_critic(
    existing: str | None, fallback_reason: str, monkeypatch,
) -> None:
    content = _full_coverage_content("Текст.\n", existing)
    client, transport = _client([_translate("Draft."), _critic()])
    separate_verify = MagicMock(side_effect=AssertionError("unexpected whole-PR verify"))
    monkeypatch.setattr(workflow, "run_doc_verify", separate_verify)

    result = run_pr_translation([content], client, load_glossary())

    run = result.pair_results[0]
    assert run.error is None
    assert run.target_text == "Draft.\n"
    assert run.file_result is not None
    assert run.file_result.critic_initial is not None
    assert [record.model_slug for record in client.usage_tracker.records] == [
        "deepseek-v32", "yandexgpt-5.1",
    ]
    review = transport.chat.completions.create.call_args_list[1].kwargs["messages"][-1]["content"]
    assert review.startswith("Review a ru → en translation batch")
    assert transport.chat.completions.create.call_count == 2
    assert run.file_result.verdict == "ok"
    assert content.coverage_plan is not None
    assert content.coverage_plan.mode == "full"
    assert run.file_result.differential_meta["mode"] == "full"
    assert fallback_reason in run.file_result.differential_meta["fallback_reasons"]
    body = build_translation_pr_body(1, "ydb-platform/ydb", publication_result=result)
    assert "QA K: 🟢 GREEN" in body
    assert fallback_reason in body
    separate_verify.assert_not_called()


@pytest.mark.parametrize(
    ("source", "draft", "correction", "expected"),
    [
        (
            "Для подключения нужны имя и пароль.\n",
            "A name or a password is required to connect.",
            "A name and a password are required to connect.",
            "A name and a password are required to connect.\n",
        ),
        (
            "Для подключения нужны имя `account_name` и пароль `account_password`.\n",
            "A name ⟦C1⟧ or a password ⟦C2⟧ is required to connect.",
            "A name ⟦C1⟧ and a password ⟦C2⟧ are required to connect.",
            "A name `account_name` and a password `account_password` are required to connect.\n",
        ),
    ],
    ids=["plain-prose", "protected-identifiers"],
)
def test_F109_full_coverage_semantic_issue_is_repaired(
    source: str, draft: str, correction: str, expected: str,
) -> None:
    content = _full_coverage_content(source)
    client, transport = _client([
        _translate(draft), _semantic_issue(correction), _critic(),
    ])

    result = run_pr_translation([content], client, load_glossary())

    run = result.pair_results[0]
    assert run.error is None
    assert run.target_text == expected
    fr = run.file_result
    assert fr is not None
    assert fr.final_text == expected
    assert fr.critic_initial is not None
    assert fr.critic_initial.verdict == "blocked"
    assert [(issue.category, issue.suggested_text) for issue in fr.critic_applied] == [
        ("meaning_drift", correction),
    ]
    assert fr.critic_unresolved is not None
    assert fr.critic_unresolved.verdict == "ok"
    assert fr.critic_unresolved.issues == []
    assert [record.model_slug for record in client.usage_tracker.records] == [
        "deepseek-v32", "yandexgpt-5.1", "yandexgpt-5.1",
    ]
    requests = transport.chat.completions.create.call_args_list
    assert len(requests) == 3
    verification = requests[2].kwargs["messages"][-1]["content"]
    assert verification.startswith("Re-verify")
    pairs = json.loads(verification.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert pairs["segments"][0]["translated_text"] == correction
    assert fr.verdict == "ok"
    assert "QA K: 🟢 GREEN" in build_translation_pr_body(
        1, "ydb-platform/ydb", publication_result=result,
    )


def test_F109_full_coverage_unresolved_semantic_issue_is_not_green() -> None:
    content = _full_coverage_content("Для подключения нужны имя и пароль.\n")
    draft = "A name or a password is required to connect."
    # Initial translation plus both supported feedback rounds stay blocked;
    # each critic pass is followed by verification of its unresolved issue.
    client, transport = _client([
        _translate(draft), _semantic_issue(), _semantic_issue(),
    ] * 3)

    result = run_pr_translation([content], client, load_glossary())

    run = result.pair_results[0]
    assert run.error is None
    assert run.target_text == draft + "\n"
    fr = run.file_result
    assert fr is not None
    assert fr.critic_initial is not None
    assert fr.critic_unresolved is not None
    assert fr.critic_unresolved.verdict == "blocked"
    assert [(issue.category, issue.severity, issue.suggested_text)
            for issue in fr.critic_unresolved.issues] == [("meaning_drift", "blocked", None)]
    assert fr.verdict == "blocked"
    assert [record.model_slug for record in client.usage_tracker.records] == [
        "deepseek-v32", "yandexgpt-5.1", "yandexgpt-5.1",
    ] * 3
    assert transport.chat.completions.create.call_count == 9
    report = build_full_report(
        result,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=0),
        config=load_config(env={}),
    )
    assert "Both the name and the password are required, not alternatives." in report
    assert "Статус QA (K): 🔴 RED" in report
    body = build_translation_pr_body(1, "ydb-platform/ydb", publication_result=result)
    assert "QA K: 🔴 RED" in body
    assert "QA K: 🟢 GREEN" not in body


def test_F109_units_translation_is_critic_covered_and_refusal_blocks() -> None:
    source = "Сохранённый источник.\n\nНовый источник.\n"  # noqa: RUF001
    existing = "Accepted existing EN.\n\n"
    pair = DocPair(
        ru_path="ydb/docs/ru/concepts/glossary.md",
        en_path="ydb/docs/en/concepts/glossary.md",
        ru_changed=True,
    )
    coverage = CoveragePlan(
        source_path=pair.ru_path,
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
        en_hash=hashlib.sha256(existing.encode()).hexdigest(),
        units=(
            CoverageUnit(
                key="1" * 64,
                action="reuse_verified",
                source="Сохранённый источник.",
                en_span=(0, len("Accepted existing EN.")),
                target="Accepted existing EN.",
                reason="exact verified receipt",
            ),
            CoverageUnit(
                key="2" * 64,
                action="translate_required",
                source="Новый источник.\n",
                en_span=(len(existing), len(existing)),
                target=None,
                reason="required missing section",
            ),
        ),
        required_fragments=frozenset(),
        mode="units",
    )
    client, transport = _client(
        [_translate("Translated required prose.")]
        + ["Я не могу обсуждать эту тему."] * 3
    )

    result = run_pr_translation(
        [
            PairContent(
                pair=pair,
                ru_text=source,
                en_text=existing,
                coverage_plan=coverage,
            )
        ],
        client,
        load_glossary(),
        use_analyze_llm=False,
    )

    run = result.pair_results[0]
    assert run.file_result is not None
    assert run.file_result.verdict == "blocked"
    assert run.file_result.critic_initial is not None
    refusal_issues = run.file_result.critic_initial.issues
    assert [
        (issue.category, issue.severity) for issue in refusal_issues
    ] == [
        ("critic_model_refusal", "blocked"),
        ("critic_model_refusal", "blocked"),
    ]
    assert all(issue.comment for issue in refusal_issues)
    assert {
        segment_id: sum(
            f"segments: {segment_id}." in issue.comment
            for issue in refusal_issues
        )
        for segment_id in ("s0001", "s0002")
    } == {"s0001": 1, "s0002": 1}
    critic_requests = transport.chat.completions.create.call_args_list[1:]
    assert len(critic_requests) == 3
    critic_payloads = [
        call.kwargs["messages"][-1]["content"] for call in critic_requests
    ]
    assert all(
        text in critic_payloads[0]
        for text in ("Accepted existing EN.", "Translated required prose.")
    )
    assert "Accepted existing EN." in critic_payloads[1]
    assert "Translated required prose." in critic_payloads[2]
    from ydbdoc_review.pipeline.publication import evaluate_publication_impact
    from ydbdoc_review.pipeline.types import PublicationImpact

    assert evaluate_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE


@pytest.mark.parametrize(
    ("source", "target", "action", "mode"),
    [
        ("Текст.\n", "Accepted text.\n", "reuse_verified", "units"),
        ("```bash\necho hi\n```\n", "```bash\necho hi\n```\n", "materialize_protected", "units"),
        ("```bash\necho hi\n```\n", "```bash\necho hi\n```\n", "materialize_protected", "full"),
    ],
    ids=["reused-units", "protected-units", "protected-full"],
)
def test_F109_coverage_reuse_and_protected_content_make_no_model_calls(
    source: str, target: str, action: str, mode: str,
) -> None:
    content = _full_coverage_content(source, target)
    if mode == "units":
        coverage = CoveragePlan(
            source_path=content.pair.ru_path,
            source_hash=hashlib.sha256(source.encode()).hexdigest(),
            en_hash=hashlib.sha256(target.encode()).hexdigest(),
            units=(CoverageUnit(
                key="a" * 64,
                action=action,
                source=source,
                en_span=(0, len(target)),
                target=target,
                reason="exact verified receipt" if action == "reuse_verified" else "protected source structure",
            ),),
            required_fragments=frozenset(),
            mode="units",
        )
        content = replace(content, coverage_plan=coverage)
    client, transport = _client([])

    result = run_pr_translation([content], client, load_glossary())

    run = result.pair_results[0]
    assert run.error is None
    assert run.target_text == target
    assert run.file_result is not None
    assert run.file_result.verdict == "ok"
    assert run.file_result.critic_initial is None
    assert run.file_result.differential_meta["mode"] == mode
    assert client.usage_tracker.records == []
    transport.chat.completions.create.assert_not_called()


def test_F109_special_scopes(tmp_path) -> None:
    glossary_pair = DocPair(
        ru_path="ydb/docs/ru/concepts/glossary.md",
        en_path="ydb/docs/en/concepts/glossary.md",
        ru_changed=True,
    )
    glossary_client, glossary_transport = _client(
        [_translate("Draft term."), _critic(suggestion="Approved term."), _critic()]
    )
    glossary = run_pr_translation(
        [PairContent(pair=glossary_pair, ru_text="Термин.\n")],
        glossary_client,
        load_glossary(),
        use_analyze_llm=False,
    )
    glossary_run = glossary.pair_results[0]
    assert glossary_run.target_text is not None
    assert "Approved term." in glossary_run.target_text
    assert glossary_run.file_result is not None
    assert len(glossary_run.file_result.critic_applied) == 1
    assert glossary_transport.chat.completions.create.call_count == 3

    non_model_client, non_model_transport = _client([])
    deleted_pair = DocPair(
        ru_path="ydb/docs/ru/gone.md",
        en_path="ydb/docs/en/gone.md",
        ru_changed=True,
        ru_deleted=True,
    )
    deleted = run_pr_translation(
        [PairContent(pair=deleted_pair)],
        non_model_client,
        load_glossary(),
        use_analyze_llm=False,
    )
    assert deleted.pair_results[0].deleted

    navigation = merge_navigation_pair(
        NavigationPair(
            ru_path="ydb/docs/ru/toc.yaml",
            en_path="ydb/docs/en/toc.yaml",
            ru_changed=True,
            ru_deleted=True,
        ),
        repo_path=str(tmp_path),
        merge_base_with="HEAD",
        client=non_model_client,
        glossary=load_glossary(),
        config=load_config(
            env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}
        ),
    )
    assets_only = run_pr_translation(
        [], non_model_client, load_glossary(), use_analyze_llm=False
    )

    assert navigation.verdict == "ok"
    assert navigation.target_text is None
    assert assets_only.pair_results == []
    assert assets_only.failed_count == 0
    non_model_transport.chat.completions.create.assert_not_called()
