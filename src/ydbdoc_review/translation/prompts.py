"""Load and render versioned prompt templates for LLM roles."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.placeholder_align import segment_atom_legend
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.file_profiles import GLOSSARY_PROFILE, detect_file_profile
from ydbdoc_review.translation.glossary import Glossary

DEFAULT_PROMPT_VERSION = "v1"

_TEMPLATE_NAMES = frozenset(
    {
        "system_common",
        "system_glossary",
        "translate",
        "translate_glossary",
        "critic",
        "critic_batch",
        "critic_glossary_batch",
        "verify",
        "verify_batch",
        "analyze",
        "en_style_guide",
        "repair",
        "critic_feedback_repair",
    }
)


def load_template(name: str, *, version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Load a packaged markdown template (without ``.md`` suffix)."""
    if name not in _TEMPLATE_NAMES:
        raise ValueError(f"unknown prompt template: {name!r}")
    pkg = resources.files("ydbdoc_review.prompts") / version
    path = pkg / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"prompt template {version}/{name}.md not found"
        ) from exc


def render_template(template: str, variables: dict[str, str]) -> str:
    """Replace ``{key}`` placeholders; leaves unknown braces untouched."""
    out = template
    for key, value in variables.items():
        out = out.replace("{" + key + "}", value)
    return out


