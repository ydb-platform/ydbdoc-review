from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

import click
from openai import OpenAI

from ydbdoc_review import git_local, github_api
from ydbdoc_review.config import (
    Settings,
    resolved_config_path,
    resolved_models_config_path,
)
from ydbdoc_review.llm import (
    call_yandex_responses,
    load_analyze_instructions,
    parse_json_object,
    translate_markdown,
)
from ydbdoc_review.paths import DocPair, pairs_from_changed_files, truncate


_OK_STATUSES = frozenset({"added", "modified", "changed", "renamed"})


def _split_repo(repo: str) -> tuple[str, str]:
    parts = repo.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise click.BadParameter("Use owner/name, for example ydb-platform/ydb")
    return parts[0], parts[1]


def _changed_from_pr_api(
    owner: str, repo: str, pr: int, token: str, docs_prefix: str
) -> list[str]:
    out: list[str] = []
    for item in github_api.iter_pr_files(owner, repo, pr, token):
        if item.get("status") not in _OK_STATUSES:
            continue
        fn = item.get("filename")
        if isinstance(fn, str):
            out.append(fn)
    return out


def _read_pair_texts(
    *,
    head_owner: str,
    head_repo: str,
    head_sha: str,
    token: str,
    pair: DocPair,
    repo_path: str | None,
) -> tuple[str | None, str | None]:
    if repo_path:
        ru = git_local.read_text(repo_path, pair.ru_path)
        en = git_local.read_text(repo_path, pair.en_path)
        return ru, en
    ru = github_api.get_file_text(head_owner, head_repo, pair.ru_path, head_sha, token)
    en = github_api.get_file_text(head_owner, head_repo, pair.en_path, head_sha, token)
    return ru, en


def _clone_head_repo(clone_url: str, push_token: str, dest: str, head_sha: str) -> None:
    authed = git_local.remote_push_url(clone_url, push_token)
    subprocess.run(["git", "clone", authed, dest], check=True)
    subprocess.run(["git", "-C", dest, "checkout", "-q", head_sha], check=True)


@click.group()
@click.version_option()
def main() -> None:
    """YDB documentation translation parity check and optional AI translation."""


@main.command("list-models")
def list_models_cmd() -> None:
    """Print model ids from the configured OpenAI-compatible endpoint (if GET /v1/models is supported)."""
    settings = Settings.from_env()
    settings.validate_yandex()
    client = OpenAI(
        api_key=settings.yandex_api_key,
        base_url=settings.yandex_base_url,
    )
    try:
        page = client.models.list()
    except Exception as e:
        raise SystemExit(
            "Could not list models via the OpenAI SDK (this gateway may not expose GET /v1/models).\n"
            f"Error: {e}\n\n"
            "In Yandex AI Studio open «Model gallery» and copy the text-generation slug for your folder. "
            "Vendor spelling is «DeepSeek»; slugs often look like `deepseek-v3.2/latest` — always verify in UI.\n"
            f"Current config: check={settings.model_check!r}, translate={settings.model_translate!r}"
        ) from e
    ids = sorted({m.id for m in page.data if getattr(m, "id", None)})
    if not ids:
        click.echo("(empty list from API)")
        return
    click.echo(f"Models ({len(ids)}), folder-qualified and plain slugs may both appear:\n")
    for mid in ids:
        click.echo(mid)
    cfg = resolved_models_config_path()
    if cfg is not None:
        click.echo(f"\n(config file with [models]: {cfg})")


