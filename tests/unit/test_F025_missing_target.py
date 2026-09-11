"""F-025: absent target locales are created, while source deletion is explicit."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def _ctx() -> HarnessContext:
    return HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )


def _fake_harness(calls: list[str]):
    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, _ctx):
            calls.append(state.file_path)
            result = MagicMock()
            result.final_text = f"created target for {state.file_path}\n"
            result.differential_meta = {"mode": "full", "semantic_noop": False}
            result.link_contract_issues = ()
            result.critic_initial = None
            result.critic_unresolved = None
            result.segment_alignment_error = None
            result.manual_actions = []
            result.heuristic_blocking = []
            result.heuristic_warnings = []
            result.heuristic_info = []
            return result

    return _FakeHarness


def test_F025_create_locales() -> None:
    ru_pair = DocPair(
        ru_path="ydb/docs/ru/new.md",
        en_path="ydb/docs/en/new.md",
        ru_changed=True,
    )
    en_pair = DocPair(
        ru_path="ydb/docs/ru/new-en.md",
        en_path="ydb/docs/en/new-en.md",
        en_changed=True,
    )
    ru_plan = plan_pair_heuristic(PairContent(pair=ru_pair, ru_text="RU source\n"))
    en_plan = plan_pair_heuristic(PairContent(pair=en_pair, en_text="EN source\n"))
    assert ru_plan.action == "translate_to_en"
    assert en_plan.action == "translate_to_ru"

    calls: list[str] = []
    with patch("ydbdoc_review.harness.pair.FileHarness", _fake_harness(calls)):
        ru_result = run_pair_plan(
            PairContent(pair=ru_pair, ru_text="RU source\n"), ru_plan, _ctx(), {}
        )
        en_result = run_pair_plan(
            PairContent(pair=en_pair, en_text="EN source\n"), en_plan, _ctx(), {}
        )

    assert ru_result.error is None and ru_result.target_text is not None
    assert en_result.error is None and en_result.target_text is not None
    assert calls == [ru_pair.ru_path, en_pair.en_path]


def test_F025_source_deleted() -> None:
    ru_deleted = DocPair(
        ru_path="ydb/docs/ru/removed.md",
        en_path="ydb/docs/en/removed.md",
        ru_deleted=True,
    )
    en_deleted = DocPair(
        ru_path="ydb/docs/ru/removed-en.md",
        en_path="ydb/docs/en/removed-en.md",
        en_deleted=True,
    )
    ru_plan = plan_pair_heuristic(PairContent(pair=ru_deleted, en_text="old EN\n"))
    en_plan = plan_pair_heuristic(PairContent(pair=en_deleted, ru_text="old RU\n"))

    ru_result = run_pair_plan(PairContent(pair=ru_deleted, en_text="old EN\n"), ru_plan, _ctx(), {})
    en_result = run_pair_plan(PairContent(pair=en_deleted, ru_text="old RU\n"), en_plan, _ctx(), {})

    assert ru_plan.action == "delete_en"
    assert en_plan.action == "delete_ru"
    assert ru_result.deleted and ru_result.error is None
    assert en_result.deleted and en_result.error is None

