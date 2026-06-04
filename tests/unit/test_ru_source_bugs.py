"""RU source bug detection and pre-translate normalization."""

from __future__ import annotations

from ydbdoc_review.validation.ru_source_bugs import (
    check_required_anchor_lines,
    detect_ru_source_bugs,
    normalize_ru_source_for_translation,
)


def test_detect_config_dir_glued():
    text = "sudo ydb admin node config init --config-dir/opt/ydb/cfg\n"
    issues = detect_ru_source_bugs(text)
    assert any("config-dir" in i for i in issues)


def test_normalize_fixes_config_dir():
    text = "init --config-dir/opt/ydb/cfg\n"
    assert "--config-dir /opt" in normalize_ru_source_for_translation(text)


def test_missing_web_pem_anchor():
    ru = "sudo -u ydb test -r /opt/ydb/certs/web.pem\n"
    en = "sudo cp web.pem\n"
    warnings = check_required_anchor_lines(ru, en)
    assert any("missing_anchor" in w for w in warnings)
