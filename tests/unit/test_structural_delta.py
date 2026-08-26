import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.harness.pair import _try_deterministic_en_preserve, run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.validation.structural_delta import (
    HistoricalDeltaStatus,
    historical_operations_survive,
    structural_delta_satisfied,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "pr_37673_bulk_upsert"

BEFORE = """{% list tabs %}

- JavaScript

  {% include [x](../../_includes/work-in-progress.md) %}

{% endlist %}
"""

AFTER = """{% list tabs %}

- JavaScript

  {% include [x](../../_includes/feature-not-supported.md) %}

- PHP

  ```php
  echo 1;
  ```

{% endlist %}
"""


def test_structural_delta_preserves_target_when_ops_are_already_satisfied():
    target = AFTER + "\n{% list tabs %}\n\n- C#\n\n  Later bytes\n\n{% endlist %}\n"

    decision = structural_delta_satisfied(BEFORE, AFTER, target)

    assert decision.satisfied
    assert not decision.fail_closed
    assert decision.status is HistoricalDeltaStatus.ALREADY_TRANSLATED


def test_later_current_drift_is_out_of_scope_when_source_ops_are_translated():
    current_ru = AFTER + "\n{% list tabs %}\n\n- C++\n\n  later RU\n\n{% endlist %}\n"
    current_en = AFTER.replace("echo 1;", "echo 'translated';")

    decision = structural_delta_satisfied(
        BEFORE,
        AFTER,
        current_en,
        current_source=current_ru,
    )

    assert decision.satisfied
    assert decision.status is HistoricalDeltaStatus.ALREADY_TRANSLATED


def test_source_operation_removed_from_current_ru_is_superseded():
    before = "{% list tabs %}\n\n- Go\n\n  existing\n\n{% endlist %}\n"
    after = before.replace(
        "{% endlist %}",
        "- PHP\n\n  ```php\n  added();\n  ```\n\n{% endlist %}",
    )

    decision = structural_delta_satisfied(
        before,
        after,
        before,
        current_source=before,
    )

    assert decision.satisfied
    assert decision.status is HistoricalDeltaStatus.SUPERSEDED


def test_surviving_operation_missing_from_en_remains_blocking():
    decision = structural_delta_satisfied(
        BEFORE,
        AFTER,
        BEFORE,
        current_source=AFTER,
    )

    assert not decision.satisfied
    assert decision.status is HistoricalDeltaStatus.MISSING_CURRENT_DELTA


def test_prose_only_historical_delta_superseded_when_source_op_was_removed():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_base_text="Старый текст.\n",
        ru_text="Исторический текст.\n",
        current_ru_text="Более новый русский текст.\n",
        historical_en_text="Old text.\n",
        en_text="Newer English text.\n",
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")
    ctx = SimpleNamespace(docs_text_reader=None, client=MagicMock())

    result = run_pair_plan(
        content,
        plan,
        ctx,
        {},
        historical_merged_provenance=True,
    )

    assert result.skipped
    assert result.historical_disposition == "superseded"
    assert result.target_text == "Newer English text.\n"
    ctx.client.chat.assert_not_called()


def test_pr_37673_structural_delta_skips_when_current_pair_advanced():
    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    pair = DocPair(
        "ydb/docs/ru/core/recipes/ydb-sdk/bulk-upsert.md",
        "ydb/docs/en/core/recipes/ydb-sdk/bulk-upsert.md",
        ru_changed=True,
    )
    current_en = read("en_current.md")
    content = PairContent(
        pair=pair,
        ru_base_text=read("ru_before.md"),
        ru_text=read("ru_after.md"),
        current_ru_text=read("ru_current.md"),
        historical_en_text="Historical EN before the current paired rewrite.\n",
        en_text=current_en,
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")
    ctx = SimpleNamespace(docs_text_reader=None, client=MagicMock())

    result = run_pair_plan(
        content,
        plan,
        ctx,
        {},
        historical_merged_provenance=True,
    )

    assert result.error is None
    assert result.skipped
    assert result.historical_disposition == "already_translated"
    assert result.target_text == current_en
    ctx.client.chat.assert_not_called()


def test_surviving_prose_op_is_not_coarsely_preserved_when_both_blobs_changed():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_base_text="Old source.\n",
        ru_text="Required operation.\n",
        current_ru_text="Required operation.\nLater source addition.\n",
        historical_en_text="Old.\n",
        en_text="Later English addition only.\n",
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")
    ctx = SimpleNamespace(docs_text_reader=None)

    assert _try_deterministic_en_preserve(
        content,
        plan,
        content.ru_text or "",
        content.en_text,
        ctx,
        historical_merged_provenance=True,
    ) is None


def test_both_blobs_advanced_but_required_operation_missing_is_update_required():
    """A newer RU/EN pair is not proof when the historical operation still survives."""
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_base_text="Before source.\n",
        ru_text="Required source line.\n",
        current_ru_text="Required source line.\nLater source line.\n",
        historical_en_text="Before.\n",
        en_text="Later EN line only.\n",
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")

    assert _try_deterministic_en_preserve(
        content,
        plan,
        content.ru_text or "",
        content.en_text,
        SimpleNamespace(docs_text_reader=None),
        historical_merged_provenance=True,
    ) is None


def test_historical_operation_gone_from_current_ru_is_proven_superseded():
    proof = historical_operations_survive(
        "Старое.\n", "Временная операция.\n", "Современный текст.\n"
    )

    assert not proof.survives
    assert "removed or replaced" in proof.reason


def test_pr_50976_client_inline_code_delta_cannot_take_href_shortcut():
    """An unchanged link beside changed inline code is a mixed prose delta."""
    pair = DocPair(
        "ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md",
        "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md",
        ru_changed=True,
    )
    base = "См. [узлы](node.md#auth). Используйте `Имя=Значение`.\n"
    head = "См. [узлы](node.md#auth). Используйте `Name=Value`.\n"
    content = PairContent(
        pair=pair,
        ru_base_text=base,
        ru_text=head,
        current_ru_text=head,
        en_text="See [nodes](node.md#auth). Use `Name=Value`.\n",
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")

    assert _try_deterministic_en_preserve(
        content,
        plan,
        head,
        content.en_text,
        SimpleNamespace(docs_text_reader=None),
        historical_merged_provenance=True,
    ) is None


@pytest.mark.parametrize(
    "path,operation",
    [
        ("balancing-prefer-local.md", "Added balancing recommendation.\n"),
        ("health-check-api.md", "Added health-check response field.\n"),
    ],
)
def test_pr_37673_balancing_and_health_operations_survive_current_ru(
    path: str, operation: str
):
    proof = historical_operations_survive(
        "Historical baseline.\n",
        operation,
        operation + "Later current addition.\n",
    )

    assert path.endswith(".md")
    assert proof.survives


def test_structural_delta_fails_closed_when_later_target_misses_one_op():
    target = """{% list tabs %}

- JavaScript

  {% include [x](../../_includes/feature-not-supported.md) %}

- C#

  Later bytes

{% endlist %}
"""

    decision = structural_delta_satisfied(BEFORE, AFTER, target)

    assert not decision.satisfied
    assert decision.fail_closed
    assert ("pane", "php") in decision.additions


def test_pr_37673_exact_fixtures_preserve_current_and_reject_bad_candidate():
    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    before = read("ru_before.md")
    after = read("ru_after.md")
    current = read("en_current.md")

    accepted = structural_delta_satisfied(before, after, current)
    rejected = structural_delta_satisfied(before, after, read("bad_candidate.md"))

    assert accepted.satisfied
    assert not rejected.satisfied
    assert hashlib.sha256(current.encode()).hexdigest() == (
        "34472c5c6daaf43f730fee98d190d1ebbfc5af234fd37c67246eacd9039581db"
    )
    assert current == read("expected_en.md")

    # Later additions from unrelated PRs must survive byte-for-byte. These
    # language panes were not all present in historical PR #37673.
    for language in ("C++", "C#", "Java"):
        assert f"- {language}" in current


def test_structural_delta_include_replacement_is_satisfied():
    target = AFTER.replace("echo 1;", "echo 'localized';")

    decision = structural_delta_satisfied(BEFORE, AFTER, target)

    assert decision.satisfied
    assert ("include", "javascript", "../../_includes/work-in-progress.md") in decision.removals
    assert (
        "include",
        "javascript",
        "../../_includes/feature-not-supported.md",
    ) in decision.additions


def test_structural_delta_missing_php_with_later_structure_fails_closed():
    target = AFTER.replace(
        "- PHP\n\n  ```php\n  echo 1;\n  ```\n\n", ""
    ) + "\n{% list tabs %}\n\n- Java\n\n  later bytes\n\n{% endlist %}\n"

    decision = structural_delta_satisfied(BEFORE, AFTER, target)

    assert not decision.satisfied
    assert decision.fail_closed
    assert ("pane", "php") in decision.additions


def test_missing_php_fail_closed_preserves_target_and_never_calls_writer():
    target = AFTER.replace(
        "- PHP\n\n  ```php\n  echo 1;\n  ```\n\n", ""
    ) + "\n{% list tabs %}\n\n- Java\n\n  later bytes\n\n{% endlist %}\n"
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_base_text=BEFORE,
        ru_text=AFTER,
        en_text=target,
    )
    plan = PairPlan(
        pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en"
    )
    client = MagicMock()
    ctx = SimpleNamespace(docs_text_reader=None, client=client)

    result = run_pair_plan(
        content, plan, ctx, {}, historical_merged_provenance=True
    )

    assert result.error and "Refusing" not in result.error
    assert "historical structural delta" in result.error
    assert result.target_text == target
    client.chat.assert_not_called()


def test_structural_delta_respects_atom_multiplicity():
    before = "{% list tabs %}\n\n- PHP\n\n  old\n\n{% endlist %}\n"
    after = before.replace("  old", "  ```php\n  one();\n  ```\n\n  ```php\n  two();\n  ```")
    target = before.replace("  old", "  ```php\n  only_one();\n  ```")

    decision = structural_delta_satisfied(before, after, target)

    assert not decision.satisfied
    assert decision.additions.count(("fence", "php", "php")) == 2


def test_structural_delta_nested_duplicate_pane_path_is_ambiguous():
    before = """{% list tabs %}

- SDK

  {% list tabs %}

  - Java

    first

  - Java

    second

  {% endlist %}

{% endlist %}
"""
    after = before.replace("    first", "    ```java\n    first();\n    ```")
    target = before.replace("    second", "    ```java\n    second();\n    ```")

    decision = structural_delta_satisfied(before, after, target)

    assert not decision.satisfied, "nested duplicate panes need stable identity"


def test_structural_delta_does_not_accept_reordered_added_panes():
    before = "{% list tabs %}\n\n- JavaScript\n\n  old\n\n{% endlist %}\n"
    after = """{% list tabs %}

- JavaScript

  old

- PHP

  ```php
  echo 1;
  ```

- Rust

  ```rust
  fn main() {}
  ```

{% endlist %}
"""
    target = after.replace(
        "- PHP\n\n  ```php\n  echo 1;\n  ```\n\n- Rust",
        "- Rust\n\n  ```rust\n  fn main() {}\n  ```\n\n- PHP",
    ).replace(
        "- PHP\n\n  ```rust\n  fn main() {}\n  ```",
        "- PHP\n\n  ```php\n  echo 1;\n  ```",
    )

    decision = structural_delta_satisfied(before, after, target)

    assert not decision.satisfied, "pane order is part of structural identity"


def test_structural_delta_does_not_conflate_same_atoms_across_duplicate_panes():
    before = """{% list tabs %}

- JavaScript

  first

- JavaScript

  second

{% endlist %}
"""
    after = before.replace("  first", "  ```js\n  first();\n  ```")
    target = before.replace("  second", "  ```js\n  second();\n  ```")

    decision = structural_delta_satisfied(before, after, target)

    assert not decision.satisfied, "duplicate pane paths require neighbor identity"


def test_structural_delta_parse_uncertainty_fails_closed(monkeypatch):
    def fail_parse(_text: str):
        raise ValueError("ambiguous nested tabs")

    monkeypatch.setattr(
        "ydbdoc_review.validation.structural_delta.parse_markdown", fail_parse
    )

    decision = structural_delta_satisfied(BEFORE, AFTER, AFTER)

    assert not decision.satisfied
    assert decision.fail_closed
    assert "parse failed" in decision.reason


def test_ordinary_non_historical_structural_change_is_not_blocked_by_gate():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_base_text=BEFORE,
        ru_text=AFTER,
        en_text=BEFORE
        + "\n{% list tabs %}\n\n- Java\n\n  later\n\n{% endlist %}\n",
    )
    plan = PairPlan(
        pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en"
    )
    ctx = SimpleNamespace(docs_text_reader=None)

    preserved = _try_deterministic_en_preserve(
        content, plan, AFTER, content.en_text, ctx
    )

    assert preserved is None


def test_run_pair_plan_does_not_invent_historical_merged_provenance():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_base_text=BEFORE,
        ru_text=AFTER,
        en_text="Existing target.\n",
    )
    plan = PairPlan(
        pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en"
    )
    ctx = SimpleNamespace()

    with patch(
        "ydbdoc_review.harness.pair._try_deterministic_en_preserve",
        return_value=content.en_text,
    ) as preserve:
        result = run_pair_plan(content, plan, ctx, {})

    assert result.error is None
    assert preserve.call_args.kwargs["historical_merged_provenance"] is False
