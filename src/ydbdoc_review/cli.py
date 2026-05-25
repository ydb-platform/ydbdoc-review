from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import click
import httpx
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
)
from ydbdoc_review.pipeline_v2 import (
    PairQaOutcome,
    final_verdict,
    format_pair_qa_markdown,
    format_translation_pr_summary,
    translate_document,
)
from ydbdoc_review.translation_qa import run_pairs_qa_and_repair
from ydbdoc_review.paths import (
    DocPair,
    pairs_from_changed_files,
    ru_asset_files_to_mirror,
    ru_toc_yaml_paths,
    truncate,
)
from ydbdoc_review.pair_diff import diff_has_added_lines, pair_needs_en_from_ru_only_diff
from ydbdoc_review.toc_yaml import merge_en_toc_yaml, translate_toc_title
from ydbdoc_review.verify_pr import run_verify_pr


_OK_STATUSES = frozenset({"added", "modified", "changed", "renamed"})


def _translation_branch_name(pr_number: int) -> str:
    return f"ydbdoc-review/pr-{pr_number}"


def _pr_changed_path_set(changed: list[str]) -> set[str]:
    return {p.replace("\\", "/").lstrip("./") for p in changed}


def _apply_ru_diff_generation_overrides(
    results: list[dict[str, Any]],
    *,
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]],
    pr_changed: set[str],
) -> int:
    """
    Force ``needs_generation_for=en`` when RU diff adds lines but EN diff in this PR does not.
    Returns count of overridden pairs.
    """
    n = 0
    for item in results:
        ru_p = item.get("ru_path")
        en_p = item.get("en_path")
        if not isinstance(ru_p, str) or not isinstance(en_p, str):
            continue
        ru_diff, en_diff = pair_diffs.get((ru_p, en_p), (None, None))
        if not pair_needs_en_from_ru_only_diff(
            ru_path=ru_p,
            ru_diff=ru_diff,
            en_diff=en_diff,
            pr_changed_paths=pr_changed,
        ):
            continue
        if item.get("needs_generation_for") == "en":
            continue
        item["needs_generation_for"] = "en"
        item["semantically_aligned"] = False
        item["summary"] = (
            "RU PR diff adds content; EN file unchanged in PR — generating EN update "
            "(deterministic override)."
        )
        n += 1
        click.echo(
            f"Override check model: force EN generation for `{ru_p}` "
            "(RU diff has additions, EN diff does not).",
            err=True,
        )
    return n


def _analyze_batch_json_size(batch: list[dict[str, Any]]) -> int:
    return len(json.dumps({"pairs": batch}, ensure_ascii=False))


