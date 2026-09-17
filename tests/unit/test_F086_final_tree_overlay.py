from __future__ import annotations

from ydbdoc_review.validation.glossary_toc_links import collect_en_toc_reachable_md
from ydbdoc_review.validation.toc_targets import check_orphan_pages_for_locale


def test_F086_empty_deleted(monkeypatch):
    root = "ydb/docs/en/core/toc_p.yaml"
    page = "ydb/docs/en/core/section/page.md"
    old_page = "old text"

    def read_baseline(_repo_path: str, _ref: str, path: str) -> str | None:
        assert path == page
        return old_page

    monkeypatch.setattr(
        "ydbdoc_review.github.git_ops.read_text_at_upstream_tip",
        read_baseline,
    )

    pending_toc = """\
items:
- name: Page
  href: section/page.md
"""
    assert check_orphan_pages_for_locale(
        {page},
        repo_path="unused",
        pending_toc_texts={root: pending_toc},
        pending_md_texts={page: ""},
        baseline_ref="base",
    ) == {}
    assert check_orphan_pages_for_locale(
        {page},
        repo_path="unused",
        pending_toc_texts={root: pending_toc},
        unavailable_md_paths={page},
        baseline_ref="base",
    ) == {page: [
        "orphan_toc_page: translated EN page `ydb/docs/en/core/section/page.md` "
        "is not linked from any EN toc (reachable from `ydb/docs/en/core/toc_p.yaml` "
        "via href/include.path)"
    ]}


def test_F086_cycle_locale():
    root = "ydb/docs/en/core/toc_p.yaml"
    child = "ydb/docs/en/core/section/toc_i.yaml"
    ru_page = "ydb/docs/ru/core/section/page.md"
    files = {
        root: """\
items:
- include: {path: section/toc_i.yaml}
""",
        child: """\
items:
- name: Foreign
  href: ../../../ru/core/section/page.md
- include: {path: ../toc_p.yaml}
""",
        ru_page: "RU\n",
    }

    reachable = collect_en_toc_reachable_md(
        files.get,
        root_toc=root,
        extra_toc_paths={child},
    )

    assert ru_page not in reachable
