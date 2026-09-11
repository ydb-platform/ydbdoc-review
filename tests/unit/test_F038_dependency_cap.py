from ydbdoc_review.navigation.dependency_budget import MarkdownDependencyBudget


def test_F038_shared_cap() -> None:
    budget = MarkdownDependencyBudget(roots={"docs/ru/source.md"})
    candidates = [
        ("link", f"docs/ru/dependency-{index:02d}.md")
        for index in range(21)
    ]
    candidates += [
        ("include", "docs/ru/dependency-05.md"),
        ("fragment-owner", "docs/ru/dependency-05.md"),
    ]

    admitted = [
        path
        for path in sorted({path for _, path in candidates})
        if budget.admit(path)
    ]

    assert admitted == [f"docs/ru/dependency-{index:02d}.md" for index in range(20)]
    assert budget.admit("docs/ru/dependency-20.md") is False
    assert len(budget.admitted_ru_paths) == 20


def test_F038_exempt_order() -> None:
    roots = {
        "docs/ru/source.md",
        "docs/ru/navigation-only.md",
        "docs/ru/binary-copy.md",
        "docs/ru/anchor-mechanical.md",
    }
    candidates = [
        "docs/ru/dependency-b.md",
        "docs/ru/dependency-a.md",
        "docs/ru/dependency-b.md",
    ]

    first = MarkdownDependencyBudget(roots=roots)
    first_order = [path for path in sorted(set(candidates)) if first.admit(path)]
    second = MarkdownDependencyBudget(roots=roots)
    second_order = [path for path in sorted(set(candidates)) if second.admit(path)]

    assert first_order == second_order == [
        "docs/ru/dependency-a.md",
        "docs/ru/dependency-b.md",
    ]
    assert all(first.admit(path) for path in roots)
    assert len(first.admitted_ru_paths) == 2
