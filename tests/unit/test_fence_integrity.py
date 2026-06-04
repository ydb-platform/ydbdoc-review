"""Fence copy guarantees: code blocks must not be altered by translation."""

from __future__ import annotations

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
