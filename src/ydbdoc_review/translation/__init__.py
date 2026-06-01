"""Translation pipeline: glossary, prompts, translator, critic."""

from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry, load_glossary
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_analyze_messages,
    build_critic_messages,
    build_translate_messages,
    build_verify_messages,
    load_template,
)
from ydbdoc_review.translation.translator import (
    parse_translate_response,
    translate_batch,
    translate_segments,
)

__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "Glossary",
    "GlossaryEntry",
    "build_analyze_messages",
    "build_critic_messages",
    "build_translate_messages",
    "build_verify_messages",
    "load_glossary",
    "load_template",
    "parse_translate_response",
    "translate_batch",
    "translate_segments",
]
