"""Parse ``/ydbdoc continue`` instructions from PR comments."""

from __future__ import annotations

from typing import Any

CONTINUE_PREFIX = "/ydbdoc continue"
MAX_CONTINUES_PER_PR = 3


def parse_continue_instruction(comment_body: str) -> str | None:
    """Return instruction text if ``comment_body`` is a continue command."""
    if not comment_body:
        return None
    text = comment_body.strip()
    # Allow leading bot mention noise on first line
    lines = text.splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    # Strip optional leading @mention
    if first.startswith("@") and " " in first:
        first = first.split(None, 1)[1].strip()
    lower = first.lower()
    prefix = CONTINUE_PREFIX.lower()
    if not lower.startswith(prefix):
        return None
    rest_first = first[len(CONTINUE_PREFIX) :].strip()
    if len(lines) == 1:
        return rest_first or None
    rest = "\n".join([rest_first, *lines[1:]]).strip() if rest_first else "\n".join(lines[1:]).strip()
    return rest or None


def find_latest_continue_instruction(
    comments: list[dict[str, Any]],
) -> str | None:
    """Newest matching ``/ydbdoc continue`` instruction, or None."""
    ordered = sorted(
        comments,
        key=lambda c: str(c.get("created_at") or ""),
        reverse=True,
    )
    for comment in ordered:
        body = str(comment.get("body") or "")
        instr = parse_continue_instruction(body)
        if instr:
            return instr
    return None
