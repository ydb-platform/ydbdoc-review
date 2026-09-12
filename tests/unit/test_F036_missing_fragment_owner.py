"""F-036 contracts for missing target fragment ownership."""

from __future__ import annotations

import pytest

from ydbdoc_review.navigation.scope_planner import plan_translation_scope

ROOT = "ydb/docs"
PAGE = f"{ROOT}/ru/core/page.md"
INCLUDE = f"{ROOT}/ru/core/_includes/details.md"
OWNER = f"{ROOT}/ru/core/reference.md"
OWNER_EN = OWNER.replace("/ru/", "/en/")


def _plan(source: str, *, source_kind: str, ru: dict[str, str], en: dict[str, str]):
    return plan_translation_scope(
        [(source, source_kind)],
        read_ru=ru.get,
        read_en_base=en.get,
    )


@pytest.mark.parametrize(
    ("source", "source_kind", "ru_source", "expected"),
    [
        (
            PAGE,
            "added",
            "See [details](reference.md#details).\n",
            OWNER,
        ),
        (
            PAGE,
            "modified",
            "See [details](reference.md#details).\n",
            OWNER,
        ),
        (
            PAGE,
            "added",
            "{% include [details](_includes/details.md) %}\n",
            OWNER,
        ),
        (
            PAGE,
            "modified",
            "{% include [details](_includes/details.md) %}\n",
            OWNER,
        ),
    ],
)
def test_F036_owner(source: str, source_kind: str, ru_source: str, expected: str) -> None:
    ru = {
        source: ru_source,
        INCLUDE: "See [details](../reference.md#details).\n",
        OWNER: "## Details {#details}\n",
    }
    en = {
        OWNER_EN: "## Reference\n",
    }
    if "include" in ru_source:
        if source_kind == "added":
            en.pop(INCLUDE.replace("/ru/", "/en/"), None)
        else:
            en[INCLUDE.replace("/ru/", "/en/")] = ru[INCLUDE]

    plan = _plan(source, source_kind=source_kind, ru=ru, en=en)

    assert expected in plan.doc_from_main


def test_F036_missing_source_fragment() -> None:
    ru = {
        PAGE: "See [details](reference.md#missing).\n",
        OWNER: "## Details {#details}\n",
    }

    plan = _plan(PAGE, source_kind="added", ru=ru, en={OWNER_EN: "## Reference\n"})

    assert OWNER not in plan.doc_from_main
    assert all("details" not in path for path in plan.doc_from_main)
