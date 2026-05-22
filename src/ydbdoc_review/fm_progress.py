"""Timestamped progress logs for Foundation Models calls (GitHub Actions / CLI)."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Iterator


def fm_progress_enabled() -> bool:
    raw = os.environ.get("YDBDOC_FM_PROGRESS_LOG", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def fm_log(message: str) -> None:
    if not fm_progress_enabled():
        return
    ts = time.strftime("%H:%M:%S")
    print(f"[ydbdoc-fm {ts}] {message}", file=sys.stderr, flush=True)


def fm_http_timeout_sec() -> float:
    raw = os.environ.get("YDBDOC_FM_HTTP_TIMEOUT_SEC", "").strip()
    if raw.replace(".", "", 1).isdigit():
        return max(30.0, float(raw))
    return 600.0


@contextmanager
def fm_call_span(
    *,
    operation: str,
    model: str,
    detail: str = "",
) -> Iterator[None]:
    """Log start/end/duration of one FM request; re-raise after logging failures."""
    label = operation
    if detail:
        label = f"{operation} ({detail})"
    t0 = time.monotonic()
    fm_log(f"→ {label} | model={model}")
    try:
        yield
    except Exception as exc:
        elapsed = time.monotonic() - t0
        fm_log(f"✗ {label} | model={model} | {elapsed:.1f}s | {type(exc).__name__}: {exc}")
        raise
    else:
        elapsed = time.monotonic() - t0
        fm_log(f"✓ {label} | model={model} | {elapsed:.1f}s")
