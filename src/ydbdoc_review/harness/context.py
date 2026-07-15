"""Runtime dependencies for harness steps (LLM, config, options)."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.translation.glossary import Glossary, load_glossary


@dataclass
class HarnessContext:
    client: YandexLLMClient
    glossary: Glossary
    config: Config
    source_lang: str
    target_lang: str
    batch_chars: int
    prompt_version: str
    parallel: int
    cache: dict[str, str] | None
    enable_critic: bool
    critic_feedback_retries: int
    usage_record_start: int
    en_toc_reachable: frozenset[str] | None = None

    @classmethod
    def from_options(
        cls,
        client: YandexLLMClient,
        *,
        glossary: Glossary | None = None,
        config: Config | None = None,
        source_lang: str | None = None,
        target_lang: str | None = None,
        max_chars: int | None = None,
        prompt_version: str | None = None,
        cache: dict[str, str] | None = None,
        max_parallel_batches: int | None = None,
        enable_critic: bool = True,
        critic_feedback_retries: int | None = None,
        usage_record_start: int | None = None,
        en_toc_reachable: frozenset[str] | None = None,
    ) -> HarnessContext:
        cfg = config or load_config()
        return cls(
            client=client,
            glossary=glossary or load_glossary(),
            config=cfg,
            source_lang=source_lang or cfg.translation.source_lang,
            target_lang=target_lang or cfg.translation.target_lang,
            batch_chars=max_chars or cfg.translation.segments_per_batch_chars,
            prompt_version=prompt_version or cfg.prompts.version,
            parallel=max_parallel_batches or cfg.llm.concurrency.batches_per_file,
            cache=cache,
            enable_critic=enable_critic,
            critic_feedback_retries=(
                critic_feedback_retries
                if critic_feedback_retries is not None
                else cfg.translation.critic_feedback_retries
            ),
            usage_record_start=(
                usage_record_start
                if usage_record_start is not None
                else len(client.usage_tracker.records)
            ),
            en_toc_reachable=en_toc_reachable,
        )