def _shrink_one_entry_to_max_json(entry: dict[str, Any], max_chars: int) -> None:
    """Last resort when a single pair still exceeds ``max_chars`` (FM input limit)."""
    keys = ("ru_text", "en_text", "ru_diff_vs_base", "en_diff_vs_base")
    guard = 0
    while _analyze_batch_json_size([entry]) > max_chars and guard < 10_000:
        guard += 1
        best: tuple[int, str] | None = None
        for k in keys:
            v = entry.get(k)
            if not isinstance(v, str) or len(v) <= 256:
                continue
            if best is None or len(v) > best[0]:
                best = (len(v), k)
        if best is None:
            break
        k = best[1]
        v = entry[k]
        new_len = max(256, len(v) * 2 // 3)
        entry[k] = (
            v[:new_len]
            + "\n\n…(truncated: pair alone exceeds YDBDOC_ANALYZE_MAX_JSON_CHARS)\n"
        )


def _batch_analyze_payload_pairs(
    payload_pairs: list[dict[str, Any]],
    max_batch_chars: int,
) -> list[list[dict[str, Any]]]:
    """
    Split ``payload_pairs`` into several API-sized batches (multiple check-model calls).

    Full ``ru_text`` / ``en_text`` / diffs are kept per pair; only batch boundaries split pairs.
    """
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for entry in payload_pairs:
        if not cur:
            lone = [entry]
            if _analyze_batch_json_size(lone) > max_batch_chars:
                click.echo(
                    f"Warning: one pair exceeds analyze batch size ({max_batch_chars} chars); "
                    "truncating that pair only so the check model can run.",
                    err=True,
                )
                _shrink_one_entry_to_max_json(entry, max_batch_chars)
            cur = [entry]
            continue
        trial = cur + [entry]
        if _analyze_batch_json_size(trial) <= max_batch_chars:
            cur = trial
            continue
        batches.append(cur)
        lone = [entry]
        if _analyze_batch_json_size(lone) > max_batch_chars:
            click.echo(
                f"Warning: one pair exceeds analyze batch size ({max_batch_chars} chars); "
                "truncating that pair only.",
                err=True,
            )
            _shrink_one_entry_to_max_json(entry, max_batch_chars)
        cur = [entry]
    if cur:
        batches.append(cur)
    return batches


def _merge_analyze_batch_results(
    payload_pairs: list[dict[str, Any]],
    per_batch: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for batch in per_batch:
        for item in batch:
            ru = item.get("ru_path")
            en = item.get("en_path")
            if isinstance(ru, str) and isinstance(en, str):
                by_key[(ru, en)] = item
    merged: list[dict[str, Any]] = []
    for entry in payload_pairs:
        ru = entry["ru_path"]
        en = entry["en_path"]
        got = by_key.get((ru, en))
        if got is None:
            raise SystemExit(
                f"Check model returned no result for pair `{ru}` / `{en}` "
                f"(expected {len(payload_pairs)} pair(s) total)."
            )
        merged.append(got)
    return merged


def _slim_analyze_batch_for_retry(
    batch: list[dict[str, Any]],
    *,
    text_limit: int = 4000,
) -> list[dict[str, Any]]:
    """Smaller JSON for a second check-model attempt (drops diffs, caps bodies)."""
    slim: list[dict[str, Any]] = []
    for e in batch:
        ne = dict(e)
        for k in ("ru_diff_vs_base", "en_diff_vs_base"):
            ne.pop(k, None)
        rt = ne.get("ru_text")
        if isinstance(rt, str) and len(rt) > text_limit:
            ne["ru_text"] = rt[:text_limit] + "\n…(slim retry: truncated)\n"
        et = ne.get("en_text")
        if isinstance(et, str) and len(et) > text_limit:
            ne["en_text"] = et[:text_limit] + "\n…(slim retry: truncated)\n"
        slim.append(ne)
    return slim


def _fallback_check_batch_results(
    batch: list[dict[str, Any]],
    *,
    pr_changed: set[str],
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]],
) -> list[dict[str, Any]]:
    """
    When the check model returns prose (refusal) or invalid JSON, infer ``results``.

    Uses the same RU-diff override inputs as the normal path (``pair_diffs`` + ``pr_changed``).
    """
    out: list[dict[str, Any]] = []
    for entry in batch:
        ru_p = str(entry["ru_path"])
        en_p = str(entry["en_path"])
        ru_t = (entry.get("ru_text") or "").strip()
        en_t = (entry.get("en_text") or "").strip()
        ru_present = len(ru_t) > 30
        en_present = len(en_t) > 30
        rd, ed = pair_diffs.get((ru_p, en_p), (None, None))
        gen: str | None = None
        if ru_present and not en_present:
            gen = "en"
        elif en_present and not ru_present:
            gen = "ru"
        elif ru_present and en_present:
            if pair_needs_en_from_ru_only_diff(
                ru_path=ru_p,
                ru_diff=rd,
                en_diff=ed,
                pr_changed_paths=pr_changed,
            ):
                gen = "en"
            elif ru_p in pr_changed and en_p not in pr_changed:
                gen = "en"
            elif en_p in pr_changed and ru_p not in pr_changed:
                gen = "ru"
            elif ru_p in pr_changed:
                gen = "en"
        aligned = bool(ru_present and en_present and gen is None)
        out.append(
            {
                "ru_path": ru_p,
                "en_path": en_p,
                "ru_present": ru_present,
                "en_present": en_present,
                "semantically_aligned": aligned,
                "needs_generation_for": gen,
                "summary": (
                    "Heuristic fallback: check model refused or returned non-JSON "
                    "(safety filter, outage, or non-JSON prose)."
                ),
            }
        )
    return out


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


