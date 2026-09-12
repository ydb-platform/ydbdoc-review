"""Frozen-B evidence and K/P pair-seam contracts for wrapper-only repair."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from ydbdoc_review.validation import href_parity
from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/pr52330-wrapper-repair-snapshots.json"
)
OLD_HREF = (
    "../../devops/deployment-options/manual/node-authorization.md"
    "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
)
FROZEN_HREF = (
    "../../devops/concepts/node-authorization.md"
    "#enabling-the-node-authentication-and-authorization-mode"
)
LABEL = "registering dynamic nodes"


def _fixture() -> dict[str, object]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    capture = payload["capture"]
    reachable = capture["reachable"]
    encoded = json.dumps(
        sorted(reachable), ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert len(reachable) == capture["reachable_count"] == 804
    assert hashlib.sha256(encoded).hexdigest() == capture["reachable_sha256"]
    assert capture["reachable_sha256"] == (
        "283d68e2556b4c810ab1b0c49c8b72df7753c26806003351f037f5532ed4c93e"
    )
    assert "ydb/docs/en/core/devops/concepts/node-authorization.md" in reachable
    assert (
        "ydb/docs/en/core/devops/deployment-options/manual/node-authorization.md"
        not in reachable
    )
    assert len(capture["reachable_read_manifest"]) == 964
    assert sum(row["present"] for row in capture["reachable_read_manifest"]) == 963
    return payload


def _texts(payload: dict[str, object]) -> tuple[str, str, str, str, str]:
    paths = payload["paths"]
    trees = payload["trees"]
    ru = paths["ru_clientcert"]
    en = paths["en_clientcert"]
    h0 = trees["H0"][ru]["text"]
    h = trees["H"][ru]["text"]
    b = trees["B"][en]["text"]
    k = trees["K"][en]["text"]
    assert all(isinstance(text, str) for text in (h0, h, b, k))
    p = k.replace(LABEL, f"[{LABEL}]({FROZEN_HREF})", 1)
    assert p != k
    return h0, h, b, k, p


def _strict_reader(
    payload: dict[str, object], role: str
) -> tuple[Callable[[str], str | None], list[str]]:
    trees = payload["trees"]
    catalog = {
        (captured_role, path)
        for captured_role, entries in trees.items()
        for path in entries
    }
    calls: list[str] = []

    def read(path: str) -> str | None:
        calls.append(path)
        assert (role, path) in catalog, f"unknown frozen {role} lookup: {path}"
        entry = trees[role][path]
        return entry["text"]

    return read, calls


def _proposal(**kwargs: object) -> str | None:
    function = getattr(
        href_parity, "propose_frozen_baseline_link_wrapper_repair", None
    )
    assert callable(function), "automatic frozen-baseline wrapper proposal is missing"
    return function(**kwargs)


def test_v015_three_pair_baseline_controls() -> None:
    payload = _fixture()
    h0, h, b, k, p = _texts(payload)
    read_b, _calls = _strict_reader(payload, "B")
    target_path = payload["paths"]["en_target"]
    assert read_b(target_path) == payload["trees"]["B"][target_path]["text"]
    sentinel = "ydb/docs/en/core/__uncaptured_v017_sentinel__.md"
    assert sentinel not in payload["trees"]["B"]
    with pytest.raises(AssertionError, match="unknown frozen B lookup"):
        read_b(sentinel)
    k_b = href_parity.restore_md_link_hrefs(
        k, h, source_ru_base=h0, target_baseline=b
    )
    k_k = href_parity.restore_md_link_hrefs(
        k, h, source_ru_base=h0, target_baseline=k
    )
    p_p = href_parity.restore_md_link_hrefs(
        p, h, source_ru_base=h0, target_baseline=p
    )

    assert k_b.text == k and k_b.issues == ()
    assert k_k.text == k
    assert [issue.code for issue in k_k.issues] == ["missing_link_wrapper"]
    assert k_k.issues[0].href == OLD_HREF
    assert k_k.issues[0].slot == 0
    assert p_p.text == p and p_p.issues == ()


def test_real_fixture_proposes_exact_one_wrapper_and_is_idempotent() -> None:
    payload = _fixture()
    h0, h, b, k, p = _texts(payload)
    read_b, b_calls = _strict_reader(payload, "B")
    read_k, k_calls = _strict_reader(payload, "K")
    common = dict(
        source_text=h,
        source_base_text=h0,
        target_baseline_text=b,
        missing_href=OLD_HREF,
        en_page_path=payload["paths"]["en_clientcert"],
        read_baseline=read_b,
        read_final=read_k,
    )

    assert _proposal(target_text=k, **common) == p
    assert _proposal(target_text=p, **common) is None
    assert all("/ru/" not in path for path in [*b_calls, *k_calls])


def _synthetic(
    *,
    target_text: str = "Intro. right choice.\n",
    source_text: str = "Введение. [право](old.md#ru-anchor).\n",
    source_base_text: str | None = None,
    target_baseline_text: str = (
        "Intro. [right](target.md?view=compact#anchor) choice.\n"
    ),
    docs_root: str = "ydb/docs",
) -> dict[str, object]:
    root = docs_root.strip("/")
    page = f"{root}/en/core/guide/page.md"
    target = f"{root}/en/core/guide/target.md"
    redirects = (
        "common:\n"
        "  - from: /guide/old.md\n"
        "    to: /guide/target.md\n"
    )
    bodies = {
        f"{root}/redirects.yaml": redirects,
        target: "# Target {#anchor}\n",
    }

    def read(path: str) -> str | None:
        assert "/ru/" not in path
        return bodies.get(path)

    return {
        "target_text": target_text,
        "source_text": source_text,
        "source_base_text": source_base_text or source_text,
        "target_baseline_text": target_baseline_text,
        "missing_href": "old.md#ru-anchor",
        "en_page_path": page,
        "read_baseline": read,
        "read_final": read,
        "docs_root": docs_root,
    }


def test_query_and_fragment_are_preserved_byte_exactly() -> None:
    kwargs = _synthetic()
    expected = (
        "Intro. [right](target.md?view=compact#anchor) choice.\n"
    )
    assert _proposal(**kwargs) == expected
    assert _proposal(**{**kwargs, "target_text": expected}) is None
    restored = href_parity.restore_md_link_hrefs(
        expected,
        kwargs["source_text"],
        source_ru_base=kwargs["source_base_text"],
        target_baseline=expected,
    )
    assert restored.text == expected and restored.issues == ()
    assert href_parity.collect_internal_hrefs(expected) == [
        "target.md?view=compact#anchor"
    ]


@pytest.mark.parametrize(
    ("target_text", "expected_detail"),
    [
        ("# Target {#anchor}\n", None),
        (None, "missing file"),
        ("# Target {#actual}\n", "missing fragment: anchor"),
    ],
)
def test_query_href_final_gate_requires_target_and_fragment(
    target_text: str | None, expected_detail: str | None
) -> None:
    target_path = "ydb/docs/en/core/guide/target.md"
    calls: list[str] = []

    def read(path: str) -> str | None:
        calls.append(path)
        assert path == target_path
        return target_text

    issues = check_en_page_link_targets(
        "ydb/docs/en/core/guide/page.md",
        "See [right](target.md?view=compact#anchor).\n",
        read_text=read,
    )
    assert calls == [target_path]
    if expected_detail is None:
        assert issues == []
    else:
        assert len(issues) == 1
        assert "target: guide/target.md" in issues[0]
        assert expected_detail in issues[0]


def test_visible_occurrence_can_coexist_with_protected_copy() -> None:
    target = "Intro `right` and right choice.\n"
    kwargs = _synthetic(
        target_text=target,
        target_baseline_text=(
            "Intro `safe` and [right](target.md?view=compact#anchor) choice.\n"
        ),
        source_text="Введение `safe` и [право](old.md#ru-anchor).\n",
    )
    assert _proposal(**kwargs) == (
        "Intro `right` and [right](target.md?view=compact#anchor) choice.\n"
    )


@pytest.mark.parametrize(
    "target,baseline,source",
    [
        (
            "Intro `right` choice.\n",
            "Intro `safe` [right](target.md#anchor) choice.\n",
            "Введение `safe` [право](old.md#ru-anchor).\n",
        ),
        (
            "```text\nright\n```\n\nNo label here.\n",
            "```text\nsafe\n```\n\n[right](target.md#anchor).\n",
            "```text\nsafe\n```\n\n[право](old.md#ru-anchor).\n",
        ),
        (
            "\n    right\n\nNo label here.\n",
            "\n    safe\n\n[right](target.md#anchor).\n",
            "\n    safe\n\n[право](old.md#ru-anchor).\n",
        ),
        (
            "<!-- right -->\n\nNo label here.\n",
            "<!-- safe -->\n\n[right](target.md#anchor).\n",
            "<!-- safe -->\n\n[право](old.md#ru-anchor).\n",
        ),
        (
            "[right](other.md) only.\n",
            "[right](target.md#anchor) only.\n",
            "[право](old.md#ru-anchor) only.\n",
        ),
        (
            "![right](image.png) only.\n",
            "![safe](image.png) [right](target.md#anchor) only.\n",
            "![safe](image.png) [право](old.md#ru-anchor) only.\n",
        ),
        (
            "{% include [right](piece.md) %}\n",
            "{% include [safe](piece.md) %} [right](target.md#anchor).\n",
            "{% include [safe](piece.md) %} [право](old.md#ru-anchor).\n",
        ),
    ],
)
def test_protected_only_labels_are_never_wrapped(
    target: str, baseline: str, source: str
) -> None:
    assert _proposal(
        **_synthetic(
            target_text=target,
            target_baseline_text=baseline,
            source_text=source,
        )
    ) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_text": "Введение без ссылки.\n"},
        {
            "source_base_text": "Старое [право](old.md#ru-anchor).\n",
            "source_text": "Новое [право](old.md#ru-anchor).\n",
        },
        {"target_text": "Intro. no label.\n"},
        {"target_text": "right then right again.\n"},
        {"target_text": "bright choice.\n"},
        {"target_text": "No label here.\n\nright in another paragraph.\n"},
        {
            "target_baseline_text": (
                "Intro. [first](target.md#anchor) and "
                "[right](target.md#anchor).\n"
            )
        },
        {"target_baseline_text": "# [right](target.md#anchor)\n"},
    ],
)
def test_ambiguous_or_unowned_slot_refuses(overrides: dict[str, str]) -> None:
    assert _proposal(**_synthetic(**overrides)) is None


@pytest.mark.parametrize("reader_role", ["baseline", "final"])
@pytest.mark.parametrize("missing", ["target", "fragment"])
def test_target_and_fragment_must_exist_at_b_and_k(
    reader_role: str, missing: str
) -> None:
    kwargs = _synthetic()
    original = kwargs[f"read_{reader_role}"]

    def broken(path: str) -> str | None:
        text = original(path)
        if path.endswith("target.md"):
            return None if missing == "target" else "# Different {#different}\n"
        return text

    kwargs[f"read_{reader_role}"] = broken
    assert _proposal(**kwargs) is None


def test_redirect_cycle_and_traversal_refuse() -> None:
    for redirects in (
        "common:\n  - from: /guide/old.md\n    to: /guide/old.md\n",
        "common:\n  - from: /guide/old.md\n    to: /../outside.md\n",
    ):
        kwargs = _synthetic()
        target_path = "ydb/docs/en/core/guide/target.md"

        def read(
            path: str,
            *,
            redirects: str = redirects,
            target_path: str = target_path,
        ) -> str | None:
            if path == "ydb/docs/redirects.yaml":
                return redirects
            if path == target_path:
                return "# Target {#anchor}\n"
            return None

        kwargs["read_baseline"] = read
        kwargs["read_final"] = read
        assert _proposal(**kwargs) is None


def test_custom_docs_root_repairs_but_cross_root_escape_refuses() -> None:
    good = _synthetic(docs_root="alt/docs")
    assert _proposal(**good) == (
        "Intro. [right](target.md?view=compact#anchor) choice.\n"
    )
    escaped = _synthetic(
        docs_root="alt/docs",
        source_text="Введение. [право](../../../../other/docs/en/core/x.md#ru).\n",
    )
    escaped["source_base_text"] = escaped["source_text"]
    escaped["missing_href"] = "../../../../other/docs/en/core/x.md#ru"
    assert _proposal(**escaped) is None


@pytest.mark.parametrize("docs_root", ["ydb/docs", "alt/docs"])
@pytest.mark.parametrize("evidence_side", ["source", "baseline"])
def test_raw_core_exit_and_reentry_is_refused(
    docs_root: str, evidence_side: str
) -> None:
    kwargs = _synthetic(docs_root=docs_root)
    if evidence_side == "source":
        source = "Введение. [право](../../core/guide/old.md#ru-anchor).\n"
        kwargs["source_text"] = source
        kwargs["source_base_text"] = source
        kwargs["missing_href"] = "../../core/guide/old.md#ru-anchor"
    else:
        kwargs["target_baseline_text"] = (
            "Intro. [right](../../core/guide/target.md?view=compact#anchor) "
            "choice.\n"
        )
    assert _proposal(**kwargs) is None


@pytest.mark.parametrize("docs_root", ["ydb/docs", "alt/docs"])
def test_parent_segments_inside_core_remain_repairable(docs_root: str) -> None:
    source = "Введение. [право](../guide/old.md#ru-anchor).\n"
    kwargs = _synthetic(
        docs_root=docs_root,
        source_text=source,
        source_base_text=source,
        target_baseline_text=(
            "Intro. [right](../guide/target.md?view=compact#anchor) choice.\n"
        ),
    )
    kwargs["missing_href"] = "../guide/old.md#ru-anchor"
    assert _proposal(**kwargs) == (
        "Intro. [right](../guide/target.md?view=compact#anchor) choice.\n"
    )


@pytest.mark.parametrize(
    "token",
    [
        "right_choice",
        "prefix_right",
        "right\u0301",
        "\u0301right",
        "right\u0903",
        "right\u20dd",
    ],
)
def test_word_continuation_is_not_a_plain_label_boundary(token: str) -> None:
    assert _proposal(**_synthetic(target_text=f"Intro. {token} choice.\n")) is None


def test_plain_label_at_punctuation_boundary_remains_repairable() -> None:
    assert _proposal(**_synthetic(target_text="Intro. right, choice.\n")) == (
        "Intro. [right](target.md?view=compact#anchor), choice.\n"
    )
