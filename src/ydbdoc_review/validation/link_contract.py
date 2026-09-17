"""Typed source-owned Markdown link contract results (#51797)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LinkContractCode = Literal[
    "missing_link_wrapper",
    "extra_link_wrapper",
    "reordered_link_wrapper",
    "href_mismatch",
    "autotitle_mismatch",
    "leftover_protect_marker",
    "missing_link_target",
    "missing_target_fragment",
    "ambiguous_link_slot",
    "incomplete_cyrillic_retarget",
]


@dataclass(frozen=True)
class LinkContractIssue:
    code: LinkContractCode
    message: str
    file_path: str = ""
    slot: int | None = None
    href: str | None = None


@dataclass(frozen=True, eq=False)
class LinkContractResult:
    text: str
    issues: tuple[LinkContractIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def __str__(self) -> str:
        return self.text

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LinkContractResult):
            return self.text == other.text and self.issues == other.issues
        if isinstance(other, str):
            return self.text == other
        return NotImplemented

    def __contains__(self, item: str) -> bool:
        return item in self.text

    def count(self, value: str) -> int:
        return self.text.count(value)

    def __iter__(self):
        return iter(self.text)

    def __len__(self) -> int:
        return len(self.text)

    def __getitem__(self, key):
        return self.text[key]

    def __getattr__(self, name: str):
        return getattr(self.text, name)


def coerce_link_contract(value: str | LinkContractResult) -> LinkContractResult:
    """Normalize legacy/test-double string results at the harness boundary."""
    if isinstance(value, LinkContractResult):
        return value
    return LinkContractResult(value)


class LinkContractValidationError(ValueError):
    def __init__(self, issues: tuple[LinkContractIssue, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in issues))