def _blocked_needs_other_pr(
    blocked_raw: list[tuple[str, str]],
    blocked_prereq: list[tuple[str, str, github_api.PathPrerequisiteInfo | None]],
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> bool:
    """
    False when we can run doc_translate on the current PR for every blocked RU file.

    If this PR's changed-files list includes the RU path, we translate here (even when a
    later merged PR also touched the same path, e.g. #38242 after #38357).
    """
    if not blocked_raw:
        return False
    by_ru = {ru: prereq for _en, ru, prereq in blocked_prereq}
    for _en_p, ru_p in blocked_raw:
        if github_api.pr_touches_path(owner, repo, pr_number, ru_p, token):
            continue
        prereq = by_ru.get(ru_p)
        if prereq is None or not prereq.recommended:
            return True
        if prereq.recommended.number != pr_number:
            return True
    return False


def _prefer_current_pr_in_prereq(
    prereq: github_api.PathPrerequisiteInfo,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    ru_path: str,
    token: str,
) -> github_api.PathPrerequisiteInfo:
    """If this PR changed `ru_path`, recommend it over a later unrelated merge in the chain."""
    if not github_api.pr_touches_path(owner, repo, pr_number, ru_path, token):
        return prereq
    for ref in prereq.chain:
        if ref.number == pr_number:
            return github_api.PathPrerequisiteInfo(chain=prereq.chain, recommended=ref)
    return prereq


def _h3_heading_count(text: str) -> int:
    return len(re.findall(r"^###\s+\S", text, re.MULTILINE))


def _texts_at_base_for_pairs(
    workdir: str,
    base_ref_local: str,
    analyze_results: list[dict[str, Any]],
) -> dict[str, tuple[str | None, str | None]]:
    """``ru_path`` → (ru_on_main, en_on_main)."""
    out: dict[str, tuple[str | None, str | None]] = {}
    for item in analyze_results:
        ru_p = item.get("ru_path")
        en_p = item.get("en_path")
        if not isinstance(ru_p, str) or not isinstance(en_p, str):
            continue
        if ru_p in out:
            continue
        out[ru_p] = (
            git_local.read_text_at_ref(workdir, base_ref_local, ru_p),
            git_local.read_text_at_ref(workdir, base_ref_local, en_p),
        )
    return out


def _supplementary_pair_notes(
    *,
    ru_path: str,
    en_path: str,
    ru_text: str | None,
    en_text: str | None,
    ru_at_base: str | None,
    en_at_base: str | None,
    ru_diff: str | None,
    en_diff: str | None,
    pr_changed: set[str],
) -> list[str]:
    """Deterministic cross-check for a pair skipped by translation generation."""
    notes: list[str] = []
    ru_pr = (ru_text or "").strip()
    en_pr = (en_text or "").strip()
    ru_ref = (ru_at_base or "").strip() or ru_pr

    if not ru_pr or not en_pr:
        notes.append("⚠️ на ветке PR нет полного RU или EN — проверьте вручную.")
        return notes

    if len(en_pr) < int(len(ru_ref) * 0.75):
        notes.append(
            f"⚠️ EN заметно короче RU: ~{len(en_pr)} симв. vs ~{len(ru_ref)}; "
            f"заголовков `###`: {_h3_heading_count(en_pr)} vs {_h3_heading_count(ru_ref)}."
        )
    else:
        notes.append("✅ EN сопоставим с RU по длине (эвристика).")

    ru_only = pair_needs_en_from_ru_only_diff(
        ru_path=ru_path,
        ru_diff=ru_diff,
        en_diff=en_diff,
        pr_changed_paths=pr_changed,
    )
    if ru_only:
        notes.append("⚠️ в diff PR новые строки только в RU — обычно нужен EN.")
    elif ru_path in pr_changed and en_path in pr_changed:
        ru_add = diff_has_added_lines(ru_diff)
        en_add = diff_has_added_lines(en_diff)
        if ru_add and en_add:
            notes.append("✅ RU и EN менялись в этом PR (есть добавления в обоих diff).")
        elif ru_add or en_add:
            notes.append("✅ RU и EN в списке изменённых файлов PR.")
        else:
            notes.append("✅ оба файла в PR (без добавленных строк в diff — правки/удаления).")
    elif ru_path in pr_changed:
        notes.append("ℹ️ в PR менялся только RU.")
    elif en_path in pr_changed:
        notes.append("ℹ️ в PR менялся только EN.")

    if en_at_base and en_pr and len(en_pr) < int(len(en_at_base) * 0.9):
        notes.append(
            f"ℹ️ EN на PR короче EN на main (~{len(en_pr)} vs ~{len(en_at_base)} симв.)."
        )
    return notes


def _build_pair_scope_report(
    *,
    analyze_results: list[dict[str, Any]],
    generated_en_paths: set[str],
    generated_ru_paths: set[str],
    full_texts: dict[tuple[str, str], tuple[str | None, str | None]],
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]],
    pr_changed: set[str],
    texts_at_base: dict[str, tuple[str | None, str | None]] | None = None,
) -> list[str]:
    """
  Markdown lines: translated list + skipped pairs with check-model summary and heuristics.
    """
    lines: list[str] = []
    if generated_en_paths:
        lines.extend(
            [
                "### Переведённые файлы",
                "",
                "В translation PR попали только эти пути (модель или override запросили генерацию EN/RU):",
                "",
                *[f"- `{p}`" for p in sorted(generated_en_paths | generated_ru_paths)],
                "",
            ]
        )

    skipped: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for item in analyze_results:
        ru_p = item.get("ru_path")
        en_p = item.get("en_path")
        if not isinstance(ru_p, str) or not isinstance(en_p, str):
            continue
        gen = item.get("needs_generation_for")
        aligned = bool(item.get("semantically_aligned"))
        if gen in ("en", "ru"):
            if gen == "en" and en_p in generated_en_paths:
                continue
            if gen == "ru" and ru_p in generated_ru_paths:
                continue
        if gen in ("en", "ru"):
            continue
        if aligned:
            skipped.append(item)
        else:
            review.append(item)

    if skipped:
        lines.extend(
            [
                "### Пропущено — дополнительная проверка",
                "",
                "Пары из этого PR, для которых **отдельный translation PR не создавал** "
                "(модель проверки: `semantically_aligned`, генерация не нужна). "
                "Ниже — её вывод и **детерминированная** сверка RU↔EN.",
                "",
            ]
        )
        for item in skipped:
            ru_p = str(item["ru_path"])
            en_p = str(item["en_path"])
            ru_t, en_t = full_texts.get((ru_p, en_p), (None, None))
            ru_base, en_base = (None, None)
            if texts_at_base:
                ru_base, en_base = texts_at_base.get(ru_p, (None, None))
            rd, ed = pair_diffs.get((ru_p, en_p), (None, None))
            lines.append(f"#### `{ru_p}` ↔ `{en_p}`")
            lines.append("")
            lines.append(f"_Модель проверки:_ {item.get('summary', '—')}")
            lines.append("")
            for note in _supplementary_pair_notes(
                ru_path=ru_p,
                en_path=en_p,
                ru_text=ru_t,
                en_text=en_t,
                ru_at_base=ru_base,
                en_at_base=en_base,
                ru_diff=rd,
                en_diff=ed,
                pr_changed=pr_changed,
            ):
                lines.append(f"- {note}")
            lines.append("")

    if review:
        lines.extend(
            [
                "### Требует ручной проверки",
                "",
                "Модель **не** считает пару выровненной и **не** выбрала сторону для автоперевода:",
                "",
            ]
        )
        for item in review:
            ru_p = item.get("ru_path")
            en_p = item.get("en_path")
            lines.append(
                f"- `{ru_p}` ↔ `{en_p}`: _{item.get('summary', '—')}_"
            )
        lines.append("")

    return lines


