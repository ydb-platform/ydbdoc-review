"""Fix Cyrillic letters that look like Latin in mostly-ASCII EN lines (YAML comments)."""

from __future__ import annotations

import re

# Common Cyrillic → Latin confusables in technical text (not full alphabet).
_CYRILLIC_TO_LATIN: dict[int, str] = {
    ord("А"): "A",
    ord("В"): "V",  # often confused with B; in FQDN «ВМ» means VM
    ord("Е"): "E",
    ord("К"): "K",
    ord("М"): "M",
    ord("Н"): "H",
    ord("О"): "O",
    ord("Р"): "P",
    ord("С"): "C",
    ord("Т"): "T",
    ord("У"): "Y",
    ord("Х"): "X",
    ord("а"): "a",
    ord("е"): "e",
    ord("о"): "o",
    ord("р"): "p",
    ord("с"): "c",
    ord("у"): "y",
    ord("х"): "x",
}


def _ascii_ratio(text: str) -> float:
    if not text:
        return 1.0
    ascii_count = sum(1 for ch in text if ord(ch) < 128)
    return ascii_count / len(text)


def _line_should_fix_homoglyphs(line: str) -> bool:
    """True when the line is config/comment-like, not prose in Russian."""
    stripped = line.strip()
    if not stripped:
        return False
    if _ascii_ratio(stripped) < 0.75:
        return False
    if "#" in stripped and any(
        hint in stripped for hint in ("FQDN", "host:", "node_id:", "pdisk_", "host_config")
    ):
        return True
    if stripped.startswith(("-", "host:", "  -", "    -")) and "#" in stripped:
        return True
    return False


def fix_cyrillic_homoglyphs_in_en(text: str) -> str:
    """Replace look-alike Cyrillic letters with Latin on ASCII-heavy lines."""
    table = str.maketrans(_CYRILLIC_TO_LATIN)
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n\r")
        suffix = line[len(body) :]
        if _line_should_fix_homoglyphs(body):
            out.append(body.translate(table) + suffix)
        else:
            out.append(line)
    return "".join(out)


# Diplodoc often indents fences inside list items (leading spaces before ```).
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
_CYRILLIC_IN_ANGLE = re.compile(r"[а-яА-ЯёЁ]")
# Russian angle-bracket placeholders in shell examples (RU source often uses these).
_ANGLE_PLACEHOLDER_EN: dict[str, str] = {
    "строка": "string",
    "значение": "value",
    "имя": "name",
    "путь": "path",
    "адрес": "address",
    "описание ошибки": "error description",
    "uuid": "uuid",
}


def _fix_angle_placeholders_on_line(line: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        if not _CYRILLIC_IN_ANGLE.search(inner):
            return match.group(0)
        en = _ANGLE_PLACEHOLDER_EN.get(inner.lower())
        if en is None:
            return match.group(0)
        return f"<{en}>"

    return re.sub(r"<([^<>]+)>", repl, line)


def fix_russian_angle_placeholders_in_en(text: str) -> str:
    """Map RU ``<путь>``-style placeholders to EN in fences and prose/backticks."""
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
            out.append(line)
            continue
        body = line.rstrip("\n\r")
        suffix = line[len(body) :]
        out.append(_fix_angle_placeholders_on_line(body) + suffix)
    return "".join(out)


def fix_russian_angle_placeholders_in_en_fences(text: str) -> str:
    """Backward-compatible alias — placeholders are fixed in prose too (§6.71)."""
    return fix_russian_angle_placeholders_in_en(text)


def postprocess_en_target_markdown(text: str) -> str:
    """Homoglyphs, fence placeholders, and markdownlint-friendly fence spacing."""
    from ydbdoc_review.validation.markdown_layout import fix_blanks_around_fences

    text = fix_cyrillic_homoglyphs_in_en(text)
    text = fix_russian_angle_placeholders_in_en(text)
    return fix_blanks_around_fences(text)
