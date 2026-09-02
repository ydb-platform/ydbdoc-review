"""Coverage-preserving migration checks for the v010 production path."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

from ydbdoc_review.pipeline.analyze import PairAction, PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import translate_ru_to_en_once

MANIFEST = TranslationJobManifest(
    TranslationModelPolicy(
        translate=ModelPair("translate-primary", "translate-fallback"),
        critic=ModelPair("critic-primary", "critic-fallback"),
        repair=ModelPair("repair-primary", "repair-fallback"),
    )
)


class ExactClient:
    def __init__(self, *, fail_translate: bool = False):
        self.fail_translate = fail_translate
        self.calls: list[tuple[str, str, list[dict[str, str]]]] = []

    def chat_once(self, messages, *, explicit_model, role, **kwargs):
        self.calls.append((role, explicit_model, messages))
        if role == "critic":
            return SimpleNamespace(content=json.dumps({"findings": []}))
        if self.fail_translate:
            return SimpleNamespace(content="not-json")
        payload = json.loads(messages[-1]["content"])
        replacements = {
            "Заголовок": "Heading",
            "Текст": "Text",
            "Ссылка": "Link",
            "Первый": "First",
            "Второй": "Second",
        }
        translated = []
        for item in payload["segments"]:
            text = item["text"]
            for source, target in replacements.items():
                text = text.replace(source, target)
            translated.append({"id": item["id"], "text": text})
        return SimpleNamespace(content=json.dumps({"segments": translated}))


def _content(path: str, text: str, *, en_text: str | None = None) -> PairContent:
    return PairContent(
        pair=DocPair(
            ru_path=path,
            en_path=path.replace("/ru/", "/en/", 1),
            ru_changed=True,
        ),
        ru_text=text,
        en_text=en_text,
    )


def test_production_workflow_calls_only_one_pass_transaction():
    from ydbdoc_review.github import workflow
    from ydbdoc_review.pipeline import orchestrator

    workflow_source = inspect.getsource(workflow.run_doc_translate)
    orchestrator_source = inspect.getsource(orchestrator.run_pr_translation)
    assert "run_pr_translation(" in workflow_source
    assert "run_translation_transaction(" in orchestrator_source
    for forbidden in ("harness", "critic_retranslate", "differential"):
        assert forbidden not in workflow_source
        assert forbidden not in orchestrator_source


def test_all_added_or_modified_ru_markdown_selects_translate_ru_to_en_once():
    cases = (
        _content("ydb/docs/ru/core/page.md", "Текст.\n"),
        _content("ydb/docs/ru/core/glossary.md", "Текст.\n"),
        _content("ydb/docs/ru/core/_includes/previously-skipped.md", "Текст.\n"),
    )
    assert {plan_pair_heuristic(case).action for case in cases} == {
        "translate_ru_to_en_once"
    }


def test_verify_workflow_is_read_only_for_document_bytes():
    original = "Existing English bytes.\n"

    class NoModelClient:
        def chat_once(self, *args, **kwargs):
            raise AssertionError("read-only QA must not invoke a model")

    result = translate_file(
        "Русский исходник.\n",
        NoModelClient(),
        enable_translate=False,
        existing_target_text=original,
        file_path="ydb/docs/en/page.md",
    )
    assert result.final_text == original


def test_existing_en_bytes_are_never_read_for_translation():
    content = _content(
        "ydb/docs/ru/a.md",
        "Текст.\n",
        en_text="POISON OLD EN THAT MUST NOT BE READ",
    )

    def reject_reader(path: str):
        raise AssertionError(f"old EN reader used: {path}")

    result = run_pr_translation(
        [content],
        ExactClient(),
        manifest=MANIFEST,
        docs_text_reader=reject_reader,
    )
    assert result.translated_count == 1
    assert "POISON" not in (result.pair_results[0].target_text or "")


def test_translation_failure_stages_nothing_and_never_reads_old_en():
    result = run_pr_translation(
        [_content("ydb/docs/ru/a.md", "Текст.\n", en_text="OLD EN")],
        ExactClient(fail_translate=True),
        manifest=MANIFEST,
        docs_text_reader=lambda path: (_ for _ in ()).throw(
            AssertionError(f"old EN reader used: {path}")
        ),
    )
    assert result.failed_count == 1
    assert result.translated_count == 0
    assert result.pair_results[0].target_text is None


def test_empty_or_unparseable_ru_blocks_without_model_or_stage():
    client = ExactClient()
    result = run_pr_translation(
        [_content("ydb/docs/ru/empty.md", "")],
        client,
        manifest=MANIFEST,
    )
    assert result.failed_count == 1
    assert result.pair_results[0].target_text is None
    assert client.calls == []


def test_real_markdown_fixture_round_trips_all_source_atoms():
    source = (
        "# Заголовок {#ascii-anchor}\n\n"
        "Текст [Ссылка `cmd`](../node.md?q=1#frag \"title\").\n\n"
        "{% include [часть](../_includes/config.md) %}\n\n"
        "```yaml\nserver:\n  port: 2135 # источник\n```\n"
        "\n{% list tabs %}\n\n- YDB CLI\n\n  Текст YDB.\n\n{% endlist %}\n"
    )
    result = translate_ru_to_en_once(
        source,
        ExactClient(),
        file_path="ydb/docs/ru/core/page.md",
        manifest=MANIFEST,
    )
    assert '{#ascii-anchor}' in result.text
    assert '(../node.md?q=1#frag "title")' in result.text
    assert "{% include [часть](../_includes/config.md) %}" in result.text
    assert "```yaml\nserver:\n  port: 2135 # источник\n```" in result.text
    assert "{% list tabs %}" in result.text
    assert "YDB CLI" in result.text
    assert "Text YDB." in result.text


def test_code_and_fence_comments_are_source_owned_and_never_sent_to_repair():
    source = "Текст.\n\n```yaml\nkey: value # комментарий\n```\n"
    client = ExactClient()
    result = translate_ru_to_en_once(
        source,
        client,
        file_path="ydb/docs/ru/page.md",
        manifest=MANIFEST,
    )
    assert "# комментарий" in result.text
    for role, _model, messages in client.calls:
        if role in {"translate", "repair"}:
            assert "комментарий" not in json.dumps(messages, ensure_ascii=False)


def test_source_owned_cyrillic_fence_blocks_transaction_without_writer():
    source = "Текст.\n\n```yaml\nkey: value # комментарий\n```\n"
    result = run_pr_translation(
        [_content("ydb/docs/ru/page.md", source)],
        ExactClient(),
        manifest=MANIFEST,
    )
    assert result.failed_count == 1
    assert result.translated_count == 0
    assert result.pair_results[0].target_text is None
    assert "cyrillic_in_fence" in (result.pair_results[0].error or "")


def test_no_legacy_translation_modules_importable():
    assert importlib.util.find_spec("ydbdoc_review.translation.differential") is None
    assert importlib.util.find_spec("ydbdoc_review.translation.critic_retranslate") is None


def test_forbidden_post_translation_writers_are_absent():
    assert importlib.util.find_spec("ydbdoc_review.translation.repair") is None
    assert importlib.util.find_spec("ydbdoc_review.validation.structural_repair") is None

    from ydbdoc_review.validation import fence_comments, fragment_repair, prose_cyrillic

    forbidden = {
        fence_comments: {
            "translate_cyrillic_fence_comments",
            "translate_cyrillic_fence_comments_with_client",
            "translate_cyrillic_text_fences",
            "translate_cyrillic_text_fences_with_client",
        },
        prose_cyrillic: {
            "translate_cyrillic_prose",
            "translate_cyrillic_prose_with_client",
        },
        fragment_repair: {
            "prefer_baseline_href_when_fragment_missing",
            "repair_en_fragments",
        },
    }
    assert {
        f"{module.__name__}.{name}"
        for module, names in forbidden.items()
        for name in names
        if hasattr(module, name)
    } == set()


def test_no_translation_imports_harness_package():
    source_root = Path(__file__).parents[2] / "src" / "ydbdoc_review"
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        if "harness" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "ydbdoc_review.harness"
            ):
                offenders.append(str(path.relative_to(source_root)))
    assert offenders == []


def test_translation_action_enum_contains_only_v010_translation_action():
    actions = set(get_args(PairAction))
    assert actions == {"translate_ru_to_en_once", "delete_en", "read_only_qa"}


def test_critic_only_has_no_producer_or_apply_reachability():
    """v008 step 6: no producer, deserializer, dispatch, or apply branch remains."""
    source_root = Path(__file__).parents[2] / "src" / "ydbdoc_review"
    occurrences: list[tuple[str, str]] = []
    harness_paths = list((source_root / "harness").glob("*.py"))
    assert harness_paths == []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            literals = {
                value.value
                for value in ast.walk(node)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            }
            if "critic_only" in literals:
                occurrences.append((str(path.relative_to(source_root)), node.name))
    assert occurrences == []
    assert "critic_only" not in get_args(PairAction)
    from ydbdoc_review.github.workflow import _apply_results_to_disk

    assert "critic_only" not in inspect.getsource(_apply_results_to_disk)
