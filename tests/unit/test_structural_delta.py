import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.harness.pair import _try_deterministic_en_preserve, run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.validation.structural_delta import structural_delta_satisfied

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
