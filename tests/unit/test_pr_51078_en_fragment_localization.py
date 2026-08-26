"""#51078: EN links must not keep RU Diplodoc auto-slugs on EN targets.

Regression fixtures from ``client_certificate_authorization.md`` →
``node-authorization.md#vklyuchenie-rezhima-…`` (wrong) vs
``#enabling-node-authentication-and-authorization-mode`` (expected).
"""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.fragment_repair import (
    _remap_fragment_via_ru_en_pages,
    repair_en_fragments,
)
from ydbdoc_review.validation.href_parity import (
    apply_href_only_delta,
    check_href_parity,
    restore_md_link_hrefs,
)

PR_51078_EN_PAGE = (
    "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
)
PR_51078_RU_PAGE = (
    "ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md"
)
PR_51078_TARGET_EN = "ydb/docs/en/core/devops/concepts/node-authorization.md"
PR_51078_TARGET_RU = "ydb/docs/ru/core/devops/concepts/node-authorization.md"
PR_51078_RU_FRAG = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
PR_51078_EN_FRAG = "enabling-node-authentication-and-authorization-mode"
PR_51078_HREF = f"../../devops/concepts/node-authorization.md#{PR_51078_RU_FRAG}"
PR_51078_HREF_EN = f"../../devops/concepts/node-authorization.md#{PR_51078_EN_FRAG}"

def _pr_51078_target_pages() -> dict[str, str]:
    return {
        PR_51078_TARGET_EN: dedent(
            """\
            ## Enabling node authentication and authorization mode

            Body.
            """
        ),
        PR_51078_TARGET_RU: dedent(
            """\
            ## Включение режима аутентификации и авторизации узлов

            Тело.
            """
        ),
    }


def _pr_51078_en_bad_paragraph() -> str:
    return dedent(
        f"""\
        The `CN` component may contain the server's network name rather than the user name. \
        This option is advisable to use when [registering dynamic nodes]({PR_51078_HREF}). \
        The following configuration fragment requires that the "Subject" field of the node's \
        client certificate contain the `O=YDB` and `CN=server1.internal.corp` components.
        """
    )


def _pr_51078_ru_paragraph() -> str:
    return dedent(
        f"""\
        В компоненте `CN` может указываться сетевое имя сервера, а не имя пользователя. \
        Такой вариант целесообразно использовать при [регистрации динамических узлов]({PR_51078_HREF}). \
        Следующий фрагмент конфигурации требует, чтобы в поле "Subject" клиентского сертификата узла \
        были компоненты `O=YDB` и `CN=server1.internal.corp`.
        """
    )


def _assert_en_fragment(href_text: str) -> None:
    assert PR_51078_EN_FRAG in href_text, href_text
    assert PR_51078_RU_FRAG not in href_text, href_text


def test_pr_51078_remap_fragment_maps_transliterated_ru_slug_to_en_autoslug():
    """Low-level mapping documents the expected EN Diplodoc slug."""
    pages = _pr_51078_target_pages()
    mapped = _remap_fragment_via_ru_en_pages(
        PR_51078_RU_FRAG,
        pages[PR_51078_TARGET_RU],
        pages[PR_51078_TARGET_EN],
    )
    assert mapped == PR_51078_EN_FRAG


def test_pr_51078_repair_en_fragments_without_ru_source():
    """Repair must localize via paired RU/EN target pages alone (#51078)."""
    bad = _pr_51078_en_bad_paragraph()
    fixed = repair_en_fragments(
        bad,
        en_page_path=PR_51078_EN_PAGE,
        read_text=_pr_51078_target_pages().get,
    )
    _assert_en_fragment(fixed)


def test_pr_51078_repair_en_fragments_real_client_certificate_snippet():
    """Full EN paragraph from #51078 must not keep the RU transliterated slug."""
    bad = _pr_51078_en_bad_paragraph()
    ru = _pr_51078_ru_paragraph()
    fixed = repair_en_fragments(
        bad,
        en_page_path=PR_51078_EN_PAGE,
        read_text=_pr_51078_target_pages().get,
        ru_source=ru,
    )
    _assert_en_fragment(fixed)


