"""Tests for fence parity, link path parity, RU bugs, fence repair, scoped translate."""

from ydbdoc_review.document_segments import parse_document_units
from ydbdoc_review.fence_repair import extract_fence_blocks, repair_fences_from_source
from ydbdoc_review.heuristics import (
    _check_fence_parity,
    _check_markdown_link_path_parity,
)
from ydbdoc_review.markdown_link_paths import (
    extract_relative_link_refs,
    missing_relative_link_details,
)
from ydbdoc_review.ru_source_bugs import (
    detect_ru_source_bugs,
    fix_ru_source_bugs_in_text,
    format_ru_reviewer_suggestions,
)
from ydbdoc_review.translate_scope import (
    compute_translate_scope,
    h3_indices_touched_by_diff,
)


def test_list_tabs_parsed_as_tabs_unit():
    text = (
        "{% list tabs %}\n"
        "- OSS\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "{% endlist %}\n"
    )
    units = parse_document_units(text, doc_label="t.md")
    assert any(u.kind == "tabs" for u in units)
    tabs = [u for u in units if u.kind == "tabs"][0]
    assert "{% list tabs %}" in tabs.text
    assert "```bash" in tabs.text


def test_fence_parity_detects_missing_closer():
    ru = "```yaml\na: 1\n```\n"
    en = "```yaml\na: 1\n"
    f = _check_fence_parity(source=ru, translation=en)
    assert f is not None
    assert f.rule == "fence_parity"
    assert "незакрытый" in f.detail or "нет в EN" in f.detail


def test_fence_parity_lists_missing_block_not_only_count():
    ru = "```yaml\na: 1\n```\n\n```bash\necho\n```\n"
    en = "```yaml\na: 1\n```\n"
    f = _check_fence_parity(source=ru, translation=en)
    assert f is not None
    assert "нет в EN" in f.detail
    assert "```bash" in f.detail


def test_missing_relative_link_details():
    ru = "[A](one.md) and [B](two.md)"
    en = "[A](one.md)"
    missing = missing_relative_link_details(ru, en)
    assert len(missing) == 1
    assert "two.md" in missing[0]


def test_markdown_link_path_parity_lists_missing_href():
    ru = "[A](one.md) and [B](two.md)"
    en = "[A](one.md)"
    f = _check_markdown_link_path_parity(source=ru, translation=en)
    assert f is not None
    assert "нет в EN" in f.detail
    assert "two.md" in f.detail


def test_fence_parity_ok_when_matched():
    ru = "```yaml\na: 1\n```\n"
    en = "```yaml\na: 1\n```\n"
    assert _check_fence_parity(source=ru, translation=en) is None


def test_markdown_link_path_parity_depth():
    ru = "See [req](../../../../devops/concepts/system-requirements.md)."
    en = "See [req](../../../concepts/system-requirements.md)."
    f = _check_markdown_link_path_parity(source=ru, translation=en)
    assert f is not None
    assert "глубина" in f.detail or "SOURCE=" in f.detail


def test_markdown_link_yandex_locale_normalized():
    ru = "[IAM](https://yandex.cloud/ru/docs/iam)"
    en = "[IAM](https://yandex.cloud/en/docs/iam)"
    refs_ru = extract_relative_link_refs(ru)
    refs_en = extract_relative_link_refs(en)
    assert not refs_ru and not refs_en


def test_ru_config_dir_bug_detect_and_fix():
    ru = "sudo ydb admin node config init --config-dir/opt/ydb/cfg\n"
    bugs = detect_ru_source_bugs(ru, file_path="x.md")
    assert len(bugs) == 1
    fixed, found = fix_ru_source_bugs_in_text(ru, file_path="x.md")
    assert found
    assert "--config-dir /opt/ydb/cfg" in fixed


def test_format_ru_reviewer_suggestions():
    from ydbdoc_review.ru_source_bugs import RuSourceBug

    md = format_ru_reviewer_suggestions(
        [
            (
                "a.md",
                [
                    RuSourceBug(
                        kind="config_dir_spacing",
                        location="a.md",
                        detail="опечатка",
                        suggested_fix="--config-dir /x",
                    )
                ],
            )
        ]
    )
    assert "Предложения ревьюеру" in md
    assert "a.md" in md


def test_repair_fences_from_source():
    ru = "Before\n```yaml\na: 1\n```\nAfter\n"
    en = "Before\n```yaml\na: 1\nAfter\n"
    out, applied = repair_fences_from_source(ru, en)
    assert applied
    assert "```yaml" in out
    assert out.count("```") == 2


def test_scoped_translate_small_diff():
    padding = "\n".join(f"pad{i}" for i in range(20))
    ru = f"### A\nline1\n\n### B\nline2\n{padding}\n"
    en = f"### A\nline1en\n\n### B\nline2en\n{padding}\n"
    diff = "@@ -2,1 +2,1 @@\n-line1\n+line1changed\n"
    touched = h3_indices_touched_by_diff(diff, ru)
    assert 1 in touched
    scope = compute_translate_scope(
        ru_text=ru, en_on_main=en, ru_pr_diff=diff
    )
    assert scope.mode == "sections"
    assert 1 in scope.changed_h3
