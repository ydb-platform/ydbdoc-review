"""Full closure and variable-based limits before runner integration (T04)."""

from dataclasses import replace

import pytest

from ydbdoc_review.config.loader import load_settings
from ydbdoc_review.plan import (
    ChangedFile,
    PlanError,
    PlanLimitError,
    Snapshot,
    build_plan,
    discover_dependencies,
    enforce_limits,
    markdown_dependencies,
    prepare_translation_plan,
    raw_source_characters,
    translation_sources,
)

SHA = "b" * 40


def snapshot():
    return Snapshot(
        owner="o",
        repo="r",
        pr_number=1,
        source_sha=SHA,
        head_sha=SHA,
        publication_base="feature",
        source_repo="o/r",
    )


def settings(deps=20, chars=250000):
    return load_settings(
        {
            "YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE": str(deps),
            "YDBDOC_MAX_SOURCE_CHARACTERS": str(chars),
            "YDBDOC_ALLOWED_ACTORS": "actor",
            "YDBDOC_DAILY_BUDGET_RUB": "10",
            "YDB_SA_KEY": "test",
        }
    )


def page(lang, name):
    return f"ydb/docs/{lang}/core/{name}.md"


def reader(files, calls=None):
    def read(sha, path):
        assert sha == SHA
        if calls is not None:
            calls.append(path)
        return files.get(path)

    return read


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_chain_cycle_diamond_and_pr_sources_not_extras(lang):
    files = {
        page(lang, "a"): "[b](b.md) [c](c.md)",
        page(lang, "b"): "[d](d.md)",
        page(lang, "c"): "[d](d.md)",
        page(lang, "d"): "[a](a.md)",
    }
    plan = prepare_translation_plan(
        snapshot(),
        [ChangedFile(page(lang, "a"), "added"), ChangedFile(page(lang, "c"), "modified")],
        reader(files),
        settings(2),
    )
    assert set(plan.dependencies) == {page(lang, "b"), page(lang, "d")}
    assert plan.files == files
    assert len(plan.operations) == 4
    assert raw_source_characters(plan) == sum(map(len, files.values()))
    assert discover_dependencies(plan, reader(files)) == plan


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_global_limit_both_languages_and_complete_discovery(limit):
    files = {
        page("ru", "a"): "[b](b.md)",
        page("ru", "b"): "[c](c.md)",
        page("ru", "c"): "C",
        page("en", "x"): "[y](y.md)",
        page("en", "y"): "Y",
    }
    changes = [ChangedFile(page("ru", "a"), "modified"), ChangedFile(page("en", "x"), "added")]
    if limit == 3:
        plan = prepare_translation_plan(snapshot(), changes, reader(files), settings(limit))
    else:
        with pytest.raises(PlanLimitError) as error:
            prepare_translation_plan(snapshot(), changes, reader(files), settings(limit))
        plan = error.value.plan
        assert str(error.value).startswith(
            f"Перевод не запущен: требуется 3 зависимых статей, лимит — {limit} "
            "(`YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE`). Сначала переведите часть "
            "зависимых статей отдельным PR или увеличьте переменную в настройках "
            "Actions репозитория ydb-platform/ydb\n"
        )
        assert all(path in str(error.value) for path in plan.dependencies)
    assert plan.files == files
    assert len(plan.dependencies) == 3


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_existing_empty_target_and_skipped_bilingual_pairs(lang):
    other = "en" if lang == "ru" else "ru"
    files = {page(lang, "a"): "[b](b.md) [skip](skip.md)", page(other, "b"): ""}
    changes = [ChangedFile(page(lang, "a"), "added")]
    changes += [ChangedFile(page(language, "skip"), "modified") for language in (lang, other)]
    calls = []
    plan = prepare_translation_plan(snapshot(), changes, reader(files, calls), settings(0))
    assert not plan.dependencies
    assert plan.files == {page(lang, "a"): files[page(lang, "a")]}
    assert calls == [page(lang, "a"), page(other, "b")]


@pytest.mark.parametrize("count", [249999, 250000, 250001])
def test_exact_raw_character_boundary_including_dependency(count):
    source = "[b](b.md)\r\n```\r\n raw защищённый 😀 \r\n```\r\n"
    files = {page("ru", "a"): source, page("ru", "b"): "я" * (count - len(source))}
    plan = discover_dependencies(
        build_plan(snapshot(), [ChangedFile(page("ru", "a"), "added")], reader(files)),
        reader(files),
    )
    assert raw_source_characters(plan) == count
    if count <= 250000:
        enforce_limits(plan, settings())
    else:
        with pytest.raises(PlanLimitError) as error:
            enforce_limits(plan, settings())
        assert str(error.value) == (
            "Перевод не запущен: объём исходных текстов — 250001 символов, лимит — 250000 "
            "(`YDBDOC_MAX_SOURCE_CHARACTERS`). Разделите изменения на несколько PR или "
            "увеличьте переменную в настройках Actions репозитория ydb-platform/ydb"
        )
    enforce_limits(plan, settings(chars=count))


