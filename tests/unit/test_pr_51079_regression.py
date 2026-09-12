"""Regression for translation obligations discovered while translating PR #51079."""

# ruff: noqa: RUF001

from __future__ import annotations

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.github.workflow import _attach_source_coverage_plans
from ydbdoc_review.navigation.scope_planner import (
    doc_pairs_from_plan,
    plan_translation_scope,
)
from ydbdoc_review.pipeline.analyze import PairContent, plan_pairs

AUTH_CONFIG_RU = "ydb/docs/ru/core/reference/configuration/auth_config.md"
AUTHENTICATION_RU = "ydb/docs/ru/core/security/authentication.md"
GLOSSARY_RU = "ydb/docs/ru/core/concepts/glossary.md"
CACHING_RU = "ydb/docs/ru/core/security/caching-authentication-results.md"
USER_TOKEN_RU = "ydb/docs/ru/core/security/_assets/user-token.md"
LIFECYCLE_RU = "ydb/docs/ru/core/security/_assets/user-token-lifecycle.md"

SOURCE_CHANGES = [
    (AUTH_CONFIG_RU, "modified"),
    (AUTHENTICATION_RU, "modified"),
]
DEPENDENCIES = frozenset({GLOSSARY_RU, CACHING_RU, USER_TOKEN_RU, LIFECYCLE_RU})


def _en(path: str) -> str:
    return path.replace("/ru/", "/en/")


def _scope_fixture():
    ru = {
        AUTH_CONFIG_RU: (
            "# Auth config\n\n"
            "[Токен](../../concepts/glossary.md#user-token). "
            "[{#T}](../../security/caching-authentication-results.md).\n"
        ),
        AUTHENTICATION_RU: (
            "# Аутентификация\n\n"
            "[Кеширование](./caching-authentication-results.md).\n"
        ),
        GLOSSARY_RU: (
            "# Глоссарий\n\n"
            "## Токен пользователя {#user-token}\n\nОпределение токена.\n"
        ),
        CACHING_RU: (
            "# Кеширование результатов аутентификации\n\n"
            "{% include [Токен](_assets/user-token.md) %}\n\n"
            "{% include [Жизненный цикл](_assets/user-token-lifecycle.md) %}\n"
        ),
        USER_TOKEN_RU: "```mermaid\ngraph LR\n  Auth --> Token\n```\n",
        LIFECYCLE_RU: "```mermaid\ngraph LR\n  Token --> Refresh\n```\n",
    }
    en = {
        _en(AUTH_CONFIG_RU): "# Auth config\n",
        _en(AUTHENTICATION_RU): "# Authentication\n",
        _en(GLOSSARY_RU): "# Glossary\n",
    }
    plan = plan_translation_scope(
        SOURCE_CHANGES,
        read_ru=ru.get,
        read_en_base=en.get,
        read_ru_base=lambda _path: None,
    )
    return ru, en, plan


def _authority() -> RuAuthority:
    return RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=51079,
        source_base_sha="0aa50f3ab4688eb53d04888eba9fb3c36968ff14",
        source_head_sha="673b924813e35646535e20d917b014094bf7de14",
        baseline_sha="7886ff84e31c2f52c99adf8fbeb908fda38e4192",
        ru_sha="7886ff84e31c2f52c99adf8fbeb908fda38e4192",
        mode=RuAuthorityMode.CURRENT,
    )


def test_pr_51079_main_dependencies_are_translation_obligations_with_false_provenance():
    ru, en, scope = _scope_fixture()

    assert scope.doc_from_diff == frozenset({AUTH_CONFIG_RU, AUTHENTICATION_RU})
    assert scope.doc_from_main == DEPENDENCIES
    assert scope.required_fragments_for(GLOSSARY_RU) == frozenset({"user-token"})

    pairs = doc_pairs_from_plan(scope, changes=SOURCE_CHANGES)
    dependencies = [pair for pair in pairs if pair.ru_path in DEPENDENCIES]
    assert len(dependencies) == 4
    assert all(pair.translation_required for pair in dependencies)
    assert all(not pair.ru_changed and not pair.en_changed for pair in dependencies)

    contents = [
        PairContent(pair=pair, ru_text=ru.get(pair.ru_path), en_text=en.get(pair.en_path))
        for pair in pairs
    ]
    planned_contents = _attach_source_coverage_plans(
        contents,
        scope_plan=scope,
        authority=_authority(),
        checkpoint=None,
        resume_parent_run_id=None,
    )
    planned_dependencies = [
        content for content in planned_contents if content.pair.ru_path in DEPENDENCIES
    ]
    assert all(content.pair.translation_required for content in planned_dependencies)
    glossary = next(
        content for content in planned_dependencies if content.pair.ru_path == GLOSSARY_RU
    )
    assert glossary.coverage_plan is not None
    assert glossary.coverage_plan.required_fragments == frozenset({"user-token"})

    actions = {plan.pair.ru_path: plan.action for plan in plan_pairs(planned_contents)}
    assert {path: actions[path] for path in DEPENDENCIES} == {
        GLOSSARY_RU: "translate_to_en",
        CACHING_RU: "translate_to_en",
        USER_TOKEN_RU: "translate_to_en",
        LIFECYCLE_RU: "translate_to_en",
    }
