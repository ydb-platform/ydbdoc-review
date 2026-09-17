from __future__ import annotations

from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets
from ydbdoc_review.validation.href_parity import (
    _iter_visible_md_link_matches,
    reconcile_final_en_same_fragment_paths,
)
from ydbdoc_review.validation.link_contract import LinkContractIssue

RU_PAGE = "ydb/docs/ru/core/security/authentication.md"
EN_PAGE = RU_PAGE.replace("/ru/", "/en/")
RU_OWNER = "ydb/docs/ru/core/reference/configuration/auth_config.md"
EN_OWNER = RU_OWNER.replace("/ru/", "/en/")
EN_SECURITY = "ydb/docs/en/core/reference/configuration/security_config.md"
BROKEN = "../reference/configuration/auth_config.md#security-auth"
HISTORICAL = "../reference/configuration/security_config.md#security-auth"
FILLER = "../reference/configuration/filler.md"
EN_FILLER = "ydb/docs/en/core/reference/configuration/filler.md"

RU_SENTENCE = (
    "За отключение анонимной аутентификации отвечает флаг "  # noqa: RUF001
    f"[настройки режима аутентификации]({BROKEN}) {{{{ ydb-short-name }}}}."
)
EN_BROKEN_SENTENCE = (
    "The `enforce_user_token_requirement` flag in the "
    "[authentication mode settings]"
    f"({BROKEN}) of {{{{ ydb-short-name }}}} is responsible for disabling "
    "anonymous authentication."
)
EN_HISTORICAL_SENTENCE = (
    "The [authentication mode settings]"
    f"({HISTORICAL}) flag is responsible for disabling anonymous authentication "
    "in {{ ydb-short-name }}."
)


def _filler(label: str, count: int) -> str:
    return "".join(f"[{label} {index}]({FILLER})\n" for index in range(count))


def _texts_70_75_67_75() -> tuple[str, str, str, str]:
    ru_base = _filler("RU before", 12) + "\n" + RU_SENTENCE + "\n\n" + _filler("RU after", 57)
    ru_current = (
        _filler("RU current before", 13)
        + "\n"
        + RU_SENTENCE
        + "\n\n"
        + _filler("RU current after", 61)
    )
    en_tip = (
        _filler("EN tip before", 12)
        + "\n"
        + EN_HISTORICAL_SENTENCE
        + "\n\n"
        + _filler("EN tip after", 54)
    )
    candidate = (
        _filler("EN candidate before", 13)
        + "\n"
        + EN_BROKEN_SENTENCE
        + "\n\n"
        + _filler("EN candidate after", 61)
    )
    return ru_base, ru_current, en_tip, candidate


def _readers(
    candidate: str,
    *,
    ru_owner: str = "# auth_config\n",
    en_owner: str = "# auth_config\n",
    en_security: str | None = "# Security {#security-auth}\n",
):
    source_pages = {RU_PAGE: candidate, RU_OWNER: ru_owner}
    final_pages = {EN_PAGE: candidate, EN_OWNER: en_owner, EN_FILLER: "# Filler\n"}
    if en_security is not None:
        final_pages[EN_SECURITY] = en_security
    return source_pages.get, final_pages.get


def _reconcile(
    ru_base: str,
    ru_current: str,
    en_tip: str,
    candidate: str,
    *,
    source_owner: str = "# auth_config\n",
    final_owner: str = "# auth_config\n",
    historical_owner: str | None = "# Security {#security-auth}\n",
    issues: tuple[LinkContractIssue, ...] = (),
) -> str:
    source_read, final_read = _readers(
        candidate,
        ru_owner=source_owner,
        en_owner=final_owner,
        en_security=historical_owner,
    )
    return reconcile_final_en_same_fragment_paths(
        ru_base,
        ru_current,
        en_tip,
        candidate,
        ru_page_path=RU_PAGE,
        en_page_path=EN_PAGE,
        read_source_ru=source_read,
        read_final_en=final_read,
        link_contract_issues=issues,
    )


def test_pr_51079_reuses_one_proven_historical_path_across_70_75_67_75_topology():
    """Deleting the global R0/E0 count guard must not permit any other byte change."""
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()
    assert [
        len(list(_iter_visible_md_link_matches(text)))
        for text in (ru_base, ru_current, en_tip, candidate)
    ] == [70, 75, 67, 75]

    fixed = _reconcile(ru_base, ru_current, en_tip, candidate)

    expected = candidate.replace(BROKEN, HISTORICAL)
    assert fixed == expected
    assert fixed.count(HISTORICAL) == 1
    assert fixed.count(BROKEN) == 0
    _, final_read = _readers(fixed)
    assert check_en_page_link_targets(EN_PAGE, fixed, read_text=final_read) == []