@pytest.mark.parametrize("failure", [None, OSError("permission denied")])
def test_dependency_read_failure_has_path_reason_and_no_partial_result(failure):
    source = page("en", "a")

    def read(sha, path):
        assert sha == SHA
        if path == source:
            return "[b](b.md)"
        if path == page("en", "b") and failure is not None:
            raise failure
        return None

    with pytest.raises(PlanError, match=r"en/core/b.md.*(отсутствует|permission denied)"):
        prepare_translation_plan(snapshot(), [ChangedFile(source, "added")], read, settings())


def test_translation_total_excludes_mechanical_and_skipped_sources():
    files = {page("en", "new"): "[missing](missing.md)" * 100}
    changes = [
        ChangedFile(page("en", "new"), "renamed", page("en", "old"), content_changed=False),
        ChangedFile(page("ru", "gone"), "deleted"),
    ]
    calls = []
    plan = prepare_translation_plan(snapshot(), changes, reader(files, calls), settings(0, 0))
    assert raw_source_characters(plan) == 0
    assert translation_sources(plan) == {}
    assert not plan.needs_model and not plan.no_work
    assert calls == [page("en", "new")]


def test_parser_links_references_titles_containers_and_exclusions():
    text = """[ref][r] [nested](a(b).md "title") [{#T}](auto.md)

[r]: refs.md?x=1#anchor

{% note info %}
[note](note.md)
{% endnote %}

`[code](code.md)` ![image](image.md)

```md
[code](fence.md)
```

<!-- [hidden](comment.md) -->
[external](https://host/a.md) [cross](../../en/core/no.md)
[escape](../../../../evil.md) [anchor](#here)
[abs](/ru/core/abs.md) [public](/pub.md)
[encoded](with%20space.md)
"""
    assert set(markdown_dependencies(page("ru", "a"), text)) == {
        page("ru", name) for name in ["refs", "a(b)", "auto", "note", "abs", "with space"]
    }


def test_limits_refusal_retains_both_totals_and_original_plan_unchanged():
    files = {page("ru", "a"): "[b](b.md)", page("ru", "b"): "B"}
    original = build_plan(snapshot(), [ChangedFile(page("ru", "a"), "added")], reader(files))
    plan = discover_dependencies(original, reader(files))
    assert original.dependencies == [] and len(original.files) == 1
    with pytest.raises(PlanLimitError) as error:
        enforce_limits(plan, settings(0, 0))
    assert "YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE" in str(error.value)
    assert "YDBDOC_MAX_SOURCE_CHARACTERS" in str(error.value)
    assert error.value.plan is plan


def test_dependency_snapshot_is_immutable_even_after_branch_moves(git_repo):
    from functools import partial

    from ydbdoc_review.plan import read_at_sha

    repo, git = git_repo
    source, dependency = page("ru", "a"), page("ru", "b")
    (repo / source).parent.mkdir(parents=True)
    (repo / source).write_bytes(b"[b](b.md)\r\n")
    (repo / dependency).write_bytes(b" exact\r\n")
    git("add", ".")
    git("commit", "-m", "frozen")
    sha = git("rev-parse", "HEAD").decode().strip()
    frozen = snapshot()
    frozen = replace(frozen, source_sha=sha)
    (repo / dependency).write_text("later")
    target = repo / page("en", "b")
    target.parent.mkdir(parents=True)
    target.write_text("new target must not hide dependency in frozen tree")
    git("add", ".")
    git("commit", "-m", "later")
    plan = prepare_translation_plan(
        frozen, [ChangedFile(source, "added")], partial(read_at_sha, str(repo)), settings()
    )
    assert plan.dependencies == [dependency]
    assert plan.files[dependency] == " exact\r\n"
    assert raw_source_characters(plan) == len("[b](b.md)\r\n exact\r\n")


def test_unreadable_target_is_not_treated_as_absent():
    source = page("ru", "a")

    def read(sha, path):
        if path == source:
            return "[b](b.md)"
        raise OSError("cannot inspect target")

    with pytest.raises(PlanError, match=r"en/core/b.md.*cannot inspect target"):
        prepare_translation_plan(snapshot(), [ChangedFile(source, "added")], read, settings())
