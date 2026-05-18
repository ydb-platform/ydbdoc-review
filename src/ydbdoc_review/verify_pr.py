"""Verify translation PR: EN on PR branch vs RU on merge base (main)."""

from __future__ import annotations

import os
import re
from typing import Any

import click

from ydbdoc_review import git_local, github_api
from ydbdoc_review.config import Settings
from ydbdoc_review.paths import pairs_from_changed_files
from ydbdoc_review.translation_qa import (
    PairQaOutcome,
    format_pair_qa_markdown,
    format_translation_pr_summary,
    pr_merge_blocked,
    pr_merge_verdict_unavailable,
    run_pair_qa_repair,
)
from ydbdoc_review.translate_postprocess import (
    collect_quality_gate_failures,
    translation_quality_issues,
)


def parse_source_pr_number(*texts: str | None) -> int | None:
    """Extract doc PR number from translation PR title/body."""
    patterns = (
        r"Translation of PR\s+#?(\d+)",
        r"Translation PR for\s+#?(\d+)",
        r"для PR\s+#?(\d+)",
        r"pull/(\d+)",
    )
    for raw in texts:
        if not raw:
            continue
        for pat in patterns:
            m = re.search(pat, raw, re.IGNORECASE)
            if m:
                return int(m.group(1))
    return None


def _source_pr_ru_diff(
    workdir: str,
    base_ref: str,
    ru_path: str,
    *,
    source_pr: dict[str, Any] | None,
) -> str | None:
    """RU file diff introduced by the source documentation PR (if merged)."""
    if not source_pr or not workdir:
        return None
    merge_sha = source_pr.get("merge_commit_sha")
    if not isinstance(merge_sha, str) or not merge_sha.strip():
        return None
    try:
        return git_local.file_diff_between_refs(
            workdir, base_ref, merge_sha, ru_path
        )
    except RuntimeError:
        return None


def _build_verify_comment(
    *,
    pr_number: int,
    base_ref: str,
    source_pr_number: int | None,
    outcomes: list[PairQaOutcome],
    gate_failures: list[str],
    skipped: list[str],
) -> str:
    lines = [
        "## ydbdoc-review — doc_verify",
        "",
        f"Self-check translation PR **#{pr_number}**: EN на ветке PR vs RU на **`{base_ref}`**.",
    ]
    if source_pr_number is not None:
        lines.append(f"_Исходный doc PR: #{source_pr_number}._")
    lines.append("")

    if skipped:
        lines.extend(
            [
                "### Пропущено",
                "",
                "Нет EN-файла в diff этого PR (проверка только затронутых EN):",
                "",
                *[f"- `{p}`" for p in skipped],
                "",
            ]
        )

    if gate_failures:
        lines.extend(
            [
                "### Quality gate (детерминированно)",
                "",
                *[f"- {line}" for line in gate_failures],
                "",
            ]
        )

    if outcomes:
        summary = format_translation_pr_summary(
            source_pr_number=source_pr_number or pr_number,
            outcomes=outcomes,
        )
        if summary.startswith("## Вердикт для translation PR"):
            summary = "## Вердикт doc_verify" + summary[len("## Вердикт для translation PR") :]
        lines.append(summary)
        lines.append("---")
        lines.append("")
        for o in outcomes:
            lines.append(format_pair_qa_markdown(o))
            lines.append("")

    if not outcomes and not gate_failures:
        lines.append("_Нет пар RU↔EN для проверки в diff PR._")

    return "\n".join(lines).strip()


