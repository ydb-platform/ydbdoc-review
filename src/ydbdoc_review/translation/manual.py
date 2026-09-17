"""Types for human follow-up when automatic translation cannot finish."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManualAction:
    """One segment the reviewer must fix by hand."""

    segment_id: str
    location: str
    message: str
