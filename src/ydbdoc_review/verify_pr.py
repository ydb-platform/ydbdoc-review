"""doc_verify: run QA on a PR as if ydbdoc-review had produced the translation.

Same pipeline as ``doc_translate`` (compare → fix-diff → re-validate → heuristics);
the only difference is that there is no translate phase — RU and EN are read
verbatim from the PR head branch.
"""

from __future__ import annotations

import os
import re

import click

from ydbdoc_review import git_local, github_api
from ydbdoc_review.config import Settings
from ydbdoc_review.paths import DocPair, pairs_from_changed_files
from ydbdoc_review.translation_qa import run_pairs_qa_and_repair


def parse_source_pr_number(*texts: str | None) -> int | None:
    """Extract linked doc PR number from translation PR title/body."""
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


def _pair_diffs_for_pr(
    workdir: str,
    merge_base_with: str,
    pairs: list[DocPair],
) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    out: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for pair in pairs:
        ru_d: str | None = None
        en_d: str | None = None
        try:
            ru_d = git_local.file_diff_range(workdir, merge_base_with, pair.ru_path)
        except RuntimeError:
            pass
        try:
            en_d = git_local.file_diff_range(workdir, merge_base_with, pair.en_path)
        except RuntimeError:
            pass
        out[(pair.ru_path, pair.en_path)] = (ru_d, en_d)
    return out


def _pairs_to_verify(
    pairs: list[DocPair],
    *,
    workdir: str,
    pr_changed: set[str],
) -> tuple[list[tuple[str, str]], list[str]]:
    """Pairs with RU+EN on the PR branch; at least one side in this PR's diff."""
    ok: list[tuple[str, str]] = []
    skipped: list[str] = []
    for pair in pairs:
        if pair.ru_path not in pr_changed and pair.en_path not in pr_changed:
            continue
        ru_text = git_local.read_text(workdir, pair.ru_path) or ""
        en_text = git_local.read_text(workdir, pair.en_path) or ""
        if len(ru_text.strip()) < 30:
            skipped.append(f"{pair.ru_path} (нет RU на ветке PR)")
            continue
        if len(en_text.strip()) < 30:
            skipped.append(f"{pair.en_path} (нет EN на ветке PR)")
            continue
        ok.append((pair.ru_path, pair.en_path))
    return ok, skipped


