"""One job-global admission budget for synthetic Markdown dependencies."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

MAX_EXTRA_MARKDOWN_DEPENDENCIES = 20

DEPENDENCY_LIMIT_WARNING = (
    "link dependency budget exhausted ({limit}): missing EN for {target}; "
    "manual action required — translate or add EN mirror manually"
)

DEPENDENCY_RECOVERY_WARNING = (
    "{target}: не удалось восстановить доступный бюджет; {reason}; "
    "manual action required"
)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


@dataclass(frozen=True)
class MarkdownDependencyBudgetState:
    """Durable in-process evidence for one planner/admission lifecycle."""

    limit: int = MAX_EXTRA_MARKDOWN_DEPENDENCIES
    root_ru_paths: frozenset[str] = frozenset()
    admitted_ru_paths: frozenset[str] = frozenset()
    denied_ru_paths: frozenset[str] = frozenset()
    uncertain_ru_paths: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()


class MarkdownDependencyBudget:
    """Admit at most ``limit`` unique non-root RU Markdown identities.

    ``uncertain_ru_paths`` contains only source-linked late owners whose
    producer admission cannot be reconstructed exactly.  It does not invent
    spent slots.  While uncertainty exists, known roots and proven admissions
    remain usable, but new extras are conservatively refused.
    """

    def __init__(
        self,
        roots: Iterable[str] = (),
        *,
        limit: int = MAX_EXTRA_MARKDOWN_DEPENDENCIES,
    ) -> None:
        if limit < 0:
            raise ValueError("Markdown dependency budget cannot be negative")
        self.limit = limit
        self._roots = {_norm(path) for path in roots}
        self._admitted: set[str] = set()
        self._denied: set[str] = set()
        self._uncertain: set[str] = set()
        self._warnings: list[str] = []
        self._warning_targets: set[str] = set()

    @classmethod
    def from_state(cls, state: MarkdownDependencyBudgetState) -> Self:
        budget = cls(state.root_ru_paths, limit=state.limit)
        budget._admitted = set(state.admitted_ru_paths)
        budget._denied = set(state.denied_ru_paths)
        budget._uncertain = set(state.uncertain_ru_paths)
        budget._warnings = list(state.warnings)
        budget._warning_targets = set(state.denied_ru_paths)
        return budget

    @property
    def admitted_ru_paths(self) -> frozenset[str]:
        return frozenset(self._admitted)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    def _warn_once(self, identity: str, warning: str) -> None:
        if identity in self._warning_targets:
            return
        self._warning_targets.add(identity)
        self._warnings.append(warning)

    def admit(self, ru_path: str, *, warning_path: str | None = None) -> bool:
        """Admit ``ru_path`` before it is added to scope or traversal."""
        identity = _norm(ru_path)
        if identity in self._roots or identity in self._admitted:
            return True
        if identity in self._denied:
            return False
        target = _norm(warning_path or identity)
        if self._uncertain:
            self._denied.add(identity)
            reason = (
                "source-linked late admission evidence is ambiguous for "
                + ", ".join(sorted(self._uncertain))
            )
            self._warn_once(
                identity,
                DEPENDENCY_RECOVERY_WARNING.format(target=target, reason=reason),
            )
            return False
        if len(self._admitted) >= self.limit:
            self._denied.add(identity)
            self._warn_once(
                identity,
                DEPENDENCY_LIMIT_WARNING.format(limit=self.limit, target=target),
            )
            return False
        self._admitted.add(identity)
        return True

    def mark_uncertain(self, ru_path: str, *, reason: str) -> None:
        """Record a source-linked ambiguous late owner without faking a slot."""
        identity = _norm(ru_path)
        if identity in self._roots or identity in self._admitted:
            return
        self._uncertain.add(identity)

    def replay_proven(self, ru_path: str) -> bool:
        """Replay an artifact-proven admission without manufacturing warnings."""
        identity = _norm(ru_path)
        if identity in self._roots or identity in self._admitted:
            return True
        if (
            identity in self._denied
            or len(self._admitted) >= self.limit
        ):
            return False
        self._admitted.add(identity)
        return True

    def snapshot(self) -> MarkdownDependencyBudgetState:
        return MarkdownDependencyBudgetState(
            limit=self.limit,
            root_ru_paths=frozenset(self._roots),
            admitted_ru_paths=frozenset(self._admitted),
            denied_ru_paths=frozenset(self._denied),
            uncertain_ru_paths=frozenset(self._uncertain),
            warnings=tuple(self._warnings),
        )
