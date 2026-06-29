"""Runtime dependencies for PR-level harness."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.translation.glossary import Glossary, load_glossary


@dataclass
class PRHarnessContext:
    client: YandexLLMClient
    glossary: Glossary
    config: Config
    use_analyze_llm: bool = False

    @classmethod
    def from_options(
        cls,
        client: YandexLLMClient,
        *,
        glossary: Glossary | None = None,
        config: Config | None = None,
        use_analyze_llm: bool = False,
    ) -> PRHarnessContext:
        return cls(
            client=client,
            glossary=glossary or load_glossary(),
            config=config or load_config(),
            use_analyze_llm=use_analyze_llm,
        )
