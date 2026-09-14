"""Production adapter binds model advice to exact candidate locations."""
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit.test_final_readonly_critic import EN_PATH, Reviewer, plan_for
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import _review_translation_candidate
from ydbdoc_review.pipeline.types import FileTranslationResult
from ydbdoc_review.translation.glossary import Glossary


def test_candidate_adapter_uses_whole_units_and_trusted_locations():
    plan = plan_for("\n" * 105 + "Источник.\n", "\n" * 103 + "Source.\n")
    fr = FileTranslationResult(file_path=EN_PATH,
        segments_count=1, verdict="ok", prompt_version="v1", final_text=plan.en.text)
    run = SimpleNamespace(deleted=False, skipped=False, file_result=fr,
        source_text=plan.ru.text, plan=SimpleNamespace(target_path=EN_PATH,
            source_path=plan.ru.path, source_lang="ru", target_lang="en"))
    def answer(messages):
        import json
        unit = json.loads(messages[-1]["content"])["units"][0]
        return json.dumps({"verdict":"blocked", "issues":[{"segment_id":unit["id"],
            "severity":"blocked", "category":"meaning", "comment":"Meaning differs",
            "suggested_text":"Advice only"}]})
    client = Reviewer(answer)
    with patch("ydbdoc_review.github.workflow.read_candidate_bytes", return_value=plan.en.text.encode()), \
         patch("ydbdoc_review.translation.critic.run_critic", side_effect=AssertionError("legacy pass")):
        _review_translation_candidate("unused", plan.candidate, SimpleNamespace(pair_results=[run]),
            client, Glossary(entries=[]), load_config())
    assert fr.verdict == "warnings"
    issue = fr.critic_unresolved.issues[0]
    assert fr.segment_lines[issue.segment_id] == (104, 104)
    assert fr.segment_locations[issue.segment_id] == EN_PATH
    assert fr.segment_excerpts[issue.segment_id] == "Source.\n"
    assert fr.segment_source_excerpts[issue.segment_id] == "Источник.\n"
    assert fr.final_text == plan.en.text
    assert not fr.critic_applied and not fr.critic_skipped
    assert len(client.calls) == 1
