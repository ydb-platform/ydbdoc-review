"""Persisted job continuability state (§2 / P7).

``doc_continue`` is allowed only when a state artifact explicitly records
``continuable=true``, a non-empty ``unfinished_stage``, and fixed SHAs from a
successful SHA freeze. Other stops require a fresh ``doc_translate``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_DIRNAME = ".ydbdoc-state"
CONTINUABILITY_STORE_KEY = "job/continuability.json"


@dataclass
class ContinuabilityState:
    """Explicit continuability artifact for a source PR job."""

    continuable: bool
    unfinished_stage: str | None
    fixed_shas: dict[str, str] = field(default_factory=dict)
    source_pr: int = 0
    translation_pr: int | None = None
    updated_at: str = ""

    def allows_continue(self) -> bool:
        return (
            self.continuable
            and bool(self.unfinished_stage)
            and bool(self.fixed_shas)
            and self.source_pr > 0
        )


def state_path(repo_path: str | Path, source_pr: int) -> Path:
    return Path(repo_path) / STATE_DIRNAME / f"pr-{source_pr}.json"


def load_continuability(repo_path: str | Path, source_pr: int) -> ContinuabilityState | None:
    path = state_path(repo_path, source_pr)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read continuability state %s: %s", path, exc)
        return None
    return _from_dict(raw)


def load_continuability_from_bytes(data: bytes | str | None) -> ContinuabilityState | None:
    if not data:
        return None
    try:
        raw = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to parse continuability payload: %s", exc)
        return None
    return _from_dict(raw)


def save_continuability(
    repo_path: str | Path,
    state: ContinuabilityState,
) -> Path:
    """Write state under ``.ydbdoc-state/``; return the file path."""
    if not state.updated_at:
        state.updated_at = datetime.now(timezone.utc).isoformat()
    path = state_path(repo_path, state.source_pr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def clear_continuability(repo_path: str | Path, source_pr: int) -> Path | None:
    """Mark job non-continuable (terminal success/failure). Returns path if written."""
    existing = load_continuability(repo_path, source_pr)
    fixed = dict(existing.fixed_shas) if existing else {}
    state = ContinuabilityState(
        continuable=False,
        unfinished_stage=None,
        fixed_shas=fixed,
        source_pr=source_pr,
        translation_pr=existing.translation_pr if existing else None,
    )
    return save_continuability(repo_path, state)


def mark_continuable(
    repo_path: str | Path,
    *,
    source_pr: int,
    unfinished_stage: str,
    fixed_shas: dict[str, str],
    translation_pr: int | None = None,
) -> Path | None:
    """Set continuable flag after SHA freeze with an unfinished stage."""
    if not unfinished_stage or not fixed_shas or source_pr <= 0:
        return None
    state = ContinuabilityState(
        continuable=True,
        unfinished_stage=unfinished_stage,
        fixed_shas=dict(fixed_shas),
        source_pr=source_pr,
        translation_pr=translation_pr,
    )
    return save_continuability(repo_path, state)


def dump_continuability_json(state: ContinuabilityState) -> str:
    payload = asdict(state)
    if not payload.get("updated_at"):
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def relative_state_path(source_pr: int) -> str:
    return f"{STATE_DIRNAME}/pr-{source_pr}.json"


def _from_dict(raw: dict[str, Any]) -> ContinuabilityState | None:
    try:
        fixed = raw.get("fixed_shas") or {}
        if not isinstance(fixed, dict):
            fixed = {}
        return ContinuabilityState(
            continuable=bool(raw.get("continuable")),
            unfinished_stage=(
                str(raw["unfinished_stage"])
                if raw.get("unfinished_stage") is not None
                else None
            ),
            fixed_shas={str(k): str(v) for k, v in fixed.items()},
            source_pr=int(raw.get("source_pr") or 0),
            translation_pr=(
                int(raw["translation_pr"])
                if raw.get("translation_pr") is not None
                else None
            ),
            updated_at=str(raw.get("updated_at") or ""),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid continuability state: %s", exc)
        return None