@main.command("run")
@click.option("--repo", required=True, help="Repository where the PR is opened (owner/name).")
@click.option("--pr", "pr_number", type=int, required=True, help="Pull request number.")
@click.option(
    "--repo-path",
    type=click.Path(exists=True, file_okay=False, path_type=str),
    default=None,
    help="Local checkout of the PR branch (recommended for debugging). "
    "If omitted, uses YDBDOC_REPO_PATH when set, otherwise clones the head repository.",
)
@click.option(
    "--merge-base-with",
    default="origin/main",
    show_default=True,
    help="Used with --repo-path to compute changed files: git merge-base MERGE_BASE_WITH HEAD.",
)
@click.option("--dry-run", is_flag=True, help="Do not write files, commit, push, or comment.")
@click.option(
    "--no-commit",
    is_flag=True,
    help="Write generated files to the working tree only (no git commit, no push, no PR comment).",
)
@click.option("--no-push", is_flag=True, help="Commit locally but do not push.")
@click.option("--no-comment", is_flag=True, help="Do not post a GitHub comment.")
def run_cmd(
    repo: str,
    pr_number: int,
    repo_path: str | None,
    merge_base_with: str,
    dry_run: bool,
    no_commit: bool,
    no_push: bool,
    no_comment: bool,
) -> None:
    settings = Settings.from_env()
    if not settings.review_enabled:
        cfg = resolved_config_path()
        hint = f" Config: {cfg}." if cfg else ""
        click.echo(
            "ydbdoc-review: skipped (review disabled)."
            f"{hint} "
            "Enable with YDBDOC_REVIEW_ENABLED=true or [feature] review_enabled=true in ydbdoc-review.toml."
        )
        return
    settings.validate_github()

    base_owner, base_repo = _split_repo(repo)
    pr = github_api.get_pull(base_owner, base_repo, pr_number, settings.github_token)
    head_owner, head_repo_name, head_sha, head_ref = github_api.head_repo_from_pr(pr)
    head_clone_url = str(pr["head"]["repo"]["clone_url"])

    effective_repo_path = (
        repo_path
        or os.environ.get("YDBDOC_REPO_PATH", "").strip()
        or None
    )

    if effective_repo_path:
        changed = git_local.local_changed_paths(effective_repo_path, merge_base_with)
    else:
        changed = _changed_from_pr_api(
            base_owner, base_repo, pr_number, settings.github_token, settings.docs_prefix
        )

    pairs = pairs_from_changed_files(changed, settings.docs_prefix)
    if not pairs:
        body = (
            "## ydbdoc-review\n\n"
            "_No Russian/English markdown pairs were detected in changed paths "
            f"under `{settings.docs_prefix}/` for this PR._"
        )
        if not dry_run and not no_comment and not no_commit:
            github_api.post_issue_comment(
                base_owner, base_repo, pr_number, body, settings.github_token
            )
        click.echo("No doc pairs to analyze. Exiting.")
        return

    settings.validate_yandex()

    per_side = max(4000, settings.max_chars_per_side_analyze // max(1, len(pairs)))

    payload_pairs: list[dict[str, Any]] = []
    full_texts: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    for pair in pairs:
        ru_t, en_t = _read_pair_texts(
            head_owner=head_owner,
            head_repo=head_repo_name,
            head_sha=head_sha,
            token=settings.github_token,
            pair=pair,
            repo_path=effective_repo_path,
        )
        full_texts[(pair.ru_path, pair.en_path)] = (ru_t, en_t)
        ru_s, _ = truncate(ru_t, per_side)
        en_s, _ = truncate(en_t, per_side)
        payload_pairs.append(
            {
                "ru_path": pair.ru_path,
                "en_path": pair.en_path,
                "ru_text": ru_s,
                "en_text": en_s,
            }
        )

    analyze_input = json.dumps({"pairs": payload_pairs}, ensure_ascii=False)
    click.echo(
        f"Calling check model `{settings.model_check}` "
        f"(translate model `{settings.model_translate}` — only if generation is needed) …"
    )
    raw = call_yandex_responses(
        settings,
        settings.model_check,
        instructions=load_analyze_instructions(settings).strip(),
        user_input=analyze_input,
        max_output_tokens=8000,
    )
    try:
        data = parse_json_object(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise SystemExit(f"Check model returned non-JSON output:\n{raw[:2000]}\nError: {e}") from e

    results = data.get("results")
    if not isinstance(results, list):
        raise SystemExit("Check model JSON has no 'results' list.")

    workdir = effective_repo_path
    tmp: str | None = None
    if not workdir and not dry_run:
        tmp = tempfile.mkdtemp(prefix="ydbdoc-review-")
        click.echo(f"Cloning head repo {head_owner}/{head_repo_name} @ {head_sha[:7]} …")
        _clone_head_repo(head_clone_url, settings.github_push_token, tmp, head_sha)
        workdir = tmp

    generated: list[str] = []
    warnings: list[str] = []

    for item in results:
        gen = item.get("needs_generation_for")
        ru_p = item.get("ru_path")
        en_p = item.get("en_path")
        summary = str(item.get("summary", ""))
        aligned = bool(item.get("semantically_aligned"))
        if not aligned and gen is None:
            warnings.append(f"- `{ru_p}` / `{en_p}`: {summary} _(needs human review)_")

        if dry_run or gen not in ("en", "ru"):
            continue
        if not isinstance(ru_p, str) or not isinstance(en_p, str):
            continue
        key = (ru_p, en_p)
        ru_full, en_full = full_texts.get(key, (None, None))
        if workdir is None:
            click.echo("Dry-run disabled writes; skipping translation generation.")
            continue

        if gen == "en":
            if not ru_full:
                warnings.append(f"- Cannot translate to EN: missing Russian source `{ru_p}`")
                continue
            click.echo(f"Translating RU→EN `{ru_p}` → `{en_p}` with `{settings.model_translate}` …")
            out_md = translate_markdown(
                settings,
                source_lang="Russian",
                target_lang="English",
                source_path=ru_p,
                source_text=ru_full,
            )
            git_local.write_text(workdir, en_p, out_md)
            generated.append(en_p)
        else:
            if not en_full:
                warnings.append(f"- Cannot translate to RU: missing English source `{en_p}`")
                continue
            click.echo(f"Translating EN→RU `{en_p}` → `{ru_p}` with `{settings.model_translate}` …")
            out_md = translate_markdown(
                settings,
                source_lang="English",
                target_lang="Russian",
                source_path=en_p,
                source_text=en_full,
            )
            git_local.write_text(workdir, ru_p, out_md)
            generated.append(ru_p)

    committed = False
    if workdir and not dry_run and generated and not no_commit:
        msg = (
            f"docs: add AI translations ({len(generated)} file(s))\n\n"
            f"PR #{pr_number} — generated by ydbdoc-review."
        )
        committed = git_local.git_commit_all(
            workdir,
            msg,
            author_name="ydbdoc-review",
            author_email="ydbdoc-review@users.noreply.github.com",
        )
        if committed:
            click.echo(f"Committed {len(generated)} file(s).")
        else:
            click.echo("Nothing to commit (empty diff after write).")

    if no_commit and generated:
        click.echo(
            f"Wrote {len(generated)} file(s) under `{workdir}`; "
            "`--no-commit`: review with `git diff`, then commit when ready."
        )

    if workdir and not dry_run and committed and not no_push:
        click.echo(f"Pushing to {head_owner}/{head_repo_name} branch `{head_ref}` …")
        git_local.push_branch(
            workdir,
            remote_name="ydbdoc-push",
            branch=head_ref,
            token=settings.github_push_token,
            base_https_url=head_clone_url,
        )
        click.echo("Push completed.")

    comment_lines = [
        "## ydbdoc-review",
        "",
        f"_Head repository:_ `{head_owner}/{head_repo_name}` @ `{head_sha[:7]}`",
        "",
        "### Check model results",
    ]
    for item in results:
        comment_lines.append(
            f"- `{item.get('ru_path')}` ↔ `{item.get('en_path')}`: "
            f"aligned={item.get('semantically_aligned')} "
            f"generate_for={item.get('needs_generation_for')} — _{item.get('summary')}_"
        )
    if generated:
        comment_lines.extend(["", "### Generated / updated", *[f"- `{p}`" for p in generated]])
    if warnings:
        comment_lines.extend(["", "### Follow-ups", *warnings])
    if dry_run:
        comment_lines.extend(["", "_Dry run: no files written, no push, no comment._"])
    elif no_commit:
        if generated:
            comment_lines.extend(
                [
                    "",
                    "_Local run: translation files were written to disk; "
                    "`--no-commit`: no commit, no push, no PR comment._",
                ]
            )
        else:
            comment_lines.extend(
                ["", "_`--no-commit`: no PR comment posted (local-only mode)._"]
            )

    comment_body = "\n".join(comment_lines)
    click.echo("\n--- Comment preview ---\n")
    click.echo(comment_body)
    click.echo("\n--- End preview ---\n")

    effective_no_comment = no_comment or no_commit
    if not dry_run and not effective_no_comment:
        url = github_api.post_issue_comment(
            base_owner, base_repo, pr_number, comment_body, settings.github_token
        )
        click.echo(f"Posted comment: {url}")

    if tmp:
        click.echo(f"Leaving clone at {tmp} (delete manually if not needed).")


if __name__ == "__main__":
    main()