def segments_to_batch_json(segments: list[Segment]) -> str:
    """Serialize segments for the translator prompt."""
    payload = {
        "segments": [
            {
                "id": seg.id,
                "kind": seg.kind.value,
                "path": seg.path,
                "text": seg.text,
            }
            for seg in segments
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def segments_to_index_json(segments: list[Segment]) -> str:
    """Compact segment list for critic/verify (id, kind, path only)."""
    payload = {
        "segments": [
            {"id": seg.id, "kind": seg.kind.value, "path": seg.path}
            for seg in segments
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _style_guide_block(*, target_lang: str, version: str) -> str:
    if target_lang.lower() not in {"en", "english"}:
        return ""
    return load_template("en_style_guide", version=version)


def _system_template_name(file_path: str) -> str:
    if detect_file_profile(file_path) == GLOSSARY_PROFILE:
        return "system_glossary"
    return "system_common"


def _system_message(
    glossary: Glossary,
    *,
    version: str,
    file_path: str = "",
) -> str:
    template = load_template(_system_template_name(file_path), version=version)
    return render_template(template, {"glossary_yaml": glossary.to_prompt_yaml()})


def build_translate_messages(
    batch: Batch,
    glossary: Glossary,
    *,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for one translation batch."""
    translate_template = (
        "translate_glossary"
        if detect_file_profile(file_path) == GLOSSARY_PROFILE
        else "translate"
    )
    user_template = load_template(translate_template, version=version)
    user_content = render_template(
        user_template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "batch_json": segments_to_batch_json(batch.segments),
            "style_guide_block": _style_guide_block(
                target_lang=target_lang, version=version
            ),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def build_repair_messages(
    segment: Segment,
    glossary: Glossary,
    *,
    validation_error: str,
    failed_attempt: str,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for a single-segment repair after validation failure."""
    user_template = load_template("repair", version=version)
    path_label = " › ".join(segment.path) if segment.path else "(document root)"
    user_content = render_template(
        user_template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "segment_id": segment.id,
            "segment_kind": segment.kind.value,
            "segment_path": path_label,
            "validation_error": validation_error,
            "source_text": segment.text,
            "failed_attempt": failed_attempt or "(none)",
            "style_guide_block": _style_guide_block(
                target_lang=target_lang, version=version
            ),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def build_critic_feedback_repair_messages(
    segment: Segment,
    glossary: Glossary,
    *,
    current_translation: str,
    critic_issues: list,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for critic-guided segment re-translation."""
    user_template = load_template("critic_feedback_repair", version=version)
    path_label = " › ".join(segment.path) if segment.path else "(document root)"
    issues_payload = [
        issue.model_dump() if hasattr(issue, "model_dump") else issue
        for issue in critic_issues
    ]
    user_content = render_template(
        user_template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "segment_id": segment.id,
            "segment_kind": segment.kind.value,
            "segment_path": path_label,
            "source_text": segment.text,
            "current_translation": current_translation,
            "critic_issues_json": json.dumps(
                issues_payload, ensure_ascii=False, indent=2
            ),
            "style_guide_block": _style_guide_block(
                target_lang=target_lang, version=version
            ),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def segments_to_critic_batch_json(
    segments: list[Segment],
    translations: dict[str, str],
    *,
    include_atom_map: bool = True,
) -> str:
    """Segment source/target pairs for batched critic or verify."""
    items: list[dict[str, object]] = []
    for seg in segments:
        entry: dict[str, object] = {
            "id": seg.id,
            "kind": seg.kind.value,
            "path": seg.path,
            "source_text": seg.text,
            "translated_text": translations.get(seg.id, seg.text),
        }
        if include_atom_map and seg.placeholders:
            entry["atom_map"] = segment_atom_legend(seg)
        items.append(entry)
    payload = {"segments": items}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_critic_batch_messages(
    batch: Batch,
    translations: dict[str, str],
    glossary: Glossary,
    *,
    file_path: str,
    batch_count: int,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for one critic batch."""
    critic_template = (
        "critic_glossary_batch"
        if detect_file_profile(file_path) == GLOSSARY_PROFILE
        else "critic_batch"
    )
    template = load_template(critic_template, version=version)
    user_content = render_template(
        template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "batch_index": str(batch.index + 1),
            "batch_count": str(batch_count),
            "batch_json": segments_to_critic_batch_json(batch.segments, translations),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def build_verify_batch_messages(
    batch: Batch,
    translations: dict[str, str],
    prior_issues: list[dict[str, Any]],
    glossary: Glossary,
    *,
    file_path: str,
    batch_count: int,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for one verify batch."""
    template = load_template("verify_batch", version=version)
    user_content = render_template(
        template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "batch_index": str(batch.index + 1),
            "batch_count": str(batch_count),
            "batch_json": segments_to_critic_batch_json(batch.segments, translations),
            "prior_issues_json": json.dumps(
                prior_issues, ensure_ascii=False, indent=2
            ),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def build_critic_messages(
    *,
    source_text: str,
    translated_text: str,
    segments: list[Segment],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for the per-file critic pass."""
    template = load_template("critic", version=version)
    user_content = render_template(
        template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "source_text": source_text,
            "translated_text": translated_text,
            "segments_index_json": segments_to_index_json(segments),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def build_verify_messages(
    *,
    source_text: str,
    translated_text: str,
    segments: list[Segment],
    prior_issues: list[dict[str, Any]],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for the second (verify) critic pass."""
    template = load_template("verify", version=version)
    user_content = render_template(
        template,
        {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "file_path": file_path,
            "source_text": source_text,
            "translated_text": translated_text,
            "prior_issues_json": json.dumps(
                prior_issues, ensure_ascii=False, indent=2
            ),
            "segments_index_json": segments_to_index_json(segments),
        },
    )
    return [
        {
            "role": "system",
            "content": _system_message(
                glossary, version=version, file_path=file_path
            ),
        },
        {"role": "user", "content": user_content},
    ]


def build_analyze_messages(
    pairs: list[dict[str, Any]],
    glossary: Glossary,
    *,
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[ChatCompletionMessageParam]:
    """Chat messages for PR pre-analyze (translation pair review)."""
    template = load_template("analyze", version=version)
    user_content = render_template(
        template,
        {"pairs_json": json.dumps({"pairs": pairs}, ensure_ascii=False, indent=2)},
    )
    return [
        {"role": "system", "content": _system_message(glossary, version=version)},
        {"role": "user", "content": user_content},
    ]  # analyze is PR-level; no file_path
