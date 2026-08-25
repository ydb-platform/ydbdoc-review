"""Regression: en_toc_reachable must reach finalize_en_target (§6.112 / #46846)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.fence_integrity import fence_marker_tokens


def test_run_pair_plan_forwards_en_toc_reachable_to_harness():
    """Bug #46846: strip never ran because pair rebuilt HarnessContext without reachable."""
    pair = DocPair(
        ru_path="ydb/docs/ru/core/dev/streaming-query/index.md",
        en_path="ydb/docs/en/core/dev/streaming-query/index.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# RU\n\nSee [Watermarks](watermarks.md).\n",
        en_text=None,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="test",
    )
    reachable = frozenset(
        {
            "ydb/docs/en/core/dev/streaming-query/index.md",
            "ydb/docs/en/core/dev/streaming-query/patterns.md",
        }
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
        en_toc_reachable=reachable,
    )

    captured: dict[str, object] = {}

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            captured["en_toc_reachable"] = ctx.en_toc_reachable
            result = MagicMock()
            result.final_text = "ok"
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        run_pair_plan(content, plan, parent, {})

    assert captured["en_toc_reachable"] is reachable


def test_run_pair_plan_keeps_existing_en_on_translate_llm_failure():
    """§6.184: glossary timeout must not leave a completeness gap for the whole PR."""
    from ydbdoc_review.llm.errors import LLMError

    pair = DocPair(
        ru_path="ydb/docs/ru/core/concepts/glossary.md",
        en_path="ydb/docs/en/core/concepts/glossary.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# RU\n\nТекст.\n",
        en_text="# EN\n\nText.\n",
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="test",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    class _BoomHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            raise LLMError("Request timed out")

    with patch("ydbdoc_review.harness.pair.FileHarness", _BoomHarness):
        result = run_pair_plan(content, plan, parent, {})

    assert result.error is None
    assert result.target_text == "# EN\n\nText.\n"


def test_pr_50904_deterministic_index_patch_skips_unrelated_href_repairs():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/devops/concepts/index.md",
        en_path="ydb/docs/en/core/devops/concepts/index.md",
        ru_changed=True,
    )
    clean = "# Concepts\n\n* [{#T}](../backup-and-recovery.md)\n"
    patched = clean + "* [{#T}](./node-authorization.md)\n"
    content = PairContent(
        pair=pair,
        ru_text="# Концепции\n\n* [{#T}](./node-authorization.md)\n",
        ru_base_text="# Концепции\n",
        en_text=clean,
        en_base_text=clean,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="source-only list insertion",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(), glossary=load_glossary(), config=cfg
    )

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = patched
            result.differential_meta = {"deterministic_autotitle_patch": True}
            return result

    with (
        patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness),
        patch("ydbdoc_review.harness.pair.restore_autotitle_hrefs") as restore,
    ):
        result = run_pair_plan(content, plan, parent, {})

    assert result.target_text == patched
    restore.assert_not_called()


def test_pr_50904_critic_only_receives_ru_merge_base():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/devops/concepts/index.md",
        en_path="ydb/docs/en/core/devops/concepts/index.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# Концепции\n",
        ru_base_text="# Старые концепции\n",
        en_text="# Concepts\n",
        en_base_text="# Old concepts\n",
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="verify",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(MagicMock(), glossary=load_glossary(), config=cfg)
    captured = {}

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del ctx
            captured["base_source_text"] = state.base_source_text
            result = MagicMock()
            result.final_text = content.en_text
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        run_pair_plan(content, plan, parent, {})

    assert captured["base_source_text"] == content.ru_base_text


def test_href_only_pair_bypasses_llm_and_repairs():
    """#45949: href-only source deltas are deterministic and byte-preserving."""
    pair = DocPair(
        ru_path="ydb/docs/ru/core/maintenance/manual/dynamic-config.md",
        en_path="ydb/docs/en/core/maintenance/manual/dynamic-config.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_base_text="До [узлы](../manual/node.md).\n",
        ru_text="До [узлы](../concepts/node.md).\n",
        en_base_text="Before [nodes](../manual/node.md).\n",
        en_text="Before [nodes](../manual/node.md).\n",
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="href-only",
    )
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(
            env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}
        ),
    )

    with patch("ydbdoc_review.harness.pair.FileHarness") as harness:
        result = run_pair_plan(content, plan, parent, {})

    harness.assert_not_called()
    assert result.target_text == "Before [nodes](../concepts/node.md).\n"


