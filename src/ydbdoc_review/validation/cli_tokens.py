"""CLI flag and shell-variable preservation checks."""

from __future__ import annotations

import re

from ydbdoc_review.validation.markers import PLACEHOLDER_RE

_CLI_FLAG_RE = re.compile(r"(?<![\w`])--[\w][\w-]*")
_SHELL_VAR_RE = re.compile(r"\$[\w]+")


def _text_without_placeholders(text: str) -> str:
    return PLACEHOLDER_RE.sub("", text)


def extract_cli_tokens(text: str) -> set[str]:
    """Extract ``--flag`` tokens and ``$var`` shell variables from prose."""
    cleaned = _text_without_placeholders(text)
    flags = set(_CLI_FLAG_RE.findall(cleaned))
    vars_ = set(_SHELL_VAR_RE.findall(cleaned))
    return flags | vars_


def cli_tokens_preserved(source: str, translated: str) -> bool:
    """True when every CLI/shell token from source appears in translation."""
    tokens = extract_cli_tokens(source)
    return tokens.issubset(extract_cli_tokens(translated))
