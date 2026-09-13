"""Independent language check of final candidate text, without parser exemptions."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable, Iterable

from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    PRTranslationResult,
)


def check_final_en_language(text: str, *, target_lang: str = "en") -> list[str]:
    """Inspect the original string, including protected syntax and its payload."""
    if target_lang.casefold() not in {"en", "english"}:
        return []
    lines = [
        (number, line)
        for number, line in enumerate(text.splitlines(), 1)
        if any(unicodedata.name(char, "").startswith("CYRILLIC") for char in line)
    ]
    messages = [f"en_language: line {number}: {line[:120]}" for number, line in lines[:12]]
    if len(lines) > 12:
        messages.append(f"en_language: {len(lines) - 12} additional lines")
    return messages


def apply_final_en_language_gate(
    result: PRTranslationResult,
    *,
    en_paths: Iterable[str],
    read_text: Callable[[str], str | None],
) -> list[str]:
    """Bind language findings to explicit candidate bytes without rewriting them.

    Empty text is authoritative. Missing/deleted paths have no language finding;
    output completeness is a separate gate. Unknown paths retain their evidence.
    """
    paths = sorted(set(en_paths))
    found = []
    for path in paths:
        text = read_text(path)
        if text is None:
            continue
        result.final_tree_blockers = [
            blocker
            for blocker in result.final_tree_blockers
            if blocker.code != "en_language" or blocker.path != path
        ]
        messages = check_final_en_language(text)
        if messages:
            found.append(path)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            result.final_tree_blockers.extend(
                FinalTreeBlocker(path, "en_language", message, digest) for message in messages
            )
        mismatch = (
            f"report_checkout_mismatch: final QA text differs from authoritative candidate `{path}`"
        )
        for run in result.pair_results:
            if run.plan.target_path != path or run.deleted:
                continue
            fr = run.file_result
            if fr is None and (messages or run.target_text != text):
                fr = FileTranslationResult(
                    file_path=path,
                    final_text=run.target_text or "",
                    segments_count=0,
                    verdict="ok",
                    prompt_version="final-language",
                )
                run.file_result = fr
            if fr is None:
                continue
            had_language = any(m.startswith("en_language:") for m in fr.heuristic_blocking)
            fr.heuristic_blocking = [
                m for m in fr.heuristic_blocking if not m.startswith("en_language:")
            ]
            fr.heuristic_blocking.extend(messages)
            if run.target_text != text or fr.final_text != text:
                if mismatch not in fr.heuristic_blocking:
                    fr.heuristic_blocking.append(mismatch)
            if fr.heuristic_blocking:
                fr.verdict = "blocked"
            elif had_language:
                # Remove only the verdict contribution owned by this gate.
                from ydbdoc_review.harness.critic_verdict import compute_critic_verdict

                critic = compute_critic_verdict(
                    initial=fr.critic_initial, unresolved=fr.critic_unresolved
                )
                fr.verdict = (
                    "blocked"
                    if fr.segment_alignment_error
                    or fr.link_contract_issues
                    or run.validation_issues
                    or critic == "blocked"
                    else "warnings"
                    if fr.heuristic_warnings or fr.manual_actions or critic == "warnings"
                    else "ok"
                )
        for nav in result.navigation_results:
            if nav.en_path != path:
                continue
            had_language = any(m.startswith("en_language:") for m in nav.warnings)
            if messages and nav.language_gate_prior_verdict is None:
                nav.language_gate_prior_verdict = nav.verdict
            nav.warnings = [m for m in nav.warnings if not m.startswith("en_language:")]
            nav.warnings.extend(messages)
            if nav.target_text != text:
                if mismatch not in nav.warnings:
                    nav.warnings.append(mismatch)
            if messages or mismatch in nav.warnings:
                nav.verdict = "blocked"
            elif had_language:
                independent_blocker = nav.error or nav.heuristic_blocking or any(
                    blocker.path == path and blocker.code != "en_language"
                    for blocker in result.final_tree_blockers
                )
                nav.verdict = (
                    "blocked"
                    if independent_blocker or nav.language_gate_prior_verdict == "blocked"
                    else "warnings"
                    if nav.warnings or nav.language_gate_prior_verdict == "warnings"
                    else "ok"
                )
                nav.language_gate_prior_verdict = None
    return found
