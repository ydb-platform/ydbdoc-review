"""Diplodoc image size syntax: ![alt](src =WxH).

Strategy: pre-process the source — strip ` =WxH` from inside the image URL
parentheses so markdown-it parses it as a normal image, then attach width/height
to the resulting image token via env-keyed map.

The size suffix is matched only inside image markup `![...]()` — never in
ordinary link markup `[...]()` or unrelated `=NxM` text.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore


# Marker the image URL will end with after preprocess: unique, URL-safe.
# Format: yfmimgsize-{index}- — alphanumeric + dash, accepted by markdown-it as URL.
_PLACEHOLDER_PREFIX = "yfmimgsize-"
_PLACEHOLDER_SUFFIX = "-yfmimgsizeend"
_PLACEHOLDER_RE = re.compile(
    rf"{re.escape(_PLACEHOLDER_PREFIX)}(\d+){re.escape(_PLACEHOLDER_SUFFIX)}"
)

# Match an image markup ![alt](URL ...) and extract a trailing =WxH inside the URL.
# We allow optional " "title"" after the size.
_IMAGE_WITH_SIZE_RE = re.compile(
    r"""
    (?P<prefix>!\[[^\]]*\]\()      # ![alt](
    (?P<url>[^)\s]*)               # URL up to the first space or ')'
    \s+=(?P<w>\d*)x(?P<h>\d*)      #  =WxH
    (?P<rest>\s*(?:"[^"]*")?\s*)   # optional title
    (?P<suffix>\))                 # )
    """,
    re.VERBOSE,
)


def _preprocess_image_size(state: StateCore) -> None:
    """Rewrite ![alt](src =WxH) → ![alt](src<PLACEHOLDER>...) before tokenization."""
    src = state.src
    sizes: list[tuple[str, str]] = []  # (width, height) per index

    def repl(m: re.Match[str]) -> str:
        idx = len(sizes)
        sizes.append((m.group("w"), m.group("h")))
        marker = f"{_PLACEHOLDER_PREFIX}{idx}{_PLACEHOLDER_SUFFIX}"
        new_url = m.group("url") + marker
        return f"{m.group('prefix')}{new_url}{m.group('rest')}{m.group('suffix')}"

    new_src = _IMAGE_WITH_SIZE_RE.sub(repl, src)
    if new_src != src:
        state.src = new_src
    state.env["__yfm_image_sizes__"] = sizes


def _restore_image_size(state: StateCore) -> None:
    """Walk tokens; strip placeholder from image src and attach width/height meta."""
    sizes: list[tuple[str, str]] = state.env.get("__yfm_image_sizes__", [])
    if not sizes:
        return

    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type != "image":
                continue
            src = child.attrGet("src") or ""
            m = _PLACEHOLDER_RE.search(src)
            if not m:
                continue
            idx = int(m.group(1))
            if not (0 <= idx < len(sizes)):
                continue
            w, h = sizes[idx]
            new_src = src[: m.start()] + src[m.end() :]
            child.attrSet("src", new_src)
            if child.meta is None:
                child.meta = {}
            child.meta["width"] = w
            child.meta["height"] = h


def yfm_image_size_plugin(md: MarkdownIt) -> None:
    """Register pre- and post-processing for image size attributes."""
    md.core.ruler.before(
        "normalize",
        "yfm_image_size_pre",
        _preprocess_image_size,
    )
    md.core.ruler.after(
        "inline",
        "yfm_image_size_post",
        _restore_image_size,
    )
