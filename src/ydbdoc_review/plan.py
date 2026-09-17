"""Translation preflight: one immutable source tree and explicit mirror operations.

No model, worktree mutation or legacy source-authority heuristics live here.
``read`` callbacks take (sha, path) and return exact text or None for absence.
"""

# ruff: noqa: RUF001 -- User-facing diagnostics are Russian.
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ydbdoc_review.config.loader import Settings
    from ydbdoc_review.github.client import GitHubClient

Language = Literal["ru", "en"]
Kind = Literal["added", "modified", "deleted", "renamed"]
Reader = Callable[[str, str], str | None]


class PlanError(RuntimeError):
    """Preflight failed; the runner must report RED before creating a model."""


def _sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        raise PlanError(f"Ожидался неизменяемый SHA коммита: {value!r}")
    return value


@dataclass(frozen=True)
class Snapshot:
    owner: str
    repo: str
    pr_number: int
    source_sha: str
    head_sha: str
    publication_base: str
    source_repo: str
    merged: bool = False

    def __post_init__(self) -> None:
        for sha in (self.source_sha, self.head_sha):
            _sha(sha)


def freeze_snapshot(client: GitHubClient, owner: str, repo: str, pr_number: int) -> Snapshot:
    data = client.get_pull(owner, repo, pr_number)
    head, base = data["head"], data["base"]
    merged = bool(data.get("merged"))
    if not merged and data.get("state", "open") != "open":
        raise PlanError("Исходный PR закрыт без слияния")
    source_sha = client.get_branch_sha(owner, repo, base["ref"]) if merged else head["sha"]
    if not source_sha:
        raise PlanError(f"Не удалось зафиксировать базовую ветку {base['ref']}")
    return Snapshot(
        owner,
        repo,
        pr_number,
        source_sha,
        head["sha"],
        base["ref"] if merged else head["ref"],
        f"{owner}/{repo}" if merged else head["repo"]["full_name"],
        merged,
    )


@dataclass(frozen=True)
class ChangedFile:
    path: str
    kind: Kind
    previous_path: str | None = None
    content_changed: bool | None = None  # original PR diff, required for selected renames

    def __post_init__(self) -> None:
        if self.content_changed is not None and type(self.content_changed) is not bool:
            raise PlanError(f"Некорректный content_changed для {self.path}")


def list_changes(client: GitHubClient, owner: str, repo: str, pr_number: int) -> list[ChangedFile]:
    """Preserve GitHub rename metadata; never turn a failed listing into no work."""
    changes = []
    for item in client.iter_pull_files(owner, repo, pr_number):
        path, status = item.get("filename"), item.get("status")
        kind = {"removed": "deleted", "changed": "modified"}.get(status, status)
        if (
            not isinstance(path, str)
            or not path
            or kind not in {"added", "modified", "deleted", "renamed"}
        ):
            raise PlanError("Некорректный ответ списка файлов GitHub")
        previous = item.get("previous_filename")
        if kind == "renamed" and (not isinstance(previous, str) or not previous):
            raise PlanError(f"GitHub не вернул previous_filename для {path}")
        content_changed = kind != "deleted"
        if kind == "renamed":
            counts = [item.get(name) for name in ("changes", "additions", "deletions")]
            if (
                any(type(count) is not int or count < 0 for count in counts)
                or counts[0] != counts[1] + counts[2]
            ):
                raise PlanError(
                    f"GitHub не вернул достоверные changes/additions/deletions для {path}; "
                    "невозможно определить изменение содержимого при переименовании"
                )
            content_changed = counts[0] > 0
        changes.append(ChangedFile(path, kind, previous, content_changed))
    return changes


def read_at_sha(repo_path: str, sha: str, path: str) -> str | None:
    """Exact UTF-8, including empty files/CRLF; None means absent, errors propagate."""
    from ydbdoc_review.github.git_ops import read_text_at_commit

    _sha(sha)
    try:
        return read_text_at_commit(repo_path, sha, path)
    except Exception as exc:
        raise PlanError(f"Не удалось прочитать {path} из {sha}: {exc}") from exc


def require_source(read: Reader, sha: str, path: str) -> str:
    try:
        text = read(sha, path)
    except Exception as exc:
        raise PlanError(f"Не удалось прочитать {path} из {sha}: {exc}") from exc
    if text is None:
        raise PlanError(f"Не удалось прочитать {path} из {sha}: файл отсутствует")
    return text


