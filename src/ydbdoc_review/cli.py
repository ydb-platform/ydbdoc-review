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
    translate_en_update_from_ru_diff,
    translate_markdown,
    translate_ru_update_from_en_diff,
)
from ydbdoc_review.paths import DocPair, pairs_from_changed_files, truncate


_OK_STATUSES = frozenset({"added", "modified", "changed", "renamed"})


def _translation_branch_name(pr_number: int) -> str:
    return f"ydbdoc-review/pr-{pr_number}"


def _assess_translation_files(
    *,
    repo_path: str,
    base_ref: str,
    generated_en_to_ru: dict[str, str],
) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """
    Compare EN/RU presence on the target integration branch (e.g. origin/main).

    Returns (new_at_base, overlay_ok, blocked_prereq) where blocked_prereq is
    (en_path, ru_path) pairs: RU already on main but EN is not — translate via a
    merged PR that introduced the file, not this open fork PR.
    """
    if not generated_en_to_ru:
        return [], [], []
    new_at_base: list[str] = []
    overlay_ok: list[str] = []
    blocked: list[tuple[str, str]] = []
    for en_p, ru_p in generated_en_to_ru.items():
        en_on_base = git_local.path_exists_at_tree(repo_path, base_ref, en_p)
        if en_on_base:
            overlay_ok.append(en_p)
            continue
        ru_on_base = git_local.path_exists_at_tree(repo_path, base_ref, ru_p)
        if ru_on_base:
            blocked.append((en_p, ru_p))
        else:
            new_at_base.append(en_p)
    return new_at_base, overlay_ok, blocked


