"""RU-authoritative, single-request file translation."""

from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from ydbdoc_review.llm.structured import parse_json_model
from ydbdoc_review.parsing.ast_types import (
    BlockQuote,
    BulletList,
    Document,
    FencedCode,
    Heading,
    HTMLBlock,
    IndentedCode,
    InlineCode,
    InlineEmphasis,
    InlineHardBreak,
    InlineHTML,
    InlineImage,
    InlineLink,
    InlineSoftBreak,
    InlineStrike,
    InlineStrong,
    InlineTermRef,
    InlineText,
    InlineVariable,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    TableCell,
    TableRow,
    TermDefinition,
    ThematicBreak,
    YfmCut,
    YfmIf,
    YfmIfBranch,
    YfmInclude,
    YfmNote,
    YfmTab,
    YfmTabs,
)
from ydbdoc_review.parsing.markdown_parser import (
    ParsedMarkdownSourceMap,
    _canonical_source_slice,
    parse_markdown,
    parse_markdown_with_source_map,
)
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.inline_protector import restore_inline_text
from ydbdoc_review.segmentation.reinsert import UnknownSegmentKindError
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.acquisition import (
    AcquisitionController,
    AcquisitionProtocolError,
)
from ydbdoc_review.translation.model_policy import (
    TranslationJobManifest,
    require_translation_chat_once,
)
from ydbdoc_review.translation.schemas import TranslateBatchResponse
from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href
from ydbdoc_review.validation.yfm_anchor import diplodoc_auto_slug, english_yfm_anchor


class OnePassTranslationError(RuntimeError):
    pass


def _validate_supported_segment_kinds(segments: Sequence[Segment]) -> None:
    supported = set(SegmentKind)
    for segment in segments:
        if not isinstance(segment.kind, SegmentKind) or segment.kind not in supported:
            raise UnknownSegmentKindError(
                f"Unsupported segment kind: {segment.kind}"
            )


def _parse_translate_response(
    raw: str, *, expected_segments: Sequence[Segment]
) -> dict[str, str]:
    parsed = parse_json_model(raw, TranslateBatchResponse)
    expected = [segment.id for segment in expected_segments]
    got = [item.id for item in parsed.segments]
    if got != expected or Counter(got) != Counter(expected):
        raise ValueError(
            "segment id mismatch: "
            f"expected={expected!r}, got={got!r}"
        )
    translated = {item.id: item.text for item in parsed.segments}
    for segment in expected_segments:
        candidate = translated[segment.id]
        _assert_tokens(segment.text, candidate)
        if not candidate.strip():
            raise ValueError(f"empty translated prose: {segment.id}")
        if _CYRILLIC.search(candidate):
            raise ValueError(f"residual Cyrillic in translated prose: {segment.id}")
    return translated


@dataclass(frozen=True, slots=True)
class OnePassResult:
    text: str
    prose_count: int
    validation_context: CompleteDocumentValidationContext
    model_calls: int = 1
    acquisition_attempts: tuple[object, ...] = ()
    anchor_map: tuple[tuple[str, str], ...] = ()


_TOKEN = re.compile(r"⟦[A-Z][A-Za-z0-9_-]*⟧")
_CYRILLIC = re.compile(r"[\u0410-\u042f\u0430-\u044f\u0401\u0451]")
_EXPLICIT_HEADING = re.compile(
    r"^(#{1,6})[ \t]+(.+?)[ \t]+\{#([^}\n]+)\}[ \t]*$", re.MULTILINE
)
@dataclass(frozen=True, slots=True)
class ValidationAtomRecord:
    block_id: str
    atom_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class CompleteDocumentValidationContext:
    source_text: str
    source_file: str
    source_container_signature: tuple[tuple[str, int, int], ...]
    source_atoms: tuple[ValidationAtomRecord, ...]
    source_fence_config_signature: tuple[tuple[str, int, int, str], ...]
    expected_links: tuple[tuple[str, str], ...]
    expected_anchor_map: tuple[tuple[str, str], ...]
    expected_anchors: tuple[str, ...]
    en_toc_reachable: frozenset[str] | None
    residual_cyrillic_allowed_ranges: tuple[tuple[int, int, str], ...]


