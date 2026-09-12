"""F-109: translate/continue finish one inline quality cycle before publication."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
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


def test_F109_one_cycle() -> None:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    client, transport = _client([_translate("Draft."), _critic()])

    result = run_pr_translation(
        [PairContent(pair=pair, ru_text="Текст.\n")],
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