def _build_prerequisites_comment(
    *,
    new_at_base: list[str],
    overlay_ok: list[str],
    blocked_prereq: list[tuple[str, str, github_api.PathPrerequisiteInfo | None]],
    translation_pr_url: str | None,
    translation_branch: str,
    publish_owner: str,
    publish_repo: str,
    base_ref: str,
    is_fork: bool,
    head_owner: str,
    head_repo: str,
    head_ref: str,
    pr_number: int,
    blocked_only: bool,
) -> list[str]:
    if blocked_only:
        lines = [
            "## ydbdoc-review — сначала переведите другой (уже смерженный) PR",
            "",
            "Перевод для **этого** PR сейчас **не** создан: на целевой ветке (`"
            f"{base_ref}`) уже есть русский файл, но **нет** английского. "
            "Точечно наложить изменения этого PR на EN нельзя, пока не появится "
                "полный английский перевод по актуальному RU на базовой ветке.",
        ]
    else:
        lines = [
            "## ydbdoc-review — перевод вынесен в отдельный PR в upstream",
            "",
            "Перевод **не** коммитится в ветку исходного PR.",
            f"PR с переводом открывается в **`{publish_owner}/{publish_repo}`** "
            f"(`base` = `{base_ref}`), его можно смержить **после** вашего PR с документацией.",
        ]
        if translation_pr_url:
            lines.append(f"\n**PR с переводом:** {translation_pr_url}")
        else:
            lines.append(
                f"\nВетка: `{translation_branch}` в `{publish_owner}/{publish_repo}` "
                f"(создайте PR вручную: `base` = `{base_ref}`)."
            )

    if is_fork and not blocked_only:
        lines.extend(
            [
                "",
                "### Порядок (PR из форка → `main`)",
                "",
                "1. **Смержите** этот PR с документацией в `main` (как обычно).",
                "2. **Смержите** PR с переводом в `main` чуть позже (отдельный PR в upstream).",
                "3. Если нужно донести EN **по diff** после появления файла в `main` — "
                "снова повесьте `doc_translate` на **этот** PR (или на обновлённую ветку).",
                "",
                f"_Исходный PR: #{pr_number}, форк `{head_owner}/{head_repo}` → `{head_ref}`._",
            ]
        )
    elif not blocked_only:
        lines.extend(
            [
                "",
                "### Порядок действий",
                "",
                f"1. Смержите PR с переводом в `{base_ref}`.",
                "2. Обновите ветку документации при необходимости.",
                "3. Повторите `doc_translate` для точечного обновения по diff.",
            ]
        )

    if blocked_prereq:
        lines.extend(
            [
                "",
                "### Сначала: `doc_translate` на уже **смерженном** PR",
                "",
                "На `"
                + base_ref
                + "` уже есть RU, но нет EN. Сначала все перечисленные ниже "
                "смерженные PR с правками RU должны быть в базе (обычно уже так). "
                "Затем повесьте **`doc_translate`** на **последний** PR в цепочке — "
                "откроется отдельный PR с **полным** переводом в "
                f"`{publish_owner}/{publish_repo}` → `{base_ref}`. "
                "Смержите **его**, затем снова запустите перевод **здесь**.",
            ]
        )
        for en_p, ru_p, prereq in blocked_prereq:
            lines.append("")
            lines.append(f"#### `{ru_p}` → `{en_p}`")
            if prereq is None or not prereq.chain:
                lines.append(
                    "_Не удалось найти смерженные PR по истории файла на `"
                    + base_ref
                    + "`._"
                )
                continue
            if len(prereq.chain) > 1:
                lines.append("")
                lines.append("Смерженные PR, менявшие этот RU (по порядку):")
                for ref in prereq.chain:
                    lines.append(
                        f"- [#{ref.number}]({ref.url}) — _{ref.title}_ "
                        f"_(merged {ref.merged_at[:10]})_"
                    )
            rec = prereq.recommended
            if rec is None:
                lines.append(
                    "_Не удалось выбрать PR для `doc_translate` (исключён текущий PR)._"
                )
                continue
            if len(prereq.chain) == 1:
                lines.append(
                    f"**`doc_translate`:** [#{rec.number}]({rec.url}) — _{rec.title}_"
                )
            else:
                others = [r for r in prereq.chain if r.number != rec.number]
                if others:
                    nums = ", ".join(f"#{r.number}" for r in others)
                    lines.append(
                        f"_PR {nums} уже должны быть в `{base_ref}`; "
                        "отдельный перевод по ним не нужен._"
                    )
                lines.append("")
                lines.append(
                    f"**`doc_translate` (последний в цепочке):** "
                    f"[#{rec.number}]({rec.url}) — _{rec.title}_"
                )
    if new_at_base and not blocked_only:
        lines.extend(
            [
                "",
                "### Новые EN-файлы (на `"
                + base_ref
                + "` их ещё не было)",
                "",
                "Переведён **весь** файл. Проверьте ссылки на другие `.md` в `en/` — "
                "при отсутствии целей билд может падать; недостающие страницы переводите "
                "через `doc_translate` на **других** (уже смерженных) PR с появлением RU.",
                "",
                *[f"- `{p}`" for p in new_at_base],
            ]
        )
    if overlay_ok and not blocked_only:
        lines.extend(
            [
                "",
                "### EN уже был на `"
                + base_ref
                + "`",
                "",
                "После мержа PR перевода повторный `doc_translate` на исходном PR "
                "сможет обновить EN **по diff**.",
                "",
                *[f"- `{p}`" for p in overlay_ok],
            ]
        )
    return lines


