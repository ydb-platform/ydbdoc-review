"""Tests for line-aligned markdown link repair from RU source."""

from ydbdoc_review.heuristics import _check_diplodoc_t_link_drift
from ydbdoc_review.markdown_links import (
    DIPLODOC_T_MACRO,
    fix_broken_fence_lines_from_ru,
    fix_glued_extraneous_t_macro_from_ru,
    repair_markdown_links_from_ru,
    restore_markdown_links_from_ru,
    strip_duplicate_cyrillic_links,
)


def test_repair_broken_topology_line_from_pr41505():
    ru = (
        "Подготовьте конфигурационный файл {{ ydb-short-name }} в зависимости от "
        "выбранной вами топологии (см. [выбор топологии]"
        "(../../../deployment-options/ansible/initial-deployment/"
        "deployment-preparation.md#topology-select))."
    )
    en_broken = (
        "Prepare the {{ ydb-short-name }} configuration file depending on your topology "
        "(see [{#T}][(][(](deployment-preparation.md)#requirements)"
        "#tls-certificates)#topology-select))."
    )
    out = repair_markdown_links_from_ru(ru, en_broken)
    assert DIPLODOC_T_MACRO not in out or "[{#T}]" not in out.split("topology")[0]
    assert "topology-select)" in out
    assert "][(" not in out
    assert "выбор топологии" not in out


def test_preserve_diplodoc_t_when_ru_has_it():
    ru = "См. [{#T}](deployment-preparation.md#tls-certificates)."
    en_wrong = "See [TLS preparation](deployment-preparation.md#tls-certificates)."
    out = repair_markdown_links_from_ru(ru, en_wrong)
    assert "[{#T}](deployment-preparation.md#tls-certificates)" in out


def test_diplodoc_t_heuristic_flags_invented_t():
    ru = "См. [выбор топологии](../deployment-preparation.md#topology-select)."
    en = "See [{#T}](deployment-preparation.md#topology-select)."
    f = _check_diplodoc_t_link_drift(source=ru, translation=en)
    assert f is not None
    assert f.rule == "diplodoc_t_link_drift"


def test_repair_en_line_that_lost_leading_prose():
    """Do not paste Cyrillic prefix from RU when EN line is only a broken link."""
    ru = (
        "Подготовьте файл (см. [выбор топологии](../deployment-preparation.md#topology-select)). "
        "Примеры ниже."
    )
    en = (
        "[{#T}][(][(](../deployment-preparation.md#topology-select)). "
        "Examples below."
    )
    out = repair_markdown_links_from_ru(ru, en)
    assert "Подготовьте" not in out
    assert "[topology selection](../deployment-preparation.md#topology-select)" in out
    assert "Examples below" in out


def test_strip_duplicate_cyrillic_link_on_tab_line():
    ru = (
        "- С использованием systemd\n\n"
        "  Образец можно [скачать из репозитория](https://example.com/svc).\n"
    )
    en = (
        "- Using systemd[скачать из репозитория](https://example.com/svc)\n\n"
        "  You can [download from the repository](https://example.com/svc).\n"
    )
    out = strip_duplicate_cyrillic_links(en, ru)
    assert "скачать" not in out
    assert "Using systemd\n" in out or "Using systemd\n\n" in out
    assert "download from the repository" in out


def test_fix_glued_extraneous_t_macro():
    ru = (
        "Сохраните файл на сервере.\n\n"
        "Подробнее в разделе [документации](../../../reference/configuration/index.md).\n"
    )
    en = (
        "Save the file on the server.[{#T}](../../../reference/configuration/index.md)\n\n"
        "More details in [configuration reference](../../../reference/configuration/index.md).\n"
    )
    out = fix_glued_extraneous_t_macro_from_ru(ru, en)
    assert "server.[{#T}]" not in out
    assert "configuration reference" in out


def test_fix_broken_fence_line_with_glued_link():
    ru = "```\n\nInstead of the value"
    en = "```[документации CLI](../../../reference/ydb-cli/profile/index.md)\n\nInstead of the value"
    out = fix_broken_fence_lines_from_ru(ru, en)
    assert out.splitlines()[0] == "```"


def test_restore_calls_repair_first():
    ru = "Link [выбор топологии](../x.md#topology-select) here."
    en = "Link [{#T}][(][(](../x.md#topology-select)) here."
    out = restore_markdown_links_from_ru(ru, en)
    assert "][(" not in out
