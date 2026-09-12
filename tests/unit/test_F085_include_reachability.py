from __future__ import annotations

from pathlib import Path

from ydbdoc_review.validation.toc_targets import check_orphan_translated_pages


def _write(repo: Path, rel_path: str, text: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_F085_nested_empty(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Start\n  href: start.md\n",
    )
    _write(
        tmp_path,
        "ydb/docs/en/core/start.md",
        "{% include [fragment](fragment.md) %}\n",
    )
    _write(
        tmp_path,
        "ydb/docs/en/core/fragment.md",
        "{% include [empty](empty.md) %}\n",
    )
    _write(tmp_path, "ydb/docs/en/core/empty.md", "")

    assert check_orphan_translated_pages(
        {
            "ydb/docs/en/core/start.md",
            "ydb/docs/en/core/fragment.md",
            "ydb/docs/en/core/empty.md",
        },
        repo_path=str(tmp_path),
    ) == {}


def test_F085_false_edges(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Start\n  href: start.md\n",
    )
    _write(tmp_path, "ydb/docs/en/core/start.md", "Start\n")
    _write(
        tmp_path,
        "ydb/docs/en/core/orphan-parent.md",
        "{% include [child](orphan-child.md) %}\n",
    )
    _write(tmp_path, "ydb/docs/en/core/orphan-child.md", "child\n")
    _write(
        tmp_path,
        "ydb/docs/en/core/markdown-link.md",
        "[child](linked-child.md)\n",
    )
    _write(tmp_path, "ydb/docs/en/core/linked-child.md", "child\n")
    _write(
        tmp_path,
        "ydb/docs/en/core/code-comment-frontmatter.md",
        "---\ninclude: [frontmatter-child.md]\n---\n"
        "<!-- {% include [comment](comment-child.md) %} -->\n"
        "```md\n{% include [code](code-child.md) %}\n```\n",
    )
    _write(tmp_path, "ydb/docs/en/core/frontmatter-child.md", "child\n")
    _write(tmp_path, "ydb/docs/en/core/comment-child.md", "child\n")
    _write(tmp_path, "ydb/docs/en/core/code-child.md", "child\n")

    orphans = check_orphan_translated_pages(
        {
            "ydb/docs/en/core/start.md",
            "ydb/docs/en/core/orphan-parent.md",
            "ydb/docs/en/core/orphan-child.md",
            "ydb/docs/en/core/markdown-link.md",
            "ydb/docs/en/core/linked-child.md",
            "ydb/docs/en/core/code-comment-frontmatter.md",
            "ydb/docs/en/core/frontmatter-child.md",
            "ydb/docs/en/core/comment-child.md",
            "ydb/docs/en/core/code-child.md",
        },
        repo_path=str(tmp_path),
    )

    assert set(orphans) == {
        "ydb/docs/en/core/orphan-parent.md",
        "ydb/docs/en/core/orphan-child.md",
        "ydb/docs/en/core/markdown-link.md",
        "ydb/docs/en/core/linked-child.md",
        "ydb/docs/en/core/code-comment-frontmatter.md",
        "ydb/docs/en/core/frontmatter-child.md",
        "ydb/docs/en/core/comment-child.md",
        "ydb/docs/en/core/code-child.md",
    }