def test_pr_51079_unrelated_internal_link_edits_around_paragraph_do_not_block_reuse():
    ru_base = _filler("base before", 2) + "\n" + RU_SENTENCE + "\n\n" + _filler("base after", 2)
    ru_current = (
        _filler("current before", 4) + "\n" + RU_SENTENCE + "\n\n" + _filler("current after", 1)
    )
    en_tip = _filler("tip before", 1) + "\n" + EN_HISTORICAL_SENTENCE + "\n\n"
    candidate = (
        _filler("candidate before", 3)
        + "\n"
        + EN_BROKEN_SENTENCE
        + "\n\n"
        + _filler("candidate after", 2)
    )

    assert _reconcile(ru_base, ru_current, en_tip, candidate) == candidate.replace(
        BROKEN,
        HISTORICAL,
    )


def test_pr_51079_paragraph_local_fallback_preserves_wrapper_title_and_raw_fragment():
    broken = "../reference/configuration/auth_config.md#security%2Dauth"
    historical = "../reference/configuration/security_config.md#security%2Dauth"
    ru_sentence = f'See [Security](<{broken}> "source title").'
    en_candidate = f'See [Security](  <{broken}> "source title"  ).'
    en_tip = f'Historically, [Security](  <{historical}> "source title"  ) was configured here.'
    expected = en_candidate.replace(broken.partition("#")[0], historical.partition("#")[0])

    assert (
        _reconcile(
            _filler("base", 1) + "\n" + ru_sentence,
            _filler("current", 2) + "\n" + ru_sentence,
            en_tip,
            en_candidate,
        )
        == expected
    )


def test_pr_51079_paragraph_local_fallback_rejects_unproven_lineage():
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()

    cases = (
        # The RU occurrence changed its label between R0 and R1.
        (
            ru_base,
            ru_current.replace("настройки режима", "параметры режима"),
            en_tip,
            candidate,
            {},
        ),
        # A second qualifying candidate occurrence makes EN correspondence ambiguous.
        (ru_base, ru_current, en_tip, candidate + "\n\n" + EN_BROKEN_SENTENCE, {}),
        # A source-resolvable href remains source authority.
        (
            ru_base,
            ru_current,
            en_tip,
            candidate,
            {"source_owner": "# auth_config {#security-auth}\n"},
        ),
        # An already resolvable candidate needs no fallback.
        (
            ru_base,
            ru_current,
            en_tip,
            candidate,
            {"final_owner": "# auth_config {#security-auth}\n"},
        ),
        # Missing/deleted/fragmentless historical targets provide no evidence.
        (ru_base, ru_current, en_tip, candidate, {"historical_owner": None}),
        (ru_base, ru_current, en_tip, candidate, {"historical_owner": "# Security\n"}),
        # Existing deterministic contract issues must keep publication fail-closed.
        (
            ru_base,
            ru_current,
            en_tip,
            candidate,
            {"issues": (LinkContractIssue("missing_source_href", "ambiguous"),)},
        ),
    )

    for base, current, tip, proposed, kwargs in cases:
        assert _reconcile(base, current, tip, proposed, **kwargs) == proposed


def test_pr_51079_reconciliation_preserves_candidate_query_and_fragment_bytes():
    broken = "../reference/configuration/auth_config.md?view=current#security%2Dauth"
    historical = "../reference/configuration/security_config.md?view=historical#security%2Dauth"
    ru_sentence = f"See [Security]({broken})."
    candidate = f"Current prose [Security]({broken}) remains translated."
    en_tip = f"Old prose [Security]({historical}) differed."

    fixed = _reconcile(ru_sentence, ru_sentence, en_tip, candidate)

    assert fixed == candidate.replace(
        "../reference/configuration/auth_config.md",
        "../reference/configuration/security_config.md",
    )
    assert "?view=current#security%2Dauth" in fixed


def test_pr_51079_same_file_stale_source_route_can_restore_cross_file_tip_route():
    broken = "#security-auth"
    ru_sentence = f"See [Security]({broken})."
    candidate = f"See translated [Security]({broken})."
    en_tip = f"See historical [Security]({HISTORICAL})."

    assert _reconcile(ru_sentence, ru_sentence, en_tip, candidate) == candidate.replace(
        broken,
        HISTORICAL,
    )


def test_pr_51079_unique_auto_slug_owner_is_valid_historical_route():
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()

    assert _reconcile(
        ru_base,
        ru_current,
        en_tip,
        candidate,
        historical_owner="# Security auth\n",
    ) == candidate.replace(BROKEN, HISTORICAL)


def test_pr_51079_duplicate_effective_anchor_owners_fail_closed():
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()
    ambiguous_owner = "# Security {#security-auth}\n\n## Security auth\n"

    fixed = _reconcile(
        ru_base,
        ru_current,
        en_tip,
        candidate,
        historical_owner=ambiguous_owner,
    )

    assert fixed == candidate
    _, final_read = _readers(candidate, en_security=ambiguous_owner)
    assert check_en_page_link_targets(EN_PAGE, fixed, read_text=final_read)


def test_pr_51079_only_link_contract_issue_for_this_slot_blocks_reconciliation():
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()
    unrelated = LinkContractIssue(
        "missing_link_wrapper",
        "other slot",
        file_path=EN_PAGE,
        slot=0,
        href="../reference/configuration/other.md#other",
    )
    matching = LinkContractIssue(
        "missing_link_wrapper",
        "same href",
        file_path=EN_PAGE,
        href=BROKEN,
    )

    assert _reconcile(
        ru_base,
        ru_current,
        en_tip,
        candidate,
        issues=(unrelated,),
    ) == candidate.replace(BROKEN, HISTORICAL)
    assert _reconcile(
        ru_base,
        ru_current,
        en_tip,
        candidate,
        issues=(matching,),
    ) == candidate


