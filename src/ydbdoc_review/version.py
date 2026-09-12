"""Release label for reports and commit messages (CI / Docker metadata)."""

from __future__ import annotations

import os
import subprocess


def _short_sha(sha: str) -> str:
    sha = sha.strip()
    if len(sha) >= 7:
        return sha[:7]
    return sha or "dev"


def _git_head_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def action_release_label() -> str:
    """Label matching the Action ref CI uses (e.g. ``v0.1.0 @ a1e8e92``).

    - ``GITHUB_ACTION_REF`` — Action ref from the workflow (``v0.1.0``, branch name).
    - ``YDBDOC_GIT_SHA`` — image build revision (Docker ``ARG`` / ``ENV``).
    """
    ref = os.environ.get("GITHUB_ACTION_REF", "").strip()
    sha = os.environ.get("YDBDOC_GIT_SHA", "").strip()
    if sha and sha != "dev" and not sha.startswith("v"):
        short = _short_sha(sha)
    elif sha and sha != "dev":
        short = sha
    else:
        short = _short_sha(_git_head_sha() or "") or "dev"
    if ref:
        return f"ydbdoc-review {ref} @ {short}"
    if short != "dev":
        return f"ydbdoc-review @ {short}"
    return "ydbdoc-review dev"