def test_pr_51078_restore_md_link_hrefs_localizes_transliterated_slug():
    """Preserve-path restore must not copy the RU slug onto EN (#51078)."""
    ru = _pr_51078_ru_paragraph()
    en = _pr_51078_en_bad_paragraph()
    fixed = restore_md_link_hrefs(
        en,
        ru,
        source_ru_base=ru,
        target_baseline=en,
        en_page_path=PR_51078_EN_PAGE,
        docs_text_reader=_pr_51078_target_pages().get,
    )
    _assert_en_fragment(fixed)


def test_pr_51078_href_parity_accepts_en_localized_fragment_for_same_section():
    """Correct EN slug must not be flagged against the RU twin (#51078)."""
    ru = _pr_51078_ru_paragraph()
    en = _pr_51078_en_bad_paragraph().replace(PR_51078_HREF, PR_51078_HREF_EN)
    assert (
        check_href_parity(
            ru,
            en,
            en_page_path=PR_51078_EN_PAGE,
            docs_text_reader=_pr_51078_target_pages().get,
        )
        == []
    )


def test_pr_51078_href_only_delta_repair_pipeline_documents_expected_anchor():
    """Href-only RU path retarget + repair is the happy path for new PR deltas."""
    old = (
        "../../devops/deployment-options/manual/node-authorization.md"
        f"#{PR_51078_RU_FRAG}"
    )
    new = PR_51078_HREF
    ru_base = f"[регистрации динамических узлов]({old})\n"
    ru_now = f"[регистрации динамических узлов]({new})\n"
    en_base = f"[registering dynamic nodes]({old})\n"
    delta = apply_href_only_delta(
        ru_base,
        ru_now,
        en_base,
        en_page_path=PR_51078_EN_PAGE,
        docs_text_reader=_pr_51078_target_pages().get,
    )
    assert delta is not None
    fixed = repair_en_fragments(
        delta,
        en_page_path=PR_51078_EN_PAGE,
        read_text=_pr_51078_target_pages().get,
        ru_source=ru_now,
        en_baseline=en_base,
    )
    _assert_en_fragment(fixed)


def test_pr_51078_pair_critic_only_repairs_existing_en_ru_slug():
    """Verify/critic must repair a static EN page that still carries the RU slug."""
    pair = DocPair(
        ru_path=PR_51078_RU_PAGE,
        en_path=PR_51078_EN_PAGE,
        ru_changed=True,
    )
    ru = _pr_51078_ru_paragraph()
    en = _pr_51078_en_bad_paragraph()
    content = PairContent(
        pair=pair,
        ru_text=ru,
        en_text=en,
        ru_base_text=ru,
        en_base_text=en,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="verify static EN slug",
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
        docs_text_reader=_pr_51078_target_pages().get,
    )

    class _FakeHarness:
        def __init__(self, _profile: object) -> None:
            pass

        def run(self, state, _ctx):
            result = MagicMock()
            result.final_text = state.existing_target_text
            result.differential_meta = {}
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        result = run_pair_plan(content, plan, parent, {})

    assert result.target_text is not None
    _assert_en_fragment(result.target_text)


def test_fragment_localization_does_not_invent_missing_counterpart():
    bad = "See [target](./target.md#russkii-slug).\n"
    pages = {
        "ydb/docs/en/core/target.md": "## Unrelated heading\n",
        "ydb/docs/ru/core/target.md": "## Другой заголовок\n",
    }

    assert repair_en_fragments(
        bad,
        en_page_path="ydb/docs/en/core/source.md",
        read_text=pages.get,
    ) == bad


def test_fragment_localization_does_not_guess_ambiguous_counterpart():
    bad = "See [target](./target.md#razdel).\n"
    pages = {
        "ydb/docs/en/core/target.md": "## First\n\n## Second\n",
        "ydb/docs/ru/core/target.md": "## Раздел\n\n## Раздел\n",
    }

    assert repair_en_fragments(
        bad,
        en_page_path="ydb/docs/en/core/source.md",
        read_text=pages.get,
    ) == bad
