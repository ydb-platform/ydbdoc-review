"""Deterministic glossary-term RED gate (§7 / P6)."""

from __future__ import annotations

import re

from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry

_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")


def _strip_fenced_blocks(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    fence_char = ""
    for line in lines:
        m = _FENCE_OPEN.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if not in_fence:
            out.append(line)
    return "".join(out)


def _glossary_ru_forms(entry: GlossaryEntry) -> list[str]:
    forms: list[str] = []
    if entry.ru:
        forms.append(entry.ru)
    forms.extend(a for a in entry.aliases_ru if a)
    # Longer forms first so «база данных» wins over a hypothetical shorter alias.
    return sorted(dict.fromkeys(forms), key=len, reverse=True)


def _contains_glossary_form(haystack: str, form: str) -> bool:
    """Whole-token match for a glossary RU form in prose."""
    if not form or not haystack:
        return False
    return (
        re.search(
            r"(?<![0-9A-Za-zА-Яа-яЁё_])"
            + re.escape(form)
            + r"(?![0-9A-Za-zА-Яа-яЁё_])",
            haystack,
            flags=re.IGNORECASE,
        )
        is not None
    )


def check_glossary_term_violations(
    target_text: str,
    *,
    target_lang: str,
    glossary: Glossary | None,
) -> list[str]:
    """Blocking RED when configured glossary RU terms leak into EN prose.

    No-op when glossary is unset or empty. ``do_not_translate`` / ``term`` rows
    are skipped. Overlaps residual Cyrillic on purpose: glossary miss must be an
    explicit hard gate, not prompt-only critic terminology.
    """
    if glossary is None or not glossary.entries:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []
    body = _strip_fenced_blocks(target_text)
    body = re.sub(r"\{#[^}\s]+\}", "", body)
    found: list[str] = []
    seen: set[str] = set()
    for entry in glossary.entries:
        if entry.do_not_translate or entry.term is not None:
            continue
        if not entry.ru or not entry.en:
            continue
        for form in _glossary_ru_forms(entry):
            if not _contains_glossary_form(body, form):
                continue
            key = form.casefold()
            if key in seen:
                break
            seen.add(key)
            found.append(
                f"glossary_violation: leftover RU «{form}» "
                f"(expected EN «{entry.en}»)"
            )
            break
    if not found:
        return []
    out = found[:12]
    if len(found) > 12:
        out.append(f"glossary_violation: … (+{len(found) - 12} more)")
    return out