def _build_source_pr_comment(
    *,
    translation_pr_url: str | None,
    generated: list[str],
    analyze_results: list[dict[str, Any]] | None = None,
    generated_en_to_ru: dict[str, str] | None = None,
    full_texts: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
    pr_changed: set[str] | None = None,
    texts_at_base: dict[str, tuple[str | None, str | None]] | None = None,
) -> list[str]:
    """Short comment on the source doc PR after a successful translation run."""
    lines = ["## ydbdoc-review", ""]
    if translation_pr_url:
        lines.append(f"**PR с переводом:** {translation_pr_url}")
        lines.append(
            "_Отчёт QA (ревью, исправления критиком, подтверждение переводчиком) — "
            "в первом комментарии к PR с переводом._"
        )
    else:
        lines.append(
            "_PR с переводом не создан автоматически — см. лог workflow "
            "(права `YDBDOC_PUSH_PAT`, создание pull request)._"
        )
    if (
        analyze_results
        and full_texts is not None
        and pair_diffs is not None
        and pr_changed is not None
    ):
        gen_en = {
            p
            for p in (generated or [])
            if p.endswith(".md") and generated_en_to_ru and p in generated_en_to_ru
        }
        scope_lines = _build_pair_scope_report(
            analyze_results=analyze_results,
            generated_en_paths=gen_en,
            generated_ru_paths={
                p for p in (generated or []) if p.endswith(".md") and p not in gen_en
            },
            full_texts=full_texts,
            pair_diffs=pair_diffs,
            pr_changed=pr_changed,
            texts_at_base=texts_at_base,
        )
        if scope_lines:
            lines.extend(["", *scope_lines])
    elif generated:
        lines.extend(["", "Переведённые файлы:", *[f"- `{p}`" for p in generated]])
    return lines


def _build_translation_pr_self_check_body(
    *,
    source_pr_number: int,
    base_owner: str,
    base_repo: str,
    translate_model: str,
    verify_model: str,
    self_check_section: str,
) -> str:
    source = f"https://github.com/{base_owner}/{base_repo}/pull/{source_pr_number}"
    return "\n".join(
        [
            "## ydbdoc-review — отчёт по переводу",
            "",
            f"Перевод для исходного PR {source}.",
            "",
            f"_Модель перевода:_ `{translate_model}` · "
            f"_модель-критик (ревью и исправление):_ `{verify_model}`",
            "",
            self_check_section.strip(),
        ]
    )


def _run_translation_qa_and_repair(
    settings: Settings,
    *,
    workdir: str,
    generated: list[str],
    generated_en_to_ru: dict[str, str],
    generated_ru_to_en: dict[str, str],
    warnings: list[str],
    source_pr_number: int,
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]],
    base_ref_local: str | None,
) -> tuple[str | None, int, list[PairQaOutcome]]:
    """QA: critic compare → fix-diff (optional) → re-validate (optional) → heuristics.

    Writes repaired markdown to *workdir* before commit. Returns comment markdown,
    count of files updated on disk, and per-file outcomes. Never raises on critic
    failure — the failure becomes part of the report.
    """
    md_generated = [p for p in generated if p.endswith(".md")]
    if not md_generated:
        return None, 0, []
    pairs: list[tuple[str, str]] = []
    for gen_p in md_generated:
        if gen_p in generated_en_to_ru:
            pairs.append((generated_en_to_ru[gen_p], gen_p))
        elif gen_p in generated_ru_to_en:
            pairs.append((gen_p, generated_ru_to_en[gen_p]))
    text, repaired_paths, outcomes = run_pairs_qa_and_repair(
        settings,
        workdir=workdir,
        pairs=pairs,
        pair_diffs=pair_diffs,
        source_pr_number=source_pr_number,
        base_ref_local=base_ref_local,
    )
    for p in repaired_paths:
        click.echo(f"  QA fix applied: `{p}`")
    if repaired_paths:
        warnings.append(
            f"- QA: критик обновил {len(repaired_paths)} файл(ов) на диске перед коммитом."
        )
    return text, len(repaired_paths), outcomes


