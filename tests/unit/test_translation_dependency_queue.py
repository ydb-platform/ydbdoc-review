from ydbdoc_review.pipeline.dependency_queue import (
    parser_link_edge_walker,
    plan_dependency_queue,
)


def _plan(files, initial, *, en=(), budget=20):
    return plan_dependency_queue(
        initial,
        read_ru=files.__getitem__,
        ru_exists=files.__contains__,
        en_paths=set(en),
        budget=budget,
    )


def test_bfs_deduplicates_fragments_aliases_and_cycles():
    files = {
        "ydb/docs/ru/a.md": "[B1](b.md#one) [B2](./b.md#two)\n",
        "ydb/docs/ru/b.md": "[A](a.md) [C](c.md)\n",
        "ydb/docs/ru/c.md": "[B](b.md)\n",
    }
    plan = _plan(files, ["ydb/docs/ru/a.md"])

    assert [entry.ru_path for entry in plan.entries] == list(files)
    assert [entry.origin for entry in plan.entries] == ["initial", "auto_added", "auto_added"]
    assert plan.auto_added_count == 2
    assert not plan.unresolved


def test_markdown_and_include_share_budget_and_report_twenty_first():
    root = "ydb/docs/ru/root.md"
    files = {root: "\n".join(f"[p{i}](p{i:02}.md)" for i in range(1, 21)) + "\n{% include [last](p21.md) %}\n"}
    files.update({f"ydb/docs/ru/p{i:02}.md": "Текст.\n" for i in range(1, 22)})

    plan = _plan(files, [root], budget=20)

    assert plan.initial_count == 1
    assert plan.auto_added_count == 20
    assert len(plan.entries) == 21
    assert len(plan.unresolved) == 1
    warning = plan.unresolved[0]
    assert warning.reason == "budget_exceeded"
    assert warning.dependency_kind == "include"
    assert warning.original_href == "p21.md"
    assert warning.resolved_en_target == "ydb/docs/en/p21.md"


def test_existing_en_is_not_read_or_queued_and_missing_ru_is_reported():
    files = {
        "ydb/docs/ru/root.md": "[Existing](existing.md) [Missing](missing.md)\n"
    }
    plan = _plan(files, ["ydb/docs/ru/root.md"], en={"ydb/docs/en/existing.md"})

    assert [entry.ru_path for entry in plan.entries] == ["ydb/docs/ru/root.md"]
    assert len(plan.unresolved) == 1
    warning = plan.unresolved[0]
    assert warning.reason == "missing_source"
    assert warning.resolved_ru_target is None
    assert warning.original_href == "missing.md"


def test_dependency_only_in_current_ru_uses_pinned_publication_state():
    root = "ydb/docs/ru/root.md"
    dependency = "ydb/docs/ru/current-only.md"
    plan = _plan(
        {root: "[Current](current-only.md)\n", dependency: "Current only.\n"},
        [root],
        en=(),
    )

    assert [entry.ru_path for entry in plan.entries] == [root, dependency]
    assert plan.auto_added_count == 1
    assert _plan.__kwdefaults__["budget"] == 20


def test_non_markdown_and_external_targets_do_not_consume_budget():
    files = {
        "ydb/docs/ru/root.md": (
            "[local](#part) [web](https://example.com/a.md) "
            "[mail](mailto:x@example.com) ![image](asset.png)\n"
        )
    }
    plan = _plan(files, ["ydb/docs/ru/root.md"])
    assert plan.auto_added_count == 0
    assert not plan.unresolved


def test_parser_edges_keep_every_duplicate_occurrence_with_utf8_coordinates():
    source = "Ё [first](missing.md) and [second](missing.md)\n{% include [third](missing.md) %}\n"
    edges = parser_link_edge_walker("ydb/docs/ru/root.md", source)

    assert [(edge.edge_kind, edge.raw_destination) for edge in edges] == [
        ("include", "missing.md"),
        ("link", "missing.md"),
        ("link", "missing.md"),
    ]
    assert edges[1].source_span.byte_start == len("Ё ".encode())
    assert edges[1].source_span.line == 1
    assert edges[1].source_span.column == 3

    plan = _plan({"ydb/docs/ru/root.md": source}, ["ydb/docs/ru/root.md"])
    assert len(plan.unresolved) == 1
    warning = plan.unresolved[0]
    assert warning.reason == "missing_source"
    assert len(warning.occurrences) == 3
    assert [item.edge_kind for item in warning.occurrences] == ["include", "link", "link"]
