"""Allow YFM {{ variable }} inside markdown link URLs and image src.

Strategy: a core preprocess rule rewrites the source before tokenization,
substituting `{{ name }}` inside `[...](...)` with a placeholder that
markdown-it accepts as a normal URL. A core postprocess rule then walks
the tokens and restores the original variable text on `link_open` href
and `image` src attributes.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore


# Placeholder pattern: we use a token that markdown-it accepts in URLs.
# Format: yfmvar-{index}- (lowercase, alphanumeric + dash, URL-safe).
_PLACEHOLDER_PREFIX = "yfmvar-"
_PLACEHOLDER_SUFFIX = "-yfmvarend"
_PLACEHOLDER_RE = re.compile(
    rf"{re.escape(_PLACEHOLDER_PREFIX)}(\d+){re.escape(_PLACEHOLDER_SUFFIX)}"
)

# Find [text](url) where url contains {{ ... }}.
# The URL part allows {{...}}, slashes, letters, digits, dashes, dots.
# We match the minimal viable shape.
_LINK_WITH_VAR_RE = re.compile(
    r"(\[[^\]]*\]\()([^)]*?)(\))"
)

_VAR_RE = re.compile(r"\{\{\s*[\w\-\.]+\s*\}\}")


def _preprocess_substitute(state: StateCore) -> None:
    """Replace {{ var }} inside link URLs with placeholders BEFORE tokenization."""
    src = state.src
    substitutions: list[str] = []

    def replace_in_url(m: re.Match[str]) -> str:
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)

        def _sub(vm: re.Match[str]) -> str:
            idx = len(substitutions)
            substitutions.append(vm.group(0))
            return f"{_PLACEHOLDER_PREFIX}{idx}{_PLACEHOLDER_SUFFIX}"

        new_url = _VAR_RE.sub(_sub, url)
        return f"{prefix}{new_url}{suffix}"

    new_src = _LINK_WITH_VAR_RE.sub(replace_in_url, src)
    state.src = new_src
    state.env["__yfm_var_substitutions__"] = substitutions


def _restore_in_tokens(state: StateCore) -> None:
    """Walk tokens and restore original {{ var }} inside link href and image src."""
    substitutions: list[str] = state.env.get("__yfm_var_substitutions__", [])
    if not substitutions:
        return

    def restore(text: str) -> str:
        def _back(m: re.Match[str]) -> str:
            idx = int(m.group(1))
            if 0 <= idx < len(substitutions):
                return substitutions[idx]
            return m.group(0)

        return _PLACEHOLDER_RE.sub(_back, text)

    for token in state.tokens:
        if token.type == "inline" and token.children:
            for child in token.children:
                if child.type == "link_open":
                    href = child.attrGet("href")
                    if href and _PLACEHOLDER_PREFIX in href:
                        child.attrSet("href", restore(href))
                elif child.type == "image":
                    src = child.attrGet("src")
                    if src and _PLACEHOLDER_PREFIX in src:
                        child.attrSet("src", restore(src))


def yfm_link_with_variable_plugin(md: MarkdownIt) -> None:
    """Register pre- and post-processing rules for variables inside link URLs."""
    md.core.ruler.before(
        "normalize",
        "yfm_var_substitute",
        _preprocess_substitute,
    )
    md.core.ruler.after(
        "inline",
        "yfm_var_restore",
        _restore_in_tokens,
    )

