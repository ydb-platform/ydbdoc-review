# ruff: noqa: RUF001
"""Fence copy guarantees: code blocks must not be altered by translation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ydbdoc_review.pipeline.dependency_queue import DependencyPlan, QueueEntry
from ydbdoc_review.pipeline.translation_transaction import run_translation_transaction
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import translate_ru_to_en_once
from ydbdoc_review.validation.fence_integrity import (
    check_fence_body_copy,
    code_blocks_from_text,
    fence_content_matches_source,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


def test_fence_content_allows_whitespace_only_diff():
    """§6.61 #43860: extra blank line inside yql fence is not corruption."""
    src = "DECLARE $customer_id AS Uint64;\nSELECT *\nFROM orders\n"
    tgt = "DECLARE $customer_id AS Uint64;\n\nSELECT *\nFROM orders\n"
    assert fence_content_matches_source(src, tgt)
    assert not check_fence_body_copy(f"```yql\n{src}```\n", f"```yql\n{tgt}```\n")

    assert fence_content_matches_source(
        "bootstrap --uuid <строка>\n",
        "bootstrap --uuid <string>\n",
    )
    assert not fence_content_matches_source(
        "bootstrap --uuid <строка>\n",
        "bootstrap --uuid <string>\nextra\n",
    )


def test_enforce_source_fenced_blocks_restores_tampered_fence():
    ru = (
        "## Step\n\n"
        "Prose here.\n\n"
        "```bash\n"
        "sudo ydb admin node config init --config-dir /opt/ydb/cfg\n"
        "```\n"
    )
    en_bad = (
        "## Step\n\n"
        "Prose translated.\n\n"
        "```bash\n"
        "sudo ydb admin node config init --config-dir/opt/ydb/cfg\n"
        "```\n"
    )
    findings = check_fence_body_copy(ru, en_bad)
    assert findings
    assert "--config-dir/opt" in en_bad


def test_check_fence_body_copy_detects_pipeline_change():
    ru = "```bash\n/opt/ydb/bin/ydb --ca-file /opt/ydb/certs/ca.crt\n```\n"
    en = "```bash\n/opt/ydb/bin/ydb --ca-file ca.crt\n```\n"
    warnings = check_fence_body_copy(ru, en)
    assert warnings
    assert "fence_body_copy" in warnings[0]


def test_malformed_legacy_source_is_normalized_before_body_validation():
    source = """{% list tabs %}

- Python

    {% cut "asyncio" %}

    ```python
    A

      ```python
      B

    {% endcut %}

    ```python
    C

      ```

    - Native SDK (Asyncio)

      ```python
      D
      ```

    {% endlist %}

{% endlist %}
"""
    same_markers = source.replace("    A\n", "    translated comment\n")
    assert check_fence_body_copy(source, same_markers)

    extra_marker = same_markers + "```\n"
    assert check_fence_body_copy(source, extra_marker)


def test_normalize_ru_config_dir_before_translate():
    ru = "```bash\ninit --config-dir/opt/ydb/cfg\n```\n"
    norm = normalize_ru_source_for_translation(ru)
    assert "--config-dir /opt" in norm
    assert "--config-dir/opt" not in norm


def test_fence_content_allows_homoglyph_vm():
    assert fence_content_matches_source(
        "    - host: x #FQDN ВМ\n",
        "    - host: x #FQDN VM\n",
    )


def test_check_fence_body_copy_ignores_normalize_fix():
    """EN may differ from raw RU when pipeline fixed --config-dir/opt in fences."""
    raw_ru = "```bash\ninit --config-dir/opt/ydb/cfg\n```\n"
    en = "```bash\ninit --config-dir /opt/ydb/cfg\n```\n"
    assert not check_fence_body_copy(raw_ru, en, source_lang="ru")


def test_check_fence_body_copy_ignores_homoglyph_only_diff():
    raw_ru = "```yaml\n    - host: x #FQDN ВМ\n```\n"
    en = "```yaml\n    - host: x #FQDN VM\n```\n"
    assert not check_fence_body_copy(raw_ru, en, source_lang="ru")


def test_enforce_source_fenced_blocks_preserves_text_fence_body():
    """§6.59: read-only detector accepts translated text-diagram labels."""
    ru = "```text\n├─ попытка: ERROR\n```\n"
    en = "```text\n├─ attempt: ERROR\n```\n"
    assert check_fence_body_copy(ru, en) == []
    assert "attempt" in en


def test_fence_content_allows_cyrillic_comment_translation_only():
    ru = (
        "package main\n\n"
        "func main() {\n"
        "    // 1. Настраиваем провайдер логов.\n"
        "    // ... используйте db ...\n"
        "}\n"
    )
    en = (
        "package main\n\n"
        "func main() {\n"
        "    // 1. Configure the log provider.\n"
        "    // ... use db ...\n"
        "}\n"
    )
    assert fence_content_matches_source(ru, en)
    assert not check_fence_body_copy(f"```go\n{ru}```", f"```go\n{en}```")


