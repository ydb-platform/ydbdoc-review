"""Source-owned href retargeting across frozen prefix redirects."""

from __future__ import annotations

import pytest

from tests.unit.test_harness import _mock_client
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.render import finalize_en_target_result
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import FinalizeEnStep
from ydbdoc_review.validation import href_parity
from ydbdoc_review.validation.link_contract import LinkContractIssue, LinkContractResult

PAGE = "ydb/docs/en/core/reference/configuration/monitoring_config.md"
REDIRECTS = (
    "common:\n"
    "  - from: /reference/embedded-ui/(.*)$\n"
    "    to: /reference/ydb-ui/$1\n"
)


def _reader(path: str) -> str | None:
    files = {
        "ydb/docs/redirects.yaml": REDIRECTS,
        "ydb/docs/en/core/reference/ydb-ui/index.md": "# UI {#kept}\n",
        "ydb/docs/en/core/reference/ydb-ui/ydb-monitoring.md": "# Monitoring\n",
    }
    return files.get(path)


def test_retarget_owned_occurrences_preserves_bytes_and_is_idempotent() -> None:
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    source = (
        "[обычная](../embedded-ui/index.md?x=%2F#kept)\n"
        "[{#T}](../embedded-ui/ydb-monitoring.md)\n"
        "[dup](../embedded-ui/ydb-monitoring.md)\n"
    )
    target = (
        "[translated](../embedded-ui/index.md?x=%2F#kept)\n"
        "[{#T}](../embedded-ui/ydb-monitoring.md)\n"
        "[dup](../embedded-ui/ydb-monitoring.md)\n"
        "[extra](../embedded-ui/ydb-monitoring.md)\n"
        "`[code](../embedded-ui/index.md)`\n"
        "![image](../embedded-ui/index.md)\n"
        "{% include [inc](../embedded-ui/index.md) %}\n"
    )
    expected = target.replace("../embedded-ui/index.md?x=%2F#kept", "../ydb-ui/index.md?x=%2F#kept", 1)
    expected = expected.replace("../embedded-ui/ydb-monitoring.md", "../ydb-ui/ydb-monitoring.md", 2)

    out = retarget(target, source, en_page_path=PAGE, read_text=_reader)

    assert out == expected
    assert retarget(out, source, en_page_path=PAGE, read_text=_reader) == out


def test_retarget_rejects_missing_fragment_escape_and_absent_ownership() -> None:
    prove = href_parity.redirected_md_href
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    assert prove("../embedded-ui/index.md#missing", en_page_path=PAGE, read_text=_reader) is None
    assert prove("../../../../escape.md", en_page_path=PAGE, read_text=_reader) is None
    target = "[foreign](../embedded-ui/index.md)\n"
    assert retarget(target, "# no link\n", en_page_path=PAGE, read_text=_reader) == target


def test_parity_uses_only_local_canonical_source_view() -> None:
    source = "[источник](../embedded-ui/ydb-monitoring.md)\n"
    target = "[translation](../ydb-ui/ydb-monitoring.md)\n"
    assert href_parity.check_href_parity(
        source,
        target,
        en_page_path=PAGE,
        docs_text_reader=_reader,
    ) == []
    assert href_parity.check_href_parity(
        source,
        target + "[extra](../ydb-ui/ydb-monitoring.md)\n",
        en_page_path=PAGE,
        docs_text_reader=_reader,
    )


def test_finalize_retargets_before_unreachable_strip_and_preserves_issues() -> None:
    source = "See [UI](../embedded-ui/index.md).\n"
    foreign = LinkContractIssue("missing_source_href", "foreign")
    # LinkContractResult input retains an already typed issue through finalize.
    result = finalize_en_target_result(
        LinkContractResult(source, (foreign,)),
        source,
        file_path=PAGE.replace("/en/", "/ru/"),
        protected_source_text=source,
        docs_text_reader=_reader,
        en_toc_reachable=frozenset(
            {"ydb/docs/en/core/reference/ydb-ui/index.md"}
        ),
    )
    assert result.text == "See [UI](../ydb-ui/index.md).\n"
    assert foreign in result.issues


@pytest.mark.parametrize("mode", ["translate", "verify"])
def test_finalize_en_step_forwards_frozen_reader_before_strip(mode: str) -> None:
    source = "See [UI](../embedded-ui/index.md).\n"
    foreign = LinkContractIssue("missing_source_href", "foreign")
    state = FileRunState(
        mode=mode,
        file_path=PAGE.replace("/en/", "/ru/"),
        raw_source_text=source,
        source_text=source,
        translated_text=source,
        fence_reference_text=source,
        link_contract_issues=[foreign],
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={}),
        en_toc_reachable=frozenset(
            {"ydb/docs/en/core/reference/ydb-ui/index.md"}
        ),
        docs_text_reader=_reader,
    )

    step = FinalizeEnStep()
    step.run(state, ctx)

    expected = "See [UI](../ydb-ui/index.md).\n"
    assert state.translated_text == expected
    assert foreign in state.link_contract_issues
    assert state.source_text == source
    assert state.raw_source_text == source
    assert not any(
        warning.startswith("strip_unreachable_links:")
        for warning in state.finalize_warnings
    )

    step.run(state, ctx)

    assert state.translated_text == expected
    assert foreign in state.link_contract_issues
    assert state.source_text == source
    assert state.raw_source_text == source


@pytest.mark.parametrize(
    "href",
    [
        "../embedded-ui/index.md?x=%2F#kept",
        "./../embedded-ui/index.md?x=%2F#kept",
        "../../reference/embedded-ui/index.md?x=%2F#kept",
    ],
)
def test_legal_parent_segments_stay_inside_en_core(href: str) -> None:
    prove = href_parity.redirected_md_href
    assert prove(href, en_page_path=PAGE, read_text=_reader) == (
        "../ydb-ui/index.md?x=%2F#kept"
    )


