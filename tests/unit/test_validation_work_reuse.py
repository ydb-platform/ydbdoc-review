"""Optimizations preserve parsed semantics and worktree freshness."""
from types import SimpleNamespace

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_headings, parse_markdown
from ydbdoc_review.validation.en_link_targets import _duplicate_explicit_fragments
from ydbdoc_review.validation.href_parity import check_inbound_fragments
from ydbdoc_review.validation.toc_targets import _collect_reachable_include_dependencies
from ydbdoc_review.validation.yfm_anchor import _iter_headings


@pytest.mark.parametrize("text", [
    "# [Link](https://{{ host }}/path) {#link}\n",
    "# A &amp; B {#x}\n\n## C {#&#120;}\n",
    "# A \\* B {#x}\n\nSetext title\n===\n",
    "> ## Quoted {#q}\n\n- ### Listed {#l}\n",
    "{% note info %}\n\n## Nested &amp; escaped\n\n{% endnote %}\n",
    "{% list tabs %}\n\n- First\n\n  ### Tab {#tab}\n\n{% endlist %}\n",
    "```md\n# Fake {#fake}\n```\n\n# Real {#real}\n",
    "{% if audience == 'admin' %}\n\n# Conditional {#c}\n\n{% endif %}\n",
])
def test_heading_only_parse_matches_full_grammar(text):
    assert parse_headings(text) == list(_iter_headings(parse_markdown(text).children))


def test_duplicate_anchor_entities_are_not_lost():
    assert _duplicate_explicit_fragments("# A {#x}\n\n# B {#&#120;}\n") == ["x"]


def test_preserved_inbound_anchors_need_no_external_reads():
    def unexpected(_path):
        pytest.fail("An unchanged anchor cannot produce an inbound finding")
    assert check_inbound_fragments(
        "ydb/docs/en/a.md", "# A {#kept}", ru_text="# Пример {#kept}",
        en_baseline_text="# Old {#kept}", read_text=unexpected,
        en_paths=["ydb/docs/en/b.md"],
    ) == []


def test_required_include_search_still_reports_unreachable_targets():
    root = "ydb/docs/en/core/"
    files = {
        root + "a.md": "```md\n{% include [fake](missing.md) %}\n```\n\n{% include [real](included.md) %}\n",
        root + "included.md": "Included.\n",
        root + "missing.md": "This exists but is not included.\n",
    }
    reached = _collect_reachable_include_dependencies(
        files.get, toc_reachable={root + "a.md"}, docs_root="ydb/docs", locale="en",
        required_paths={root + "missing.md"},
    )
    assert root + "included.md" in reached
    assert root + "missing.md" not in reached


def test_informational_critic_findings_do_not_retranslate(monkeypatch):
    from ydbdoc_review.pipeline import candidate_repair
    candidate = object()
    plan = SimpleNamespace(complete=True, candidate=candidate, units=[SimpleNamespace(id="one")])
    response = SimpleNamespace(_review_incomplete=False, issues=[SimpleNamespace(segment_id="one", severity="info")])
    fr = SimpleNamespace(final_review_plan=plan, final_review_response=response)
    run = SimpleNamespace(file_result=fr, deleted=False, skipped=False, error=None, source_text="Source")
    def unexpected(*args, **kwargs):
        pytest.fail("Informational findings must not invoke a repair")
    monkeypatch.setattr(candidate_repair, "read_candidate_bytes", unexpected)
    monkeypatch.setattr(candidate_repair, "translate_file", unexpected)
    assert candidate_repair.repair_candidate_files(
        "unused", candidate, SimpleNamespace(pair_results=[run]), None, None, None,
    ) == []
