from __future__ import annotations

import base64
import json
from pathlib import Path

from .pipeline import ManifestEntry


class Pr45949Fixture:
    """Immutable current-main adapter used by the real composition test."""

    def __init__(self, root: Path):
        self.root = root
        self.manifest_doc = json.loads((root / "manifest.json").read_text())
        inventory = json.loads((root / "inventory.json").read_text())
        self.rows = {
            row["path"]: row
            for row in inventory["lookups"]
            if row["scope"] in {"current_main", "current_main_target"}
        }

    @property
    def manifest(self) -> tuple[ManifestEntry, ...]:
        return tuple(
            ManifestEntry(row["filename"], row["status"], row.get("previous_filename"))
            for row in self.manifest_doc["files"]
        )

    def read(self, path: str) -> bytes | None:
        row = self.rows.get(path)
        if not row or not row["present"]:
            return None
        return base64.b64decode((self.root / row["fixture"]).read_bytes())