def test_pr_51079_reconciliation_keeps_protected_link_like_bytes_unchanged():
    protected = (
        f"`[inline]({BROKEN})`\n\n"
        f"![image]({BROKEN})\n\n"
        f"```md\n[fenced]({BROKEN})\n```\n\n"
    )
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()

    fixed = _reconcile(
        protected + ru_base,
        protected + ru_current,
        protected + en_tip,
        protected + candidate,
    )

    assert fixed.startswith(protected)
    assert fixed[len(protected) :] == candidate.replace(BROKEN, HISTORICAL)


def test_pr_51079_cross_kind_block_reorder_invalidates_structural_slot():
    fence = "```text\nprotected\n```"
    ru_base = RU_SENTENCE + "\n\n" + fence
    ru_current = fence + "\n\n" + RU_SENTENCE
    en_tip = EN_HISTORICAL_SENTENCE + "\n\n" + fence
    candidate = EN_BROKEN_SENTENCE + "\n\n" + fence

    assert _reconcile(ru_base, ru_current, en_tip, candidate) == candidate


def test_pr_51079_same_fragment_on_unrelated_route_does_not_create_ambiguity():
    unrelated = "../reference/configuration/other.md#security-auth"
    ru = RU_SENTENCE + f"\n\n[Other route]({unrelated})."
    en_tip = EN_HISTORICAL_SENTENCE + f"\n\n[Other route]({unrelated})."
    candidate = EN_BROKEN_SENTENCE + f"\n\n[Other route]({unrelated})."

    assert _reconcile(ru, ru, en_tip, candidate) == candidate.replace(
        BROKEN,
        HISTORICAL,
    )


def test_pr_51079_equal_tip_and_candidate_paths_do_not_read_target_context():
    same = "../reference/configuration/other.md#same-fragment"
    source = f"See [Same]({same})."

    def unexpected_read(path: str) -> str | None:
        raise AssertionError(f"unexpected target read: {path}")

    assert reconcile_final_en_same_fragment_paths(
        source,
        source,
        source,
        source,
        ru_page_path=RU_PAGE,
        en_page_path=EN_PAGE,
        read_source_ru=unexpected_read,
        read_final_en=unexpected_read,
    ) == source


def test_pr_51079_paragraph_local_fallback_rejects_duplicate_historical_fragment():
    ru_base, ru_current, en_tip, candidate = _texts_70_75_67_75()
    duplicate = f"[Other]({BROKEN})\n"
    duplicate_tip = f"[Other]({HISTORICAL})\n"

    assert (
        _reconcile(
            duplicate + ru_base,
            duplicate + ru_current,
            duplicate_tip + en_tip,
            candidate,
        )
        == candidate
    )


def test_pr_51079_paragraph_local_fallback_rejects_target_outside_docs_root():
    outside_href = "../../../../outside.md#security-auth"
    en_tip = EN_BROKEN_SENTENCE.replace(BROKEN, outside_href)
    source_pages = {RU_OWNER: "# auth_config\n"}
    final_pages = {
        EN_PAGE: EN_BROKEN_SENTENCE,
        EN_OWNER: "# auth_config\n",
        "ydb/outside.md": "# Outside {#security-auth}\n",
    }

    fixed = reconcile_final_en_same_fragment_paths(
        RU_SENTENCE,
        _filler("current", 1) + "\n\n" + RU_SENTENCE,
        en_tip,
        EN_BROKEN_SENTENCE,
        ru_page_path=RU_PAGE,
        en_page_path=EN_PAGE,
        read_source_ru=source_pages.get,
        read_final_en=final_pages.get,
    )

    assert fixed == EN_BROKEN_SENTENCE


def test_pr_51079_paragraph_local_fallback_rejects_moved_ru_paragraph():
    ru_base = RU_SENTENCE + "\n\nStable trailing RU paragraph."
    ru_current = "Inserted RU paragraph.\n\n" + ru_base
    en_tip = EN_HISTORICAL_SENTENCE + "\n\nStable trailing EN paragraph."
    candidate = EN_BROKEN_SENTENCE + "\n\nStable trailing EN paragraph."

    assert _reconcile(ru_base, ru_current, en_tip, candidate) == candidate


def test_pr_51079_paragraph_local_fallback_rejects_moved_en_paragraph():
    ru = RU_SENTENCE + "\n\nStable trailing RU paragraph."
    en_tip = EN_HISTORICAL_SENTENCE + "\n\nStable trailing EN paragraph."
    candidate = (
        "Inserted EN paragraph one.\n\n"
        "Inserted EN paragraph two.\n\n"
        + EN_BROKEN_SENTENCE
        + "\n\nStable trailing EN paragraph."
    )

    assert _reconcile(ru, ru, en_tip, candidate) == candidate
