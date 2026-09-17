"""Independent T04 checks derived from REQUIREMENTS_RU sections 1-2."""

# ruff: noqa: RUF001 -- Exact Russian diagnostics from requirements.

import pytest

from ydbdoc_review.config.loader import load_settings
from ydbdoc_review.plan import (
    ChangedFile,
    PlanError,
    PlanLimitError,
    Snapshot,
    prepare_translation_plan,
    raw_source_characters,
)

SHA = "c" * 40


def p(language, name):
    return f"ydb/docs/{language}/core/{name}.md"


def run(files, changes, *, deps=20, chars=250000, calls=None):
    settings = load_settings(
        {
            "YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE": str(deps),
            "YDBDOC_MAX_SOURCE_CHARACTERS": str(chars),
            "YDBDOC_ALLOWED_ACTORS": "tester",
            "YDBDOC_DAILY_BUDGET_RUB": "10",
            "YDB_SA_KEY": "test",
        }
    )

    def read(sha, path):
        assert sha == SHA
        if calls is not None:
            calls.append(path)
        value = files.get(path)
        if isinstance(value, Exception):
            raise value
        return value

    snapshot = Snapshot("o", "r", 2, SHA, SHA, "feature", "o/r")
    return prepare_translation_plan(snapshot, iter(changes), read, settings)


@pytest.mark.parametrize("language,other", [("ru", "en"), ("en", "ru")])
def test_references_table_yfm_diamond_cycle_and_normalized_duplicate(language, other):
    files = {
        p(language, "a"): "[B][] [see][b] [b](./b.md#x)\n\n[B]: b.md?q=1\n\n"
        "| h |\n|---|\n| [c](c.md) |\n",
        p(language, "b"): "{% note info %}\n[d](d.md)\n{% endnote %}",
        p(language, "c"): "[d](d.md) [old](old.md)",
        p(language, "d"): "[a](a.md)",
        p(other, "old"): "",
    }
    calls = []
    plan = run(files, [ChangedFile(p(language, "a"), "modified")], deps=3, calls=calls)
    assert set(plan.dependencies) == {p(language, x) for x in ("b", "c", "d")}
    assert len(plan.operations) == 4
    assert all(calls.count(p(language, x)) == 1 for x in ("a", "b", "c", "d"))
    assert p(language, "old") not in calls
    assert raw_source_characters(plan) == sum(len(files[p(language, x)]) for x in "abcd")


def test_complete_long_closure_combines_directions_and_exact_diagnostics():
    files = {}
    changes = []
    for language, count in [("ru", 24), ("en", 3)]:
        changes.append(ChangedFile(p(language, language + "_root"), "added"))
        files[p(language, language + "_root")] = f"[0]({language}_0.md)"
        for i in range(count):
            files[p(language, f"{language}_{i}")] = (
                f"[{i + 1}]({language}_{i + 1}.md)" if i + 1 < count else "end"
            )
    with pytest.raises(PlanLimitError) as error:
        run(files, changes, deps=20, chars=0)
    plan = error.value.plan
    assert len(plan.dependencies) == 27
    assert set(plan.files) == set(files)
    assert p("ru", "ru_23") in plan.dependencies
    expected = (
        "Перевод не запущен: требуется 27 зависимых статей, лимит — 20 "
        "(`YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE`). Сначала переведите часть "
        "зависимых статей отдельным PR или увеличьте переменную в настройках "
        "Actions репозитория ydb-platform/ydb\n"
        + "\n".join(plan.dependencies)
        + "\n\nПеревод не запущен: объём исходных текстов — "
        + str(sum(map(len, files.values())))
        + " символов, лимит — 0 (`YDBDOC_MAX_SOURCE_CHARACTERS`). Разделите изменения "
        "на несколько PR или увеличьте переменную в настройках Actions репозитория ydb-platform/ydb"
    )
    assert str(error.value) == expected


def test_original_pr_files_and_bilingual_pairs_never_become_extras():
    files = {
        p("ru", "a"): "[b](b.md) [gone](gone.md) [skip](skip.md) [dep](dep.md)",
        p("ru", "b"): "[dep](dep.md)",
        p("ru", "dep"): "[b](b.md) [skip](skip.md)",
        p("en", "x"): "[y](y.md)",
        p("en", "y"): "😀\r\n",
    }
    changes = [
        ChangedFile(p("ru", "a"), "added"),
        ChangedFile(p("ru", "b"), "modified"),
        ChangedFile(p("ru", "gone"), "deleted"),
        ChangedFile(p("ru", "skip"), "modified"),
        ChangedFile(p("en", "skip"), "modified"),
        ChangedFile(p("en", "x"), "modified"),
        ChangedFile(p("en", "y"), "added"),
    ]
    calls = []
    plan = run(files, changes, deps=1, chars=sum(map(len, files.values())), calls=calls)
    assert plan.dependencies == [p("ru", "dep")]
    assert raw_source_characters(plan) == sum(map(len, files.values()))
    assert not any(path in calls for path in [p("ru", "gone"), p("ru", "skip"), p("en", "skip")])


@pytest.mark.parametrize(
    "value,reason", [(None, "отсутствует"), (OSError("read failure"), "read failure")]
)
def test_selected_source_failures_remain_errors_even_with_zero_limits(value, reason):
    with pytest.raises(PlanError) as error:
        run({p("ru", "a"): value}, [ChangedFile(p("ru", "a"), "modified")], deps=0, chars=0)
    assert not isinstance(error.value, PlanLimitError)
    assert p("ru", "a") in str(error.value)
    assert reason in str(error.value)


def test_missing_target_adds_dependency_but_unreadable_target_fails():
    files = {p("ru", "a"): "[b](b.md)", p("ru", "b"): "source"}
    plan = run(files, [ChangedFile(p("ru", "a"), "added")])
    assert plan.dependencies == [p("ru", "b")]
    files[p("en", "b")] = OSError("target IO failure")
    with pytest.raises(PlanError) as error:
        run(files, [ChangedFile(p("ru", "a"), "added")])
    assert p("en", "b") in str(error.value)
    assert "target IO failure" in str(error.value)
