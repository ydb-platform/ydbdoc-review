"""P6 / §7: glossary term RED hard-gates publication like residual Cyrillic."""

from __future__ import annotations

from ydbdoc_review.pipeline.qa import compose_file_verdict
from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry
from ydbdoc_review.validation.heuristics import (
    ClassifiedHeuristics,
    bump_verdict_for_blocking_heuristics,
    check_cyrillic_in_en,
    check_glossary_term_violations,
    check_unrestored_placeholders,
    run_file_heuristics_classified,
)


def _mini_glossary() -> Glossary:
    return Glossary(
        entries=[
            GlossaryEntry(ru="планшет", en="tablet", aliases_ru=["планшета", "планшеты"]),
            GlossaryEntry(ru="узел", en="node", aliases_ru=["узла", "узлы"]),
            GlossaryEntry(term="YDB", do_not_translate=True),
        ]
    )


def test_glossary_unset_is_noop():
    text = "The планшет stores rows.\n"
    assert check_glossary_term_violations(text, target_lang="en", glossary=None) == []
    assert (
        check_glossary_term_violations(
            text, target_lang="en", glossary=Glossary(entries=[])
        )
        == []
    )


def test_glossary_leftover_ru_term_is_blocking():
    glossary = _mini_glossary()
    text = "Each планшет stores a shard of the table.\n"
    msgs = check_glossary_term_violations(text, target_lang="en", glossary=glossary)
    assert len(msgs) == 1
    assert msgs[0].startswith("glossary_violation:")
    assert "планшет" in msgs[0]
    assert "tablet" in msgs[0]

    classified = run_file_heuristics_classified(
        "Каждый планшет хранит часть таблицы.\n",
        text,
        normalized_source_text="Каждый планшет хранит часть таблицы.\n",
        source_lang="ru",
        target_lang="en",
        glossary=glossary,
    )
    assert any(m.startswith("glossary_violation:") for m in classified.blocking)
    assert bump_verdict_for_blocking_heuristics("ok", classified.blocking) == "blocked"
    assert (
        compose_file_verdict(
            critic_verdict="ok",
            alignment_error=None,
            heuristics=classified,
            manual_actions=False,
        )
        == "blocked"
    )


def test_glossary_correct_en_is_green():
    glossary = _mini_glossary()
    text = "Each tablet stores a shard of the table.\n"
    assert check_glossary_term_violations(text, target_lang="en", glossary=glossary) == []
    classified = run_file_heuristics_classified(
        "Каждый планшет хранит часть таблицы.\n",
        text,
        normalized_source_text="Каждый планшет хранит часть таблицы.\n",
        source_lang="ru",
        target_lang="en",
        glossary=glossary,
    )
    assert not any(m.startswith("glossary_violation:") for m in classified.blocking)
    assert (
        compose_file_verdict(
            critic_verdict="ok",
            alignment_error=None,
            heuristics=classified,
            manual_actions=False,
        )
        == "ok"
    )


def test_residual_cyrillic_still_blocks_publication():
    msgs = check_cyrillic_in_en("Hello привет world\n", target_lang="en")
    assert msgs
    classified = ClassifiedHeuristics(blocking=list(msgs))
    assert bump_verdict_for_blocking_heuristics("ok", classified.blocking) == "blocked"
    assert (
        compose_file_verdict(
            critic_verdict="ok",
            alignment_error=None,
            heuristics=classified,
            manual_actions=False,
        )
        == "blocked"
    )


def test_unrestored_protect_marker_still_blocks_publication():
    text = "The cluster uses ⟦V1⟧ placeholders.\n"
    msgs = check_unrestored_placeholders(text, target_lang="en")
    assert msgs and msgs[0].startswith("unrestored_placeholder:")
    classified = run_file_heuristics_classified(
        "Кластер.\n",
        text,
        normalized_source_text="Кластер.\n",
        source_lang="ru",
        target_lang="en",
        glossary=None,
    )
    assert any(m.startswith("unrestored_placeholder:") for m in classified.blocking)
    assert (
        compose_file_verdict(
            critic_verdict="ok",
            alignment_error=None,
            heuristics=classified,
            manual_actions=False,
        )
        == "blocked"
    )