def test_fence_content_allows_trailing_slash_comment_translation():
    ru = "    panic(err) // аварийный выход при ошибке\n"
    en = "    panic(err) // Abort on connection error\n"
    assert fence_content_matches_source(ru, en)
    assert not check_fence_body_copy(f"```go\n{ru}```", f"```go\n{en}```")


def test_fence_content_allows_trailing_hash_yaml_comment_translation():
    """Regression #47164: YAML ``#`` trailing comments may be translated."""
    ru = "    disk_scope: <disk_scope>  # необязательный атрибут\n"
    en = "    disk_scope: <disk_scope>  # optional attribute\n"
    assert fence_content_matches_source(ru, en)
    assert not check_fence_body_copy(f"```yaml\n{ru}```", f"```yaml\n{en}```")


def test_fence_content_allows_angle_placeholder_translation():
    """Regression #47164: RU ``<имя домена>`` vs EN ``<domain name>`` in fences."""
    ru = (
        "domains:\n"
        "- name: <имя домена>\n"
        "  storage_pool_types:\n"
        "  - kind: <тип используемых физических устройств>\n"
    )
    en = (
        "domains:\n"
        "- name: <domain name>\n"
        "  storage_pool_types:\n"
        "  - kind: <type of physical devices used>\n"
    )
    assert fence_content_matches_source(ru, en)
    assert not check_fence_body_copy(f"```yaml\n{ru}```", f"```yaml\n{en}```")


def test_fence_content_allows_angle_placeholder_plus_hash_comment():
    ru = "    disk_scope: <имя>  # необязательный атрибут\n"
    en = "    disk_scope: <name>  # optional attribute\n"
    assert fence_content_matches_source(ru, en)


def test_fence_content_rejects_code_line_change_beside_comments():
    ru = "x := 1 // значение\n"
    en = "y := 1 // value\n"
    assert not fence_content_matches_source(ru, en)


def test_fence_content_allows_mermaid_label_translation():
    ru = (
        "sequenceDiagram\n"
        "    participant Топик\n"
        "    participant Запрос v1\n"
        "    Топик->>Запрос v1: События A..D\n"
    )
    en = (
        "sequenceDiagram\n"
        "    participant Topic\n"
        "    participant Query v1\n"
        "    Topic->>Query v1: Events A..D\n"
    )
    assert fence_content_matches_source(ru, en)
    assert not check_fence_body_copy(
        f"```mermaid\n{ru}```",
        f"```mermaid\n{en}```",
        source_lang="ru",
    )


def test_fence_content_allows_mermaid_quoted_hyphen_vs_space_labels():
    """#49578: RU «Дата-центр» vs EN «Data center» must not fence_body_copy."""
    ru = (
        "graph TD\n"
        '    subgraph DC1["Дата-центр 1 (Fail realm 1)"]\n'
        '        Hash["Хеш-функция\\nот ID записи"]\n'
        "    end\n"
    )
    en = (
        "graph TD\n"
        '    subgraph DC1["Data center 1 (Fail realm 1)"]\n'
        '        Hash["Hash function\\nfrom record ID"]\n'
        "    end\n"
    )
    assert fence_content_matches_source(ru, en, fence_info="mermaid")
    assert not check_fence_body_copy(
        f"```mermaid\n{ru}```",
        f"```mermaid\n{en}```",
        source_lang="ru",
    )


def test_fence_content_allows_mermaid_note_and_message_translation():
    """Regression #41206: Note/arrow message text may be shorter in EN."""
    ru = (
        "sequenceDiagram\n"
        "    participant Топик\n"
        "    participant Запрос v1\n"
        "    participant Запрос v2\n"
        "    Топик->>Запрос v1: События A..D\n"
        "    Note over Запрос v1: Чекпоинт: смещение = 4\n"
        "    Note over Топик: События E, F поступают в топик\n"
        "    Топик--xЗапрос v2: E, F (не прочитаны)\n"
        "    Топик->>Запрос v2: G (новое)\n"
    )
    en = (
        "sequenceDiagram\n"
        "    participant Topic\n"
        "    participant Query v1\n"
        "    participant Query v2\n"
        "    Topic->>Query v1: Events A..D\n"
        "    Note over Query v1: Checkpoint: offset = 4\n"
        "    Note over Topic: Events E, F arrive\n"
        "    Topic--xQuery v2: E, F (not read)\n"
        "    Topic->>Query v2: G (new)\n"
    )
    assert fence_content_matches_source(ru, en)
    assert not check_fence_body_copy(
        f"```mermaid\n{ru}```",
        f"```mermaid\n{en}```",
        source_lang="ru",
    )


