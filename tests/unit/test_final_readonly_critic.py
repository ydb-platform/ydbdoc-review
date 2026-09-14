"""Read-only semantic review exercises real batching and recovery at client.chat."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ydbdoc_review.llm.errors import LLMRequestError
from ydbdoc_review.pipeline.final_candidate import FinalCandidate
from ydbdoc_review.translation import critic
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.review_blocks import AuthoritativeDocument, prepare_review_document

EN_PATH = "ydb/docs/en/core/security/authentication.md"


def plan_for(ru="Источник.\n", en="Source.\n"):
    candidate = FinalCandidate("432f16a5749cc40dc9233545b66d8e98f5288efc", "b" * 40, (EN_PATH,), ())
    return prepare_review_document(candidate, AuthoritativeDocument(
        EN_PATH.replace("/en/", "/ru/"), "authoritative-source", ru.encode()), EN_PATH, en.encode())


class Reviewer:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def model_chain_for_role(self, role):
        assert role == "critic"
        return ["yandexgpt", "qwen"]

    def chat(self, messages, **kwargs):
        assert kwargs["role"] == "critic"
        self.calls.append((messages, kwargs))
        if isinstance(self.answer, Exception):
            raise self.answer
        answer = self.answer(messages) if callable(self.answer) else self.answer
        return SimpleNamespace(content=answer)


def run(plan, client, **kwargs):
    entry = getattr(critic, "run_readonly_semantic_critic", None)
    assert entry is not None, "missing read-only semantic entrypoint"
    return entry(client, units=plan, glossary=Glossary(entries=[]), file_path=EN_PATH, **kwargs)


@pytest.mark.parametrize("severity", ["warning", "blocked"])
def test_pr53007_whole_paragraph_yellow_and_advice_never_applied(severity, monkeypatch):
    directory = Path(__file__).parents[1] / "fixtures/final-review-block-coverage"
    ru = (directory / "53007.ru.md").read_text()
    en = (directory / "53007.en.md").read_text()
    plan = plan_for("\n" * 105 + ru, "\n" * 103 + en)
    before = repr(plan)
    suggestion = en.replace("`bind_dn` or `bind_password`", "`bind_dn` and `bind_password`")
    def answer(messages):
        payload = json.loads(messages[-1]["content"])
        unit = payload["units"][0]
        assert unit["ru_text"] == ru and unit["en_text"] == en
        return json.dumps({"verdict": "blocked", "issues": [{"segment_id": unit["id"],
            "severity": severity, "category": "meaning", "comment": "RU requires both credentials; EN makes them alternatives.",
            "suggested_text": suggestion}]})
    def forbidden(*args, **kwargs):
        pytest.fail("critic attempted mutation or another quality round")
    for name in ("apply_critic_fixes", "run_verify", "review_with_critic", "run_critic"):
        monkeypatch.setattr(critic, name, forbidden)
    client = Reviewer(answer)
    response = run(plan, client)
    assert response.verdict == "warnings"
    assert [(i.severity, i.category) for i in response.issues] == [("warning", "translation_quality")]
    assert response.issues[0].suggested_text == suggestion
    assert len(client.calls) == 1
    assert repr(plan) == before and plan.en.blocks[0].line_start == 104


def test_clean_translation_no_synthetic_issue_or_extra_call():
    plan = plan_for(en="Either credential or certificate.\n")
    client = Reviewer('{"verdict":"ok","issues":[]}')
    response = run(plan, client)
    assert response.verdict == "ok" and not response.issues
    assert len(client.calls) == 1


@pytest.mark.parametrize("answer,category", [
    ("I cannot discuss this", "critic_model_refusal"),
    ("not json", "critic_execution_failed"), ("", "critic_execution_failed"),
    (LLMRequestError("offline"), "critic_execution_failed"),
    ('{"verdict":"warnings","issues":[]}', "critic_execution_failed"),
    ('{"verdict":"blocked","issues":[{"segment_id":"unknown","severity":"blocked","category":"meaning","comment":"wrong"}]}', "critic_execution_failed"),
    ('{"verdict":"ok","issues":[{"segment_id":"unknown","suggested_text":"rewrite"}]}', "critic_execution_failed"),
])
def test_failed_or_incomplete_response_is_red(answer, category):
    plan = plan_for()
    before = repr(plan)
    client = Reviewer(answer)
    response = run(plan, client)
    assert response.verdict == "blocked" and response._review_incomplete
    assert any(i.category == category for i in response.issues)
    assert 1 <= len(client.calls) <= 4
    assert repr(plan) == before


def test_incomplete_input_never_calls_model_but_explicit_empty_is_clean():
    plan = plan_for()
    client = Reviewer('{"verdict":"ok","issues":[]}')
    invalid = replace(plan, units=(replace(plan.units[0], en_text=""),))
    assert run(invalid, client).verdict == "blocked"
    assert not client.calls
    assert run(plan_for("\n", "\n"), client).verdict == "ok"
    assert not client.calls


def test_each_whole_unit_delivered_once_across_batches():
    plan = plan_for("# Один {#one}\n\n# Два {#two}\n", "# One {#one}\n\n# Two {#two}\n")
    client = Reviewer('{"verdict":"ok","issues":[]}')
    assert run(plan, client, max_chars=1000).verdict == "ok"
    delivered = [unit["id"] for messages, _ in client.calls for unit in json.loads(messages[-1]["content"])["units"]]
    assert len(client.calls) == 2
    assert delivered == [unit.id for unit in plan.units]


def test_glossary_overhead_does_not_consume_unit_budget():
    from ydbdoc_review.translation.glossary import load_glossary
    client = Reviewer('{"verdict":"ok","issues":[]}')
    response = critic.run_readonly_semantic_critic(client, units=plan_for(),
        glossary=load_glossary(), file_path=EN_PATH, max_chars=1500)
    assert response.verdict == "ok"
    assert len(client.calls) == 1
