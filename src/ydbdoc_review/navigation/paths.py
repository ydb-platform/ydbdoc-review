"""Detect navigation YAML paths in ydb/docs layout."""

from __future__ import annotations

from pathlib import PurePosixPath


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name.lower()


def is_toc_yaml(path: str) -> bool:
    """True for Diplodoc ``toc*.yaml`` menu files."""
    name = _basename(path)
    return name.startswith("toc") and name.endswith((".yaml", ".yml"))


def is_redirect_yaml(path: str) -> bool:
    """True for Diplodoc redirect list YAML files."""
    name = _basename(path)
    return "redirect" in name and name.endswith((".yaml", ".yml"))


def is_navigation_yaml(path: str) -> bool:
    return is_toc_yaml(path) or is_redirect_yaml(path)


def navigation_yaml_kind(path: str) -> str | None:
    if is_toc_yaml(path):
        return "toc"
    if is_redirect_yaml(path):
        return "redirect"
    return None
