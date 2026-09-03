"""REQUIREMENTS §5 / §13: run_pair_plan must not publish patched/preserved old EN."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def _pair() -> DocPair:
    return DocPair(
        ru_path="ydb/docs/ru/core/page.md",
        en_path="ydb/docs/en/core/page.md",
        ru_changed=True,
    )


def _plan(pair: DocPair) -> PairPlan:
    return PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="p1c no old-EN shortcuts",
    )


def _ctx() -> HarnessContext:
    return HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )


def _fake_harness(final_text: str, *, existing: str, calls: dict[str, int]):
    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del ctx
            calls["n"] += 1
            assert state.existing_target_text == existing
            result = MagicMock()
            result.final_text = final_text
            result.differential_meta = {
                "mode": "full",
                "semantic_noop": False,
                "enabled": False,
            }
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


def test_run_pair_plan_href_only_does_not_publish_patched_old_en() -> None:
    """Former apply_href_only_delta shortcut must not emit old EN body."""
    pair = _pair()
    old_en = "See [nodes](../a.md).\nHREF_ONLY_OLD_EN_BODY\n"
    fresh = "See [nodes](../b.md).\nFULL_ONE_PASS_FROM_RU\n"
    content = PairContent(
        pair=pair,
        ru_base_text="См. [узлы](../a.md).\n",
        ru_text="См. [узлы](../b.md).\n",
        en_base_text=old_en,
        en_text=old_en,
    )
    calls = {"n": 0}
    import inspect

    from ydbdoc_review.harness import pair as pair_mod

    assert "apply_href_only_delta" not in inspect.getsource(pair_mod.run_pair_plan)
    with patch(
        "ydbdoc_review.harness.pair.FileHarness",
        _fake_harness(fresh, existing=old_en, calls=calls),
    ):
        result = run_pair_plan(content, _plan(pair), _ctx(), cache={})

    assert calls["n"] == 1
    assert result.target_text is not None
    assert "HREF_ONLY_OLD_EN_BODY" not in result.target_text
    assert "FULL_ONE_PASS_FROM_RU" in result.target_text


def test_run_pair_plan_deterministic_preserve_does_not_publish_old_en() -> None:
    """Former _try_deterministic_en_preserve shortcut must not emit old EN body."""
    pair = _pair()
    old_en = "# Page\n\nPRESERVED_OLD_EN_BODY\n"
    fresh = "# Page\n\nFULL_ONE_PASS_FROM_RU\n"
    content = PairContent(
        pair=pair,
        # Distinct base/tip so preserve helper would historically consider a delta.
        ru_base_text="# Страница\n\nСтарый RU.\n",
        ru_text="# Страница\n\nНовый RU.\n",
        en_base_text=old_en,
        en_text=old_en,
        force_full_overwrite=False,
    )
    calls = {"n": 0}
    with (
        patch(
            "ydbdoc_review.harness.pair._try_deterministic_en_preserve",
            return_value=old_en,
        ) as preserve,
        patch(
            "ydbdoc_review.harness.pair.FileHarness",
            _fake_harness(fresh, existing=old_en, calls=calls),
        ),
    ):
        result = run_pair_plan(content, _plan(pair), _ctx(), cache={})

    preserve.assert_not_called()
    assert calls["n"] == 1
    assert result.target_text is not None
    assert "PRESERVED_OLD_EN_BODY" not in result.target_text
    assert "FULL_ONE_PASS_FROM_RU" in result.target_text


def test_run_pair_plan_localized_mirror_bait_still_full_translates() -> None:
    """Even when localized mirror would return patched EN, translate path ignores it."""
    pair = _pair()
    old_en = "See [{#T}](../a.md).\nLOCALIZED_MIRROR_OLD_EN\n"
    mirror_bait = "See [{#T}](../b.md).\nLOCALIZED_MIRROR_OLD_EN\n"
    fresh = "See [{#T}](../b.md).\nFULL_ONE_PASS_FROM_RU\n"
    content = PairContent(
        pair=pair,
        ru_base_text="См. [{#T}](../a.md).\n",
        ru_text="См. [{#T}](../b.md).\n",
        en_base_text=old_en,
        en_text=old_en,
    )
    calls = {"n": 0}
    with (
        patch(
            "ydbdoc_review.harness.pair.apply_localized_mirror_delta",
            return_value=mirror_bait,
        ) as mirror,
        patch(
            "ydbdoc_review.harness.pair.FileHarness",
            _fake_harness(fresh, existing=old_en, calls=calls),
        ),
    ):
        result = run_pair_plan(content, _plan(pair), _ctx(), cache={})

    # Preserve helper is not invoked on translate, so mirror is never consulted.
    mirror.assert_not_called()
    assert calls["n"] == 1
    assert result.target_text is not None
    assert "LOCALIZED_MIRROR_OLD_EN" not in result.target_text
    assert "FULL_ONE_PASS_FROM_RU" in result.target_text
