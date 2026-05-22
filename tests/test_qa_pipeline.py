from ydbdoc_review.translation_qa import (
    critic_needs_structure_rebuild,
    review_needs_repair,
)


def test_critic_needs_structure_rebuild_from_scope():
    md = "### Scope\n\n**Полный resync с RU** — структура нарушена.\n"
    assert critic_needs_structure_rebuild(md)


def test_critic_needs_structure_rebuild_from_blockers():
    md = (
        "### Блокеры (для исправителя)\n"
        "1. Удалить дублированный блок в начале документа.\n"
        "### Scope\n"
        "Исправить структуру файла.\n"
    )
    assert critic_needs_structure_rebuild(md)


def test_review_needs_repair_without_structure():
    md = "### Найдено критиком\n\n- Опечатка в слове query.\n"
    assert review_needs_repair(md)
    assert not critic_needs_structure_rebuild(md)


def test_cyrillic_scope_does_not_force_structure_rebuild():
    md = (
        "### Найдено критиком\n"
        "Проблема: кириллица в SQL-комментариях.\n"
        "### Блокеры\n"
        "1. Перевести комментарии.\n"
        "### Scope\n"
        "**Полный resync с RU** — устранить кириллицу.\n"
    )
    assert not critic_needs_structure_rebuild(md)
