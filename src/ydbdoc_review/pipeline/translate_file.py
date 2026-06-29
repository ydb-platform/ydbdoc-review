"""Per-file translation pipeline — delegates to ``harness`` (translate / verify profiles)."""

from __future__ import annotations

from ydbdoc_review.config.loader import Config
from ydbdoc_review.harness import (
    FileHarness,
    FileRunState,
    HarnessContext,
    TRANSLATE_PROFILE,
    VERIFY_PROFILE,
)
from ydbdoc_review.harness.critic_verdict import compute_critic_verdict
from ydbdoc_review.harness.render import finalize_en_target, render_with_translations
from ydbdoc_review.harness.render import remap_translations_by_position
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.types import FileTranslationResult
from ydbdoc_review.translation.glossary import Glossary

# Backward-compatible re-exports for tests and internal callers.
_compute_critic_verdict = compute_critic_verdict
_finalize_en_target = finalize_en_target
_render_with_translations = render_with_translations
_remap_translations_by_position = remap_translations_by_position


def translate_file(
    source_text: str,
    client: YandexLLMClient,
    glossary: Glossary | None = None,
    *,
    file_path: str = "",
    config: Config | None = None,
    source_lang: str | None = None,
    target_lang: str | None = None,
    max_chars: int | None = None,
    prompt_version: str | None = None,
    cache: dict[str, str] | None = None,
    max_parallel_batches: int | None = None,
    enable_critic: bool = True,
    enable_translate: bool = True,
    existing_target_text: str | None = None,
) -> FileTranslationResult:
    """Run the per-file harness; QA after render is identical for translate and verify."""
    ctx = HarnessContext.from_options(
        client,
        glossary=glossary,
        config=config,
        source_lang=source_lang,
        target_lang=target_lang,
        max_chars=max_chars,
        prompt_version=prompt_version,
        cache=cache,
        max_parallel_batches=max_parallel_batches,
        enable_critic=enable_critic,
    )
    profile = TRANSLATE_PROFILE if enable_translate else VERIFY_PROFILE
    state = FileRunState(
        mode=profile.name,  # type: ignore[arg-type]
        file_path=file_path,
        raw_source_text=source_text,
        source_text=source_text,
        existing_target_text=existing_target_text,
    )
    return FileHarness(profile).run(state, ctx)