def _walk_ast_preorder(node):
    yield node
    if isinstance(node, Document):
        children = node.children
    elif isinstance(node, (Paragraph, Heading, TermDefinition)):
        children = node.children
    elif isinstance(node, (BlockQuote, ListItem, YfmNote, YfmCut)):
        children = node.children
    elif isinstance(node, (BulletList, OrderedList)):
        children = node.children
    elif isinstance(node, Table):
        children = [node.header, *node.rows]
    elif isinstance(node, TableRow):
        children = node.cells
    elif isinstance(node, TableCell):
        children = node.children
    elif isinstance(node, YfmTabs):
        children = node.children
    elif isinstance(node, YfmTab):
        children = [*node.title, *node.children]
    elif isinstance(node, YfmIf):
        children = node.branches
    elif isinstance(node, YfmIfBranch):
        children = node.children
    elif isinstance(node, (InlineEmphasis, InlineStrong, InlineStrike, InlineLink)):
        children = node.children
    elif isinstance(node, (FencedCode, IndentedCode, ThematicBreak, HTMLBlock, YfmInclude, InlineText, InlineCode, InlineImage, InlineHTML, InlineSoftBreak, InlineHardBreak, InlineVariable, InlineTermRef)):
        children = ()
    else:
        raise OnePassTranslationError(f"unhandled_ast_node:{type(node).__name__}")
    for child in children:
        yield from _walk_ast_preorder(child)


def _map_expected_destination(href: str, source_file: str, expected_anchor_map: tuple[tuple[str, str], ...]) -> str:
    split = urlsplit(href)
    path = split.path
    original_path = path
    if path.startswith("/ru/"):
        path = f"/en/{path.removeprefix('/ru/')}"
    fragment = split.fragment
    if not split.scheme and not split.netloc:
        relative_same = not original_path or posixpath.normpath(
            posixpath.join(posixpath.dirname(source_file), original_path.lstrip("/"))
        ) == source_file
        public_ru = None
        marker = "/docs/ru/"
        if marker in source_file:
            public_ru = "/ru/" + source_file.split(marker, 1)[1]
        elif "/ru/" in source_file:
            public_ru = "/ru/" + source_file.split("/ru/", 1)[1]
        public_en = (
            f"/en/{public_ru.removeprefix('/ru/')}" if public_ru is not None else None
        )
        absolute_same = public_en is not None and (
            original_path in {public_ru, public_en} or path == public_en
        )
        if (relative_same or absolute_same) and fragment:
            mapping = dict(expected_anchor_map)
            # Source links may keep a percent-encoded Cyrillic fragment while the
            # anchor map uses decoded keys; look up both forms.
            fragment = mapping.get(
                fragment, mapping.get(unquote(fragment), fragment)
            )
    return urlunsplit((split.scheme, split.netloc, path, split.query, fragment))


