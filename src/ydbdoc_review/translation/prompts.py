"""Load and render versioned prompt templates for LLM roles."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.glossary import Glossary

DEFAULT_PROMPT_VERSION = "v1"

_TEMPLATE_NAMES = frozenset(
    {"system_common", "translate", "critic", "verify", "analyze", "en_style_guide"}
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


def _system_message(glossary: Glossary, *, version: str) -> str:
    template = load_template("system_common", version=version)
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
    user_template = load_template("translate", version=version)
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
        {"role": "system", "content": _system_message(glossary, version=version)},
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
        {"role": "system", "content": _system_message(glossary, version=version)},
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
        {"role": "system", "content": _system_message(glossary, version=version)},
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
    ]