def run_verify_pr(
    settings: Settings,
    *,
    repo: str,
    pr_number: int,
    repo_path: str | None,
    merge_base_with: str,
    source_pr_number: int | None,
    no_comment: bool,
) -> None:
    """Run critic + translator verify on all EN files changed in the PR."""
    settings.validate_github()
    settings.validate_yandex()

    if not settings.translation_self_check_enabled:
        click.echo(
            "Warning: translation_self_check disabled in config; "
            "doc_verify still runs QA if models are configured.",
            err=True,
        )

    owner, repo_name = repo.split("/", 1)
    pr = github_api.get_pull(owner, repo_name, pr_number, settings.github_token)
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")

    resolved_source = source_pr_number or parse_source_pr_number(title, body)
    source_pr: dict[str, Any] | None = None
    if resolved_source is not None:
        try:
            source_pr = github_api.get_pull(
                owner, repo_name, resolved_source, settings.github_token
            )
            click.echo(f"Linked source documentation PR #{resolved_source}.")
        except Exception as exc:
            click.echo(
                f"Warning: could not load source PR #{resolved_source}: {exc}",
                err=True,
            )

    workdir = repo_path or os.environ.get("YDBDOC_REPO_PATH", "").strip() or None
    if not workdir:
        raise SystemExit(
            "doc_verify requires --repo-path or YDBDOC_REPO_PATH (checkout of PR head)."
        )

    base_ref = github_api.base_ref_from_pr(pr)
    base_clone_url = github_api.base_clone_url_from_pr(pr)

    changed = git_local.local_changed_paths(workdir, merge_base_with)
    pr_changed = {p.replace("\\", "/").lstrip("./") for p in changed}
    pairs = pairs_from_changed_files(changed, settings.docs_prefix)
    if not pairs:
        msg = (
            "## ydbdoc-review — doc_verify\n\n"
            "_В diff PR нет пар markdown RU↔EN под "
            f"`{settings.docs_prefix}/`._"
        )
        if not no_comment:
            github_api.post_issue_comment(
                owner, repo_name, pr_number, msg, settings.github_token
            )
        click.echo("No doc pairs in PR diff.")
        return

    base_ref_local: str | None = None
    try:
        git_local.ensure_remote(
            workdir,
            "ydbdoc-base",
            git_local.remote_push_url(base_clone_url, settings.github_token),
        )
        base_ref_local = git_local.fetch_remote_branch(
            workdir, "ydbdoc-base", base_ref
        )
    except RuntimeError as exc:
        click.echo(f"Warning: could not fetch `{base_ref}`: {exc}", err=True)

    outcomes: list[PairQaOutcome] = []
    gate_pairs: list[tuple[str, str, str, str | None, str | None, str | None]] = []
    skipped: list[str] = []

    click.echo(
        f"doc_verify: {len(pairs)} pair(s), RU authority=`{base_ref_local or base_ref}`, "
        f"critic `{settings.model_translation_verify}` …"
    )

    for pair in pairs:
        if pair.en_path not in pr_changed:
            skipped.append(pair.ru_path)
            continue

        if not base_ref_local:
            click.echo(
                f"  Skip `{pair.en_path}`: could not load RU from `{base_ref}`.",
                err=True,
            )
            skipped.append(pair.en_path)
            continue

        ru_main = git_local.read_text_at_ref(workdir, base_ref_local, pair.ru_path)
        en_pr = git_local.read_text(workdir, pair.en_path) or ""
        en_main = git_local.read_text_at_ref(workdir, base_ref_local, pair.en_path)

        if not ru_main or len(ru_main.strip()) < 30:
            click.echo(f"  Skip `{pair.en_path}`: no RU on base.", err=True)
            skipped.append(pair.en_path)
            continue
        if not en_pr.strip():
            click.echo(f"  Skip `{pair.en_path}`: empty EN on PR.", err=True)
            skipped.append(pair.en_path)
            continue

        ru_pr_diff = _source_pr_ru_diff(
            workdir,
            base_ref_local or merge_base_with,
            pair.ru_path,
            source_pr=source_pr,
        )
        en_pr_diff: str | None = None
        try:
            en_pr_diff = git_local.file_diff_range(
                workdir, merge_base_with, pair.en_path
            )
        except RuntimeError:
            pass

        click.echo(f"  QA `{pair.ru_path}` ↔ `{pair.en_path}` …")
        _new_text, outcome = run_pair_qa_repair(
            settings,
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            target_path=pair.en_path,
            source_text=ru_main,
            translated_text=en_pr,
            source_lang="Russian",
            target_lang="English",
            repair_enabled=False,
            source_pr_number=resolved_source,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_main,
        )
        outcomes.append(outcome)

        q_issues = translation_quality_issues(
            ru_main,
            en_pr,
            target_lang="English",
            en_main=en_main,
            source_diff=en_pr_diff,
            ru_authority=ru_main,
        )
        if q_issues:
            click.echo(
                f"    heuristics: {', '.join(q_issues)}",
                err=True,
            )

        gate_pairs.append((pair.en_path, ru_main, en_pr, en_main, en_pr_diff, ru_main))

    gate_failures = collect_quality_gate_failures(gate_pairs)
    comment = _build_verify_comment(
        pr_number=pr_number,
        base_ref=base_ref,
        source_pr_number=resolved_source,
        outcomes=outcomes,
        gate_failures=gate_failures,
        skipped=skipped,
    )

    click.echo("\n--- doc_verify report ---\n")
    click.echo(comment)
    click.echo("\n--- end ---\n")

    blocked = bool(gate_failures) or (
        outcomes and pr_merge_blocked(outcomes)
    )
    unavailable = outcomes and pr_merge_verdict_unavailable(outcomes)

    if not no_comment:
        github_api.post_issue_comment_chunked(
            owner, repo_name, pr_number, comment, settings.github_token
        )
        click.echo("Posted doc_verify comment on PR.")

    if unavailable:
        raise SystemExit(
            "## ydbdoc-review — doc_verify: вердикт не получен\n\n"
            + comment
        )
    if blocked:
        raise SystemExit(
            "## ydbdoc-review — doc_verify: не готово к мержу\n\n" + comment
        )