def _canonical_atom_payload(node, source_file: str, expected_anchor_map: tuple[tuple[str, str], ...]) -> bytes:
    def canonical(value):
        if isinstance(value, dict):
            result = {key: canonical(item) for key, item in value.items() if key != "source_span"}
            if result.get("kind") == "link":
                result["href"] = _map_expected_destination(result["href"], source_file, expected_anchor_map)
            return result
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value
    return json.dumps(canonical(node.model_dump(mode="json")), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _slice_hashes(text: str, source_map: ParsedMarkdownSourceMap) -> tuple[tuple[str, int, int, str], ...]:
    return tuple(
        (
            record.descriptor,
            record.start_byte,
            record.end_byte,
            hashlib.sha256(_canonical_source_slice(text, record)).hexdigest(),
        )
        for record in source_map.non_prose
    )


def build_complete_document_validation_context(
    source_text: str,
    source_file: str,
    segments: Sequence[Segment],
    expected_anchor_map: tuple[tuple[str, str], ...],
    en_toc_reachable: frozenset[str] | None = None,
) -> CompleteDocumentValidationContext:
    """Freeze source-owned facts once. This function never calls a model."""
    document, source_map = parse_markdown_with_source_map(source_text)
    atoms = tuple(
        ValidationAtomRecord(segment.id, placeholder.placeholder, hashlib.sha256(_canonical_atom_payload(placeholder.node, source_file, expected_anchor_map)).hexdigest())
        for segment in segments for placeholder in segment.placeholders
    )
    containers = tuple((record.descriptor, record.start_byte, record.end_byte) for record in source_map.containers)
    non_prose = _slice_hashes(source_text, source_map)
    links = tuple((node.href, _map_expected_destination(node.href, source_file, expected_anchor_map)) for node in _walk_ast_preorder(document) if isinstance(node, InlineLink))
    anchor_lookup = dict(expected_anchor_map)
    anchors = tuple(anchor_lookup.get(node.anchor, node.anchor) for node in _walk_ast_preorder(document) if isinstance(node, Heading) and node.anchor is not None)
    ranges = tuple((start, end, descriptor) for descriptor, start, end, _sha in non_prose)
    return CompleteDocumentValidationContext(source_text, source_file, containers, atoms, non_prose, links, expected_anchor_map, anchors, en_toc_reachable, ranges)


def validate_complete_document(text: str, context: CompleteDocumentValidationContext) -> None:
    """Read-only fail-closed check against the frozen source context."""
    assert_no_protect_token(text)
    projected_ranges = tuple(
        (start, end, descriptor)
        for descriptor, start, end, _sha in context.source_fence_config_signature
    )
    if projected_ranges != context.residual_cyrillic_allowed_ranges:
        raise OnePassTranslationError("validation_context_invalid:residual_cyrillic_ranges")
    try:
        candidate_document, candidate_map = parse_markdown_with_source_map(text)
    except Exception as exc:
        raise OnePassTranslationError("candidate_parse_failed") from exc
    if tuple(record.descriptor for record in candidate_map.containers) != tuple(
        descriptor for descriptor, _start, _end in context.source_container_signature
    ):
        raise OnePassTranslationError("container_structure_parity")
    candidate_segments = extract_segments(candidate_document)
    atoms = tuple(
        ValidationAtomRecord(
            segment.id,
            placeholder.placeholder,
            hashlib.sha256(
                _canonical_atom_payload(
                    placeholder.node, context.source_file, context.expected_anchor_map
                )
            ).hexdigest(),
        )
        for segment in candidate_segments
        for placeholder in segment.placeholders
    )
    if atoms != context.source_atoms:
        raise OnePassTranslationError("protected_atom_parity")
    candidate_non_prose = _slice_hashes(text, candidate_map)
    if tuple((item[0], item[3]) for item in candidate_non_prose) != tuple(
        (item[0], item[3]) for item in context.source_fence_config_signature
    ):
        raise OnePassTranslationError("fence_config_parity")
    candidate_hrefs = tuple(
        node.href
        for node in _walk_ast_preorder(candidate_document)
        if isinstance(node, InlineLink)
    )
    expected_hrefs = tuple(candidate for _source, candidate in context.expected_links)
    if candidate_hrefs != expected_hrefs:
        raise OnePassTranslationError("href_parity")
    if context.en_toc_reachable is not None:
        for candidate_href in expected_hrefs:
            target = resolve_internal_md_href(context.source_file, candidate_href)
            if target is not None and target not in context.en_toc_reachable:
                raise OnePassTranslationError(f"unreachable_en_internal_link:{target}")
    candidate_anchors = tuple(
        node.anchor
        for node in _walk_ast_preorder(candidate_document)
        if isinstance(node, Heading) and node.anchor is not None
    )
    if candidate_anchors != context.expected_anchors:
        raise OnePassTranslationError("explicit_anchor_parity")
    if any(_CYRILLIC.search(segment.text) for segment in candidate_segments):
        raise OnePassTranslationError("residual_cyrillic_prose")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _decode_non_ascii_percent_escapes(text: str) -> str:
    """Decode only UTF-8 destination bytes, retaining ASCII URL escapes verbatim."""
    def replace(match: re.Match[str]) -> str:
        raw = bytes.fromhex(match.group(0).replace("%", ""))
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            return match.group(0)
        return decoded if any(ord(char) > 127 for char in decoded) else match.group(0)

    return re.sub(r"(?:%[0-9A-Fa-f]{2})+", replace, text)


def _assert_tokens(source: str, target: str) -> None:
    if _tokens(source) != _tokens(target):
        raise OnePassTranslationError(
            "translation changed the protect-token sequence or multiset"
        )
    stack: list[str] = []
    for token in _tokens(target):
        if token.startswith("⟦LBEGIN_"):
            stack.append(token.removeprefix("⟦LBEGIN_").removesuffix("⟧"))
        elif token.startswith("⟦LEND_"):
            identity = token.removeprefix("⟦LEND_").removesuffix("⟧")
            if not stack or stack.pop() != identity:
                raise OnePassTranslationError("crossing or unbalanced link tokens")
    if stack:
        raise OnePassTranslationError("unbalanced link tokens")


def assert_no_protect_token(text: str) -> None:
    variants = (text, html.unescape(text), html.unescape(text).replace("\\", ""))
    variants += tuple(__import__("urllib.parse", fromlist=["unquote"]).unquote(v) for v in variants)
    if any(_TOKEN.search(v) for v in variants):
        raise OnePassTranslationError("unrestored_protect_token")


def _render_source_template_once(
    source: str, segments: list[Segment], translated: dict[str, str]
) -> str:
    """Replace only RU prose slots while retaining all source container bytes."""
    output: list[str] = []
    cursor = 0
    for segment in segments:
        source_slot = _decode_non_ascii_percent_escapes(
            restore_inline_text(segment.text, segment.placeholders)
        )
        target_slot = _decode_non_ascii_percent_escapes(
            restore_inline_text(
                translated[segment.id], segment.placeholders, target_locale=True
            )
        )
        candidates = (source_slot, unquote(source_slot))
        located = [
            (source.find(candidate, cursor), candidate, None)
            for candidate in candidates
            if source.find(candidate, cursor) >= 0
        ]
        if not located:
            normalized: list[str] = []
            raw_positions: list[int] = []
            index = 0
            while index < len(source):
                if source[index] == "\\" and index + 1 < len(source) and source[index + 1] in "[]":
                    index += 1
                normalized.append(source[index])
                raw_positions.append(index)
                index += 1
            normalized_source = "".join(normalized)
            normalized_cursor = next(
                (i for i, raw in enumerate(raw_positions) if raw >= cursor),
                len(normalized_source),
            )
            for candidate in candidates:
                normalized_index = normalized_source.find(candidate, normalized_cursor)
                if normalized_index < 0:
                    continue
                raw_start = raw_positions[normalized_index]
                raw_end = raw_positions[normalized_index + len(candidate) - 1] + 1
                located.append((raw_start, source[raw_start:raw_end], source[raw_start:raw_end]))
        index, matched_slot, raw_equivalent = min(
            located, default=(-1, source_slot, None), key=lambda item: item[0]
        )
        if index < 0:
            raise OnePassTranslationError(
                f"lossless source slot not found: {segment.id}"
            )
        output.append(source[cursor:index])
        output.append(
            raw_equivalent
            if raw_equivalent is not None and target_slot == source_slot
            else target_slot
        )
        cursor = index + len(matched_slot)
    output.append(source[cursor:])
    return "".join(output)


def _localize_cyrillic_explicit_anchors(
    source: str, rendered: str, *, propose_anchor=None
) -> tuple[str, tuple[tuple[str, str], ...], int, int]:
    """Apply the sole permitted explicit-anchor exception deterministically."""
    source_headings = list(_EXPLICIT_HEADING.finditer(source))
    target_headings = list(_EXPLICIT_HEADING.finditer(rendered))
    if len(source_headings) != len(target_headings):
        raise OnePassTranslationError("explicit heading anchor cardinality changed")
    used = {m.group(3) for m in target_headings if not _CYRILLIC.search(m.group(3))}
    replacements: list[tuple[str, str]] = []
    repair_calls = 0
    repair_findings = 0
    for source_heading, target_heading in zip(
        source_headings, target_headings, strict=True
    ):
        source_anchor = source_heading.group(3)
        target_anchor = target_heading.group(3)
        if not _CYRILLIC.search(source_anchor):
            if target_anchor != source_anchor:
                raise OnePassTranslationError("ascii_anchor_parity")
            continue
        proposed = english_yfm_anchor(source_anchor, target_heading.group(2))
        if proposed and not proposed.isascii():
            proposed = diplodoc_auto_slug(target_heading.group(2))
        if not proposed or not proposed.isascii() or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", proposed
        ):
            if propose_anchor is None:
                raise OnePassTranslationError("english_anchor_proposal_required")
            proposed, used_calls = propose_anchor(
                source_anchor, target_heading.group(2), used
            )
            repair_findings += 1
            repair_calls += used_calls
            if repair_findings > 4 or repair_calls > 8:
                raise OnePassTranslationError("english_anchor_document_cap")
        base = proposed
        suffix = 2
        while proposed in used:
            proposed = f"{base}-{suffix}"
            suffix += 1
        used.add(proposed)
        replacements.append((source_anchor, proposed))

    for source_anchor, target_anchor in replacements:
        rendered = rendered.replace(f"{{#{source_anchor}}}", f"{{#{target_anchor}}}")
        rendered = rendered.replace(f"](#{source_anchor})", f"](#{target_anchor})")
        rendered = rendered.replace(
            f"](#{quote(source_anchor, safe='')})", f"](#{target_anchor})"
        )
    return rendered, tuple(replacements), repair_calls, repair_findings


