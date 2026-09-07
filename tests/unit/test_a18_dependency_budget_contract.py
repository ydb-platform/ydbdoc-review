"""A18 RED contract for one global Markdown dependency budget.

Acceptance uses the real planner, immutable Git readers, workflow entrypoints,
disk application, local commits, a local bare remote, A05 authority evidence,
and report builders. The lifecycle replaces model, GitHub, and push transports,
stubs the unrelated orphan-TOC gate, and uses a fake model that replaces page
bodies (and therefore their links). Those seams are not acceptance claims.

The ``MutationSnapshot`` parameters and rollover snapshot are supplemental
synthetic oracle-sensitivity checks. They are not behavioral controls or
product mutation runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github import git_ops, workflow
from ydbdoc_review.github.git_ops import read_text_at_commit
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    parse_authority_evidence,
)
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.navigation.dependency_budget import MarkdownDependencyBudget
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    _exact_ascii_fragment_owner_dependency,
    doc_pairs_from_plan,
    plan_translation_scope,
)
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair, counterpart
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_full_report,
    build_source_pr_comment,
    result_has_blocking_findings,
)
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.validation.fragment_repair import _page_declares_fragment

DOCS_ROOT = "ydb/docs"
RU_ROOT = f"{DOCS_ROOT}/ru/core/a18"
EN_ROOT = f"{DOCS_ROOT}/en/core/a18"
SOURCE_PR = 18
TRANSLATION_PR = 918
REPO_ID = "ydb-platform/ydb"
BUDGET = 20
FAMILIES = ("include", "toc", "link", "fragment")
GLOBAL_ROOTS = tuple(f"{RU_ROOT}/{family}-root.md" for family in FAMILIES)
GLOBAL_TOC = f"{RU_ROOT}/toc_p.yaml"
ORDINARY_ROOT = f"{RU_ROOT}/ordinary-root.md"


def _readers(ru: dict[str, str], en: dict[str, str], base: dict[str, str]) -> tuple[Any, Any, Any]:
    return (
        lambda path: ru.get(path.replace("\\", "/")),
        lambda path: en.get(path.replace("\\", "/")),
        lambda path: base.get(path.replace("\\", "/")),
    )


def _ordered_dict(values: dict[str, str], *, reverse: bool) -> dict[str, str]:
    items = list(values.items())
    if reverse:
        items.reverse()
    return dict(items)


def _global_budget_fixture(
    *, reverse_maps: bool = False
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    tuple[str, ...],
    dict[str, str],
]:
    """Return 28 interleaved, distinct non-seed Markdown candidates."""
    candidates = tuple(f"{RU_ROOT}/candidates/candidate-{index:02d}.md" for index in range(28))
    family_by_path = {
        path: FAMILIES[index % len(FAMILIES)] for index, path in enumerate(candidates)
    }
    by_family = {
        family: tuple(path for path in candidates if family_by_path[path] == family)
        for family in FAMILIES
    }

    ru: dict[str, str] = {}
    en: dict[str, str] = {}
    base: dict[str, str] = {root: "# Base root\n" for root in GLOBAL_ROOTS}
    base[GLOBAL_TOC] = "items: []\n"
    ru[GLOBAL_ROOTS[0]] = (
        "\n".join(
            f"{{% include [candidate]({Path(path).relative_to(RU_ROOT).as_posix()}) %}}"
            for path in by_family["include"]
        )
        + "\n"
    )
    ru[GLOBAL_ROOTS[1]] = "# Toc source root\n"
    ru[GLOBAL_ROOTS[2]] = (
        "\n".join(
            f"[candidate]({Path(path).relative_to(RU_ROOT).as_posix()})"
            for path in by_family["link"]
        )
        + "\n"
    )
    ru[GLOBAL_ROOTS[3]] = (
        "\n".join(
            f"[candidate]({Path(path).relative_to(RU_ROOT).as_posix()}#a18-{index:02d})"
            for index, path in enumerate(by_family["fragment"])
        )
        + "\n"
    )
    ru[GLOBAL_TOC] = "items:\n" + "".join(
        f"  - name: candidate\n    href: {Path(path).relative_to(RU_ROOT).as_posix()}\n"
        for path in by_family["toc"]
    )
    for path in candidates:
        family = family_by_path[path]
        if family == "fragment":
            fragment_index = by_family[family].index(path)
            ru[path] = f"# Candidate {{#a18-{fragment_index:02d}}}\n"
            en_path = counterpart(path, DOCS_ROOT)
            assert en_path is not None
            en[en_path] = "# Candidate without the explicit source anchor\n"
        else:
            ru[path] = f"# {family} candidate\n"
    return (
        _ordered_dict(ru, reverse=reverse_maps),
        _ordered_dict(en, reverse=reverse_maps),
        _ordered_dict(base, reverse=reverse_maps),
        candidates,
        family_by_path,
    )


def _plan_global_budget(
    *, reverse_roots: bool = False, reverse_maps: bool = False
) -> tuple[TranslationScopePlan, tuple[str, ...]]:
    ru, en, base, candidates, _family_by_path = _global_budget_fixture(reverse_maps=reverse_maps)
    read_ru, read_en, read_base = _readers(ru, en, base)
    changes = [(root, "modified") for root in GLOBAL_ROOTS]
    changes.append((GLOBAL_TOC, "modified"))
    if reverse_roots:
        changes.reverse()
    plan = plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=read_base,
        docs_root=DOCS_ROOT,
    )
    return plan, candidates


def _warning_targets(
    warnings: tuple[str, ...] | list[str], candidates: tuple[str, ...]
) -> tuple[str, ...]:
    targets: list[str] = []
    for warning in warnings:
        assert "budget" in warning.lower(), warning
        assert "manual" in warning.lower(), warning
        matching = [
            path
            for path in candidates
            if path in warning or (counterpart(path, DOCS_ROOT) or "") in warning
        ]
        assert len(matching) == 1, warning
        targets.append(matching[0])
    return tuple(targets)


def _assert_global_budget(
    plan: TranslationScopePlan,
    candidates: tuple[str, ...],
) -> None:
    extras = plan.doc_ru_paths - plan.doc_from_diff
    candidate_set = frozenset(candidates)
    admitted = extras
    denied = candidate_set - admitted
    assert plan.doc_from_diff == frozenset(GLOBAL_ROOTS)
    assert GLOBAL_TOC in plan.nav_from_diff
    assert len(candidates) == 28
    assert len(candidate_set) == 28
    assert admitted <= candidate_set
    assert len(admitted) == BUDGET
    assert len(denied) == 8
    assert admitted.isdisjoint(denied)
    assert admitted | denied == candidate_set
    warning_targets = _warning_targets(plan.link_dep_warnings, candidates)
    assert Counter(warning_targets) == Counter(denied)
    assert len(plan.link_dep_warnings) == len(denied)


def test_a18_global_union_admits_exactly_twenty_and_warns_for_eight_without_family_priority() -> (
    None
):
    plan, candidates = _plan_global_budget()
    _assert_global_budget(plan, candidates)
    assert [pair.ru_path for pair in doc_pairs_from_plan(plan)] == sorted(plan.doc_ru_paths)


def _plan_payload(plan: TranslationScopePlan) -> dict[str, object]:
    return {
        "docs": sorted(plan.doc_ru_paths),
        "diff": sorted(plan.doc_from_diff),
        "main": sorted(plan.doc_from_main),
        "nav": sorted(plan.nav_ru_paths),
        "warnings": list(plan.link_dep_warnings),
    }


def test_a18_admission_is_stable_under_permuted_roots_dicts_and_pythonhashseed() -> None:
    local_payloads = []
    last_case: tuple[TranslationScopePlan, tuple[str, ...]] | None = None
    for reverse_roots, reverse_maps in (
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ):
        plan, candidates = _plan_global_budget(
            reverse_roots=reverse_roots, reverse_maps=reverse_maps
        )
        local_payloads.append(_plan_payload(plan))
        last_case = (plan, candidates)
    assert local_payloads.count(local_payloads[0]) == len(local_payloads)

    module_path = Path(__file__).resolve()
    script = """
