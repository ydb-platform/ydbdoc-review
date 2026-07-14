"""Command-line interface for ydbdoc-review v2."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.table import Table

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.errors import GitHubConfigError, GitHubError
from ydbdoc_review.github.workflow import run_doc_translate, run_doc_verify
from ydbdoc_review.llm.client import create_llm_client
from ydbdoc_review.llm.errors import LLMConfigError, LLMError
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary

app = typer.Typer(
    name="ydbdoc-review",
    help="AST-based RU↔EN translation pipeline for YDB documentation.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    """Configure root logging once per process (idempotent for Typer re-entry)."""
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if getattr(_setup_logging, "_configured", False):
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    _setup_logging._configured = True  # type: ignore[attr-defined]


def _resolve_repo_path(repo_path: Path | None) -> Path:
    if repo_path is not None:
        return repo_path.expanduser().resolve()
    env_raw = os.environ.get("YDBDOC_REPO_PATH", "")
    if env_raw:
        env = Path(env_raw).expanduser()
        if env.is_dir():
            return env.resolve()
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return cwd
    raise typer.BadParameter(
        "Repository path required: pass --repo-path or set YDBDOC_REPO_PATH "
        "to a git checkout of the docs repo."
    )


@app.callback()
def main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Debug logging."),
    ] = False,
) -> None:
    """Load ``.env`` and configure logging."""
    load_dotenv()
    _setup_logging(verbose)


@app.command()
def run(
    repo: Annotated[str, typer.Option(help="GitHub repo owner/name.")],
    pr: Annotated[int, typer.Option(help="Source PR number (doc_translate).")],
    repo_path: Annotated[
        Path | None,
        typer.Option(help="Local git checkout of the PR head."),
    ] = None,
    merge_base_with: Annotated[
        str,
        typer.Option(help="Second ref for git merge-base."),
    ] = "origin/main",
    dry_run: Annotated[
        bool,
        typer.Option(help="No disk writes, commit, push, or PR comments."),
    ] = False,
    no_commit: Annotated[
        bool,
        typer.Option(help="Run pipeline but skip git commit/push/comments."),
    ] = False,
) -> None:
    """Translate changed doc pairs for a source PR (``doc_translate``)."""
    path = _resolve_repo_path(repo_path)
    try:
        result = run_doc_translate(
            repo_path=str(path),
            github_repo=repo,
            pr_number=pr,
            merge_base_with=merge_base_with,
            dry_run=dry_run,
            no_commit=no_commit,
        )
    except (GitHubError, GitHubConfigError, LLMConfigError, LLMError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_job_summary(result.mode, result)
    if result.pr_result.failed_count:
        console.print(
            f"[yellow]Warning:[/yellow] {result.pr_result.failed_count} pair(s) failed — "
            "see logs and translation PR report."
        )


@app.command()
def verify(
    repo: Annotated[str, typer.Option(help="GitHub repo owner/name.")],
    pr: Annotated[
        int,
        typer.Option(help="Translation PR number (doc_verify)."),
    ],
    repo_path: Annotated[
        Path | None,
        typer.Option(help="Local git checkout of the translation PR head."),
    ] = None,
    merge_base_with: Annotated[
        str,
        typer.Option(help="Second ref for git merge-base."),
    ] = "origin/main",
    dry_run: Annotated[bool, typer.Option(help="No writes or comments.")] = False,
    no_commit: Annotated[
        bool,
        typer.Option(help="Run QA but skip repair commit/push."),
    ] = False,
) -> None:
    """Re-run critic QA on a translation PR (``doc_verify``)."""
    path = _resolve_repo_path(repo_path)
    try:
        result = run_doc_verify(
            repo_path=str(path),
            github_repo=repo,
            pr_number=pr,
            merge_base_with=merge_base_with,
            dry_run=dry_run,
            no_commit=no_commit,
        )
    except (GitHubError, GitHubConfigError, LLMConfigError, LLMError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_job_summary(result.mode, result)


@app.command()
def job(
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="translate (doc_translate) or verify (doc_verify).",
        ),
    ],
    repo: Annotated[str, typer.Option(help="GitHub repo owner/name.")],
    pr: Annotated[int, typer.Option(help="PR number (source for translate; translation for verify).")],
    repo_path: Annotated[
        Path | None,
        typer.Option(help="Local git checkout of the PR head."),
    ] = None,
    merge_base_with: Annotated[
        str,
        typer.Option(help="Second ref for git merge-base."),
    ] = "origin/main",
    dry_run: Annotated[
        bool,
        typer.Option(help="No disk writes, commit, push, or PR comments."),
    ] = False,
    no_commit: Annotated[
        bool,
        typer.Option(help="Run pipeline but skip git commit/push/comments."),
    ] = False,
) -> None:
    """Unified entry point for external schedulers (Reactor/Nirvana)."""
    path = _resolve_repo_path(repo_path)
    m = mode.strip().lower()
    try:
        if m in ("translate", "doc_translate", "run"):
            result = run_doc_translate(
                repo_path=str(path),
                github_repo=repo,
                pr_number=pr,
                merge_base_with=merge_base_with,
                dry_run=dry_run,
                no_commit=no_commit,
            )
        elif m in ("verify", "doc_verify"):
            result = run_doc_verify(
                repo_path=str(path),
                github_repo=repo,
                pr_number=pr,
                merge_base_with=merge_base_with,
                dry_run=dry_run,
                no_commit=no_commit,
            )
        else:
            raise typer.BadParameter("mode must be translate or verify")
    except (GitHubError, GitHubConfigError, LLMConfigError, LLMError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_job_summary(getattr(result, "mode", m), result)


@app.command("list-models")
def list_models(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Optional YAML config path."),
    ] = None,
    live: Annotated[
        bool,
        typer.Option(help="Query GET /v1/models from the configured API."),
    ] = False,
) -> None:
    """Show configured model chains (and optionally list remote models)."""
    cfg = load_config(yaml_path=config)
    table = Table(title="Configured model chains")
    table.add_column("Role")
    table.add_column("Primary")
    table.add_column("Fallbacks")
    for role in ("analyze", "translate", "critic"):
        choice = getattr(cfg.llm.models, role)
        table.add_row(role, choice.primary, ", ".join(choice.fallbacks) or "—")
    console.print(table)
    console.print(f"Base URL: {cfg.llm.base_url}")

    if not live:
        return

    try:
        folder_id, api_key = cfg.secrets.require_yandex()
    except RuntimeError as exc:
        console.print(f"[yellow]Skipping live list:[/yellow] {exc}")
        raise typer.Exit(code=1) from exc

    client = OpenAI(api_key=api_key, base_url=cfg.llm.base_url, timeout=float(cfg.llm.timeout_s))
    try:
        response = client.models.list()
    except Exception as exc:
        console.print(f"[red]API error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    ids = sorted({m.id for m in response.data})
    console.print(f"\nRemote models ({len(ids)}, folder {folder_id}):")
    for model_id in ids[:50]:
        console.print(f"  {model_id}")
    if len(ids) > 50:
        console.print(f"  … and {len(ids) - 50} more")


@app.command("translate-file")
def translate_file_cmd(
    source: Annotated[Path, typer.Argument(help="Source markdown file.")],
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Output path (default: stdout)."),
    ] = None,
    source_lang: Annotated[str, typer.Option()] = "ru",
    target_lang: Annotated[str, typer.Option()] = "en",
    no_critic: Annotated[
        bool,
        typer.Option(help="Skip critic / verify passes (default for translate-only)."),
    ] = True,
    with_critic: Annotated[
        bool,
        typer.Option(
            "--with-critic",
            help="Run critic + heuristics after translate (legacy single-step QA).",
        ),
    ] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Translate one markdown file locally (no GitHub)."""
    cfg = load_config(yaml_path=config)
    try:
        client = create_llm_client(cfg)
    except (RuntimeError, LLMConfigError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    text = source.read_text(encoding="utf-8")
    try:
        result = translate_file(
            text,
            client,
            load_glossary(),
            file_path=str(source),
            config=cfg,
            source_lang=source_lang,
            target_lang=target_lang,
            enable_critic=with_critic and not no_critic,
        )
    except (LLMError, ValueError) as exc:
        console.print(f"[red]Translation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if output:
        output.write_text(result.final_text, encoding="utf-8")
        console.print(f"Wrote {output} (verdict={result.verdict})")
    else:
        sys.stdout.write(result.final_text)


@app.command()
def extract(
    source: Annotated[Path, typer.Argument(help="Markdown file to segment.")],
    fmt: Annotated[
        str,
        typer.Option("--format", case_sensitive=False, help="json or text."),
    ] = "json",
) -> None:
    """Extract translatable segments from a markdown file (debug)."""
    text = source.read_text(encoding="utf-8")
    segments = extract_segments(parse_markdown(text))
    if fmt.lower() == "text":
        for seg in segments:
            console.print(f"{seg.id}\t{seg.kind}\t{seg.text[:80]!r}")
        return
    payload = [
        {"id": s.id, "kind": s.kind, "text": s.text, "char_len": len(s.text)}
        for s in segments
    ]
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


def _print_job_summary(mode: str, result: object) -> None:
    from ydbdoc_review.github.workflow import DocJobResult

    if not isinstance(result, DocJobResult):
        return
    console.print(f"[green]Done[/green] ({mode})")
    console.print(f"  Pairs processed: {len(result.pr_result.pair_results)}")
    console.print(f"  Translated: {result.pr_result.translated_count}")
    console.print(f"  Failed: {result.pr_result.failed_count}")
    if result.translation_pr_number:
        console.print(f"  Translation PR: #{result.translation_pr_number}")
    if result.translation_branch:
        console.print(f"  Branch: {result.translation_branch}")
    if result.committed:
        console.print("  Git: committed")
    if result.pushed:
        console.print("  Git: pushed")


if __name__ == "__main__":
    app()