def _build_translation_pr_body(
    *,
    pr_number: int,
    base_owner: str,
    base_repo: str,
    base_ref: str,
    head_owner: str,
    head_repo: str,
    head_ref: str,
    is_fork: bool,
    generated: list[str],
    new_at_base: list[str],
    overlay_ok: list[str],
    results: list[dict[str, Any]],
) -> str:
    source = f"https://github.com/{base_owner}/{base_repo}/pull/{pr_number}"
    lines = [
        "## ydbdoc-review (автоматический перевод)",
        "",
        f"Перевод для PR {source}.",
    ]
    if is_fork:
        lines.append(
            f"Исходная ветка: форк `{head_owner}/{head_repo}` (`{head_ref}`). "
            f"Этот PR в **upstream** (`base` = `{base_ref}`) — смержите **после** "
            "исходного PR с документацией, если он ещё открыт."
        )
    else:
        lines.append(
            f"Исходная ветка: `{head_ref}`. Отдельный PR в `{base_ref}` — "
            "не смешивайте с исходным PR документации."
        )
    lines.extend(
        [
            "",
            "### Сгенерированные файлы",
            *[f"- `{p}`" for p in generated],
        ]
    )
    if new_at_base:
        lines.extend(
            [
                "",
                "### Внимание: новые EN-файлы (не было на merge-base)",
                "",
                *[f"- `{p}`" for p in new_at_base],
                "",
                "Переведён весь файл. Проверьте ссылки на другие страницы в `en/` — "
                "при отсутствии целевых файлов билд документации может сломаться. "
                "При необходимости откройте `doc_translate` на **других** PR, "
                "где появились соответствующие RU-файлы, и смержите те переводы **раньше**.",
            ]
        )
    if overlay_ok:
        lines.extend(
            [
                "",
                "### EN уже существовал на merge-base",
                "",
                *[f"- `{p}`" for p in overlay_ok],
                "",
                "После мержа этого PR в базу повторите `doc_translate` на исходном PR "
                "для точечного обновления по diff, если нужно.",
            ]
        )
    lines.extend(["", "### Результаты проверочной модели"])
    for item in results:
        lines.append(
            f"- `{item.get('ru_path')}` ↔ `{item.get('en_path')}`: "
            f"aligned={item.get('semantically_aligned')} "
            f"generate_for={item.get('needs_generation_for')} — _{item.get('summary')}_"
        )
    lines.append("\n_Generated by ydbdoc-review._")
    return "\n".join(lines)


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
    base_ref = github_api.base_ref_from_pr(pr)
    base_clone_url = github_api.base_clone_url_from_pr(pr)
    is_fork = github_api.is_fork_pr(pr)

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

    trunc_raw = os.environ.get("YDBDOC_ANALYZE_TRUNCATE_CHARS", "").strip()
    analyze_trunc: int | None = (
        int(trunc_raw) if trunc_raw.isdigit() and int(trunc_raw) > 0 else None
    )

    payload_pairs: list[dict[str, Any]] = []
    full_texts: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    diff_preview_raw = os.environ.get("YDBDOC_ANALYZE_DIFF_MAX", "").strip()
    diff_preview_cap = (
        int(diff_preview_raw)
        if diff_preview_raw.isdigit() and int(diff_preview_raw) > 0
        else 500_000
    )
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
        if analyze_trunc is not None:
            ru_s, _ = truncate(ru_t, analyze_trunc)
            en_s, _ = truncate(en_t, analyze_trunc)
        else:
            ru_s = ru_t if ru_t is not None else ""
            en_s = en_t if en_t is not None else ""
        entry: dict[str, Any] = {
            "ru_path": pair.ru_path,
            "en_path": pair.en_path,
            "ru_text": ru_s,
            "en_text": en_s,
        }
        if effective_repo_path:
            try:
                dru = git_local.file_diff_range(
                    effective_repo_path, merge_base_with, pair.ru_path
                )
                if dru.strip():
                    entry["ru_diff_vs_base"] = (
                        dru
                        if len(dru) <= diff_preview_cap
                        else dru[:diff_preview_cap] + "\n…(diff truncated)\n"
                    )
            except RuntimeError:
                pass
            try:
                den = git_local.file_diff_range(
                    effective_repo_path, merge_base_with, pair.en_path
                )
                if den.strip():
                    entry["en_diff_vs_base"] = (
                        den
                        if len(den) <= diff_preview_cap
                        else den[:diff_preview_cap] + "\n…(diff truncated)\n"
                    )
            except RuntimeError:
                pass
        payload_pairs.append(entry)

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
    generated_en_to_ru: dict[str, str] = {}
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
            out_md: str
            if effective_repo_path and en_full is not None:
                try:
                    ru_diff = git_local.file_diff_range(
                        effective_repo_path, merge_base_with, ru_p
                    )
                except RuntimeError as exc:
                    click.echo(
                        f"Note: diff-based translate unavailable ({exc}); "
                        "using full-file translate.",
                        err=True,
                    )
                    ru_diff = ""
                if ru_diff.strip():
                    click.echo("  (mode: merge-base..HEAD Russian diff + English reference)")
                    out_md = translate_en_update_from_ru_diff(
                        settings,
                        en_reference=en_full,
                        ru_diff=ru_diff,
                        ru_path=ru_p,
                        ru_full=ru_full,
                    )
                else:
                    out_md = translate_markdown(
                        settings,
                        source_lang="Russian",
                        target_lang="English",
                        source_path=ru_p,
                        source_text=ru_full,
                    )
            else:
                out_md = translate_markdown(
                    settings,
                    source_lang="Russian",
                    target_lang="English",
                    source_path=ru_p,
                    source_text=ru_full,
                )
            git_local.write_text(workdir, en_p, out_md)
            generated.append(en_p)
            generated_en_to_ru[en_p] = ru_p
        else:
            if not en_full:
                warnings.append(f"- Cannot translate to RU: missing English source `{en_p}`")
                continue
            click.echo(f"Translating EN→RU `{en_p}` → `{ru_p}` with `{settings.model_translate}` …")
            if effective_repo_path and ru_full is not None:
                try:
                    en_diff = git_local.file_diff_range(
                        effective_repo_path, merge_base_with, en_p
                    )
                except RuntimeError as exc:
                    click.echo(
                        f"Note: diff-based translate unavailable ({exc}); "
                        "using full-file translate.",
                        err=True,
                    )
                    en_diff = ""
                if en_diff.strip():
                    click.echo("  (mode: merge-base..HEAD English diff + Russian reference)")
                    out_md = translate_ru_update_from_en_diff(
                        settings,
                        ru_reference=ru_full,
                        en_diff=en_diff,
                        en_path=en_p,
                        en_full=en_full,
                    )
                else:
                    out_md = translate_markdown(
                        settings,
                        source_lang="English",
                        target_lang="Russian",
                        source_path=en_p,
                        source_text=en_full,
                    )
            else:
                out_md = translate_markdown(
                    settings,
                    source_lang="English",
                    target_lang="Russian",
                    source_path=en_p,
                    source_text=en_full,
                )
            git_local.write_text(workdir, ru_p, out_md)
            generated.append(ru_p)

    translation_branch = _translation_branch_name(pr_number)
    translation_pr_url: str | None = None
    new_at_base: list[str] = []
    overlay_ok: list[str] = []
    blocked_prereq: list[tuple[str, str, github_api.PathPrerequisiteInfo | None]] = []
    blocked_only = False
    base_ref_local: str | None = None

    if workdir and generated_en_to_ru:
        base_remote_name = "ydbdoc-base"
        git_local.ensure_remote(
            workdir,
            base_remote_name,
            git_local.remote_push_url(base_clone_url, settings.github_push_token),
        )
        base_ref_local = git_local.fetch_remote_branch(workdir, base_remote_name, base_ref)
        new_at_base, overlay_ok, blocked_raw = _assess_translation_files(
            repo_path=workdir,
            base_ref=base_ref_local,
            generated_en_to_ru=generated_en_to_ru,
        )
        exclude_intro_pr = None if github_api.pr_is_merged(pr) else pr_number
        for en_p, ru_p in blocked_raw:
            prereq = github_api.find_prerequisite_chain_for_path(
                base_owner,
                base_repo,
                ru_p,
                token=settings.github_token,
                repo_path=workdir,
                base_git_ref=base_ref_local,
                base_branch=base_ref,
                exclude_pr=exclude_intro_pr,
            )
            blocked_prereq.append((en_p, ru_p, prereq))
            if prereq.recommended:
                rec = prereq.recommended
                chain_nums = ", ".join(f"#{r.number}" for r in prereq.chain) or "—"
                click.echo(
                    f"Prerequisite chain for `{ru_p}`: [{chain_nums}] → "
                    f"doc_translate on #{rec.number} {rec.url}"
                )
            else:
                click.echo(
                    f"Could not resolve prerequisite PR chain for `{ru_p}`.",
                    err=True,
                )
        if blocked_prereq:
            blocked_only = True
            click.echo(
                "Blocking translation PR: EN prerequisite is missing on base branch. "
                "Label doc_translate on the merged PR(s) listed in the comment.",
                err=True,
            )

    committed = False
    if workdir and not dry_run and generated and not no_commit and not blocked_only:
        git_local.prepare_translation_branch_on_base(
            workdir,
            translation_branch=translation_branch,
            base_remote_url=git_local.remote_push_url(base_clone_url, settings.github_push_token),
            base_remote_name="ydbdoc-base",
            base_branch=base_ref,
            paths=generated,
        )
        msg = (
            f"docs: add AI translations ({len(generated)} file(s))\n\n"
            f"Translation PR for #{pr_number} — generated by ydbdoc-review.\n"
            f"Target: {base_owner}/{base_repo}:{base_ref}\n"
            f"Branch: {translation_branch}"
        )
        committed = git_local.git_commit_all(
            workdir,
            msg,
            author_name="ydbdoc-review",
            author_email="ydbdoc-review@users.noreply.github.com",
        )
        if committed:
            click.echo(
                f"Committed {len(generated)} file(s) on branch `{translation_branch}` "
                f"from `{base_owner}/{base_repo}:{base_ref}`."
            )
        else:
            click.echo("Nothing to commit (empty diff after write).")

    if no_commit and generated:
        click.echo(
            f"Wrote {len(generated)} file(s) under `{workdir}`; "
            "`--no-commit`: review with `git diff`, then commit when ready."
        )

    if workdir and not dry_run and committed and not no_push:
        click.echo(
            f"Pushing to upstream {base_owner}/{base_repo} branch `{translation_branch}` "
            f"(base `{base_ref}`) …"
        )
        git_local.push_branch(
            workdir,
            remote_name="ydbdoc-push",
            branch=translation_branch,
            token=settings.github_push_token,
            base_https_url=base_clone_url,
        )
        click.echo("Push completed.")
        pr_title = f"docs(i18n): translate PR #{pr_number} ({len(generated)} file(s))"
        pr_body = _build_translation_pr_body(
            pr_number=pr_number,
            base_owner=base_owner,
            base_repo=base_repo,
            base_ref=base_ref,
            head_owner=head_owner,
            head_repo=head_repo_name,
            head_ref=head_ref,
            is_fork=is_fork,
            generated=generated,
            new_at_base=new_at_base,
            overlay_ok=overlay_ok,
            results=results,
        )
        opened = github_api.create_pull(
            base_owner,
            base_repo,
            title=pr_title,
            head=translation_branch,
            base=base_ref,
            body=pr_body,
            token=settings.github_push_token,
        )
        if opened:
            translation_pr_url, trans_num = opened
            click.echo(f"Opened translation PR #{trans_num}: {translation_pr_url}")
        else:
            compare = (
                f"https://github.com/{base_owner}/{base_repo}/compare/"
                f"{base_ref}...{translation_branch}?expand=1"
            )
            click.echo(
                f"Could not open translation PR via API (branch may already exist). "
                f"Open manually: {compare}",
                err=True,
            )

    if dry_run:
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
            comment_lines.extend(
                ["", "### Would generate", *[f"- `{p}`" for p in generated]]
            )
        if warnings:
            comment_lines.extend(["", "### Follow-ups", *warnings])
        comment_lines.extend(
            [
                "",
                "_Dry run: no files written. With a real run, translation would go to "
                f"`{base_owner}/{base_repo}:{translation_branch}` and a separate PR "
                f"would be opened into `{base_ref}` (not `{head_owner}/{head_repo}:{head_ref}`)._",
            ]
        )
    elif no_commit:
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
            comment_lines.extend(
                ["", "### Written locally", *[f"- `{p}`" for p in generated]]
            )
        if warnings:
            comment_lines.extend(["", "### Follow-ups", *warnings])
        if generated:
            comment_lines.extend(
                [
                    "",
                    "_`--no-commit`: files on disk only; no branch, no translation PR, "
                    "no comment on merge (this preview is console-only)._",
                ]
            )
        else:
            comment_lines.extend(
                ["", "_`--no-commit`: no PR comment posted (local-only mode)._"]
            )
    elif (committed and generated) or blocked_only:
        comment_lines = _build_prerequisites_comment(
            new_at_base=new_at_base,
            overlay_ok=overlay_ok,
            blocked_prereq=blocked_prereq,
            translation_pr_url=translation_pr_url,
            translation_branch=translation_branch,
            publish_owner=base_owner,
            publish_repo=base_repo,
            base_ref=base_ref,
            is_fork=is_fork,
            head_owner=head_owner,
            head_repo=head_repo_name,
            head_ref=head_ref,
            pr_number=pr_number,
            blocked_only=blocked_only,
        )
        comment_lines.extend(["", "### Check model results"])
        for item in results:
            comment_lines.append(
                f"- `{item.get('ru_path')}` ↔ `{item.get('en_path')}`: "
                f"aligned={item.get('semantically_aligned')} "
                f"generate_for={item.get('needs_generation_for')} — _{item.get('summary')}_"
            )
        if warnings:
            comment_lines.extend(["", "### Прочее", *warnings])
    else:
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
        if warnings:
            comment_lines.extend(["", "### Follow-ups", *warnings])
        if not generated:
            comment_lines.extend(
                [
                    "",
                    "_Перевод не требовался или не был сгенерирован._",
                ]
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