def test_fence_content_allows_text_diagram_label_translation():
    """Regression #44103: `` ```text `` span tree labels may be translated (§6.59)."""
    ru = (
        "ydb.RunWithRetry  (Internal)\n"
        "├─ ydb.Try        (Internal)   ← 1-я попытка: ERROR\n"
        "│  ├─ ydb.ExecuteQuery (Client)\n"
        "│  └─ ydb.Commit       (Client) ← ERROR: Transaction Lock Invalidated\n"
        "└─ ydb.Try        (Internal)   ← 2-я попытка: SUCCESS, ydb.retry.backoff_ms=50\n"
        "   └─ ydb.Commit       (Client)\n"
    )
    en = (
        "ydb.RunWithRetry  (Internal)\n"
        "├─ ydb.Try        (Internal)   ← 1st attempt: ERROR\n"
        "│  ├─ ydb.ExecuteQuery (Client)\n"
        "│  └─ ydb.Commit       (Client) ← ERROR: Transaction Lock Invalidated\n"
        "└─ ydb.Try        (Internal)   ← 2nd attempt: SUCCESS, ydb.retry.backoff_ms=50\n"
        "   └─ ydb.Commit       (Client)\n"
    )
    assert fence_content_matches_source(ru, en, fence_info="text")
    assert not check_fence_body_copy(
        f"```text\n{ru}```",
        f"```text\n{en}```",
        source_lang="ru",
    )


def test_fence_content_rejects_text_diagram_structure_change():
    ru = "├─ ydb.Try        (Internal)   ← 1-я попытка: ERROR\n"
    en = "├─ ydb.ExecuteQuery (Client)   ← 1st attempt: ERROR\n"
    assert not fence_content_matches_source(ru, en, fence_info="text")


def test_fence_content_rejects_mermaid_structure_change():
    ru = "sequenceDiagram\n    participant Топик\n    Топик->>Приемник: событие\n"
    en = "sequenceDiagram\n    participant Topic\n    Topic->Sink: event\n"
    assert not fence_content_matches_source(ru, en)


def test_finalize_en_after_enforce_fixes_stroka_and_vm_in_indented_fence():
    """Legacy identity: v010 preserves the source fences and blocks publication."""
    raw_ru = (
        "5. Init:\n\n"
        "   ```yaml\n"
        "    - host: static-node-1.ydb-cluster.com #FQDN ВМ\n"
        "   ```\n\n"
        "   ```bash\n"
        "   ydb admin cluster bootstrap --uuid <строка>\n"
        "   ```\n"
    )
    manifest = TranslationJobManifest(TranslationModelPolicy(
        translate=ModelPair("translate-primary", "translate-fallback"),
        critic=ModelPair("critic-primary", "critic-fallback"),
        repair=ModelPair("repair-primary", "repair-fallback"),
    ))

    class Client:
        def chat_once(self, messages, *, explicit_model, role, **_kwargs):
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            payload = json.loads(messages[-1]["content"])
            return SimpleNamespace(content=json.dumps({"segments": [
                {"id": item["id"], "text": item["text"].replace("Init", "Initialization")}
                for item in payload["segments"]
            ]}, ensure_ascii=False))

    path = "ydb/docs/ru/reference/configuration/example.md"
    translated = translate_ru_to_en_once(raw_ru, Client(), file_path=path, manifest=manifest)
    source_bodies = [block.content for block in code_blocks_from_text(raw_ru)]
    translated_bodies = [block.content for block in code_blocks_from_text(translated.text)]
    assert translated_bodies == source_bodies
    assert "#FQDN ВМ" in translated.text
    assert "<строка>" in translated.text

    result = run_translation_transaction(
        DependencyPlan((QueueEntry(path, "initial"),), (), 1, 0),
        read_ru=lambda _path: raw_ru,
        client=Client(),
        to_en_path=lambda value: value.replace("/ru/", "/en/"),
        manifest=manifest,
    )
    messages = result.report["link_findings"][0]["messages"]
    assert messages[:2] == [
        "cyrillic_in_code_fence: block 1 `yaml` line 0: «- host: static-node-1.ydb-cluster.com #FQDN ВМ»",
        "cyrillic_in_code_fence: block 2 `bash` line 0: «ydb admin cluster bootstrap --uuid <строка>»",
    ]
    assert not result.publishable
    assert result.staged == {}

    production_path = Path(__file__).parents[2] / "src/ydbdoc_review"
    production_entry = "\n".join(
        (production_path / relative).read_text(encoding="utf-8")
        for relative in (
            "pipeline/orchestrator.py",
            "pipeline/translation_transaction.py",
            "translation/one_pass.py",
        )
    )
    for retired_writer in (
        "_finalize_en_target",
        "normalize_ru_source_for_translation",
        "enforce_source_fenced_blocks",
    ):
        assert retired_writer not in production_entry


def test_fence_body_allows_comment_translation_with_trailing_blank_line():
    """§6.156: RU fence often keeps a trailing blank line EN drops."""
    ru = "```yql\nSELECT\n   x,  -- ОК: колонка\nFROM t\n\n```\n"
    en = "```yql\nSELECT\n   x,  -- OK: column\nFROM t\n```\n"
    assert check_fence_body_copy(ru, en, source_lang="ru") == []