def _build_verify_comment(
    *,
    pr_number: int,
    linked_source_pr: int | None,
    qa_body: str | None,
    skipped: list[str],
    repaired_paths: list[str],
    push_failed: str | None = None,
    pushed: bool = False,
) -> str:
    lines = [
        "## ydbdoc-review — doc_verify",
        "",
        f"Проверка PR **#{pr_number}**: тот же QA, что после `doc_translate` "
        "(критик → fix-diff → переводчик → эвристики). Решение о мерже — за вами.",
    ]
    if linked_source_pr is not None and linked_source_pr != pr_number:
        lines.append(f"_Связанный doc PR (из заголовка): #{linked_source_pr}._")
    lines.append("")

    if repaired_paths:
        lines.extend(
            [
                "### Исправления критика",
                "",
                "Применены fix-diff правки в EN:",
                "",
                *[f"- `{p}`" for p in repaired_paths],
                "",
            ]
        )
        if pushed:
            lines.append("_Правки запушены в ветку PR._\n")
        elif push_failed:
            lines.extend(
                [
                    "**Push не выполнен** (job завершился без падения):",
                    "",
                    f"```\n{push_failed}\n```",
                    "",
                    "Проверьте `YDBDOC_PUSH_PAT` (PAT с `contents: write`) "
                    "и `GITHUB_PUSH_TOKEN` в workflow.",
                    "",
                ]
            )

    if skipped:
        lines.extend(
            ["### Пропущено", "", *[f"- {s}" for s in skipped], ""]
        )

    if qa_body:
        for prefix in ("## Отчёт по переводу", "## Вердикт для translation PR"):
            if qa_body.startswith(prefix):
                qa_body = "## Отчёт doc_verify" + qa_body[len(prefix) :]
                break
        lines.append(qa_body)
    else:
        lines.append("_Нет пар для QA в diff этого PR._")

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
    no_commit: bool,
    no_push: bool,
) -> None:
    """Run QA on the PR branch. Same pipeline as ``doc_translate``."""
    settings.validate_github()
    settings.validate_yandex()

    if not settings.translation_self_check_enabled:
        raise SystemExit(
            "doc_verify requires translation self-check "
            "(YDBDOC_TRANSLATION_SELF_CHECK / translation_self_check in config)."
        )

    owner, repo_name = repo.split("/", 1)
    pr = github_api.get_pull(owner, repo_name, pr_number, settings.github_token)
    head_owner, head_repo_name, _head_sha, head_ref = github_api.head_repo_from_pr(pr)
    head_clone_url = str(pr["head"]["repo"]["clone_url"])
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")

    linked_source = source_pr_number or parse_source_pr_number(title, body)
    if linked_source is not None and linked_source != pr_number:
        click.echo(f"Linked doc PR #{linked_source} (metadata only).")

    workdir = repo_path or os.environ.get("YDBDOC_REPO_PATH", "").strip() or None
    if not workdir:
        raise SystemExit(
            "doc_verify requires --repo-path or YDBDOC_REPO_PATH (checkout of PR head)."
        )

    base_ref = github_api.base_ref_from_pr(pr)
    base_clone_url = github_api.base_clone_url_from_pr(pr)

    changed = git_local.local_changed_paths(workdir, merge_base_with)
    pr_changed = {p.replace("\\", "/").lstrip("./") for p in changed}
    all_pairs = pairs_from_changed_files(changed, settings.docs_prefix)
    verify_pairs, skipped = _pairs_to_verify(
        all_pairs, workdir=workdir, pr_changed=pr_changed
    )

    if not verify_pairs:
        msg = (
            "## ydbdoc-review — doc_verify\n\n"
            "_В diff PR нет проверяемых пар RU↔EN (нужны оба файла на ветке PR "
            "и хотя бы один путь в diff)._"
        )
        if skipped:
            msg += "\n\n" + "\n".join(f"- {s}" for s in skipped)
        if not no_comment:
            github_api.post_issue_comment(owner, repo_name, pr_number, msg, settings.github_token)
        click.echo("No pairs to verify.")
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

    pair_diffs = _pair_diffs_for_pr(workdir, merge_base_with, [
        DocPair(ru_path=r, en_path=e) for r, e in verify_pairs
    ])

    click.echo(
        f"doc_verify: {len(verify_pairs)} pair(s) on PR branch, "
        f"critic `{settings.model_translation_verify}`, "
        f"repair={'on' if settings.translation_repair_enabled else 'off'} …"
    )
    if settings.github_push_token == settings.github_token:
        click.echo(
            "Note: neither GITHUB_PUSH_TOKEN nor YDBDOC_PUSH_PAT env — push uses GITHUB_TOKEN. "
            "In ydb/.github/workflows/ydbdoc-verify.yml add e.g. "
            "GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}.",
            err=True,
        )
    else:
        click.echo(
            "Git push will use a dedicated PAT (GITHUB_PUSH_TOKEN / YDBDOC_PUSH_PAT)."
        )

    qa_body, repaired_paths, _outcomes = run_pairs_qa_and_repair(
        settings,
        workdir=workdir,
        pairs=verify_pairs,
        pair_diffs=pair_diffs,
        source_pr_number=pr_number,
        base_ref_local=base_ref_local,
    )

    committed = False
    pushed = False
    push_failed: str | None = None
    paths_to_publish = list(dict.fromkeys(repaired_paths))
    if paths_to_publish and not no_commit:
        msg = (
            f"docs: doc_verify critic fixes (PR #{pr_number})\n\n"
            "Applied by ydbdoc-review doc_verify (same QA as doc_translate)."
        )
        committed = git_local.git_commit_paths(
            workdir,
            paths_to_publish,
            msg,
            author_name="ydbdoc-review",
            author_email="ydbdoc-review@users.noreply.github.com",
        )
        if committed:
            click.echo(f"Committed {len(paths_to_publish)} repaired file(s).")

    if committed and not no_push:
        push_failed = git_local.try_push_branch(
            workdir,
            remote_name="ydbdoc-push",
            branch=head_ref,
            token=settings.github_push_token,
            base_https_url=head_clone_url,
        )
        if push_failed:
            click.echo(
                f"Warning: could not push to `{head_owner}/{head_repo_name}:{head_ref}`: "
                f"{push_failed}",
                err=True,
            )
        else:
            pushed = True
            click.echo(f"Pushed to `{head_owner}/{head_repo_name}:{head_ref}`.")

    comment = _build_verify_comment(
        pr_number=pr_number,
        linked_source_pr=linked_source,
        qa_body=qa_body,
        skipped=skipped,
        repaired_paths=paths_to_publish if committed else repaired_paths,
        push_failed=push_failed,
        pushed=pushed,
    )

    click.echo("\n--- doc_verify report ---\n")
    click.echo(comment)
    click.echo("\n--- end ---\n")

    if not no_comment:
        github_api.post_issue_comment_chunked(
            owner, repo_name, pr_number, comment, settings.github_token
        )
        click.echo("Posted doc_verify comment on PR.")
