"""Fix Cyrillic letters that look like Latin in mostly-ASCII EN lines (YAML comments)."""

from __future__ import annotations

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
