"""F-035 contracts for transitive locale-specific Markdown includes."""

from __future__ import annotations

from ydbdoc_review.navigation.scope_planner import plan_translation_scope
from ydbdoc_review.parsing.include_paths import collect_yfm_includes


def test_F035_chain() -> None:
    root = "ydb/docs/ru/core/topic.md"
    child = "ydb/docs/ru/core/_includes/child.md"
    grandchild = "ydb/docs/ru/core/_includes/grandchild.md"
    files = {
        root: (
            "Before\n"
            "{% include [child](_includes/child.md) %}\n"
            "{% include [child again](_includes/child.md) %}\n"
            "After\n"
        ),
        child: "{% include [grandchild](grandchild.md) %}\n",
        grandchild: "",
    }

    plan = plan_translation_scope(
        [(root, "modified")],
        read_ru=files.get,
        read_en_base=lambda _path: None,
    )

    assert plan.doc_ru_paths == frozenset({root, child, grandchild})
    assert plan.doc_from_diff == frozenset({root})
    assert plan.doc_from_main == frozenset({child, grandchild})


def test_F035_empty_changed() -> None:
    root = "ydb/docs/ru/core/topic.md"
    empty = "ydb/docs/ru/core/_includes/empty.md"
    changed = "ydb/docs/ru/core/_includes/changed.md"
    root_text = (
        "Before\n"
        "{% include [empty](_includes/empty.md) %}\n"
        "{% include [changed](_includes/changed.md) %}\n"
        "After\n"
    )
    files = {root: root_text, empty: "", changed: "Updated\n"}
    changes = [(root, "modified"), (changed, "modified")]

    plan = plan_translation_scope(
        changes,
        read_ru=files.get,
        read_en_base=lambda _path: None,
    )

    assert empty in plan.doc_ru_paths
    assert empty in plan.doc_from_main
    assert changed in plan.doc_from_diff
    assert [include.path for include in collect_yfm_includes(root_text)] == [
        "_includes/empty.md",
        "_includes/changed.md",
    ]