@pytest.mark.parametrize(
    "href",
    [
        "../../../outside.md",
        "../../../core/reference/embedded-ui/index.md",
        "../../../../../../ydb/docs/en/core/reference/embedded-ui/index.md",
        "../../../../ru/core/reference/embedded-ui/index.md",
        "%2E%2E/embedded-ui/index.md",
        ".%2e/embedded-ui/index.md",
        "%252e%252e/embedded-ui/index.md",
        "../embedded-ui%2findex.md",
        "../embedded-ui%5Cindex.md",
        "..\\embedded-ui\\index.md",
    ],
)
def test_unsafe_relative_spellings_refuse_before_reads(href: str) -> None:
    prove = href_parity.redirected_md_href

    def forbidden(_path: str) -> str | None:
        raise AssertionError("unsafe href must not read frozen docs")

    assert prove(href, en_page_path=PAGE, read_text=forbidden) is None


@pytest.mark.parametrize(
    "page",
    [
        "ydb/docs/en/core/reference/../configuration/page.md",
        "/ydb/docs/en/core/reference/configuration/page.md",
    ],
)
def test_noncanonical_referring_page_refuses_before_reads(page: str) -> None:
    prove = href_parity.redirected_md_href

    def forbidden(_path: str) -> str | None:
        raise AssertionError("invalid referring page must not read frozen docs")

    assert prove("../embedded-ui/index.md", en_page_path=page, read_text=forbidden) is None


def test_configured_docs_root_parent_resolution() -> None:
    page = "custom/docs/en/core/reference/configuration/page.md"
    files = {
        "custom/docs/redirects.yaml": (
            "common:\n"
            "  - from: /reference/embedded-ui/(.*)$\n"
            "    to: /reference/ydb-ui/$1\n"
        ),
        "custom/docs/en/core/reference/ydb-ui/index.md": "# UI\n",
    }
    prove = href_parity.redirected_md_href
    assert prove("../embedded-ui/index.md", en_page_path=page, read_text=files.get) == (
        "../ydb-ui/index.md"
    )


def test_counted_capacity_handles_existing_canonical_and_repeated_calls() -> None:
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    old = "../embedded-ui/index.md"
    canonical = "../ydb-ui/index.md"
    source = f"[old]({old})\n[canonical]({canonical})\n"
    target = f"[old]({old})\n[canonical]({canonical})\n[extra]({old})\n"
    expected = f"[old]({canonical})\n[canonical]({canonical})\n[extra]({old})\n"
    out = retarget(target, source, en_page_path=PAGE, read_text=_reader)
    assert out == expected
    assert retarget(out, source, en_page_path=PAGE, read_text=_reader) == out


def test_shared_destination_alias_does_not_borrow_raw_ownership() -> None:
    redirects = (
        "common:\n"
        "  - from: /reference/old-a/(.*)$\n    to: /reference/ydb-ui/$1\n"
        "  - from: /reference/old-b/(.*)$\n    to: /reference/ydb-ui/$1\n"
    )
    files = {
        "ydb/docs/redirects.yaml": redirects,
        "ydb/docs/en/core/reference/ydb-ui/index.md": "# UI\n",
    }
    source = "[a](../old-a/index.md)\n[b](../old-b/index.md)\n"
    target = "[a1](../old-a/index.md)\n[a2](../old-a/index.md)\n"
    expected = "[a1](../ydb-ui/index.md)\n[a2](../old-a/index.md)\n"
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    out = retarget(target, source, en_page_path=PAGE, read_text=files.get)
    assert out == expected
    assert retarget(out, source, en_page_path=PAGE, read_text=files.get) == out


def test_protected_canonical_occurrences_do_not_consume_capacity() -> None:
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    old = "../embedded-ui/index.md"
    canonical = "../ydb-ui/index.md"
    protected = (
        f"`[code]({canonical})`\n"
        f"<!-- [comment]({canonical}) -->\n"
        f"![image]({canonical})\n"
        f"{{% include [inc]({canonical}) %}}\n"
    )
    source = f"[owned]({old})\n" + protected
    target = f"[owned]({old})\n" + protected
    out = retarget(target, source, en_page_path=PAGE, read_text=_reader)
    assert out == f"[owned]({canonical})\n" + protected


def test_foreign_canonical_occupancy_exhausts_old_capacity() -> None:
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    old = "../embedded-ui/index.md"
    canonical = "../ydb-ui/index.md"
    source = f"[owned]({old})\n"
    target = f"[foreign]({canonical})\n[extra]({old})\n"
    assert retarget(target, source, en_page_path=PAGE, read_text=_reader) == target
    assert retarget(target, source, en_page_path=PAGE, read_text=_reader) == target
    assert href_parity.check_href_parity(
        source, target, en_page_path=PAGE, docs_text_reader=_reader
    )


def test_missing_canonical_baseline_refuses_all_rewrites() -> None:
    retarget = href_parity.retarget_source_owned_redirect_hrefs
    old = "../embedded-ui/index.md"
    canonical = "../ydb-ui/index.md"
    source = f"[old]({old})\n[c1]({canonical})\n[c2]({canonical})\n"
    target = f"[one]({old})\n[two]({old})\n[three]({old})\n"
    assert retarget(target, source, en_page_path=PAGE, read_text=_reader) == target
    assert retarget(target, source, en_page_path=PAGE, read_text=_reader) == target
    assert href_parity.check_href_parity(
        source, target, en_page_path=PAGE, docs_text_reader=_reader
    )
