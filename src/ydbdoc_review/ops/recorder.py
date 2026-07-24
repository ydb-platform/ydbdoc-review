"""Capture LLM request/response pairs for transcript storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ydbdoc_review.ops.transcripts import TranscriptStore, dump_llm_exchange


@dataclass
class LlmExchange:
    role: str
    messages: list[Any]
    content: str
    model_slug: str


@dataclass
class LlmTranscriptRecorder:
    exchanges: list[LlmExchange] = field(default_factory=list)

    def record(
        self,
        *,
        role: str | None,
        messages: list[Any],
        content: str,
        model_slug: str,
    ) -> None:
        self.exchanges.append(
            LlmExchange(
                role=role or "unknown",
                messages=list(messages),
                content=content,
                model_slug=model_slug,
            )
        )

    def flush_to_store(self, store: TranscriptStore, run_id: str) -> None:
        for i, ex in enumerate(self.exchanges, start=1):
            dump_llm_exchange(
                store,
                run_id,
                i,
                ex.role,
                {"role": ex.role, "model": ex.model_slug, "messages": ex.messages},
                {"role": ex.role, "model": ex.model_slug, "content": ex.content},
            )
