"""Source-only translation. No quality retries, legacy harness or target seed.

The caller supplies model request capacity (including prompt and output reserve).
Every result, including damaged responses and partial files, is returned for the
common quality loop and publication. Offsets are Python string offsets; reading
and writing UTF-8 with newline translation disabled preserves original bytes.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from ydbdoc_review.model import ModelChoice, ModelClient
from ydbdoc_review.parsing.front_matter import (
    FrontMatterError,
    FrontMatterValueRecord,
    encode_decoded_scalar,
    parse_front_matter_with_spans,
)
from ydbdoc_review.parsing.inline_locations import LocatedText, prose_source_spans
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.segmentation.mermaid import mermaid_labels, mermaid_skeleton
from ydbdoc_review.validation.code_comments import comment_skeleton, comment_spans

_MARKER = re.compile(r"⟦[^⟦⟧]*⟧")
_URL = re.compile(r"(?:https?://|mailto:|ftp://|www\.)[^\s<>\[\]{}]+")
_ANCHOR = re.compile(r"\{#[^}\r\n]+\}")


class MarkerError(ValueError):
    pass


class FrontMatterRestoreError(FrontMatterError):
    """Failed scalar encoding, with the atom-restored candidate for repair."""

    def __init__(self, problem: str, text: str):
        super().__init__(problem)
        self.text = text


class CapacityError(ValueError):
    pass


@dataclass(frozen=True)
class Atom:
    marker: str
    raw: str
    start: int
    end: int


@dataclass(frozen=True)
class FrontMatterRegion:
    start: int
    end: int
    records: tuple[FrontMatterValueRecord, ...]


@dataclass(frozen=True)
class DecodedScalar:
    raw: str
    record: FrontMatterValueRecord
    opening: str
    closing: str


@dataclass(frozen=True)
class ProtectedDocument:
    source: str
    text: str
    atoms: tuple[Atom, ...]
    # Legal chunk ends: block/paragraph or sentence boundaries.
    boundaries: tuple[int, ...]
    issues: tuple[DocumentIssue, ...] = ()
    front_matter: tuple[FrontMatterRegion, ...] = ()
    scalars: tuple[DecodedScalar, ...] = ()
    # Decoded-value separators/URLs have semantic rather than source offsets.
    value_atoms: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class DocumentIssue:
    problem: str
    chunk: int | None = None


@dataclass(frozen=True)
class ChunkResult:
    chunk: Chunk
    response: str | None
    text: str | None
    issues: tuple[DocumentIssue, ...] = ()
    unfinished: bool = False


@dataclass(frozen=True)
class FileResult:
    path: str
    text: str | None
    issues: tuple[DocumentIssue, ...]
    unfinished: bool
    chunks: tuple[ChunkResult, ...] = ()
    protected: ProtectedDocument | None = None


@dataclass(frozen=True)
class RequestBudget:
    """Count the *complete* messages with the configured model's tokenizer.

    Reserve max_output_tokens in the context window. No hardcoded chars/token
    ratio. For a configured alternative, use capacity valid for both endpoints.
    """
    context_tokens: int
    max_output_tokens: int
    count_tokens: Callable[[list[dict[str, Any]]], int]

    def fits(self, messages: list[dict[str, Any]], *, expected_output: str = "") -> bool:
        if self.max_output_tokens <= 0 or self.context_tokens <= self.max_output_tokens:
            raise CapacityError("Invalid context/output token budget")
        count = self.count_tokens(messages)
        if count < 0:
            raise CapacityError("Negative request token count")
        # Source-length output estimate also respects the endpoint's response
        # ceiling. Translation can expand; a length finish remains incomplete.
        output = (self.count_tokens([{"role": "assistant", "content": expected_output}])
                  - self.count_tokens([{"role": "assistant", "content": ""}]))
        return (count + self.max_output_tokens <= self.context_tokens
                and output <= self.max_output_tokens)


def protect(source: str) -> ProtectedDocument:
    """Expose parser-owned prose spans, protect the complement verbatim."""
    tokens = create_parser(source_locations=True).parse(source)
    editable = bytearray(len(source))
    issues: list[DocumentIssue] = []
    front_matter: list[FrontMatterRegion] = []

    def expose(start: int, end: int) -> None:
        editable[start:end] = b"\1" * (end - start)

    for span in prose_source_spans(tokens):
        # Escapes/entities may map several visible characters to a raw atom.
        if span.end == span.start + 1:
            expose(span.start, span.end)

    offsets = [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", source)]
    if offsets[-1] != len(source):
        offsets.append(len(source))
    for token in tokens:
        if token.type == "front_matter" and token.map:
            start = offsets[token.map[0] + 1]
            end = offsets[token.map[1] - 1]
            if end < start:
                continue  # Parser can emit an empty FM token for leading list syntax.
            raw = source[start:end]
            fields, records = parse_front_matter_with_spans(raw)
            front_matter.append(FrontMatterRegion(start, end, records))
            for key in ("title", "description"):
                if fields.get(key) and key not in {r.key for r in records}:
                    issues.append(DocumentIssue(f"Front matter {key} cannot be safely translated (alias, duplicate or unsupported scalar)"))
        elif token.type in {"yfm_note_open", "yfm_cut_open"} and token.map:
            start, end = offsets[token.map[0]], offsets[token.map[0] + 1]
            match = re.search(r'"([^"\r\n]*)"', source[start:end])
            if match:
                expose(start + match.start(1), start + match.end(1))
        elif token.type in {"fence", "code_block"}:
            body = token.content
            if not isinstance(body, LocatedText):
                continue  # Unknown mapping stays opaque, never guessed by text search.
            if token.info.strip() == "mermaid":
                try:
                    mermaid_skeleton(body)
                except ValueError:
                    issues.append(DocumentIssue("Unsupported Mermaid grammar: labels remain protected; manual translation required"))
            spans = (mermaid_labels(body) if token.info.strip() == "mermaid"
                     else comment_spans(body, token.info))
            for span in spans:
                for origin in body.spans[span.start:span.end]:
                    if origin is not None:
                        expose(origin.start, origin.end)

    # Literal service markers are source atoms too: they cannot collide with IDs.
    for pattern in (_URL, _ANCHOR, _MARKER, re.compile(r"[⟦⟧]")):
        for match in pattern.finditer(source):
            editable[match.start():match.end()] = b"\0" * len(match.group())
    # Sentence separators survive models trimming the ends of individual chunks.
    for match in re.finditer(r"[.!?][ \t]+", source):
        if all(editable[match.start():match.end()]):
            start = match.start() + 1
            editable[start:match.end()] = b"\0" * (match.end() - start)
    # Preserve indentation, line endings and trailing whitespace byte-for-byte.
    for match in re.finditer(r"(?m)^[ \t]+|[ \t]*(?:\r\n|\r|\n)|[ \t]+$", source):
        editable[match.start():match.end()] = b"\0" * len(match.group())

    prefix = "C"
    while f"⟦{prefix}" in source or any(
        f"⟦{prefix}" in r.value for region in front_matter for r in region.records
    ):
        prefix += "C"
    block_ends = {
        offsets[token.map[1]] for token in tokens
        if token.map and token.type in {
            "paragraph_open", "heading_open", "fence", "code_block", "tr_open",
            "front_matter", "yfm_note_open", "yfm_cut_open", "yfm_tabs_open",
        }
    }
    atoms: list[Atom] = []
    parts: list[str] = []
    boundaries: list[int] = []
    scalars: list[DecodedScalar] = []
    value_atoms: list[tuple[str, str]] = []
    scalar_spans = {
        region.start + record.start: (region, record)
        for region in front_matter for record in region.records
    }

    def value_marker(raw: str) -> str:
        marker = f"⟦{prefix}V{len(value_atoms) + 1}⟧"
        value_atoms.append((marker, raw))
        return marker

    cursor = length = 0
    while cursor < len(source):
        if cursor in scalar_spans:
            region, record = scalar_spans[cursor]
            opening, closing = value_marker(""), value_marker("")
            scalars.append(DecodedScalar(source[region.start:region.end], record, opening, closing))
            # Protect semantic whitespace and literal atoms, never YAML syntax.
            value = record.value
            pattern = re.compile(
                rf"{_URL.pattern}|{_ANCHOR.pattern}|{_MARKER.pattern}|[⟦⟧]|`+[^`]*`+|"
                r"(?<=[.!?\u3002\uff01\uff1f])\s+|[ \t]*[\r\n]+[ \t]*|^\s+|\s+$"
            )
            pieces = [opening]
            pos = 0
            local_length = len(opening)
            for match in pattern.finditer(value):
                visible = value[pos:match.start()]
                marker = value_marker(match.group())
                pieces.extend((visible, marker))
                local_length += len(visible) + len(marker)
                if match.group().isspace() and (
                    "\n\n" in match.group()
                    or value[max(0, match.start() - 1):match.start()] in ".!?\u3002\uff01\uff1f"
                ):
                    boundaries.append(length + local_length)
                pos = match.end()
            pieces.extend((value[pos:], closing))
            part = "".join(pieces)
            parts.append(part)
            length += len(part)
            boundaries.append(length)
            cursor = region.start + record.end
            continue
        end = cursor + 1
        visible = editable[cursor]
        while end < len(source) and editable[end] == visible and end not in scalar_spans:
            end += 1
        raw = source[cursor:end]
        if visible:
            part = raw
            boundaries.extend(length + m.end() for m in re.finditer(r"[.!?\u3002\uff01\uff1f](?:[ \t]+|$)", raw))
        else:
            part = f"⟦{prefix}{len(atoms) + 1}⟧"
            atoms.append(Atom(part, raw, cursor, end))
            if (any(cursor < offset <= end for offset in block_ends)
                    or (raw.isspace() and source[max(0, cursor - 1):cursor] in {".", "!", "?"})):
                boundaries.append(length + len(part))
        parts.append(part)
        length += len(part)
        cursor = end
    boundaries.append(length)
    return ProtectedDocument(source, "".join(parts), tuple(atoms), tuple(sorted(set(boundaries))),
                             tuple(issues), tuple(front_matter), tuple(scalars), tuple(value_atoms))


def restore(document: ProtectedDocument, text: str, *, expected: str | None = None) -> str:
    """Strict sequence comparison implies both exact multiplicity and order."""
    expected = document.text if expected is None else expected
    if _MARKER.findall(text) != _MARKER.findall(expected):
        raise MarkerError("Protected marker sequence differs (missing, duplicate, unknown or reordered)")
    # Detect malformed marker fragments as well, before restoring literal atoms.
    remainder = _MARKER.sub("", text)
    if "⟦" in remainder or "⟧" in remainder:
        raise MarkerError("Malformed protected marker")
    mapping = {atom.marker: atom.raw for atom in document.atoms}
    mapping.update(document.value_atoms)

    def expand(value: str) -> str:
        return _MARKER.sub(lambda match: mapping[match.group()], value)

    # Encode only complete values. Partial chunks expose decoded fragments;
    # translate_document restores the assembled responses before publication.
    for scalar in document.scalars:
        if scalar.opening not in text or scalar.closing not in text:
            continue
        start = text.index(scalar.opening)
        end = text.index(scalar.closing)
        value = expand(text[start + len(scalar.opening):end])
        try:
            encoded = encode_decoded_scalar(scalar.raw, scalar.record, value)
        except FrontMatterError as exc:
            raise FrontMatterRestoreError(str(exc), expand(text)) from exc
        # Keep encoded source literals out of the marker substitution pass.
        mapping[scalar.opening] = encoded
        text = text[:start] + scalar.opening + text[end + len(scalar.closing):]
    return expand(text)


def chunk_document(document: ProtectedDocument, fits: Callable[[str], bool]) -> tuple[Chunk, ...]:
    """Whole file first; otherwise only legal boundaries, never split an atom.

    An overlong indivisible sentence is an explicit capacity error. Inventing a
    word/character boundary or silently dropping the tail is not a translation.
    """
    if fits(document.text):
        return (Chunk(0, 0, len(document.text), document.text),)
    chunks = []
    start = 0
    while start < len(document.text):
        ends = [end for end in document.boundaries if end > start]
        # Scan forward; stop at the first over-capacity candidate. Each chosen
        # chunk is checked in its actual request; no character-size estimate.
        chosen = None
        for end in ends:
            if fits(document.text[start:end]):
                chosen = end
            else:
                break
        if chosen is None:
            raise CapacityError(f"No safe chunk fits at protected offset {start}")
        chunks.append(Chunk(len(chunks), start, chosen, document.text[start:chosen]))
        start = chosen
    return tuple(chunks)


def translation_messages(text: str, *, source_lang: str, target_lang: str,
                         path: str) -> list[dict[str, Any]]:
    template = files("ydbdoc_review.prompts").joinpath("v1/translate_document.md").read_text()
    return [
        {"role": "system", "content": template},
        {"role": "user", "content": f"Translate {source_lang} to {target_lang}. File: {path}\n\n{text}"},
    ]


def _code_issues(source: str, target: str) -> tuple[DocumentIssue, ...]:
    """Reject prose that escapes a comment/label and injects executable syntax."""
    parser = create_parser()
    before = [t for t in parser.parse(source) if t.type in {"fence", "code_block"}]
    after = [t for t in parser.parse(target) if t.type in {"fence", "code_block"}]
    if len(before) != len(after):
        return (DocumentIssue("Code block count changed"),)
    for a, b in zip(before, after, strict=True):
        try:
            if a.info.strip() == "mermaid":
                # Unsupported grammar remains wholly protected.
                same = (mermaid_skeleton(a.content) == mermaid_skeleton(b.content)
                        if mermaid_labels(a.content) else a.content == b.content)
            else:
                same = comment_skeleton(a.content, a.info) == comment_skeleton(b.content, b.info)
        except ValueError:
            same = False
        if a.info != b.info or not same:
            return (DocumentIssue("Translation changed protected code/comment or Mermaid boundaries"),)
    return ()


def translate_document(source: str, *, path: str, source_lang: str, target_lang: str,
                       client: ModelClient, choice: ModelChoice, budget: RequestBudget,
                       on_progress: Callable[[FileResult], None] | None = None) -> FileResult:
    """One primary chat per chunk; transport alone owns its permitted fallback.

    Callback receives immutable snapshots after every chunk, before later work.
    Callback errors propagate (storage failure is not successful persistence).
    """
    document = None
    try:
        document = protect(source)
        def messages(text: str) -> list[dict[str, Any]]:
            return translation_messages(text, source_lang=source_lang, target_lang=target_lang, path=path)
        chunks = chunk_document(document, lambda text: budget.fits(messages(text), expected_output=text))
    except Exception as exc:
        return FileResult(path, None, (DocumentIssue(f"{type(exc).__name__}: {exc}"),), True,
                          protected=document)
    if not source:
        return FileResult(path, "", (), False, protected=document)
    results: list[ChunkResult] = []
    issues = list(document.issues)
    for chunk in chunks:
        response = None
        text = None
        errors: tuple[DocumentIssue, ...] = ()
        unfinished = False
        try:
            answer = client.chat(messages(chunk.text), operation="translation", choice=choice,
                                 max_tokens=budget.max_output_tokens)
            response = answer.content
            if not response or not response.strip():
                raise ValueError("No usable translation returned")
            unfinished = answer.finish_reason == "length"
            text = response  # Retain damaged marker responses for the common repair loop.
            text = restore(document, response, expected=chunk.text)
            if answer.finish_reason == "length":
                unfinished = True
                errors = (DocumentIssue("Model output truncated", chunk.index),)
        except Exception as exc:
            if isinstance(exc, FrontMatterRestoreError):
                text = exc.text
                unfinished = True
            unfinished = unfinished or text is None
            errors = (DocumentIssue(f"{type(exc).__name__}: {exc}", chunk.index),)
        results.append(ChunkResult(chunk, response, text, errors, unfinished))
        issues.extend(errors)
        # Restore contiguous usable runs together. A later failed chunk must
        # not discard an already assembled scalar in an earlier run.
        assembled_parts: list[str] = []
        runs: list[list[ChunkResult]] = []
        for item in results:
            if (runs and not item.issues and item.response is not None
                    and not runs[-1][-1].issues and runs[-1][-1].response is not None):
                runs[-1].append(item)
            else:
                runs.append([item])
        assembly_failed = False
        for run in runs:
            if run[0].issues or run[0].response is None:
                if run[0].text is not None:
                    assembled_parts.append(run[0].text)
                continue
            try:
                assembled_parts.append(restore(
                    document, "".join(r.response for r in run),
                    expected="".join(r.chunk.text for r in run),
                ))
            except (MarkerError, FrontMatterRestoreError) as exc:
                assembly_failed = True
                issues.append(DocumentIssue(f"{type(exc).__name__}: {exc}"))
                assembled_parts.append(exc.text if isinstance(exc, FrontMatterRestoreError)
                                       else "".join(r.response for r in run))
        assembled = "".join(assembled_parts) if assembled_parts else None
        result = FileResult(path, assembled, tuple(issues),
                            assembly_failed or len(results) < len(chunks)
                            or any(r.unfinished for r in results), tuple(results), document)
        if on_progress:
            on_progress(result)
    if result.text is not None and not result.unfinished:
        try:
            issues.extend(_code_issues(source, result.text))
        except Exception as exc:
            issues.append(DocumentIssue(f"Document validation failed: {type(exc).__name__}: {exc}"))
    final = FileResult(path, result.text, tuple(issues), result.unfinished, tuple(results), document)
    if on_progress and final != result:
        on_progress(final)
    return final


def translate_files(sources: Iterable[tuple[str, str]], *, source_lang: str, target_lang: str,
                    client: ModelClient, choice: ModelChoice, budget: RequestBudget,
                    on_progress: Callable[[FileResult], None] | None = None) -> dict[str, FileResult]:
    """Independent source files; one failure cannot discard preceding results."""
    results = {}
    for path, source in sources:
        result = translate_document(source, path=path, source_lang=source_lang,
                                    target_lang=target_lang, client=client, choice=choice,
                                    budget=budget, on_progress=on_progress)
        results[path] = result
        if on_progress:
            on_progress(result)
    return results