def translate_ru_to_en_once(
    source_text: str,
    client: object,
    *,
    file_path: str,
    manifest: TranslationJobManifest,
    en_toc_reachable: frozenset[str] | None = None,
) -> OnePassResult:
    """Translate every prose slot in one model request and render from RU AST."""
    acquired = None
    accepted_payload_seen = False
    render_count = 0
    doc = parse_markdown(source_text)
    segments = extract_segments(doc)
    if not source_text.strip() or not segments:
        error = OnePassTranslationError("empty_or_unparseable_ru")
        error.accepted_payload_count = 0
        error.render_count = 0
        error.acquisition_attempts = ()
        raise error
    payload = {"file": file_path, "segments": [{"id": s.id, "text": s.text} for s in segments]}
    messages = [
        {"role": "system", "content": "Translate Russian prose to English. Preserve every opaque token exactly. Return JSON: {segments:[{id,text}]}."},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        from ydbdoc_review.translation.local_repair import (
            acquire_english_anchor,
            run_bounded_local_repair,
        )

        chat_client = require_translation_chat_once(client)
        frozen_context: list[CompleteDocumentValidationContext] = []

        def validate(response: object):
            nonlocal accepted_payload_seen
            try:
                translated = _parse_translate_response(
                    response.content, expected_segments=segments
                )
                accepted_payload_seen = True
                _validate_supported_segment_kinds(segments)
                candidate = _render_source_template_once(
                    source_text, segments, translated
                )
                candidate, candidate_anchor_map, repair_calls, repair_findings = (
                    _localize_cyrillic_explicit_anchors(
                        source_text,
                        candidate,
                        propose_anchor=lambda source_anchor, heading, used: acquire_english_anchor(
                            client=chat_client,
                            repair_models=manifest.model_policy.repair,
                            source_anchor=source_anchor,
                            english_heading=heading,
                            used_anchors=used,
                        ),
                    )
                )
                # Build an attempt-local context for validation. Freeze only after
                # this candidate is accepted so a rejected primary cannot poison
                # fallback expected_anchors (FINAL008-IMPL-007).
                if frozen_context:
                    validation_context = frozen_context[0]
                else:
                    validation_context = build_complete_document_validation_context(
                        source_text,
                        file_path,
                        segments,
                        candidate_anchor_map,
                        en_toc_reachable,
                    )
                validate_complete_document(candidate, validation_context)
                if not frozen_context:
                    frozen_context.append(validation_context)
                return (
                    candidate,
                    candidate_anchor_map,
                    repair_calls,
                    repair_findings,
                    frozen_context[0],
                )
            except UnknownSegmentKindError:
                raise
            except Exception as exc:
                raise AcquisitionProtocolError(str(exc)) from exc

        acquired = AcquisitionController(
            chat_client,
            manifest.model_policy.translate,
            role="translate",
            parser=validate,
        ).acquire(messages)
        (
            rendered,
            anchor_map,
            anchor_repair_calls,
            anchor_repair_findings,
            validation_context,
        ) = acquired.payload
        render_count = 1
        repaired = run_bounded_local_repair(
            rendered,
            source_text,
            chat_client,
            critic_models=manifest.model_policy.critic,
            repair_models=manifest.model_policy.repair,
            validation_context=validation_context,
            source_file=file_path,
            repair_calls_used=anchor_repair_calls,
            repair_findings_used=anchor_repair_findings,
        )
        if repaired.reports:
            raise OnePassTranslationError(f"local_repair_failed: {repaired.reports}")
        rendered = repaired.text
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, OnePassTranslationError)
            else OnePassTranslationError(f"translation_failed: {exc}")
        )
        error.accepted_payload_count = 1 if acquired is not None or accepted_payload_seen else 0
        error.render_count = render_count
        error.acquisition_attempts = (
            () if acquired is None else tuple(acquired.attempts)
        )
        raise error from (None if error is exc else exc)
    return OnePassResult(
        text=rendered,
        prose_count=len(segments),
        validation_context=validation_context,
        model_calls=len(acquired.attempts),
        acquisition_attempts=tuple(acquired.attempts),
        anchor_map=anchor_map,
    )
