"""All-or-nothing in-memory one-pass translation transaction."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from ydbdoc_review.pipeline.dependency_queue import DependencyPlan
from ydbdoc_review.translation.model_policy import TranslationJobManifest
from ydbdoc_review.translation.one_pass import (
    translate_ru_to_en_once,
    validate_complete_document,
)
from ydbdoc_review.validation.fence_comments import (
    check_cyrillic_in_en_fence_comments,
    check_cyrillic_in_en_text_fences,
)
from ydbdoc_review.validation.heuristics import (
    check_cyrillic_in_en_all_fences,
    check_md_link_parity,
)
from ydbdoc_review.validation.href_parity import check_href_parity
from ydbdoc_review.validation.link_locale import check_link_locale_in_en
from ydbdoc_review.validation.prose_cyrillic import collect_cyrillic_prose_spans


@dataclass(frozen=True)
class TransactionResult:
    publishable: bool
    staged: dict[str, str]
    report: dict


_MD_HREF = re.compile(r"(?<!!)\[[^]]*]\(([^)\s]+)(?:\s+[^)]*)?\)")


def _rewrite_staged_inbound_anchors(
    staged: dict[str, str],
    anchor_maps: dict[str, tuple[tuple[str, str], ...]],
) -> dict[str, str]:
    """Update inbound fragments only inside the already-authorized staged set."""
    lookup = {
        (target, old): new
        for target, mappings in anchor_maps.items()
        for old, new in mappings
    }
    rewritten: dict[str, str] = {}
    for source_path, text in staged.items():
        def replace(match: re.Match[str], current_path: str = source_path) -> str:
            href = match.group(1)
            split = urlsplit(href)
            if not split.fragment:
                return match.group(0)
            if not split.path:
                target = current_path
            elif split.path.startswith("/"):
                target = f"ydb/docs{split.path}" if split.path.startswith("/en/") else split.path.lstrip("/")
            else:
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(current_path), split.path)
                )
            replacement = lookup.get((target, split.fragment))
            if replacement is None:
                return match.group(0)
            new_href = urlunsplit((split.scheme, split.netloc, split.path, split.query, replacement))
            return match.group(0).replace(href, new_href, 1)

        rewritten[source_path] = _MD_HREF.sub(replace, text)
    return rewritten


def _href_target(source_path: str, href: str) -> tuple[str, str] | None:
    split = urlsplit(href)
    if not split.fragment or split.scheme or split.netloc:
        return None
    if not split.path:
        target = source_path
    elif split.path.startswith("/en/"):
        target = f"ydb/docs{split.path}"
    elif split.path.startswith("/"):
        target = split.path.lstrip("/")
    else:
        target = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_path), split.path)
        )
    return target, split.fragment


def _out_of_scope_anchor_findings(
    *,
    staged: dict[str, str],
    anchor_maps: dict[str, tuple[tuple[str, str], ...]],
    pinned_en_paths: set[str] | None,
    read_pinned_en,
) -> list[dict[str, str]]:
    if not any(anchor_maps.values()):
        return []
    if pinned_en_paths is None or read_pinned_en is None:
        return [
            {
                "category": "local_repair_failed",
                "terminal_reason": "out_of_scope_link",
                "source_file": "",
                "original_href": "",
                "manual_action": "provide the complete pinned EN docs tree for inbound-anchor validation",
            }
        ]
    lookup = {
        (target, old): new
        for target, mappings in anchor_maps.items()
        for old, new in mappings
    }
    findings: list[dict[str, str]] = []
    for source_path in sorted(pinned_en_paths - set(staged)):
        text = read_pinned_en(source_path)
        if text is None:
            continue
        for match in _MD_HREF.finditer(text):
            resolved = _href_target(source_path, match.group(1))
            if resolved not in lookup:
                continue
            findings.append(
                {
                    "category": "local_repair_failed",
                    "terminal_reason": "out_of_scope_link",
                    "source_file": source_path,
                    "original_href": match.group(1),
                    "manual_action": "update this inbound fragment in an explicitly authorized source job",
                }
            )
    return findings


def run_translation_transaction(
    plan: DependencyPlan,
    *,
    read_ru,
    client,
    to_en_path,
    manifest: TranslationJobManifest,
    pinned_en_paths: set[str] | None = None,
    read_pinned_en=None,
    en_toc_reachable: frozenset[str] | None = None,
    docs_text_reader=None,
) -> TransactionResult:
    staged: dict[str, str] = {}
    source_by_output: dict[str, str] = {}
    anchor_maps: dict[str, tuple[tuple[str, str], ...]] = {}
    failures: list[dict[str, str]] = []
    file_reports: list[dict[str, object]] = []
    for entry in plan.entries:
        try:
            output_path = to_en_path(entry.ru_path)
            source_text = read_ru(entry.ru_path)
            translated = translate_ru_to_en_once(
                source_text,
                client,
                file_path=entry.ru_path,
                manifest=manifest,
                en_toc_reachable=en_toc_reachable,
            )
            validate_complete_document(
                translated.text, translated.validation_context
            )
            staged[output_path] = translated.text
            source_by_output[output_path] = source_text
            anchor_maps[output_path] = translated.anchor_map
            file_reports.append(
                {
                    "source_file": entry.ru_path,
                    "output_file": output_path,
                    "origin": entry.origin,
                    "accepted_payload_count": 1,
                    "render_count": 1,
                    "acquisition_attempts": [
                        attempt.__dict__ for attempt in translated.acquisition_attempts
                    ],
                }
            )
        except Exception as exc:
            failures.append({"file": entry.ru_path, "category": "translation_failed", "message": str(exc)})
            file_reports.append(
                {
                    "source_file": entry.ru_path,
                    "output_file": to_en_path(entry.ru_path),
                    "origin": entry.origin,
                    "accepted_payload_count": getattr(
                        exc, "accepted_payload_count", 0
                    ),
                    "render_count": getattr(exc, "render_count", 0),
                    "acquisition_attempts": [
                        attempt.__dict__
                        for attempt in getattr(exc, "acquisition_attempts", ())
                    ],
                }
            )
    report = {
        "initial_count": plan.initial_count,
        "auto_added_count": plan.auto_added_count,
        "queue": [entry.__dict__ for entry in plan.entries],
        "files": file_reports,
        "unresolved": [item.__dict__ for item in plan.unresolved],
        "failures": failures,
    }
    anchor_findings = _out_of_scope_anchor_findings(
        staged=staged,
        anchor_maps=anchor_maps,
        pinned_en_paths=pinned_en_paths,
        read_pinned_en=read_pinned_en,
    )
    report["anchor_findings"] = anchor_findings
    if failures or plan.unresolved or anchor_findings:
        staged.clear()
        return TransactionResult(False, staged, report)
    staged = _rewrite_staged_inbound_anchors(staged, anchor_maps)
    comparable_sources = _rewrite_staged_inbound_anchors(
        source_by_output, anchor_maps
    )
    link_findings: list[dict[str, object]] = []
    for output_path, target_text in staged.items():
        source_text = comparable_sources[output_path]
        messages = [
            *check_md_link_parity(
                source_text,
                target_text,
                source_lang="ru",
                target_lang="en",
                source_file=output_path.replace("/docs/en/", "/docs/ru/", 1),
                en_toc_reachable=en_toc_reachable,
            ),
            *check_href_parity(
                source_text,
                target_text,
                source_lang="ru",
                target_lang="en",
                en_page_path=output_path,
                en_toc_reachable=en_toc_reachable,
                docs_text_reader=docs_text_reader,
            ),
            *check_link_locale_in_en(target_text, target_lang="en"),
            *check_cyrillic_in_en_all_fences(target_text, target_lang="en"),
            *check_cyrillic_in_en_fence_comments(target_text, target_lang="en"),
            *check_cyrillic_in_en_text_fences(target_text, target_lang="en"),
            *[
                (
                    "cyrillic_in_prose: "
                    f"{span.span_id} «{span.text}» in «{span.context}»"
                )
                for span in collect_cyrillic_prose_spans(target_text)
            ],
        ]
        if messages:
            link_findings.append(
                {
                    "category": "read_only_link_validation_failed",
                    "source_file": output_path.replace("/docs/en/", "/docs/ru/", 1),
                    "output_file": output_path,
                    "messages": messages,
                }
            )
    report["link_findings"] = link_findings
    if link_findings:
        staged.clear()
        return TransactionResult(False, staged, report)
    return TransactionResult(True, staged, report)
