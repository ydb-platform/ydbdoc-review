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
    "translate_to_en",
    "translate_to_ru",
    "critic_only",
    "skip",
    "delete_en",
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
    """Deterministic plan: always full re-translate from PR source language.

    ``doc_translate`` renders the target from the source AST (``translate_file``
    with ``enable_translate=True``), replacing any existing mirror text. The
    analyze LLM is not used for action selection.

    Source language: whichever side the PR authors edited. RU→EN when only RU
    changed; EN→RU when only EN changed. When **both** sides changed in the
    source PR, skip auto-translate (§6.76) — authors updated the bilingual pair.
    """
    pair = content.pair
    ru_ok = _non_trivial(content.ru_text)
    en_ok = _non_trivial(content.en_text)

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

    if pair.ru_changed and pair.en_changed:
        return PairPlan(
            pair=pair,
            action="skip",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary=BILINGUAL_SKIP_SUMMARY,
        )

    if not ru_ok and not en_ok:
        if pair.en_changed and not pair.ru_changed:
            return PairPlan(
                pair=pair,
                action="translate_to_ru",
                source_path=pair.en_path,
                target_path=pair.ru_path,
                source_lang="en",
                target_lang="ru",
                summary="EN changed but source text missing",
            )
        if pair.ru_changed:
            return PairPlan(
                pair=pair,
                action="translate_to_en",
                source_path=pair.ru_path,
                target_path=pair.en_path,
                source_lang="ru",
                target_lang="en",
                summary="RU changed but source text missing",
            )
        return PairPlan(
            pair=pair,
            action="skip",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="Both sides empty",
        )

    if not pair.ru_changed and not pair.en_changed:
        return PairPlan(
            pair=pair,
            action="skip",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="No changes on either side",
        )

    if pair.en_changed and not pair.ru_changed:
        return PairPlan(
            pair=pair,
            action="translate_to_ru",
            source_path=pair.en_path,
            target_path=pair.ru_path,
            source_lang="en",
            target_lang="ru",
            summary="EN changed — full re-translate to RU (ignore existing RU)",
        )

    if ru_ok:
        return PairPlan(
            pair=pair,
            action="translate_to_en",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="RU changed — full re-translate to EN",
        )

    if en_ok:
        return PairPlan(
            pair=pair,
            action="translate_to_ru",
            source_path=pair.en_path,
            target_path=pair.ru_path,
            source_lang="en",
            target_lang="ru",
            summary="EN changed — full re-translate to RU (RU text missing)",
        )

    return PairPlan(
        pair=pair,
        action="skip",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="No translatable source text",
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
        return "translate_to_en"
    if result.needs_generation_for == "ru":
        return "translate_to_ru"
    if result.ru_present and result.en_present and result.semantically_aligned:
        return "critic_only"
    return "skip"


def plan_from_analyze(content: PairContent, result: AnalyzePairResult) -> PairPlan:
    action = _action_from_analyze(result)
    if action == "translate_to_en":
        src, tgt, sl, tl = content.pair.ru_path, content.pair.en_path, "ru", "en"
    elif action == "translate_to_ru":
        src, tgt, sl, tl = content.pair.en_path, content.pair.ru_path, "en", "ru"
    else:
        src, tgt, sl, tl = content.pair.ru_path, content.pair.en_path, "ru", "en"
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

    ``use_analyze_llm=True`` is deprecated: it may yield ``critic_only`` (no full
    render) and is not used in CI. ``run_analyze_batch`` / ``plan_from_analyze``
    remain for tests and tooling only.
    """
    del client, glossary, prompt_version  # reserved for deprecated analyze path
    if use_analyze_llm:
        raise ValueError(
            "use_analyze_llm=True is no longer supported for doc_translate; "
            "use plan_from_analyze() directly if needed"
        )
    plans = [plan_pair_heuristic(content) for content in contents]
    return sorted(plans, key=lambda p: p.pair.ru_path)
