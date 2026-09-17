"""Small external-service doubles, not an implementation of the product contract."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ModelReply:
    text: str | None
    cost_rub: Decimal | None  # None means unknown, never an invented zero.
    error: Exception | None = None


class FakeModel:
    def __init__(self, trace: list, replies: list[ModelReply]):
        self.trace = trace
        self.replies = deque(replies)

    def call(self, *, role: str, provider: str, messages: list, round: int, chunk: int):
        if not self.replies:
            raise AssertionError("Unexpected model call: scripted responses exhausted")
        reply = self.replies.popleft()
        self.trace.append({"event": "model.call", "role": role, "provider": provider,
                           "round": round, "chunk": chunk, "messages": deepcopy(messages),
                           "response": reply.text, "cost_rub": reply.cost_rub,
                           "error": type(reply.error).__name__ if reply.error else None})
        if reply.error:
            raise reply.error
        return reply


class FakeGitHub:
    def __init__(self, trace: list):
        self.trace = trace
        self.pulls: dict[int, dict] = {}
        self.comments: list[dict] = []
        self.error: Exception | None = None

    def create_pull(self, *, head: str, base: str, draft: bool) -> int:
        self.trace.append({"event": "github.create_pull", "head": head,
                           "base": base, "draft": draft})
        if self.error:
            raise self.error
        number = max(self.pulls, default=0) + 1
        self.pulls[number] = {"head": head, "base": base, "draft": draft}
        return number

    def comment(self, *, pr: int, body: str):
        self.trace.append({"event": "github.comment", "pr": pr, "body": body})
        if self.error:
            raise self.error
        self.comments.append({"pr": pr, "body": body})


class FakeStore:
    """Raw storage only: TTL, admission, counters and accounting belong to production."""
    def __init__(self, trace: list):
        self.trace = trace
        self.tables: dict[str, dict[str, Any]] = {"runs": {}, "run_objects": {}}
        self.error: Exception | None = None

    def put(self, table: str, key: str, value: Any):
        self.trace.append({"event": "store.put", "table": table, "key": key,
                           "value": deepcopy(value)})
        if self.error:
            raise self.error
        self.tables[table][key] = deepcopy(value)

    def get(self, table: str, key: str):
        self.trace.append({"event": "store.get", "table": table, "key": key})
        if self.error:
            raise self.error
        return deepcopy(self.tables[table].get(key))


class FakeBuild:
    """External build response bound to the requested SHA; no syntax validation here."""
    def __init__(self, trace: list, status: str = "success"):
        self.trace = trace
        self.status = status

    def check(self, sha: str):
        self.trace.append({"event": "build.check", "sha": sha, "status": self.status})
        return {"sha": sha, "status": self.status}