import importlib.util
import json
import sys
from pathlib import Path
path = Path(sys.argv[1]).resolve()
repo_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo_path / "src"))
sys.path.insert(0, str(repo_path))
spec = importlib.util.spec_from_file_location("a18_hash_probe", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert Path(module.__file__).resolve() == path
plan, candidates = module._plan_global_budget(
    reverse_roots=bool(int(sys.argv[3])),
    reverse_maps=bool(int(sys.argv[4])),
)
print(json.dumps({
    "module_file": str(Path(module.__file__).resolve()),
    "payload": module._plan_payload(plan),
}, sort_keys=True))
"""
    repo_path = module_path.parents[2]
    subprocess_results: list[dict[str, object]] = []
    for seed, reverse_roots, reverse_maps in (
        ("1", False, False),
        ("97", True, False),
        ("211", False, True),
        ("997", True, True),
    ):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(module_path),
                str(repo_path),
                str(int(reverse_roots)),
                str(int(reverse_maps)),
            ],
            cwd=repo_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess_results.append(json.loads(completed.stdout))
    assert all(result["module_file"] == str(module_path) for result in subprocess_results)
    subprocess_payloads = [result["payload"] for result in subprocess_results]
    assert subprocess_payloads.count(local_payloads[0]) == len(subprocess_payloads)
    assert last_case is not None
    _assert_global_budget(*last_case)


def _ordinary_link_plan(
    count: int = 21,
) -> tuple[TranslationScopePlan, tuple[str, ...]]:
    targets = tuple(f"{RU_ROOT}/ordinary/target-{index:02d}.md" for index in range(count))
    ru = {path: f"# Target {index}\n" for index, path in enumerate(targets)}
    ru[ORDINARY_ROOT] = (
        "\n".join(
            f"[target {index}](ordinary/{Path(path).name})" for index, path in enumerate(targets)
        )
        + "\n"
    )
    read_ru, read_en, read_base = _readers(ru, {}, {ORDINARY_ROOT: "# Base\n"})
    plan = plan_translation_scope(
        [(ORDINARY_ROOT, "modified")],
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=read_base,
        docs_root=DOCS_ROOT,
    )
    return plan, targets


def _successful_result(*, warnings: list[str] | None = None) -> PRTranslationResult:
    en_path = counterpart(ORDINARY_ROOT, DOCS_ROOT)
    assert en_path is not None
    pair = DocPair(ORDINARY_ROOT, en_path, ru_changed=True)
    pair_plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=ORDINARY_ROOT,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
        summary="A18 renderer consumer",
    )
    final_text = "# English result\n"
    file_result = FileTranslationResult(
        file_path=en_path,
        final_text=final_text,
        segments_count=1,
        verdict="ok",
        prompt_version="a18-consumer",
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=pair_plan,
                target_text=final_text,
                file_result=file_result,
                source_text="# Russian source\n",
            )
        ],
        yellow_warnings=list(warnings or ()),
    )


def _config(*, skip_globs: tuple[str, ...] = ("never",)):
    config = load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "a18-folder",
            "YDBDOC_YC_API_KEY": "a18-key",
            "GITHUB_TOKEN": "a18-token",
            "YDBDOC_TRANSLATION_RU_AUTHORITY_MODE": "current",
        }
    )
    return config.model_copy(
        update={"paths": config.paths.model_copy(update={"translate_skip_globs": list(skip_globs)})}
    )


def test_a18_renderer_consumers_show_one_real_warning_in_full_and_success_source_reports() -> None:
    plan, candidates = _ordinary_link_plan()
    assert len(plan.doc_from_main) == BUDGET
    assert _warning_targets(plan.link_dep_warnings, candidates) == (candidates[-1],)
    # The sole consumer-isolation test allowed to pass a real planner warning
    # manually into PRTranslationResult. Producer acceptance below never does.
    result = _successful_result(warnings=list(plan.link_dep_warnings))
    assert not result_has_blocking_findings(result)
    full = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=0.0),
        config=_config(),
    )
    source = build_source_pr_comment(
        result,
        translation_pr_number=TRANSLATION_PR,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=0.0),
        config=_config(),
        committed=True,
    )
    warning = plan.link_dep_warnings[0]
    assert full.count(warning) == 1
    assert source.count(warning) == 1
    assert "перевод готов" in source


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bare(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", f"--git-dir={repo}", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _pull(
    *,
    number: int,
    head_ref: str,
    head_sha: str,
    base_sha: str,
    base_ref: str = "main",
    body: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"A18 PR #{number}",
        "body": body,
        "merged": False,
        "state": "open",
        "draft": draft,
        "merge_commit_sha": None,
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {
                "clone_url": f"https://github.com/{REPO_ID}.git",
                "full_name": REPO_ID,
                "owner": {"login": REPO_ID.split("/", 1)[0]},
                "name": REPO_ID.split("/", 1)[1],
            },
        },
        "base": {"ref": base_ref, "sha": base_sha},
    }


@dataclass
class LinkHistory:
    repo: Path
    upstream: Path
    h0: str
    h: str
    targets: tuple[str, ...]
    candidate_shas: list[str] = field(default_factory=list)
    source_changes: tuple[tuple[str, str], ...] = ((ORDINARY_ROOT, "added"),)
    owner_a_ru: str | None = None
    owner_a_en: str | None = None
    owner_b_ru: str | None = None
    owner_b_en: str | None = None


def _link_history(tmp_path: Path) -> LinkHistory:
    repo = tmp_path / "a18-link-producer"
    upstream = tmp_path / "a18-upstream.git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a18@example.test")
    _git(repo, "config", "user.name", "A18 Contract")
    targets = tuple(f"{RU_ROOT}/ordinary/target-{index:02d}.md" for index in range(21))
    for index, path in enumerate(targets):
        _write(repo, path, f"# RU target {index}\n")
    h0 = _commit(repo, "A18 H0 with committed RU targets")
    links = "\n".join(
        f"[target {index}](ordinary/{Path(path).name})" for index, path in enumerate(targets)
    )
    _write(repo, ORDINARY_ROOT, f"# RU root\n\n{links}\n")
    h = _commit(repo, "A18 H with twenty-one committed target links")
    _git(repo, "branch", "source-a18", h)
    _git(repo, "checkout", "--detach", h)
    _git(repo, "branch", "-f", "main", h0)
    _git(repo, "init", "--bare", str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    _git(repo, "push", "origin", f"{h0}:refs/heads/main")
    _git(repo, "push", "origin", f"{h}:refs/heads/source-a18")
    _git(repo, "fetch", "origin")
    return LinkHistory(repo, upstream, h0, h, targets)


def _late_rollover_history(tmp_path: Path) -> LinkHistory:
    """Real H0/H history with D=19 and two valid post-generation owners."""
    repo = tmp_path / "a18-late-rollover-producer"
    upstream = tmp_path / "a18-late-rollover-upstream.git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a18-rollover@example.test")
    _git(repo, "config", "user.name", "A18 Rollover Contract")

    directory = f"{RU_ROOT}/late-rollover"
    owner_a_ru = f"{directory}/owner-a.md"
    owner_b_ru = f"{directory}/owner-b.md"
    owner_a_en = counterpart(owner_a_ru, DOCS_ROOT)
    owner_b_en = counterpart(owner_b_ru, DOCS_ROOT)
    assert owner_a_en is not None and owner_b_en is not None
    _write(repo, owner_a_ru, "# Owner A {#a18-late-owner-a}\n")
    _write(repo, owner_b_ru, "# Owner B {#a18-late-owner-b}\n")
    _write(repo, owner_a_en, "# Owner A without the explicit anchor\n")
    _write(repo, owner_b_en, "# Owner B without the explicit anchor\n")

    dependencies = tuple(f"{directory}/dependency-{index:02d}.md" for index in range(19))
    for index, dependency in enumerate(dependencies):
        body = f"# Dependency {index}\n"
        if index == 0:
            body += (
                "\n{% include [owner A](owner-a.md) %}\n"
                "{% include [owner B](owner-b.md) %}\n\n"
                "[late owner A](dependency-00.md#a18-late-owner-a)\n"
                "[late owner B](dependency-00.md#a18-late-owner-b)\n"
            )
        _write(repo, dependency, body)
    h0 = _commit(repo, "A18 H0 with nineteen dependencies and aligned owners")

    source_root = f"{directory}/root.md"
    links = "\n".join(
        f"[dependency {index}]({Path(dependency).name})"
        for index, dependency in enumerate(dependencies)
    )
    _write(repo, source_root, f"# Rollover source root\n\n{links}\n")
    h = _commit(repo, "A18 H with nineteen dependency links")
    _git(repo, "branch", "source-a18", h)
    _git(repo, "checkout", "--detach", h)
    _git(repo, "branch", "-f", "main", h0)
    _git(repo, "init", "--bare", str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    _git(repo, "push", "origin", f"{h0}:refs/heads/main")
    _git(repo, "push", "origin", f"{h}:refs/heads/source-a18")
    _git(repo, "fetch", "origin")
    return LinkHistory(
        repo=repo,
        upstream=upstream,
        h0=h0,
        h=h,
        targets=dependencies,
        source_changes=((source_root, "added"),),
        owner_a_ru=owner_a_ru,
        owner_a_en=owner_a_en,
        owner_b_ru=owner_b_ru,
        owner_b_en=owner_b_en,
    )


def _hybrid_include_rollover_history(tmp_path: Path) -> LinkHistory:
    """Real D=19 history whose generated EN wrapper exists only in C."""
    repo = tmp_path / "a18-hybrid-rollover-producer"
    upstream = tmp_path / "a18-hybrid-rollover-upstream.git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a18-hybrid@example.test")
    _git(repo, "config", "user.name", "A18 Hybrid Contract")

    directory = f"{RU_ROOT}/hybrid-rollover"
    owner_a_ru = f"{directory}/_includes/owner-a.md"
    owner_b_ru = f"{directory}/owner-b.md"
    owner_a_en = counterpart(owner_a_ru, DOCS_ROOT)
    owner_b_en = counterpart(owner_b_ru, DOCS_ROOT)
    assert owner_a_en is not None and owner_b_en is not None
    _write(repo, owner_a_ru, "# Included owner A {#a18-hybrid-owner-a}\n")
    _write(repo, owner_b_ru, "# Owner B {#a18-hybrid-owner-b}\n")
    _write(repo, owner_a_en, "# Included owner A without declaration\n")
    _write(repo, owner_b_en, "# Owner B without declaration\n")

    carrier = f"{directory}/carrier.md"
    wrapper = f"{directory}/wrapper.md"
    _write(
        repo,
        carrier,
        "# Hybrid carrier\n\n"
        "[late owner A](wrapper.md#a18-hybrid-owner-a)\n"
        "[late owner B](wrapper.md#a18-hybrid-owner-b)\n",
    )
    _write(
        repo,
        wrapper,
        "# Hybrid wrapper\n\n"
        "{% include [owner A](_includes/owner-a.md) %}\n"
        "{% include [owner B](owner-b.md) %}\n",
    )
    ordinary = tuple(f"{directory}/dependency-{index:02d}.md" for index in range(17))
    for index, dependency in enumerate(ordinary):
        _write(repo, dependency, f"# Hybrid dependency {index}\n")
    dependencies = (carrier, wrapper, *ordinary)
    h0 = _commit(repo, "A18 H0 with hybrid D19 and aligned owners")

    source_root = f"{directory}/root.md"
    links = "\n".join(
        f"[dependency {index}]({Path(dependency).name})"
        for index, dependency in enumerate(dependencies)
    )
    _write(repo, source_root, f"# Hybrid source root\n\n{links}\n")
    h = _commit(repo, "A18 H with hybrid D19 links")
    _git(repo, "branch", "source-a18", h)
    _git(repo, "checkout", "--detach", h)
    _git(repo, "branch", "-f", "main", h0)
    _git(repo, "init", "--bare", str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    _git(repo, "push", "origin", f"{h0}:refs/heads/main")
    _git(repo, "push", "origin", f"{h}:refs/heads/source-a18")
    _git(repo, "fetch", "origin")
    return LinkHistory(
        repo=repo,
        upstream=upstream,
        h0=h0,
        h=h,
        targets=dependencies,
        source_changes=((source_root, "added"),),
        owner_a_ru=owner_a_ru,
        owner_a_en=owner_a_en,
        owner_b_ru=owner_b_ru,
        owner_b_en=owner_b_en,
    )


@dataclass
class A18GitHub:
    history: LinkHistory
    translation_body: str = ""
    remote_heads: dict[str, str | None] = field(default_factory=dict)
    comments: list[tuple[int, str]] = field(default_factory=list)
    body_reads: list[str] = field(default_factory=list)
    body_events: list[tuple[str, str]] = field(default_factory=list)
    allow_draft_conversion: bool = False
    draft_conversions: list[int] = field(default_factory=list)
    allow_draft_creation: bool = False
    created_pull_drafts: list[bool] = field(default_factory=list)
    translation_draft: bool = False

    def __post_init__(self) -> None:
        self.remote_heads.setdefault(f"ydbdoc-review/pr-{SOURCE_PR}", None)

    @property
    def branch(self) -> str:
        return f"ydbdoc-review/pr-{SOURCE_PR}"

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        assert f"{owner}/{repo}" == REPO_ID
        if number == SOURCE_PR:
            return _pull(
                number=SOURCE_PR,
                head_ref="source-a18",
                head_sha=self.history.h,
                base_sha=self.history.h0,
            )
        assert number == TRANSLATION_PR
        head = self.remote_heads[self.branch]
        assert head is not None
        self.body_reads.append(self.translation_body)
        return _pull(
            number=TRANSLATION_PR,
            head_ref=self.branch,
            head_sha=head,
            base_sha=self.history.h,
            base_ref="source-a18",
            body=self.translation_body,
            draft=self.translation_draft,
        )

    def iter_pull_files(self, owner: str, repo: str, number: int) -> Iterator[dict[str, str]]:
        assert f"{owner}/{repo}" == REPO_ID
        if number == SOURCE_PR:
            for path, status in self.history.source_changes:
                yield {"filename": path, "status": status}
            return
        assert number == TRANSLATION_PR
        head = self.remote_heads[self.branch]
        assert head is not None
        output = _git_bare(self.history.upstream, "diff", "--name-status", self.history.h0, head)
        for row in output.splitlines():
            status, path = row.split("\t", 1)
            mapped = "added" if status == "A" else "removed" if status == "D" else "modified"
            yield {"filename": path, "status": mapped}

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        assert f"{owner}/{repo}" == REPO_ID
        return self.remote_heads.setdefault(branch, None)

    def find_open_pull_by_head(
        self, owner: str, repo: str, *, head_branch: str, base: str
    ) -> tuple[str, int] | None:
        assert f"{owner}/{repo}" == REPO_ID
        assert head_branch == self.branch
        assert base == "source-a18"
        return None

    def create_pull(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = False,
    ) -> tuple[str, int, bool]:
        assert f"{owner}/{repo}" == REPO_ID
        assert title == f"Auto-translate docs from PR #{SOURCE_PR}"
        assert head == self.branch
        assert base == "source-a18"
        if not self.allow_draft_creation:
            assert not draft
        self.created_pull_drafts.append(draft)
        self.translation_draft = draft
        self.translation_body = body
        self.body_events.append(("create", body))
        return (
            f"https://github.com/{REPO_ID}/pull/{TRANSLATION_PR}",
            TRANSLATION_PR,
            True,
        )

    def update_pull_body(self, owner: str, repo: str, number: int, body: str) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == TRANSLATION_PR
        self.translation_body = body
        self.body_events.append(("update", body))

    def post_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        assert f"{owner}/{repo}" == REPO_ID
        self.comments.append((number, body))
        return f"https://github.com/{REPO_ID}/issues/{number}#a18"

    def iter_issue_comments(
        self, owner: str, repo: str, number: int
    ) -> Iterator[dict[str, object]]:
        assert f"{owner}/{repo}" == REPO_ID
        return iter(())

    def add_issue_labels(self, owner: str, repo: str, number: int, labels: list[str]) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        assert labels == ["documentation"]

    def convert_pull_to_draft(self, owner: str, repo: str, number: int) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        if not self.allow_draft_conversion:
            raise AssertionError(f"unexpected draft conversion for {owner}/{repo}#{number}")
        self.draft_conversions.append(number)
        self.translation_draft = True

    def delete_branch(self, owner: str, repo: str, branch: str) -> bool:
        assert f"{owner}/{repo}" == REPO_ID
        return False


@dataclass
class RuntimeObservation:
    plans: list[TranslationScopePlan] = field(default_factory=list)
    translate_batches: list[tuple[str, ...]] = field(default_factory=list)
    verify_batches: list[tuple[str, ...]] = field(default_factory=list)
    pushed_shas: list[str] = field(default_factory=list)
    inline_jobs: list[workflow.DocJobResult] = field(default_factory=list)
    change_first_verify: bool = False
    changed_once: bool = False
    preserve_late_fragment_links: bool = False


class _BoundaryClient:
    def __init__(self) -> None:
        self.usage_tracker = UsageTracker()
        self.transcript_recorder = None

    def model_chain_for_role(self, role: str) -> list[str]:
        assert role == "translate"
        return ["a18-fixture-translate"]

    def chat(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        raise AssertionError("A18 fixture crossed the patched model boundary")


def _model_result(
    contents: list[PairContent], *, verify: bool, change_one: bool = False
) -> PRTranslationResult:
    runs: list[PairRunResult] = []
    changed = False
    for content in contents:
        pair = content.pair
        target = content.en_text or ""
        if not verify:
            target = f"# EN {Path(pair.en_path).stem}\n"
        elif change_one and not changed:
            target = target.rstrip("\n") + "\n\nA18 first-pass critic repair.\n"
            changed = True
        pair_plan = PairPlan(
            pair=pair,
            action="critic_only" if verify else "translate_to_en",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="A18 model boundary",
        )
        file_result = FileTranslationResult(
            file_path=pair.en_path,
            final_text=target,
            segments_count=1,
            verdict="ok",
            prompt_version="a18-workflow",
        )
        runs.append(
            PairRunResult(
                plan=pair_plan,
                target_text=target,
                file_result=file_result,
                source_text=content.ru_text,
            )
        )
    return PRTranslationResult(pair_results=runs)


@contextmanager
def _workflow_runtime(
    history: LinkHistory,
    github: A18GitHub,
    observed: RuntimeObservation,
) -> Iterator[None]:
    real_plan = workflow.plan_translation_scope
    real_prepare = workflow.prepare_translation_branch_on_base
    real_inline_verify = workflow.run_doc_verify
    client = _BoundaryClient()

    def capture_plan(*args: object, **kwargs: object) -> TranslationScopePlan:
        plan = real_plan(*args, **kwargs)
        observed.plans.append(plan)
        return plan

    def translate_model(
        contents: list[PairContent], *_args: object, **_kwargs: object
    ) -> PRTranslationResult:
        observed.translate_batches.append(tuple(content.pair.ru_path for content in contents))
        result = _model_result(contents, verify=False)
        if observed.preserve_late_fragment_links:
            for content, run in zip(contents, result.pair_results, strict=True):
                source = content.ru_text or ""
                carries_late_links = "[late owner A]" in source and "[late owner B]" in source
                is_hybrid_wrapper = "# Hybrid wrapper" in source and "{% include" in source
                if not carries_late_links and not is_hybrid_wrapper:
                    continue
                target = source.replace("# Dependency", "# EN dependency", 1)
                run.target_text = target
                assert run.file_result is not None
                run.file_result.final_text = target
        return result

    def verify_model(
        contents: list[PairContent], *_args: object, **_kwargs: object
    ) -> PRTranslationResult:
        observed.verify_batches.append(tuple(content.pair.ru_path for content in contents))
        change = observed.change_first_verify and not observed.changed_once
        if change:
            observed.changed_once = True
        return _model_result(contents, verify=True, change_one=change)

    def prepare(repo_path: str, **kwargs: object) -> None:
        forwarded = dict(kwargs)
        forwarded["base_remote_url"] = str(history.upstream)
        real_prepare(repo_path, **forwarded)

    def local_push(
        repo_path: str,
        _remote_name: str,
        branch: str,
        _token: str,
        _url: str,
        *,
        expected_remote_sha: str | None = None,
        source_sha: str | None = None,
        **_kwargs: object,
    ) -> git_ops.RefMutationReceipt:
        assert source_sha is not None
        resolved = _git(Path(repo_path), "rev-parse", f"{source_sha}^{{commit}}")
        before = github.remote_heads.get(branch)
        assert before == expected_remote_sha
        status = (
            git_ops.RefMutationStatus.NOOP
            if before == resolved
            else git_ops.RefMutationStatus.CHANGED
        )
        _git(
            Path(repo_path),
            "push",
            "--force",
            str(history.upstream),
            f"{resolved}:refs/heads/{branch}",
        )
        github.remote_heads[branch] = resolved
        if status is git_ops.RefMutationStatus.CHANGED:
            observed.pushed_shas.append(resolved)
            history.candidate_shas.append(resolved)
        return git_ops.RefMutationReceipt(
            lease=git_ops.RemoteRefLease(branch=branch, expected_sha=expected_remote_sha),
            operation=git_ops.RefMutationOperation.UPDATE,
            requested_sha=resolved,
            status=status,
            porcelain_flag="+" if status is git_ops.RefMutationStatus.CHANGED else "=",
            stdout="local A18 bare-remote push",
            stderr="",
        )

    def capture_inline_verify(**kwargs: object) -> workflow.DocJobResult:
        job = real_inline_verify(**kwargs)
        observed.inline_jobs.append(job)
        return job

    with ExitStack() as stack:
        stack.enter_context(patch.object(workflow, "GitHubClient", return_value=github))
        stack.enter_context(
            patch.object(
                workflow,
                "begin_ops_job",
                return_value=(None, GateResult(ok=True), None),
            )
        )
        stack.enter_context(patch.object(workflow, "create_llm_client", return_value=client))
        stack.enter_context(
            patch.object(workflow, "load_glossary", return_value=Glossary(entries=[]))
        )
        stack.enter_context(
            patch.object(workflow, "plan_translation_scope", side_effect=capture_plan)
        )
        stack.enter_context(
            patch.object(workflow, "run_pr_translation", side_effect=translate_model)
        )
        stack.enter_context(patch.object(workflow, "_run_verify_pairs", side_effect=verify_model))
        stack.enter_context(
            patch.object(
                workflow,
                "prepare_translation_branch_on_base",
                side_effect=prepare,
            )
        )
        stack.enter_context(patch.object(workflow, "push_branch", side_effect=local_push))
        stack.enter_context(patch.object(workflow, "apply_orphan_toc_page_checks", return_value=[]))
        stack.enter_context(
            patch.object(workflow, "run_doc_verify", side_effect=capture_inline_verify)
        )
        yield


def test_a18_real_skip_glob_rebuild_preserves_real_plan_at_the_pre_model_seam(
    tmp_path: Path,
) -> None:
    history = _link_history(tmp_path)
    github = A18GitHub(history)
    original_plans: list[TranslationScopePlan] = []
    downstream_plans: list[TranslationScopePlan] = []
    real_plan = workflow.plan_translation_scope

    class StopBeforeModel(RuntimeError):
        pass

    def capture_original(*args: object, **kwargs: object) -> TranslationScopePlan:
        plan = real_plan(*args, **kwargs)
        original_plans.append(plan)
        return plan

    def stop_at_pairs(plan: TranslationScopePlan, **_kwargs: object) -> list[DocPair]:
        downstream_plans.append(plan)
        raise StopBeforeModel

    with (
        patch.object(workflow, "GitHubClient", return_value=github),
        patch.object(
            workflow,
            "begin_ops_job",
            return_value=(None, GateResult(ok=True), None),
        ),
        patch.object(workflow, "plan_translation_scope", side_effect=capture_original),
        patch.object(workflow, "doc_pairs_from_plan", side_effect=stop_at_pairs),
    ):
        with pytest.raises(StopBeforeModel):
            workflow.run_doc_translate(
                repo_path=str(history.repo),
                github_repo=REPO_ID,
                pr_number=SOURCE_PR,
                merge_base_with=history.h0,
                dry_run=True,
                config=_config(skip_globs=("never",)),
            )
    assert len(original_plans) == 1
    original = original_plans[0]
    assert len(original.doc_from_main) == BUDGET
    assert _warning_targets(original.link_dep_warnings, history.targets) == (history.targets[-1],)
    assert downstream_plans == [original]


def _clone_consumer(tmp_path: Path, history: LinkHistory) -> Path:
    consumer = tmp_path / "a18-fresh-consumer"
    subprocess.run(
        [
            "git",
            "clone",
            "--branch",
            f"ydbdoc-review/pr-{SOURCE_PR}",
            "--single-branch",
            f"file://{history.upstream}",
            str(consumer),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(consumer, "config", "user.email", "a18-consumer@example.test")
    _git(consumer, "config", "user.name", "A18 Consumer")
    _git(consumer, "fetch", "origin", "main:refs/remotes/origin/main")
    return consumer


def _assert_one_warning(result: PRTranslationResult, warning: str) -> None:
    assert result.yellow_warnings == [warning]
    assert not result_has_blocking_findings(result)


def test_a18_mandatory_producer_inline_and_recursive_consumer_lifecycle(
    tmp_path: Path,
) -> None:
    history = _link_history(tmp_path)
    github = A18GitHub(history)
    observed = RuntimeObservation()
    with _workflow_runtime(history, github, observed):
        producer = workflow.run_doc_translate(
            repo_path=str(history.repo),
            github_repo=REPO_ID,
            pr_number=SOURCE_PR,
            merge_base_with="origin/main",
            config=_config(skip_globs=("never",)),
        )

    assert producer.committed and producer.pushed
    assert producer.translation_pr_number == TRANSLATION_PR
    assert len(history.candidate_shas) == 1
    candidate_one = history.candidate_shas[0]
    assert _git(history.repo, "rev-parse", f"{candidate_one}^") == history.h
    body = github.translation_body
    evidence = parse_authority_evidence(body)
    assert evidence.authority.source_head_sha == history.h
    assert evidence.candidate_sha == candidate_one
    assert observed.inline_jobs and not observed.inline_jobs[-1].pushed
    assert len(observed.translate_batches) == 1
    assert len(observed.translate_batches[0]) == BUDGET + 1
    warning = observed.plans[0].link_dep_warnings[0]
    assert all(plan.link_dep_warnings == (warning,) for plan in observed.plans)
    source_reports = [text for number, text in github.comments if number == SOURCE_PR]
    inline_reports = [text for number, text in github.comments if number == TRANSLATION_PR]

    consumer = _clone_consumer(tmp_path, history)
    fresh_github = A18GitHub(
        history,
        translation_body=body,
        remote_heads={f"ydbdoc-review/pr-{SOURCE_PR}": candidate_one},
    )
    standalone = RuntimeObservation(change_first_verify=True)
    with _workflow_runtime(history, fresh_github, standalone):
        final_job = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(skip_globs=("never",)),
            skip_ops_gates=True,
        )

    assert len(history.candidate_shas) == 2
    candidate_two = history.candidate_shas[-1]
    assert candidate_two != candidate_one
    assert _git(consumer, "rev-parse", f"{candidate_two}^") == candidate_one
    assert len(standalone.verify_batches) == 2
    assert standalone.translate_batches == []
    assert all(len(batch) == BUDGET + 1 for batch in standalone.verify_batches)
    assert len(standalone.plans) == 2
    assert all(plan.link_dep_warnings == (warning,) for plan in standalone.plans)
    assert all(
        parse_authority_evidence(body_read).authority.source_head_sha == history.h
        for body_read in fresh_github.body_reads
    )
    _assert_one_warning(final_job.pr_result, warning)
    final_reports = [text for number, text in fresh_github.comments if number == TRANSLATION_PR]
    _assert_one_warning(producer.pr_result, warning)
    _assert_one_warning(observed.inline_jobs[-1].pr_result, warning)
    assert source_reports[-1].count(warning) == 1
    assert inline_reports[-1].count(warning) == 1
    assert final_reports[-1].count(warning) == 1


def _assert_only_rollover_owner_b_warning(
    result: PRTranslationResult,
    history: LinkHistory,
) -> str:
    assert history.owner_a_ru is not None and history.owner_b_ru is not None
    assert len(result.yellow_warnings) == 1
    warning = result.yellow_warnings[0]
    assert _warning_targets(
        result.yellow_warnings,
        (history.owner_a_ru, history.owner_b_ru),
    ) == (history.owner_b_ru,)
    return warning


def _assert_rollover_broken_link_stays_red(
    result: PRTranslationResult,
    history: LinkHistory,
) -> None:
    assert history.owner_b_en is not None
    blockers = [
        blocker for blocker in result.final_tree_blockers if blocker.code == "en_link_target"
    ]
    assert blockers
    assert any(
        history.owner_b_en in blocker.message or "a18-late-owner-b" in blocker.message
        for blocker in blockers
    )
    assert result.publication_impact is PublicationImpact.PUBLISH_RED
    assert result_has_blocking_findings(result)


def test_a18_late_twentieth_admission_is_reconstructed_by_fresh_verify_from_artifact(
    tmp_path: Path,
) -> None:
    """A is slot 20 and B stays denied across an independent A05 reload."""
    history = _late_rollover_history(tmp_path)
    assert history.owner_a_en is not None and history.owner_b_en is not None
    github = A18GitHub(
        history,
        allow_draft_conversion=True,
        allow_draft_creation=True,
    )
    producer_observed = RuntimeObservation(preserve_late_fragment_links=True)
    with _workflow_runtime(history, github, producer_observed):
        producer = workflow.run_doc_translate(
            repo_path=str(history.repo),
            github_repo=REPO_ID,
            pr_number=SOURCE_PR,
            merge_base_with="origin/main",
            config=_config(skip_globs=()),
        )

    assert producer.committed and producer.pushed
    assert history.candidate_shas
    body = github.translation_body
    artifact = parse_authority_evidence(body)
    candidate = artifact.candidate_sha
    published_tip = history.candidate_shas[-1]

    # Exact A05 H0/H/B/R/C evidence proves this same-repo open producer prepared
    # C on H. A and B both lacked their anchors in that frozen parent.
    assert artifact.authority.source_base_sha == history.h0
    assert artifact.authority.source_head_sha == history.h
    assert artifact.authority.baseline_sha == history.h0
    assert artifact.authority.ru_sha == history.h
    assert _git(history.repo, "rev-parse", f"{candidate}^") == history.h
    assert "a18-late-owner-a" not in _git(history.repo, "show", f"{history.h}:{history.owner_a_en}")
    assert "a18-late-owner-b" not in _git(history.repo, "show", f"{history.h}:{history.owner_b_en}")

    producer_plan = producer_observed.plans[0]
    assert producer_plan.doc_from_main == frozenset(history.targets)
    assert len(producer_plan.doc_from_main) == BUDGET - 1
    assert producer_plan.link_dep_warnings == ()

    # This is a new process-shaped consumer: fresh checkout, new fake transport,
    # new observations, the producer's exact body, and no inherited warning or
    # mutable budget argument. The real verifier must derive spent A from P..C.
    consumer = _clone_consumer(tmp_path, history)
    assert _git(consumer, "rev-parse", "HEAD") == published_tip
    fresh_history = replace(history, repo=consumer, candidate_shas=[])
    fresh_github = A18GitHub(
        fresh_history,
        translation_body=body,
        remote_heads={f"ydbdoc-review/pr-{SOURCE_PR}": published_tip},
        allow_draft_conversion=True,
        translation_draft=True,
    )
    verifier_observed = RuntimeObservation()
    with _workflow_runtime(fresh_history, fresh_github, verifier_observed):
        verified = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(skip_globs=()),
            skip_ops_gates=True,
        )

    assert fresh_github.body_reads == [body]
    assert len(verifier_observed.plans) == 1
    verify_plan = verifier_observed.plans[0]
    assert verify_plan.doc_from_main == frozenset(history.targets)
    assert len(verify_plan.doc_from_main) == BUDGET - 1

    # A consumed the twentieth slot in C even though the repaired anchor now
    # exists. Resetting at verify would skip A, incorrectly admit B, and erase
    # the one durable manual-action warning. Denial does not suppress the real
    # broken-fragment gate left by B: that independent blocker remains RED.
    assert "a18-late-owner-a" in _git(consumer, "show", f"{candidate}:{history.owner_a_en}")
    assert "a18-late-owner-b" not in _git(consumer, "show", f"{candidate}:{history.owner_b_en}")
    changed_from_prepared_parent = set(
        _git(consumer, "diff", "--name-only", history.h, candidate).splitlines()
    )
    assert history.owner_a_en in changed_from_prepared_parent
    assert history.owner_b_en not in changed_from_prepared_parent
    warning = _assert_only_rollover_owner_b_warning(producer.pr_result, history)
    assert producer_observed.inline_jobs
    inline_result = producer_observed.inline_jobs[-1].pr_result
    assert (
        _assert_only_rollover_owner_b_warning(
            inline_result,
            history,
        )
        == warning
    )
    assert _assert_only_rollover_owner_b_warning(verified.pr_result, history) == warning
    assert verify_plan.link_dep_warnings == (warning,)
    _assert_rollover_broken_link_stays_red(producer.pr_result, history)
    _assert_rollover_broken_link_stays_red(inline_result, history)
    _assert_rollover_broken_link_stays_red(verified.pr_result, history)
    assert github.created_pull_drafts == [True]
    assert github.draft_conversions
    assert set(github.draft_conversions) == {TRANSLATION_PR}
    assert fresh_github.draft_conversions
    assert set(fresh_github.draft_conversions) == {TRANSLATION_PR}
    assert len(history.candidate_shas) == 1
    assert fresh_history.candidate_shas == []
    assert published_tip == candidate


def test_a18_fresh_verify_recovers_late_owner_through_c_only_planned_include_wrapper(
    tmp_path: Path,
) -> None:
    """C supplies planned EN pages; owner candidates stay frozen at P and RU at R."""
    history = _hybrid_include_rollover_history(tmp_path)
    assert history.owner_a_ru and history.owner_a_en
    assert history.owner_b_ru and history.owner_b_en
    github = A18GitHub(
        history,
        allow_draft_conversion=True,
        allow_draft_creation=True,
    )
    produced = RuntimeObservation(preserve_late_fragment_links=True)
    with _workflow_runtime(history, github, produced):
        producer = workflow.run_doc_translate(
            repo_path=str(history.repo),
            github_repo=REPO_ID,
            pr_number=SOURCE_PR,
            merge_base_with="origin/main",
            config=_config(skip_globs=()),
        )

    evidence = parse_authority_evidence(github.translation_body)
    candidate = evidence.candidate_sha
    prepared_parent = _git(history.repo, "rev-parse", f"{candidate}^")
    wrapper_en = counterpart(history.targets[1], DOCS_ROOT)
    assert wrapper_en is not None
    assert prepared_parent == history.h
    assert evidence.authority.ru_sha == history.h
    assert "a18-hybrid-owner-a" in _git(
        history.repo, "show", f"{evidence.authority.ru_sha}:{history.owner_a_ru}"
    )
    wrapper_at_parent = subprocess.run(
        ["git", "-C", str(history.repo), "cat-file", "-e", f"{prepared_parent}:{wrapper_en}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert wrapper_at_parent.returncode != 0
    assert "{% include" in _git(history.repo, "show", f"{candidate}:{wrapper_en}")
    assert "a18-hybrid-owner-a" not in _git(
        history.repo, "show", f"{prepared_parent}:{history.owner_a_en}"
    )
    assert "a18-hybrid-owner-a" in _git(history.repo, "show", f"{candidate}:{history.owner_a_en}")
    assert "a18-hybrid-owner-b" not in _git(
        history.repo, "show", f"{candidate}:{history.owner_b_en}"
    )
    assert len(produced.plans[0].doc_from_main) == BUDGET - 1
    assert produced.plans[0].dependency_budget.admitted_ru_paths == frozenset(history.targets)
    warning = _assert_only_rollover_owner_b_warning(producer.pr_result, history)
    assert produced.inline_jobs
    assert (
        _assert_only_rollover_owner_b_warning(
            produced.inline_jobs[-1].pr_result,
            history,
        )
        == warning
    )

    # Inline recursive verification must retain A through recovered source scope,
    # not restore its P bytes and create a second candidate.
    assert history.candidate_shas == [candidate]
    assert "a18-hybrid-owner-a" in _git(history.repo, "show", f"{candidate}:{history.owner_a_en}")

    published_tip = history.candidate_shas[-1]
    consumer = _clone_consumer(tmp_path, history)
    fresh_history = replace(history, repo=consumer, candidate_shas=[])
    fresh_github = A18GitHub(
        fresh_history,
        translation_body=github.translation_body,
        remote_heads={f"ydbdoc-review/pr-{SOURCE_PR}": published_tip},
        allow_draft_conversion=True,
        translation_draft=True,
    )
    verified_observation = RuntimeObservation()
    with _workflow_runtime(fresh_history, fresh_github, verified_observation):
        verified = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(skip_globs=()),
            skip_ops_gates=True,
        )

    assert fresh_github.body_reads == [github.translation_body]
    assert len(verified_observation.plans) == 1
    recovered_budget = verified_observation.plans[0].dependency_budget
    assert history.owner_a_ru in recovered_budget.admitted_ru_paths
    assert history.owner_b_ru not in recovered_budget.admitted_ru_paths
    assert _assert_only_rollover_owner_b_warning(verified.pr_result, history) == warning
    assert verified_observation.plans[0].link_dep_warnings == (warning,)
    assert fresh_history.candidate_shas == []
    assert _git(consumer, "rev-parse", "HEAD") == published_tip
    assert "a18-hybrid-owner-a" in _git(consumer, "show", f"HEAD:{history.owner_a_en}")


def test_a18_recovery_rejects_en_only_invented_fragment_href_without_ru_carrier_evidence() -> None:
    """A C-only EN href cannot manufacture a late proposal, D, U, or source scope."""
    directory = f"{EN_ROOT}/carrier-evidence"
    target_en = f"{directory}/target.md"
    target_ru = counterpart(target_en, DOCS_ROOT)
    invented_en = f"{directory}/invented.md"
    invented_ru = counterpart(invented_en, DOCS_ROOT)
    control_en = f"{directory}/control.md"
    control_ru = counterpart(control_en, DOCS_ROOT)
    assert target_ru and invented_ru and control_ru
    fragment = "a18-source-valid"
    candidate = {
        target_ru: f"# RU target {{#{fragment}}}\n",
        target_en: "# EN target without declaration\n",
        invented_ru: "# RU carrier has no fragment link\n",
        control_ru: f"# RU carrier\n\n[target](./target.md#{fragment})\n",
    }
    artifact_pages = {
        invented_en: f"# Invented EN carrier\n\n[target](target.md#{fragment})\n",
        control_en: f"# Control EN carrier\n\n[target](target.md#{fragment})\n",
        target_en: f"# EN target without declaration {{#{fragment}}}\n",
    }

    invented_ambiguous: list[workflow._AmbiguousExactFragmentOwner] = []
    invented = workflow._discover_exact_ascii_fragment_declaration_proposals(
        (invented_en,),
        read_page=artifact_pages.get,
        read_candidate=candidate.get,
        docs_root=DOCS_ROOT,
        ambiguous_out=invented_ambiguous,
    )
    control = workflow._discover_exact_ascii_fragment_declaration_proposals(
        (control_en,),
        read_page=artifact_pages.get,
        read_candidate=candidate.get,
        docs_root=DOCS_ROOT,
    )

    # The target's P->C bytes could coincidentally equal this exact repair; the
    # absent source-valid carrier edge at R must prevent reaching that evidence.
    assert invented == ()
    assert invented_ambiguous == []
    assert len(control) == 1
    assert control[0].ru_owner_path == target_ru
    assert control[0].en_owner_path == target_en
    assert fragment in control[0].after_en_text
    assert artifact_pages[target_en] == control[0].after_en_text


@pytest.mark.parametrize(
    "protected_carrier",
    (
        "```markdown\n[x](target.md#anchor)\n```",
        "`[x](target.md#anchor)`",
        "<!-- [x](target.md#anchor) -->",
    ),
    ids=("fenced-code", "inline-code", "html-comment"),
)
def test_a18_fragment_owner_discovery_and_recovery_ignore_protected_carrier_links(
    tmp_path: Path,
    protected_carrier: str,
) -> None:
    """Only active RU+EN carrier links can authorize an exact late repair."""
    directory = f"{EN_ROOT}/protected-carrier"
    target_en = f"{directory}/target.md"
    target_ru = counterpart(target_en, DOCS_ROOT)
    carrier_en = f"{directory}/carrier.md"
    carrier_ru = counterpart(carrier_en, DOCS_ROOT)
    assert target_ru and carrier_ru
    fragment = "anchor"
    ru_target = f"# RU target {{#{fragment}}}\n"
    en_before = "# EN target\n"
    en_after = f"# EN target {{#{fragment}}}\n"
    active_carrier = "[x](target.md#anchor)\n"

    def discover(
        ru_carrier: str, en_carrier: str
    ) -> tuple[workflow._ExactFragmentDeclarationProposal, ...]:
        candidate = {
            carrier_ru: ru_carrier,
            target_ru: ru_target,
            target_en: en_before,
        }
        artifact = {carrier_en: en_carrier, target_en: en_before}
        return workflow._discover_exact_ascii_fragment_declaration_proposals(
            (carrier_en,),
            read_page=artifact.get,
            read_candidate=candidate.get,
            docs_root=DOCS_ROOT,
        )

    def recover(
        ru_carrier: str, en_carrier: str, name: str
    ) -> tuple[frozenset[str], TranslationScopePlan, str]:
        repo = tmp_path / f"a18-protected-{name}"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "a18-protected@example.test")
        _git(repo, "config", "user.name", "A18 Protected Carrier")
        _write(repo, carrier_ru, ru_carrier)
        _write(repo, target_ru, ru_target)
        _write(repo, carrier_en, en_carrier)
        _write(repo, target_en, en_before)
        parent = _commit(repo, "A18 protected carrier parent")
        _write(repo, target_en, en_after)
        candidate_sha = _commit(repo, "A18 exact owner repair candidate")
        budget = MarkdownDependencyBudget(roots=(carrier_ru,), limit=BUDGET)
        plan = TranslationScopePlan(
            doc_ru_paths=frozenset({carrier_ru}),
            doc_from_diff=frozenset({carrier_ru}),
            doc_from_main=frozenset(),
            nav_ru_paths=frozenset(),
            nav_from_diff=frozenset(),
            nav_from_main=frozenset(),
            dependency_budget=budget,
        )
        provenance = TranslationArtifactProvenance(
            authority=RuAuthority(
                source_repo=REPO_ID,
                source_pr=SOURCE_PR,
                source_base_sha=parent,
                source_head_sha=parent,
                baseline_sha=parent,
                ru_sha=parent,
                mode=RuAuthorityMode.CURRENT,
            ),
            candidate_sha=candidate_sha,
        )
        recovered = workflow._reconstruct_late_dependency_budget_for_verify(
            str(repo),
            plan,
            provenance,
            verified_commit_sha=candidate_sha,
            docs_root=DOCS_ROOT,
        )
        return recovered, plan, candidate_sha

    for case, (ru_carrier, en_carrier) in {
        "ru-protected": (protected_carrier, active_carrier),
        "en-protected": (active_carrier, protected_carrier),
        "both-active": (active_carrier, active_carrier),
    }.items():
        proposals = discover(ru_carrier, en_carrier)
        if case == "both-active":
            assert len(proposals) == 1
            proposal = proposals[0]
            assert proposal.ru_owner_path == target_ru
            assert proposal.en_owner_path == target_en
            assert proposal.after_en_text == en_after
        else:
            assert proposals == ()

        recovered, plan, candidate_sha = recover(ru_carrier, en_carrier, case)
        candidate_owner = subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path / f"a18-protected-{case}"),
                "show",
                f"{candidate_sha}:{target_en}",
            ],
            check=True,
            capture_output=True,
        ).stdout
        assert candidate_owner == en_after.encode("utf-8")
        if case == "both-active":
            assert recovered == frozenset({target_en})
            assert plan.dependency_budget.admitted_ru_paths == frozenset({target_ru})
        else:
            assert recovered == frozenset()
            assert plan.dependency_budget.admitted_ru_paths == frozenset()
            assert plan.dependency_budget.snapshot().uncertain_ru_paths == frozenset()
            assert target_ru not in plan.doc_ru_paths
            assert target_ru not in plan.dependency_budget.snapshot().denied_ru_paths


def test_a18_artifact_proven_replay_precedes_uncertainty_barrier_for_new_admissions() -> None:
    """U does not consume a slot or veto independently proven P->C evidence."""
    roots = (f"{RU_ROOT}/replay/root.md",)
    budget = MarkdownDependencyBudget(roots, limit=BUDGET)
    admitted = tuple(f"{RU_ROOT}/replay/dependency-{index:02d}.md" for index in range(18))
    controls = [budget.admit(path) for path in admitted]
    ambiguous_a = f"{RU_ROOT}/replay/ambiguous-a.md"
    proven_b = f"{RU_ROOT}/replay/proven-b.md"
    new_c = f"{RU_ROOT}/replay/new-c.md"

    budget.mark_uncertain(
        ambiguous_a,
        reason="source-valid owner exists but exact artifact proposal is ambiguous",
    )
    replayed = budget.replay_proven(proven_b)
    root_still_allowed = budget.admit(roots[0])
    admitted_still_allowed = budget.admit(admitted[0])
    new_allowed = budget.admit(new_c, warning_path=counterpart(new_c, DOCS_ROOT))

    assert all(controls)
    assert replayed
    assert proven_b in budget.admitted_ru_paths
    assert len(budget.admitted_ru_paths) == 19
    assert root_still_allowed and admitted_still_allowed
    assert not new_allowed
    assert new_c not in budget.admitted_ru_paths
    assert len(budget.warnings) == 1
    assert counterpart(new_c, DOCS_ROOT) in budget.warnings[0]
    assert "не удалось восстановить доступный бюджет" in budget.warnings[-1]
    assert "manual action" in budget.warnings[-1]
    assert "budget exhausted" not in budget.warnings[-1]


def test_a18_late_fragment_admission_follows_document_order_not_owner_sort(
    tmp_path: Path,
) -> None:
    """With one slot, the first href z wins even though a sorts first."""
    repo = tmp_path / "a18-document-order"
    carrier_ru = f"{RU_ROOT}/document-order/carrier.md"
    carrier_en = counterpart(carrier_ru, DOCS_ROOT)
    z_ru = f"{RU_ROOT}/document-order/z.md"
    a_ru = f"{RU_ROOT}/document-order/a.md"
    z_en = counterpart(z_ru, DOCS_ROOT)
    a_en = counterpart(a_ru, DOCS_ROOT)
    assert carrier_en and z_en and a_en
    _write(
        repo,
        carrier_ru,
        "# RU carrier\n\n[z](z.md#a18-z) [a](a.md#a18-a)\n",
    )
    _write(
        repo,
        carrier_en,
        "# EN carrier\n\n[z](z.md#a18-z) [a](a.md#a18-a)\n",
    )
    _write(repo, z_ru, "# Z {#a18-z}\n")
    _write(repo, a_ru, "# A {#a18-a}\n")
    z_before = "# Z without declaration\n"
    a_before = "# A without declaration\n"
    _write(repo, z_en, z_before)
    _write(repo, a_en, a_before)
    budget = MarkdownDependencyBudget(limit=1)

    declared = workflow._declare_exact_ascii_fragment_targets_after_apply(
        str(repo),
        [carrier_en],
        dry_run=False,
        budget=budget,
    )

    assert declared == [z_en]
    assert "{#a18-z}" in (repo / z_en).read_text(encoding="utf-8")
    assert (repo / a_en).read_text(encoding="utf-8") == a_before
    assert budget.admitted_ru_paths == frozenset({z_ru})
    assert len(budget.warnings) == 1
    assert a_en in budget.warnings[0]


@dataclass(frozen=True)
class RolloverOracleSnapshot:
    admitted: tuple[str, ...]
    denied: tuple[str, ...]


def _assert_rollover_oracle(snapshot: RolloverOracleSnapshot) -> None:
    assert len(snapshot.admitted) == BUDGET
    assert snapshot.admitted[-1] == "late-owner-a"
    assert snapshot.denied == ("late-owner-b",)


def test_a18_supplemental_rollover_oracle_rejects_reset_that_skips_a() -> None:
    """Synthetic sensitivity only, not a behavioral/product mutation control."""
    dependencies = tuple(f"dependency-{index:02d}" for index in range(BUDGET - 1))
    control = RolloverOracleSnapshot(
        admitted=(*dependencies, "late-owner-a"),
        denied=("late-owner-b",),
    )
    _assert_rollover_oracle(control)
    reset_mutant = replace(
        control,
        admitted=(*dependencies, "late-owner-b"),
        denied=(),
    )
    with pytest.raises(AssertionError):
        _assert_rollover_oracle(reset_mutant)
    _assert_rollover_oracle(control)


@dataclass
class LateHistory:
    repo: Path
    h0: str
    h: str
    changes: tuple[tuple[str, str], ...]
    owner_ru: str
    owner_en: str
    owner_en_original: str
    expected_extras: int


def _late_history(
    tmp_path: Path,
    *,
    dependency_count: int | None = None,
    source_root_count: int | None = None,
) -> LateHistory:
    assert (dependency_count is None) != (source_root_count is None)
    suffix = (
        f"deps-{dependency_count}" if dependency_count is not None else f"roots-{source_root_count}"
    )
    repo = tmp_path / f"a18-late-{suffix}"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a18@example.test")
    _git(repo, "config", "user.name", "A18 Contract")
    owner_ru = f"{RU_ROOT}/late/aligned-owner.md"
    owner_en = counterpart(owner_ru, DOCS_ROOT)
    assert owner_en is not None
    owner_en_original = "# Aligned owner without explicit declaration\n"
    _write(repo, owner_ru, "# Aligned owner {#a18-aligned-owner}\n")
    _write(repo, owner_en, owner_en_original)

    changes: list[tuple[str, str]] = []
    if dependency_count is not None:
        deps = tuple(
            f"{RU_ROOT}/late/dependency-{index:02d}.md" for index in range(dependency_count)
        )
        for index, dep in enumerate(deps):
            body = f"# Dependency {index}\n"
            if index == 0:
                body += (
                    "\n{% include [aligned owner](aligned-owner.md) %}\n\n"
                    "[aligned owner](dependency-00.md#a18-aligned-owner)\n"
                )
            _write(repo, dep, body)
        h0 = _commit(repo, "A18 late baseline with RU dependencies")
        root = f"{RU_ROOT}/late/root.md"
        links = "\n".join(
            f"[dependency {index}]({Path(dep).name})" for index, dep in enumerate(deps)
        )
        _write(repo, root, f"# Source root\n\n{links}\n")
        changes.append((root, "added"))
        expected_extras = dependency_count
    else:
        dependency = f"{RU_ROOT}/late/free-root-dependency.md"
        _write(
            repo,
            dependency,
            "# Free-root dependency\n\n"
            "{% include [aligned owner](aligned-owner.md) %}\n\n"
            "[aligned owner](free-root-dependency.md#a18-aligned-owner)\n",
        )
        h0 = _commit(repo, "A18 late baseline for free source roots")
        assert source_root_count is not None
        for index in range(source_root_count):
            root = f"{RU_ROOT}/late/source-root-{index:02d}.md"
            body = f"# Source root {index}\n"
            if index == 0:
                body += "\n[dependency](free-root-dependency.md)\n"
            _write(repo, root, body)
            changes.append((root, "added"))
        expected_extras = 0
    h = _commit(repo, "A18 late source")
    _git(repo, "checkout", "--detach", h)
    return LateHistory(
        repo,
        h0,
        h,
        tuple(changes),
        owner_ru,
        owner_en,
        owner_en_original,
        expected_extras,
    )


@dataclass
class SourceGitHub:
    history: LateHistory

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == SOURCE_PR
        return _pull(
            number=SOURCE_PR,
            head_ref="source-a18-late",
            head_sha=self.history.h,
            base_sha=self.history.h0,
        )

    def iter_pull_files(self, owner: str, repo: str, number: int) -> Iterator[dict[str, str]]:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == SOURCE_PR
        for path, status in self.history.changes:
            yield {"filename": path, "status": status}

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        return None


def _late_model_result(contents: list[PairContent]) -> PRTranslationResult:
    result = _model_result(contents, verify=False)
    for content, run in zip(contents, result.pair_results, strict=True):
        if "[aligned owner]" not in (content.ru_text or ""):
            continue
        target = (
            f"# EN {Path(content.pair.en_path).stem}\n\n"
            "{% include [aligned owner](aligned-owner.md) %}\n\n"
            f"[aligned owner]({Path(content.pair.en_path).name}#a18-aligned-owner)\n"
        )
        run.target_text = target
        assert run.file_result is not None
        run.file_result.final_text = target
    return result


@pytest.mark.parametrize(
    "case",
    ("rollover", "hybrid", "late-nineteen", "late-twenty", "free-source-roots"),
    ids=("rollover", "hybrid", "late-nineteen", "late-twenty", "free-source-roots"),
)
def test_a18_late_owner_requires_candidate_only_wrapper(
    tmp_path: Path,
    case: str,
) -> None:
    if case == "rollover":
        history = _late_rollover_history(tmp_path)
        carrier = wrapper = history.targets[0]
        owner_fragments = (
            (history.owner_a_ru, "a18-late-owner-a"),
            (history.owner_b_ru, "a18-late-owner-b"),
        )
    elif case == "hybrid":
        history = _hybrid_include_rollover_history(tmp_path)
        carrier = history.targets[0]
        wrapper = history.targets[1]
        owner_fragments = (
            (history.owner_a_ru, "a18-hybrid-owner-a"),
            (history.owner_b_ru, "a18-hybrid-owner-b"),
        )
    else:
        dependency_count = {
            "late-nineteen": BUDGET - 1,
            "late-twenty": BUDGET,
        }.get(case)
        source_root_count = BUDGET + 5 if case == "free-source-roots" else None
        history = _late_history(
            tmp_path,
            dependency_count=dependency_count,
            source_root_count=source_root_count,
        )
        carrier = wrapper = (
            f"{RU_ROOT}/late/free-root-dependency.md"
            if case == "free-source-roots"
            else f"{RU_ROOT}/late/dependency-00.md"
        )
        owner_fragments = ((history.owner_ru, "a18-aligned-owner"),)

    def read_ru(path: str) -> str | None:
        return read_text_at_commit(str(history.repo), history.h, path)

    def read_en_base(path: str) -> str | None:
        return read_text_at_commit(str(history.repo), history.h0, path)

    carrier_text = read_ru(carrier)
    wrapper_text = read_ru(wrapper)
    assert carrier_text is not None
    assert wrapper_text is not None

    en_wrapper = counterpart(wrapper, DOCS_ROOT)
    assert en_wrapper is not None
    assert read_en_base(en_wrapper) is None
    assert read_text_at_commit(str(history.repo), history.h, en_wrapper) is None

    ru_include_owners = tuple(
        resolve_locale_md_path(wrapper, include.path, docs_root=DOCS_ROOT)
        for include in collect_yfm_includes(wrapper_text)
    )
    expected_ru_owners = tuple(owner for owner, _fragment in owner_fragments)
    assert ru_include_owners == expected_ru_owners
    assert tuple(counterpart(owner, DOCS_ROOT) for owner in ru_include_owners) == tuple(
        counterpart(owner, DOCS_ROOT) for owner in expected_ru_owners
    )

    for owner, fragment in owner_fragments:
        href = f"{Path(wrapper).name}#{fragment}"
        assert f"]({href})" in carrier_text
        ru_owner_text = read_ru(owner)
        en_owner = counterpart(owner, DOCS_ROOT)
        assert ru_owner_text is not None
        assert en_owner is not None
        assert _page_declares_fragment(ru_owner_text, fragment)
        en_owner_base_text = read_en_base(en_owner)
        en_owner_head_text = read_text_at_commit(str(history.repo), history.h, en_owner)
        assert en_owner_base_text is not None
        assert en_owner_head_text is not None
        assert not _page_declares_fragment(en_owner_base_text, fragment)
        assert not _page_declares_fragment(en_owner_head_text, fragment)
        assert (
            _exact_ascii_fragment_owner_dependency(
                carrier,
                href,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=DOCS_ROOT,
            )
            is None
        )


def test_a18_direct_owner_visible_at_baseline_stays_ordinary_dependency(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "a18-ordinary-owner-control"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a18-ordinary@example.test")
    _git(repo, "config", "user.name", "A18 Ordinary Owner Control")

    directory = f"{RU_ROOT}/ordinary-owner-control"
    root = f"{directory}/root.md"
    dependency = f"{directory}/z-dependency.md"
    owner = f"{directory}/a-owner.md"
    en_owner = counterpart(owner, DOCS_ROOT)
    assert en_owner is not None
    _write(
        repo,
        dependency,
        "# Dependency\n\n[a owner](a-owner.md#a18-ordinary-owner)\n",
    )
    _write(repo, owner, "# Owner {#a18-ordinary-owner}\n")
    _write(repo, en_owner, "# Owner without the explicit anchor\n")
    h0 = _commit(repo, "A18 ordinary owner baseline")
    _write(repo, root, "# Root\n\n[dependency](z-dependency.md)\n")
    h = _commit(repo, "A18 ordinary owner source")

    def read_ru(path: str) -> str | None:
        return read_text_at_commit(str(repo), h, path)

    def read_en_base(path: str) -> str | None:
        return read_text_at_commit(str(repo), h0, path)

    successful_admissions: list[str] = []
    original_admit = MarkdownDependencyBudget.admit

    def recording_admit(
        budget: MarkdownDependencyBudget,
        ru_path: str,
        *,
        warning_path: str | None = None,
    ) -> bool:
        before = budget.admitted_ru_paths
        admitted = original_admit(budget, ru_path, warning_path=warning_path)
        normalized = ru_path.replace("\\", "/")
        if admitted and normalized not in before and normalized in budget.admitted_ru_paths:
            successful_admissions.append(normalized)
        return admitted

    with patch.object(MarkdownDependencyBudget, "admit", recording_admit):
        plan = plan_translation_scope(
            [(root, "added")],
            read_ru=read_ru,
            read_en_base=read_en_base,
            read_ru_base=read_en_base,
            docs_root=DOCS_ROOT,
        )

    assert plan.doc_from_diff == frozenset({root})
    assert plan.doc_from_main == frozenset({dependency, owner})
    assert plan.dependency_budget.admitted_ru_paths == frozenset({dependency, owner})
    assert plan.link_dep_warnings == ()
    assert plan.nav_ru_paths == frozenset()
    assert plan.nav_from_diff == frozenset()
    assert plan.nav_from_main == frozenset()
    assert successful_admissions == [dependency, owner]


def _run_to_late_declaration(
    history: LateHistory,
) -> tuple[TranslationScopePlan, PRTranslationResult]:
    github = SourceGitHub(history)
    real_plan = workflow.plan_translation_scope
    plans: list[TranslationScopePlan] = []
    results: list[PRTranslationResult] = []

    class StopAfterDeclaration(RuntimeError):
        pass

    def capture_plan(*args: object, **kwargs: object) -> TranslationScopePlan:
        plan = real_plan(*args, **kwargs)
        plans.append(plan)
        return plan

    def translate_model(
        contents: list[PairContent], *_args: object, **_kwargs: object
    ) -> PRTranslationResult:
        result = _late_model_result(contents)
        results.append(result)
        return result

    def stop_after_declaration(*_args: object, **_kwargs: object) -> list[str]:
        raise StopAfterDeclaration

    with (
        patch.object(workflow, "GitHubClient", return_value=github),
        patch.object(
            workflow,
            "begin_ops_job",
            return_value=(None, GateResult(ok=True), None),
        ),
        patch.object(workflow, "create_llm_client", return_value=_BoundaryClient()),
        patch.object(workflow, "load_glossary", return_value=Glossary(entries=[])),
        patch.object(workflow, "plan_translation_scope", side_effect=capture_plan),
        patch.object(workflow, "run_pr_translation", side_effect=translate_model),
        patch.object(workflow, "apply_orphan_toc_page_checks", return_value=[]),
        patch.object(
            workflow,
            "apply_en_link_target_checks",
            side_effect=stop_after_declaration,
        ),
    ):
        with pytest.raises(StopAfterDeclaration):
            workflow.run_doc_translate(
                repo_path=str(history.repo),
                github_repo=REPO_ID,
                pr_number=SOURCE_PR,
                merge_base_with=history.h0,
                config=_config(skip_globs=()),
            )
    assert len(plans) == 1
    assert len(results) == 1
    return plans[0], results[0]


@pytest.mark.parametrize(
    ("dependency_count", "expect_repair", "expect_warning"),
    [(BUDGET - 1, True, False), (BUDGET, False, True)],
    ids=("nineteen-admits-late-owner", "twenty-denies-late-owner"),
)
def test_a18_late_declaration_uses_real_ru_admission_not_written_file_count(
    tmp_path: Path,
    dependency_count: int,
    expect_repair: bool,
    expect_warning: bool,
) -> None:
    history = _late_history(tmp_path, dependency_count=dependency_count)
    plan, result = _run_to_late_declaration(history)
    assert len(plan.doc_from_main) == history.expected_extras
    assert plan.link_dep_warnings == ()
    after = (history.repo / history.owner_en).read_text(encoding="utf-8")
    if expect_repair:
        assert after != history.owner_en_original
        assert "{#a18-aligned-owner}" in after
        assert result.yellow_warnings == []
    else:
        assert after == history.owner_en_original
        assert len(result.yellow_warnings) == 1
        assert "budget" in result.yellow_warnings[0].lower()
        assert "manual" in result.yellow_warnings[0].lower()
        assert (
            history.owner_en in result.yellow_warnings[0]
            or history.owner_ru in result.yellow_warnings[0]
        )
    assert bool(result.yellow_warnings) is expect_warning


def test_a18_more_than_twenty_source_roots_are_free_for_one_late_repair(
    tmp_path: Path,
) -> None:
    history = _late_history(tmp_path, source_root_count=BUDGET + 5)
    plan, result = _run_to_late_declaration(history)
    assert len(plan.doc_from_diff) == BUDGET + 5
    assert plan.doc_from_main == frozenset({f"{RU_ROOT}/late/free-root-dependency.md"})
    after = (history.repo / history.owner_en).read_text(encoding="utf-8")
    assert after != history.owner_en_original
    assert "{#a18-aligned-owner}" in after
    assert result.yellow_warnings == []


@dataclass(frozen=True)
class MutationSnapshot:
    admitted: tuple[str, ...]
    denied: tuple[str, ...]
    denied_warning_targets: tuple[str, ...]
    rebuilt_warning: str | None
    result_warning: str | None
    recursive_warning: str | None
    full_report: str
    source_report: str
    late_nineteen_changed: bool
    late_twenty_changed: bool
    late_twenty_warning: str | None
    source_roots: int
    many_roots_late_changed: bool


def _assert_mutation_snapshot(snapshot: MutationSnapshot, warning: str) -> None:
    assert len(snapshot.admitted) == BUDGET
    assert len(snapshot.denied) == 8
    assert len(set(snapshot.denied)) == 8
    assert snapshot.denied_warning_targets == snapshot.denied
    assert snapshot.rebuilt_warning == warning
    assert snapshot.result_warning == warning
    assert snapshot.recursive_warning == warning
    assert snapshot.full_report.count(warning) == 1
    assert snapshot.source_report.count(warning) == 1
    assert snapshot.late_nineteen_changed
    assert not snapshot.late_twenty_changed
    assert snapshot.late_twenty_warning == warning
    assert snapshot.source_roots > BUDGET
    assert snapshot.many_roots_late_changed


@pytest.mark.parametrize(
    "mutation",
    [
        "20-to-21",
        "stage-reset",
        "reconstruction-loss",
        "result-loss",
        "recursion-loss",
        "source-comment-drop",
        "late-bypass",
        "roots-by-written",
    ],
)
def test_a18_supplemental_snapshot_oracle_has_control_failure_and_recovery(
    mutation: str,
) -> None:
    """Synthetic sensitivity only, not a behavioral or product mutation control."""
    admitted = tuple(f"a{index:02d}" for index in range(BUDGET))
    denied = tuple(f"d{index:02d}" for index in range(8))
    warning = "A18 dependency budget warning"
    control = MutationSnapshot(
        admitted=admitted,
        denied=denied,
        denied_warning_targets=denied,
        rebuilt_warning=warning,
        result_warning=warning,
        recursive_warning=warning,
        full_report=f"full: {warning}",
        source_report=f"source: {warning}",
        late_nineteen_changed=True,
        late_twenty_changed=False,
        late_twenty_warning=warning,
        source_roots=BUDGET + 5,
        many_roots_late_changed=True,
    )
    _assert_mutation_snapshot(control, warning)
    if mutation == "20-to-21":
        mutant = replace(control, admitted=(*control.admitted, "a20"))
    elif mutation == "stage-reset":
        mutant = replace(
            control,
            admitted=(*control.admitted, *control.denied),
            denied=(),
            denied_warning_targets=(),
        )
    elif mutation == "reconstruction-loss":
        mutant = replace(control, rebuilt_warning=None)
    elif mutation == "result-loss":
        mutant = replace(control, result_warning=None)
    elif mutation == "recursion-loss":
        mutant = replace(control, recursive_warning=None)
    elif mutation == "source-comment-drop":
        mutant = replace(control, source_report="source without warning")
    elif mutation == "late-bypass":
        mutant = replace(
            control,
            late_twenty_changed=True,
            late_twenty_warning=None,
        )
    elif mutation == "roots-by-written":
        mutant = replace(control, many_roots_late_changed=False)
    else:  # pragma: no cover
        raise AssertionError(mutation)
    with pytest.raises(AssertionError):
        _assert_mutation_snapshot(mutant, warning)
    _assert_mutation_snapshot(control, warning)