def page_key(path: str, docs_root: str = "ydb/docs") -> tuple[Language, str] | None:
    prefix = docs_root.rstrip("/") + "/"
    if not path.startswith(prefix) or not path.endswith(".md"):
        return None
    language, separator, key = path[len(prefix) :].partition("/")
    if separator and key and language in {"ru", "en"}:
        return language, key
    return None


def mirror_path(path: str, docs_root: str = "ydb/docs") -> str:
    page = page_key(path, docs_root)
    if page is None:
        raise PlanError(f"Не является RU/EN Markdown-статьёй: {path}")
    language, key = page
    return f"{docs_root.rstrip('/')}/{'en' if language == 'ru' else 'ru'}/{key}"


@dataclass(frozen=True)
class PlanOperation:
    kind: Kind
    source_language: Language
    target_language: Language
    old_path: str | None  # source paths; target paths are explicit below
    new_path: str | None
    target_old_path: str | None
    target_new_path: str | None
    content_changed: bool

    @property
    def needs_translation(self) -> bool:
        return self.kind != "deleted" and self.content_changed


@dataclass
class Plan:
    snapshot: Snapshot
    operations: list[PlanOperation] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # selected source text only
    dependencies: list[str] = field(default_factory=list)
    skipped_pairs: set[str] = field(default_factory=set)  # language-relative keys

    @property
    def needs_model(self) -> bool:
        return any(op.needs_translation for op in self.operations)

    @property
    def no_work(self) -> bool:
        return not self.operations


def build_plan(
    snapshot: Snapshot, changes: Iterable[ChangedFile], read: Reader, *, docs_root: str = "ydb/docs"
) -> Plan:
    """Build translate operations; bilingual skip happens before any source reads.

    A bilingual rename corresponds only when both old and new keys match.
    Old translations alone never influence selection or translation inputs.
    """
    selected = []
    identities: dict[tuple[str | None, str | None], set[str]] = {}
    for change in changes:
        page = page_key(change.path, docs_root)
        if page is None:
            continue
        language, key = page
        old_key = None if change.kind == "added" else key
        new_key = None if change.kind == "deleted" else key
        if change.kind == "renamed":
            old_page = page_key(change.previous_path or "", docs_root)
            if old_page is None or old_page[0] != language:
                raise PlanError(
                    f"Неподдерживаемое переименование статьи: {change.previous_path} → {change.path}"
                )
            old_key = old_page[1]
        # add and edit of the same page are both corresponding content changes
        identity = (old_key, new_key) if change.kind in {"renamed", "deleted"} else (key, key)
        identities.setdefault(identity, set()).add(language)
        selected.append((change, language, identity))
    plan = Plan(snapshot)
    for identity, languages in identities.items():
        if len(languages) == 2:
            plan.skipped_pairs.update(key for key in identity if key is not None)
    seen = set()
    for change, language, identity in selected:
        if len(identities[identity]) == 2 or change in seen:
            continue
        seen.add(change)
        old = (
            None
            if change.kind == "added"
            else (change.previous_path if change.kind == "renamed" else change.path)
        )
        new = None if change.kind == "deleted" else change.path
        changed = change.kind != "deleted"
        if change.kind == "renamed":
            if change.content_changed is None:
                raise PlanError(
                    f"Неизвестно изменение содержимого при переименовании {change.path}; "
                    "требуются метаданные исходного PR"
                )
            changed = change.content_changed
        if new is not None:
            text = require_source(read, snapshot.source_sha, new)
            plan.files[new] = text
        plan.operations.append(
            PlanOperation(
                change.kind,
                language,
                "en" if language == "ru" else "ru",
                old,
                new,
                mirror_path(old, docs_root) if old else None,
                mirror_path(new, docs_root) if new else None,
                changed,
            )
        )
    return plan


def translation_sources(plan: Plan) -> dict[str, str]:
    """Only Markdown sent for translation; mechanical renames contribute no chars."""
    return {
        op.new_path: plan.files[op.new_path]
        for op in plan.operations
        if op.needs_translation and op.new_path is not None
    }


def raw_source_characters(plan: Plan) -> int:
    return sum(len(text) for text in translation_sources(plan).values())