def test_semantic_noop_bypasses_pair_link_stripping_exactly():
    """#49933/#50888: pair post-pass must not mutate byte-preserved EN."""
    pair = DocPair(
        ru_path="ydb/docs/ru/core/reference/export/_includes/limitations.md",
        en_path="ydb/docs/en/core/reference/export/_includes/limitations.md",
        ru_changed=True,
    )
    source = "Секреты надо [создавать](../../create-secret.md).\n"
    existing = "Secrets must be [created](../../create-secret.md).\n"
    content = PairContent(
        pair=pair,
        ru_text=source,
        en_text=existing,
        ru_base_text=source,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="formatting-only",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
        en_toc_reachable=frozenset(),
    )

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = existing
            result.differential_meta = {"semantic_noop": True}
            return result

    with (
        patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness),
        patch(
            "ydbdoc_review.validation.glossary_toc_links.strip_unreachable_internal_links"
        ) as strip,
    ):
        result = run_pair_plan(content, plan, parent, {})

    assert result.target_text == existing
    strip.assert_not_called()


def test_pr_50904_href_only_delta_localizes_fragment_against_en_target():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/reference/configuration/client.md",
        en_path="ydb/docs/en/core/reference/configuration/client.md",
        ru_changed=True,
    )
    fragment = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    old = f"../../devops/deployment-options/manual/node.md#{fragment}"
    new = f"../../devops/concepts/node.md#{fragment}"
    content = PairContent(
        pair=pair,
        ru_base_text=f"[регистрации динамических узлов]({old})\n",
        ru_text=f"[регистрации динамических узлов]({new})\n",
        en_base_text=f"[registering dynamic nodes]({old})\n",
        en_text=f"[registering dynamic nodes]({old})\n",
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="href-only",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    target = "ydb/docs/en/core/devops/concepts/node.md"
    ru_target = "ydb/docs/ru/core/devops/concepts/node.md"
    files = {
        target: "## Enabling node authentication and authorization mode\n",
        ru_target: "## Включение режима аутентификации и авторизации узлов\n",
    }
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
        docs_text_reader=files.get,
    )

    result = run_pair_plan(content, plan, parent, {})

    assert result.target_text == (
        "[registering dynamic nodes](../../devops/concepts/node.md"
        "#enabling-node-authentication-and-authorization-mode)\n"
    )


def test_run_pair_plan_restores_missing_heading_anchor_after_translate():
    """§6.191 / #49957: pair post-pass copies RU {#id} onto EN H1."""
    pair = DocPair(
        ru_path="ydb/docs/ru/core/dev/example-app/_includes/example-dotnet.md",
        en_path="ydb/docs/en/core/dev/example-app/_includes/example-dotnet.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# Приложение на C# {#csharp-app}\n\nТело.\n",
        en_text="# Example app in C# (.NET)\n\nBody.\n",
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="test",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = "# Example app in C# (.NET)\n\nBody.\n"
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        result = run_pair_plan(content, plan, parent, {})

    assert result.error is None
    assert result.target_text is not None
    assert "{#csharp-app}" in result.target_text


def test_pair_postprocess_repairs_fences_after_structural_repair():
    source = "- item\n\n    ```python\n    source()\n      ```\n"
    target = "- item\n\n    ```python\n    translated()\n      ```\n"
    pair = DocPair(
        ru_path="ydb/docs/ru/core/legacy.md",
        en_path="ydb/docs/en/core/legacy.md",
        ru_changed=True,
        en_changed=True,
    )
    content = PairContent(pair=pair, ru_text=source, en_text=target)
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="test",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = target
            return result

    with (
        patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness),
        patch(
            "ydbdoc_review.harness.pair.repair_en_structure_from_ru",
            side_effect=lambda text, _source: text + "```\n",
        ),
    ):
        result = run_pair_plan(content, plan, parent, {})

    assert result.target_text is not None
    assert result.target_text == target
    assert fence_marker_tokens(result.target_text) == fence_marker_tokens(source)


def test_critic_only_empty_ru_still_returns_exact_checkout_bytes():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/empty.md",
        en_path="ydb/docs/en/core/empty.md",
        ru_changed=True,
        en_changed=True,
    )
    target = "Checkout bytes.\n"
    content = PairContent(pair=pair, ru_text="", en_text=target)
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="test",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(MagicMock(), glossary=load_glossary(), config=cfg)

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            del state, ctx
            result = MagicMock()
            result.final_text = "Mutated in memory.\n"
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        result = run_pair_plan(content, plan, parent, {})

    assert result.target_text == target