def _build_prerequisites_comment(
    *,
    new_at_base: list[str],
    overlay_ok: list[str],
    blocked_prereq: list[tuple[str, str, github_api.PathPrerequisiteInfo | None]],
    translation_pr_url: str | None,
    translation_pr_title: str,
    translation_branch: str,
    compare_url: str | None,
    mirrored_en: list[str],
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
        raise AssertionError("_build_prerequisites_comment requires blocked_only=True")

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
                lines.append("_Не удалось выбрать PR для `doc_translate`._")
                continue
            if rec.number == pr_number:
                later = [r for r in prereq.chain if r.merged_at > rec.merged_at]
                lines.append(
                    f"**`doc_translate` на этом PR (#{pr_number})** — _{rec.title}_"
                )
                if later:
                    nums = ", ".join(f"#{r.number}" for r in later)
                    lines.append(
                        f"_Позже файл также меняли PR {nums}; для полного EN достаточно "
                        f"перевода с #{pr_number}, отдельно на них вешать лейбл не нужно._"
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
    return lines


def _build_translation_pr_body(*, pr_number: int, base_owner: str, base_repo: str) -> str:
    source = f"https://github.com/{base_owner}/{base_repo}/pull/{pr_number}"
    return "\n".join(
        [
            "### Changelog entry",
            "",
            f"DOCS: English translation for documentation PR #{pr_number} ({source}).",
            "",
            "### Changelog category",
            "",
            "* Documentation",
            "",
            "* Not for changelog (please remove the unused bullet(s) below)",
        ]
    )


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
            "Vendor spelling is «DeepSeek»; slug example: `deepseek-v4-flash` "
            "(URI `gpt://<folder>/deepseek-v4-flash`) — always verify in UI.\n"
            f"Current config: check={settings.model_check!r}, translate={settings.model_translate!r}, "
            f"translation_verify={settings.model_translation_verify!r}"
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


@main.command("verify")
@click.option("--repo", required=True, help="Repository (owner/name).")
@click.option("--pr", "pr_number", type=int, required=True, help="Translation PR number.")
@click.option(
    "--repo-path",
    type=click.Path(exists=True, file_okay=False, path_type=str),
    default=None,
    help="Local checkout of the PR head branch.",
)
@click.option(
    "--merge-base-with",
    default="origin/main",
    show_default=True,
    help="Base ref for PR diff and RU authority (e.g. origin/main).",
)
@click.option(
    "--source-pr",
    "source_pr_number",
    type=int,
    default=None,
    help="Source doc PR number (default: parse from translation PR title/body).",
)
@click.option("--no-comment", is_flag=True, help="Do not post a GitHub comment.")
@click.option(
    "--no-commit",
    is_flag=True,
    help="Apply repairs on disk only; do not commit to the PR branch.",
)
@click.option(
    "--no-push",
    is_flag=True,
    help="Commit locally but do not push (implies repairs are committed unless --no-commit).",
)
def verify_cmd(
    repo: str,
    pr_number: int,
    repo_path: str | None,
    merge_base_with: str,
    source_pr_number: int | None,
    no_comment: bool,
    no_commit: bool,
    no_push: bool,
) -> None:
    """doc_verify: critic + repair + translator on PR branch RU↔EN (like doc_translate)."""
    settings = Settings.from_env()
    if not settings.review_enabled:
        click.echo("ydbdoc-review: skipped (review disabled).")
        return
    run_verify_pr(
        settings,
        repo=repo,
        pr_number=pr_number,
        repo_path=repo_path,
        merge_base_with=merge_base_with,
        source_pr_number=source_pr_number,
        no_comment=no_comment,
        no_commit=no_commit,
        no_push=no_push,
    )


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
    if trunc_raw == "0" or not trunc_raw:
        analyze_trunc: int | None = None
    elif trunc_raw.isdigit() and int(trunc_raw) > 0:
        analyze_trunc = int(trunc_raw)
    else:
        analyze_trunc = None

    max_batch_raw = os.environ.get("YDBDOC_ANALYZE_MAX_JSON_CHARS", "").strip()
    max_analyze_batch_json = (
        int(max_batch_raw)
        if max_batch_raw.isdigit() and int(max_batch_raw) > 0
        else 24_000
    )

    payload_pairs: list[dict[str, Any]] = []
    full_texts: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    pr_changed = _pr_changed_path_set(changed)

    diff_preview_raw = os.environ.get("YDBDOC_ANALYZE_DIFF_MAX", "").strip()
    if diff_preview_raw.isdigit() and int(diff_preview_raw) > 0:
        diff_preview_cap = int(diff_preview_raw)
    else:
        diff_preview_cap = 500_000
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
        dru_full: str | None = None
        den_full: str | None = None
        if effective_repo_path:
            try:
                dru_full = git_local.file_diff_range(
                    effective_repo_path, merge_base_with, pair.ru_path
                )
                if dru_full.strip():
                    entry["ru_diff_vs_base"] = (
                        dru_full
                        if len(dru_full) <= diff_preview_cap
                        else dru_full[:diff_preview_cap] + "\n…(diff truncated)\n"
                    )
            except RuntimeError:
                pass
            try:
                den_full = git_local.file_diff_range(
                    effective_repo_path, merge_base_with, pair.en_path
                )
                if den_full and den_full.strip():
                    entry["en_diff_vs_base"] = (
                        den_full
                        if len(den_full) <= diff_preview_cap
                        else den_full[:diff_preview_cap] + "\n…(diff truncated)\n"
                    )
            except RuntimeError:
                pass
        pair_diffs[(pair.ru_path, pair.en_path)] = (dru_full, den_full)
        payload_pairs.append(entry)

    analyze_batches = _batch_analyze_payload_pairs(payload_pairs, max_analyze_batch_json)
    click.echo(
        f"Check model: {len(analyze_batches)} batch(es), "
        f"up to {max_analyze_batch_json} chars of `{{\"pairs\":...}}` per batch "
        "(YDBDOC_ANALYZE_MAX_JSON_CHARS)."
    )

    instructions = load_analyze_instructions(settings).strip()
    per_batch_results: list[list[dict[str, Any]]] = []
    for bi, batch in enumerate(analyze_batches):
        payloads: list[tuple[str, list[dict[str, Any]]]] = [
            ("full", batch),
            ("slim", _slim_analyze_batch_for_retry(batch)),
        ]
        batch_results: list[dict[str, Any]] | None = None
        last_parse_err: str | None = None
        last_raw_head: str | None = None
        for label, pl in payloads:
            if label == "slim":
                click.echo(
                    f"Retrying check model batch {bi + 1}/{len(analyze_batches)} "
                    "with reduced JSON (no diffs, shorter bodies) …",
                    err=True,
                )
            analyze_input = json.dumps({"pairs": pl}, ensure_ascii=False)
            if label == "full":
                click.echo(
                    f"Calling check model `{settings.model_check}` "
                    f"(batch {bi + 1}/{len(analyze_batches)}, {len(batch)} pair(s); "
                    f"translate `{settings.model_translate}` only when needed) …"
                )
            raw = call_yandex_responses(
                settings,
                settings.model_check,
                instructions=instructions,
                user_input=analyze_input,
                max_output_tokens=8000,
            )
            last_raw_head = (raw or "")[:500]
            try:
                data = parse_json_object(raw)
            except (json.JSONDecodeError, ValueError) as e:
                last_parse_err = str(e)
                continue
            br = data.get("results")
            if not isinstance(br, list):
                last_parse_err = "results is not a list"
                continue
            if len(br) != len(batch):
                last_parse_err = f"expected {len(batch)} results, got {len(br)}"
                continue
            batch_results = br
            break
        if batch_results is None:
            click.echo(
                f"Warning: check model batch {bi + 1}/{len(analyze_batches)} did not return valid JSON "
                f"after full+slim attempts ({last_parse_err}). "
                f"First 500 chars of last response: {last_raw_head!r}. "
                "Using heuristic fallback for this batch (often a provider safety refusal).",
                err=True,
            )
            batch_results = _fallback_check_batch_results(
                batch, pr_changed=pr_changed, pair_diffs=pair_diffs
            )
        per_batch_results.append(batch_results)

    results = _merge_analyze_batch_results(payload_pairs, per_batch_results)

    if effective_repo_path and pr_changed:
        overridden = _apply_ru_diff_generation_overrides(
            results,
            pair_diffs=pair_diffs,
            pr_changed=pr_changed,
        )
        if overridden:
            click.echo(f"Applied RU-diff override to {overridden} pair(s).")

    workdir = effective_repo_path
    tmp: str | None = None
    if not workdir and not dry_run:
        tmp = tempfile.mkdtemp(prefix="ydbdoc-review-")
        click.echo(f"Cloning head repo {head_owner}/{head_repo_name} @ {head_sha[:7]} …")
        _clone_head_repo(head_clone_url, settings.github_push_token, tmp, head_sha)
        workdir = tmp

    generated: list[str] = []
    generated_en_to_ru: dict[str, str] = {}
    generated_ru_to_en: dict[str, str] = {}
    mirrored_en: list[str] = []
    warnings: list[str] = []
    base_ref_local: str | None = None

    if workdir:
        try:
            git_local.ensure_remote(
                workdir,
                "ydbdoc-base",
                git_local.remote_push_url(base_clone_url, settings.github_push_token),
            )
            base_ref_local = git_local.fetch_remote_branch(
                workdir, "ydbdoc-base", base_ref
            )
        except RuntimeError as exc:
            click.echo(f"Warning: could not fetch `{base_ref}`: {exc}", err=True)

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
            from ydbdoc_review.ru_source_bugs import fix_ru_source_bugs_in_text

            ru_source = ru_full or ""
            use_main_ru = bool(
                workdir
                and base_ref_local
                and git_local.path_exists_at_tree(workdir, base_ref_local, ru_p)
                and not git_local.path_exists_at_tree(workdir, base_ref_local, en_p)
            )
            if use_main_ru:
                ru_on_main = git_local.read_text_at_ref(workdir, base_ref_local, ru_p)
                if ru_on_main:
                    ru_source = ru_on_main
            ru_source, ru_fixed_bugs = fix_ru_source_bugs_in_text(
                ru_source, file_path=ru_p
            )
            if ru_fixed_bugs and workdir:
                git_local.write_text(workdir, ru_p, ru_source)
                warnings.append(
                    f"- RU `{ru_p}`: исправлена опечатка `--config-dir/…` → "
                    f"`--config-dir /…` в исходной русской доке."
                )
            en_on_main: str | None = None
            if workdir and base_ref_local and git_local.path_exists_at_tree(
                workdir, base_ref_local, en_p
            ):
                en_on_main = git_local.read_text_at_ref(workdir, base_ref_local, en_p)
            ru_diff, _en_diff = pair_diffs.get(key, (None, None))
            try:
                out_md, mode = translate_document(
                    settings,
                    source_path=ru_p,
                    source_full=ru_source,
                    source_lang="Russian",
                    target_lang="English",
                    en_on_main=en_on_main,
                    ru_pr_diff=ru_diff,
                )
            except Exception as exc:
                warnings.append(f"- `{en_p}`: перевод не выполнен: {exc}")
                continue
            click.echo(f"  (mode: {mode})")
            git_local.write_text(workdir, en_p, out_md)
            generated.append(en_p)
            generated_en_to_ru[en_p] = ru_p
        else:
            if not en_full:
                warnings.append(f"- Cannot translate to RU: missing English source `{en_p}`")
                continue
            click.echo(f"Translating EN→RU `{en_p}` → `{ru_p}` with `{settings.model_translate}` …")
            try:
                out_md, mode = translate_document(
                    settings,
                    source_path=en_p,
                    source_full=en_full,
                    source_lang="English",
                    target_lang="Russian",
                )
            except Exception as exc:
                warnings.append(f"- `{ru_p}`: перевод не выполнен: {exc}")
                continue
            click.echo(f"  (mode: {mode})")
            git_local.write_text(workdir, ru_p, out_md)
            generated.append(ru_p)
            generated_ru_to_en[ru_p] = en_p

    if workdir and not dry_run:
        for ru_asset, en_asset in ru_asset_files_to_mirror(changed, settings.docs_prefix):
            if git_local.copy_file_in_repo(workdir, ru_asset, en_asset):
                mirrored_en.append(en_asset)
                click.echo(f"Copied RU→EN asset `{ru_asset}` → `{en_asset}`")

        new_toc_hrefs = {Path(p).name for p in generated if p.endswith(".md")}
        for ru_toc, en_toc in ru_toc_yaml_paths(changed, settings.docs_prefix):
            ru_pr_yaml = git_local.read_text(workdir, ru_toc)
            if not ru_pr_yaml:
                continue
            en_main_yaml = (
                git_local.read_text_at_ref(workdir, base_ref_local, en_toc)
                if base_ref_local
                else None
            )
            if not en_main_yaml:
                en_main_yaml = git_local.read_text(workdir, en_toc) or "items:\n"
            merged = merge_en_toc_yaml(
                en_main_yaml,
                ru_pr_yaml,
                new_hrefs=new_toc_hrefs,
                translate_name=lambda title: translate_toc_title(settings, title),
            )
            git_local.write_text(workdir, en_toc, merged)
            mirrored_en.append(en_toc)
            click.echo(f"Merged EN toc `{en_toc}` (kept labels from `{base_ref}`, new entries only)")

    translation_branch = _translation_branch_name(pr_number)
    translation_pr_title = f"Translation of PR {pr_number}"
    translation_pr_url: str | None = None
    translation_pr_number: int | None = None
    compare_url: str | None = None
    new_at_base: list[str] = []
    overlay_ok: list[str] = []
    blocked_prereq: list[tuple[str, str, github_api.PathPrerequisiteInfo | None]] = []
    blocked_only = False

    if workdir and generated_en_to_ru and base_ref_local is None:
        try:
            git_local.ensure_remote(
                workdir,
                "ydbdoc-base",
                git_local.remote_push_url(base_clone_url, settings.github_push_token),
            )
            base_ref_local = git_local.fetch_remote_branch(
                workdir, "ydbdoc-base", base_ref
            )
        except RuntimeError:
            pass

    if workdir and generated_en_to_ru:
        new_at_base, overlay_ok, blocked_raw = _assess_translation_files(
            repo_path=workdir,
            base_ref=base_ref_local,
            generated_en_to_ru=generated_en_to_ru,
        )
        for en_p, ru_p in blocked_raw:
            prereq = github_api.find_prerequisite_chain_for_path(
                base_owner,
                base_repo,
                ru_p,
                token=settings.github_token,
                repo_path=workdir,
                base_git_ref=base_ref_local,
                base_branch=base_ref,
                exclude_pr=None,
            )
            prereq = _prefer_current_pr_in_prereq(
                prereq,
                owner=base_owner,
                repo=base_repo,
                pr_number=pr_number,
                ru_path=ru_p,
                token=settings.github_token,
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
        if blocked_prereq and _blocked_needs_other_pr(
            blocked_raw,
            blocked_prereq,
            owner=base_owner,
            repo=base_repo,
            pr_number=pr_number,
            token=settings.github_token,
        ):
            blocked_only = True
            click.echo(
                "Blocking translation PR: EN prerequisite is missing on base branch. "
                "Label doc_translate on the merged PR(s) listed in the comment.",
                err=True,
            )
        elif blocked_prereq:
            click.echo(
                f"EN missing on {base_ref}; this PR #{pr_number} is the translation "
                "target — proceeding with full-file translation.",
            )

    translation_qa_section: str | None = None
    qa_outcomes: list[PairQaOutcome] = []
    if (
        settings.translation_self_check_enabled
        and workdir
        and generated
        and not dry_run
        and not blocked_only
    ):
        click.echo(
            f"Running translation QA "
            f"(critic `{settings.model_translation_verify}`, "
            f"translate `{settings.model_translate}`) …"
        )
        translation_qa_section, _n_repaired, qa_outcomes = _run_translation_qa_and_repair(
            settings,
            workdir=workdir,
            generated=generated,
            generated_en_to_ru=generated_en_to_ru,
            generated_ru_to_en=generated_ru_to_en,
            warnings=warnings,
            source_pr_number=pr_number,
            pair_diffs=pair_diffs,
            base_ref_local=base_ref_local,
        )

    publish_paths = list(dict.fromkeys(generated + mirrored_en))

    committed = False
    if workdir and not dry_run and publish_paths and not no_commit and not blocked_only:
        try:
            if github_api.delete_branch_if_exists(
                base_owner,
                base_repo,
                translation_branch,
                settings.github_push_token,
            ):
                click.echo(
                    f"Deleted existing upstream branch `{translation_branch}` "
                    f"(fresh translation from `{base_ref}`)."
                )
        except httpx.HTTPError as exc:
            click.echo(
                f"Warning: could not delete `{translation_branch}` on upstream ({exc}); "
                "will force-push the new translation.",
                err=True,
            )
        git_local.prepare_translation_branch_on_base(
            workdir,
            translation_branch=translation_branch,
            base_remote_url=git_local.remote_push_url(base_clone_url, settings.github_push_token),
            base_remote_name="ydbdoc-base",
            base_branch=base_ref,
            paths=publish_paths,
        )
        msg = (
            f"docs: add AI translations ({len(generated)} md, {len(mirrored_en)} companion)\n\n"
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
            compare_url = github_api.compare_branch_url(
                base_owner,
                base_repo,
                base_ref,
                translation_branch,
                title=translation_pr_title,
            )
            click.echo(
                f"Committed {len(publish_paths)} file(s) on branch `{translation_branch}` "
                f"from `{base_owner}/{base_repo}:{base_ref}`."
            )
        else:
            click.echo("Nothing to commit (empty diff after write).")

    if no_commit and generated:
        click.echo(
            f"Wrote {len(generated)} file(s) under `{workdir}`; "
            "`--no-commit`: review with `git diff`, then commit when ready."
        )

    if committed and compare_url is None:
        compare_url = github_api.compare_branch_url(
            base_owner,
            base_repo,
            base_ref,
            translation_branch,
            title=translation_pr_title,
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
            force_with_lease=True,
        )
        click.echo("Push completed.")
        pr_title = translation_pr_title
        pr_body = _build_translation_pr_body(
            pr_number=pr_number,
            base_owner=base_owner,
            base_repo=base_repo,
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
        if not opened and settings.github_token != settings.github_push_token:
            opened = github_api.create_pull(
                base_owner,
                base_repo,
                title=pr_title,
                head=translation_branch,
                base=base_ref,
                body=pr_body,
                token=settings.github_token,
            )
        if opened:
            translation_pr_url, trans_num = opened
            translation_pr_number = trans_num
            click.echo(f"Opened translation PR #{trans_num}: {translation_pr_url}")
        else:
            err = github_api.pull_create_error(
                base_owner,
                base_repo,
                title=pr_title,
                head=translation_branch,
                base=base_ref,
                body=pr_body,
                token=settings.github_push_token,
            )
            if compare_url is None:
                compare_url = github_api.compare_branch_url(
                    base_owner,
                    base_repo,
                    base_ref,
                    translation_branch,
                    title=translation_pr_title,
                    body=pr_body,
                )
            click.echo(
                "Could not open translation PR via API "
                "(check YDBDOC_PUSH_PAT: repo scope, pull_requests write)."
                + (f" GitHub: {err}" if err else ""),
                err=True,
            )
            click.echo(
                f"Open manually (title prefilled): {compare_url}",
                err=True,
            )

    if (
        translation_qa_section
        and translation_pr_number is not None
        and not dry_run
        and not no_comment
    ):
        sc_body = _build_translation_pr_self_check_body(
            source_pr_number=pr_number,
            base_owner=base_owner,
            base_repo=base_repo,
            translate_model=settings.model_translate,
            verify_model=settings.model_translation_verify,
            self_check_section=translation_qa_section,
        )
        try:
            sc_urls = github_api.post_issue_comment_chunked(
                base_owner,
                base_repo,
                translation_pr_number,
                sc_body,
                settings.github_token,
            )
            click.echo(
                f"Posted QA report on translation PR ({len(sc_urls)} comment(s)): "
                f"{sc_urls[0] if sc_urls else '—'}"
            )
        except Exception as exc:
            click.echo(
                f"Warning: could not post self-check on translation PR #{translation_pr_number}: {exc}",
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
    elif blocked_only:
        comment_lines = _build_prerequisites_comment(
            new_at_base=new_at_base,
            overlay_ok=overlay_ok,
            blocked_prereq=blocked_prereq,
            translation_pr_url=translation_pr_url,
            translation_pr_title=translation_pr_title,
            translation_branch=translation_branch,
            compare_url=compare_url,
            mirrored_en=mirrored_en,
            publish_owner=base_owner,
            publish_repo=base_repo,
            base_ref=base_ref,
            is_fork=is_fork,
            head_owner=head_owner,
            head_repo=head_repo_name,
            head_ref=head_ref,
            pr_number=pr_number,
            blocked_only=True,
        )
        if warnings:
            comment_lines.extend(["", "### Прочее", *warnings])
    elif committed:
        texts_at_base = (
            _texts_at_base_for_pairs(workdir, base_ref_local, results)
            if workdir and base_ref_local
            else None
        )
        comment_lines = _build_source_pr_comment(
            translation_pr_url=translation_pr_url,
            generated=generated,
            analyze_results=results,
            generated_en_to_ru=generated_en_to_ru,
            full_texts=full_texts,
            pair_diffs=pair_diffs,
            pr_changed=pr_changed,
            texts_at_base=texts_at_base,
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