def markdown_dependencies(path: str, text: str, *, docs_root: str = "ydb/docs") -> list[str]:
    """Same-language Markdown links, including reference links and YFM containers.

    Code, images, includes and TOC are not article-link scope expansion rules.
    URL queries/fragments do not change the identity of a source article.
    """
    import posixpath
    from urllib.parse import unquote, urlsplit

    from ydbdoc_review.parsing.markdown_parser import create_parser

    source_page = page_key(path, docs_root)
    if source_page is None:
        return []
    language = source_page[0]
    root = docs_root.rstrip("/")
    found: dict[str, None] = {}
    pending = list(reversed(create_parser().parse(text)))
    while pending:
        token = pending.pop()
        if token.type == "image":
            continue
        pending.extend(reversed(token.children or []))
        if token.type != "link_open":
            continue
        href = token.attrGet("href") or ""
        try:
            url = urlsplit(href)
        except ValueError:
            continue  # malformed href belongs to final link validation
        if url.scheme or url.netloc or not url.path:
            continue
        target = unquote(url.path)
        if target.startswith(f"/{root}/"):
            target = target.lstrip("/")
        elif target.startswith(("/ru/", "/en/")):
            target = root + target
        elif target.startswith("/"):
            target = f"{root}/{language}/core{target}"
        else:
            target = posixpath.join(posixpath.dirname(path), target)
        target = posixpath.normpath(target)
        target_page = page_key(target, root)
        if target_page is not None and target_page[0] == language:
            found[target] = None
    return list(found)


def discover_dependencies(plan: Plan, read: Reader, *, docs_root: str = "ydb/docs") -> Plan:
    """Return the complete closure, never stopping at a configured budget.

    The input plan is not mutated. All reads use its frozen source SHA. Existing
    (even empty) target files prevent expansion. Skipped bilingual pairs and
    original PR operations cannot re-enter the queue as additional articles.
    """
    from collections import deque
    from dataclasses import replace

    result = replace(
        plan,
        operations=list(plan.operations),
        files=dict(plan.files),
        dependencies=list(plan.dependencies),
        skipped_pairs=set(plan.skipped_pairs),
    )
    queue = deque(translation_sources(result))
    visited = set(result.files)
    visited.update(op.old_path for op in result.operations if op.old_path is not None)
    cache: dict[str, str | None] = dict(result.files)

    def frozen_read(sha: str, path: str) -> str | None:
        if path not in cache:
            try:
                cache[path] = read(sha, path)
            except Exception as exc:
                raise PlanError(f"Не удалось прочитать {path} из {sha}: {exc}") from exc
        return cache[path]

    while queue:
        source = queue.popleft()
        for path in markdown_dependencies(source, result.files[source], docs_root=docs_root):
            page = page_key(path, docs_root)
            assert page is not None
            language, key = page
            if path in visited or key in result.skipped_pairs:
                continue
            visited.add(path)
            target = mirror_path(path, docs_root)
            if frozen_read(plan.snapshot.source_sha, target) is not None:
                continue
            text = require_source(frozen_read, plan.snapshot.source_sha, path)
            result.files[path] = text
            result.dependencies.append(path)
            result.operations.append(
                PlanOperation(
                    "added",
                    language,
                    "en" if language == "ru" else "ru",
                    None,
                    path,
                    None,
                    target,
                    True,
                )
            )
            queue.append(path)
    return result


class PlanLimitError(PlanError):
    """Refusal retaining the full discovered plan for reporting."""

    def __init__(self, message: str, plan: Plan):
        super().__init__(message)
        self.plan = plan


def enforce_limits(plan: Plan, settings: Settings) -> None:
    """Gate the complete plan using Actions settings, with no model or writes."""
    messages = []
    count = len(plan.dependencies)
    if count > settings.max_dependency_files:
        messages.append(
            f"Перевод не запущен: требуется {count} зависимых статей, "
            f"лимит — {settings.max_dependency_files} "
            "(`YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE`). "
            "Сначала переведите часть зависимых статей отдельным PR или увеличьте "
            "переменную в настройках Actions репозитория ydb-platform/ydb\n"
            + "\n".join(plan.dependencies)
        )
    count = raw_source_characters(plan)
    if count > settings.max_source_characters:
        messages.append(
            f"Перевод не запущен: объём исходных текстов — {count} символов, "
            f"лимит — {settings.max_source_characters} (`YDBDOC_MAX_SOURCE_CHARACTERS`). "
            "Разделите изменения на несколько PR или увеличьте переменную в настройках "
            "Actions репозитория ydb-platform/ydb"
        )
    if messages:
        raise PlanLimitError("\n\n".join(messages), plan)


def prepare_translation_plan(
    snapshot: Snapshot,
    changes: Iterable[ChangedFile],
    read: Reader,
    settings: Settings,
    *,
    docs_root: str = "ydb/docs",
) -> Plan:
    """Complete translate preflight; the runner must call this before model creation."""
    plan = build_plan(snapshot, changes, read, docs_root=docs_root)
    plan = discover_dependencies(plan, read, docs_root=docs_root)
    enforce_limits(plan, settings)
    return plan
