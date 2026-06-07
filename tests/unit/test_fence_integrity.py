"""Fence copy guarantees: code blocks must not be altered by translation."""

from __future__ import annotations

from ydbdoc_review.pipeline.translate_file import _finalize_en_target
from ydbdoc_review.validation.fence_integrity import (
    check_fence_body_copy,
    enforce_source_fenced_blocks,
    fence_content_matches_source,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


def test_fence_content_allows_angle_placeholder_only():
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
