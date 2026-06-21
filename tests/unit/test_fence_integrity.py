"""Fence copy guarantees: code blocks must not be altered by translation."""

from __future__ import annotations

from ydbdoc_review.pipeline.translate_file import _finalize_en_target
from ydbdoc_review.validation.fence_integrity import (
    check_fence_body_copy,
    enforce_source_fenced_blocks,
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
    fixed = enforce_source_fenced_blocks(en_bad, ru)
    assert "--config-dir /opt/ydb/cfg" in fixed
    assert "--config-dir/opt" not in fixed


def test_check_fence_body_copy_detects_pipeline_change():
    ru = "```bash\n/opt/ydb/bin/ydb --ca-file /opt/ydb/certs/ca.crt\n```\n"
    en = "```bash\n/opt/ydb/bin/ydb --ca-file ca.crt\n```\n"
    warnings = check_fence_body_copy(ru, en)
    assert warnings
    assert "fence_body_copy" in warnings[0]


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
    """§6.59: `` ```text `` diagrams keep EN translation, not RU copy."""
    ru = "```text\n├─ попытка: ERROR\n```\n"
    en = "```text\n├─ attempt: ERROR\n```\n"
    out = enforce_source_fenced_blocks(en, ru)
    assert "attempt" in out
    assert "попытка" not in out


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
    ru = (
        "sequenceDiagram\n"
        "    participant Топик\n"
        "    Топик->>Приемник: событие\n"
    )
    en = (
        "sequenceDiagram\n"
        "    participant Topic\n"
        "    Topic->Sink: event\n"
    )
    assert not fence_content_matches_source(ru, en)


def test_finalize_en_after_enforce_fixes_stroka_and_vm_in_indented_fence():
    """Regression: postprocess must run after enforce, not before."""
    raw_ru = (
        "5. Init:\n\n"
        "   ```yaml\n"
        "    - host: static-node-1.ydb-cluster.com #FQDN ВМ\n"
        "   ```\n\n"
        "   ```bash\n"
        "   ydb admin cluster bootstrap --uuid <строка>\n"
        "   ```\n"
    )
    norm = normalize_ru_source_for_translation(raw_ru)
    en_rendered = (
        "5. Init translated.\n\n"
        "   ```yaml\n"
        "    - host: static-node-1.ydb-cluster.com #FQDN ВМ\n"
        "   ```\n\n"
        "   ```bash\n"
        "   ydb admin cluster bootstrap --uuid <строка>\n"
        "   ```\n"
    )
    final = _finalize_en_target(en_rendered, norm)
    assert "#FQDN VM" in final
    assert "ВМ" not in final
    assert "<string>" in final
    assert "<строка>" not in final
