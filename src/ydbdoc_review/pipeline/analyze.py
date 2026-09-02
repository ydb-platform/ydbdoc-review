"""Plan per-pair work for doc_translate (full re-translate) and doc_verify."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.structured import parse_json_model
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import DEFAULT_PROMPT_VERSION, build_analyze_messages
from ydbdoc_review.translation.schemas import AnalyzeBatchResponse, AnalyzePairResult

PairAction = Literal[
    "translate_ru_to_en_once",
    "delete_en",
    "read_only_qa",
]

BILINGUAL_SKIP_MARKER = "§6.76"
BILINGUAL_SKIP_SUMMARY = (
    "Both RU and EN changed in source PR — bilingual update, "
    f"skip auto-translate ({BILINGUAL_SKIP_MARKER})"
)

_ANALYZE_TEXT_LIMIT = 8000


@dataclass(frozen=True)
class PairContent:
    """File bodies and metadata for one RU/EN pair (filesystem-agnostic)."""

    pair: DocPair
    ru_text: str | None = None
    en_text: str | None = None
    ru_diff_vs_base: str | None = None
    en_diff_vs_base: str | None = None
    # Historical bodies retained only for read-only non-translation consumers.
    ru_base_text: str | None = None
    en_base_text: str | None = None


@dataclass(frozen=True)
class PairPlan:
    """Planned work for one pair."""

    pair: DocPair
    action: PairAction
    source_path: str
    target_path: str
    source_lang: str
    target_lang: str
    summary: str = ""


def _non_trivial(text: str | None) -> bool:
    return bool(text and text.strip())


def plan_pair_heuristic(content: PairContent) -> PairPlan:
    """Plan universal RU one-pass generation or read-only EN QA."""
    pair = content.pair
    ru_ok = _non_trivial(content.ru_text)

    if pair.ru_deleted:
        return PairPlan(
            pair=pair,
            action="delete_en",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="RU file deleted in PR — remove EN mirror",
        )

    if pair.en_changed and not pair.ru_changed:
        return PairPlan(
            pair=pair,
            action="read_only_qa",
            source_path=pair.en_path,
            target_path=pair.en_path,
            source_lang="en",
            target_lang="en",
            summary="EN-only source change receives read-only QA",
        )

    return PairPlan(
        pair=pair,
        action="translate_ru_to_en_once",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary=(
            "RU-authoritative one-pass translation"
            if ru_ok
            else "RU source missing; transaction will block"
        ),
    )


def _truncate(text: str | None, limit: int = _ANALYZE_TEXT_LIMIT) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [truncated]"


def _pair_to_analyze_payload(content: PairContent) -> dict[str, object]:
    pair = content.pair
    return {
        "ru_path": pair.ru_path,
        "en_path": pair.en_path,
        "ru_text": _truncate(content.ru_text),
        "en_text": _truncate(content.en_text),
        "ru_diff_vs_base": _truncate(content.ru_diff_vs_base, 4000),
        "en_diff_vs_base": _truncate(content.en_diff_vs_base, 4000),
    }


def _action_from_analyze(result: AnalyzePairResult) -> PairAction:
    if result.needs_generation_for == "en":
        return "translate_ru_to_en_once"
    return "read_only_qa"


def plan_from_analyze(content: PairContent, result: AnalyzePairResult) -> PairPlan:
    action = _action_from_analyze(result)
    if action == "translate_ru_to_en_once":
        src, tgt, sl, tl = content.pair.ru_path, content.pair.en_path, "ru", "en"
    else:
        src, tgt, sl, tl = content.pair.en_path, content.pair.en_path, "en", "en"
    return PairPlan(
        pair=content.pair,
        action=action,
        source_path=src,
        target_path=tgt,
        source_lang=sl,
        target_lang=tl,
        summary=result.summary,
    )


def parse_analyze_response(raw: str) -> AnalyzeBatchResponse:
    return parse_json_model(raw, AnalyzeBatchResponse)


def run_analyze_batch(
    client: YandexLLMClient,
    contents: list[PairContent],
    glossary: Glossary,
    *,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> AnalyzeBatchResponse:
    """LLM pre-analyze for ambiguous pairs (typically both sides changed)."""
    payload = [_pair_to_analyze_payload(c) for c in contents]
    messages = build_analyze_messages(payload, glossary, version=prompt_version)
    result = client.chat(messages, role="analyze")
    return parse_analyze_response(result.content)


def plan_pairs(
    contents: list[PairContent],
    client: YandexLLMClient | None = None,
    glossary: Glossary | None = None,
    *,
    use_analyze_llm: bool = False,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> list[PairPlan]:
    """Build execution plans for ``doc_translate`` (deterministic full re-translate).

    ``use_analyze_llm=True`` is deprecated and is not used in CI.
    ``run_analyze_batch`` / ``plan_from_analyze`` remain for read-only tests and
    tooling only.
    """
    del client, glossary, prompt_version  # reserved for deprecated analyze path
    if use_analyze_llm:
        raise ValueError(
            "use_analyze_llm=True is no longer supported for doc_translate; "
            "use plan_from_analyze() directly if needed"
        )
    plans = [plan_pair_heuristic(content) for content in contents]
    return sorted(plans, key=lambda p: p.pair.ru_path)
