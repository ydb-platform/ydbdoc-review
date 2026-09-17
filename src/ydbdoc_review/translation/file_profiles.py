"""Per-file translation profiles (glossary, default, …)."""

from __future__ import annotations

import re

GLOSSARY_PROFILE = "glossary"
DEFAULT_PROFILE = "default"

_GLOSSARY_PATH = re.compile(
    r"(?:^|/)concepts/glossary\.md$",
    re.IGNORECASE,
)


def detect_file_profile(file_path: str) -> str:
    """Return the harness profile name for ``file_path``."""
    normalized = file_path.replace("\\", "/").strip()
    if _GLOSSARY_PATH.search(normalized):
        return GLOSSARY_PROFILE
    return DEFAULT_PROFILE


def is_glossary_file(file_path: str) -> bool:
    return detect_file_profile(file_path) == GLOSSARY_PROFILE
