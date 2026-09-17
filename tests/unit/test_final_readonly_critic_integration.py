"""Production adapter binds model advice to exact candidate locations."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.unit.test_final_readonly_critic import EN_PATH, Reviewer, plan_for
from tests.unit.test_github_workflow import git_repo as git_repo
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import _review_translation_candidate
from ydbdoc_review.pipeline.types import FileTranslationResult
from ydbdoc_review.translation.glossary import Glossary


def test_candidate_adapter_uses_whole_units_and_trusted_locations():
    plan = plan_for("\n" * 105 + "Источник.\n", "\n" * 103 + "Source.\n")
    fr = FileTranslationResult(file_path=EN_PATH,
        segments_count=1, verdict="ok", prompt_version="v1", final_text=plan.en.text)
    run = SimpleNamespace(deleted=False, skipped=False, file_result=fr, validation_issues=[],
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


@pytest.mark.parametrize("severity", ["warning", "blocked"])
def test_pr53007_actual_candidate_is_repaired_then_reviewed_readonly(git_repo, severity):
    """Real translation/render/commit/review; only external services are scripted."""
    import json
    import subprocess
    from contextlib import ExitStack
    from pathlib import Path
    from unittest.mock import MagicMock

    from tests.unit.test_github_workflow import (
        _bind_fixture_artifact,
        _env,
        _head_sha,
        _wire_translation_publication,
    )
    from ydbdoc_review.github import workflow
    from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
    from ydbdoc_review.ops.gates import GateResult
    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.pipeline.types import PublicationImpact
    from ydbdoc_review.segmentation.extractor import extract_segments

    root = Path(git_repo)
    fixtures = Path(__file__).parents[1] / "fixtures/final-review-block-coverage"
    ru = (fixtures / "53007.ru.md").read_text()
    en = (fixtures / "53007.en.md").read_text()
    # Preserve fixture paragraphs verbatim; an inert comment gives actual lines
    # RU 106 / EN 104 after the real renderer normalizes inter-block whitespace.
    prefix = "# Service account {#ldap-service-account-auth}\n\n<!--\n" + "context\n" * 98 + "-->\n"
    source = prefix.replace("Service account", "Сервисный аккаунт") + "\n" * 3 + ru
    expected = prefix + "\n" + en
    source_path = EN_PATH.replace("/en/", "/ru/")
    for path in (source_path, EN_PATH):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("Old.\n")
    for lang in ("ru", "en"):
        ref = root / f"ydb/docs/{lang}/core/reference/configuration/auth_config.md"
        ref.parent.mkdir(parents=True)
        ref.write_text("# LDAP {#ldap-auth-config}\n")
    (root / "ydb/docs/en/core/toc_p.yaml").write_text(
        "items:\n- name: Authentication\n  href: security/authentication.md\n"
        "- name: Config\n  href: reference/configuration/auth_config.md\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fixture baseline"], cwd=root, check=True, capture_output=True)
    baseline_sha = _head_sha(git_repo)
    (root / source_path).write_text(source)
    subprocess.run(["git", "add", source_path], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "authoritative fixture source"], cwd=root, check=True, capture_output=True)
    source_sha = _head_sha(git_repo)
    pull = {"title": "docs", "head": {"ref": "feature/docs", "sha": source_sha,
        "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"}},
        "base": {"ref": "main", "sha": baseline_sha}}
    client = MagicMock()
    client.usage_tracker = UsageTracker()
    client.model_chain_for_role.side_effect = lambda role: ["translator" if role == "translate" else "reviewer"]
    translated = [s.text for s in extract_segments(parse_markdown(expected))]
    events, reviewed = [], []
    suggestion = en.replace("`bind_dn` or `bind_password`", "`bind_dn` and `bind_password`")

    repaired_expected = expected.replace(en, suggestion)
    repaired_segments = [s.text for s in extract_segments(parse_markdown(repaired_expected))]

    def chat(messages, *, role, **kwargs):
        client.usage_tracker.add(LLMUsage(kwargs["model"], 10, 5, 0, 0, True, role))
        if role == "translate":
            if reviewed:
                assert "Automatic correction of critic findings" in messages[0]["content"]
            events.append("translate")
            payload = json.loads(messages[-1]["content"].split("```json\n", 1)[1].split("```", 1)[0])
            assert len(payload["segments"]) == len(translated)
            return SimpleNamespace(content=json.dumps({"segments": [
                {"id": s["id"], "text": text} for s, text in zip(payload["segments"], repaired_segments if reviewed else translated, strict=True)]}))
        assert role == "critic"
        assert "create_pr" in events
        assert len(reviewed) < 2, "unexpected third semantic pass"
        candidate_sha = push.call_args.kwargs["source_sha"]
        blob = subprocess.check_output(["git", "cat-file", "blob", f"{candidate_sha}:{EN_PATH}"], cwd=root)
        assert blob == (repaired_expected if reviewed else expected).encode()
        units = json.loads(messages[-1]["content"])["units"]
        unit = next(unit for unit in units if unit["en_text"] == (suggestion if reviewed else en))
        assert unit["ru_text"] == ru
        assert unit["candidate_sha"] == candidate_sha
        source_blob = subprocess.check_output(["git", "cat-file", "blob", f"{source_sha}:{source_path}"], cwd=root)
        assert source_blob.decode().splitlines()[105] == ru.strip()
        assert blob.decode().splitlines()[103] == (suggestion if reviewed else en).strip()
        reviewed.append((candidate_sha, blob, unit["id"]))
        events.append("critic")
        if len(reviewed) == 2:
            return SimpleNamespace(content=json.dumps({"verdict": "ok", "issues": []}))
        return SimpleNamespace(content=json.dumps({"verdict": "blocked", "issues": [{
            "segment_id": unit["id"], "severity": severity, "category": "meaning",
            "comment": "RU requires both credentials; EN incorrectly makes them alternatives.",
            "suggested_text": suggestion}]}))

    client.chat.side_effect = chat
    def create(*args, **kwargs):
        events.append("create_pr")
        return "https://github.com/o/r/pull/99", 99, True

    with ExitStack() as stack:
        def stub(name, **kwargs):
            return stack.enter_context(patch("ydbdoc_review.github.workflow." + name, **kwargs))
        gh = stub("GitHubClient").return_value
        push = stub("push_branch")
        _wire_translation_publication(gh, push, pull)
        gh.create_pull.side_effect = create
        gh.find_open_pull_by_head.return_value = None
        gh.iter_issue_comments.return_value = iter([])
        gh.post_issue_comment.return_value = "url"
        stub("begin_ops_job", return_value=(None, GateResult(ok=True), None))
        stub("create_llm_client", return_value=client)
        stub("prepare_translation_branch_on_base")
        stub("list_pr_file_changes_git", return_value=[(source_path, "modified")])
        stub("list_pr_file_changes_api", return_value=[(source_path, "modified")])
        stub("bind_translation_artifact", side_effect=_bind_fixture_artifact)
        for forbidden in (
            "ydbdoc_review.translation.critic.apply_critic_fixes",
            "ydbdoc_review.translation.critic.run_verify",
            "ydbdoc_review.harness.steps.run_critic_loop",
            "ydbdoc_review.harness.steps.CriticFeedbackRetryStep.run",
        ):
            stack.enter_context(patch(forbidden, side_effect=AssertionError("forbidden mutable critic path")))
        config = load_config(env=_env())
        config.translation.segments_per_batch_chars = 24000
        result = workflow.run_doc_translate(repo_path=git_repo, github_repo="o/r", pr_number=7,
            merge_base_with=baseline_sha, config=config)

    assert len(reviewed) == 2 and push.call_count == 2
    assert reviewed[0][0] != reviewed[1][0]
    sha, blob, unit_id = reviewed[-1]
    assert sha == _head_sha(git_repo) and sha != source_sha
    assert (root / EN_PATH).read_bytes() == blob == repaired_expected.encode()
    assert events == ["translate", "create_pr", "critic", "translate", "critic"]
    assert result.pr_result.publication_impact == PublicationImpact.PUBLISH_NORMAL
    run = next(run for run in result.pr_result.pair_results if run.plan.target_path == EN_PATH)
    fr = run.file_result
    assert fr.verdict == "ok" and fr.critic_unresolved.verdict == "ok"
    assert fr.critic_unresolved.issues == []
    assert fr.segment_lines[unit_id] == (104, 104)
    assert fr.segment_locations[unit_id] == EN_PATH
    assert fr.segment_excerpts[unit_id] == suggestion
    assert fr.segment_source_excerpts[unit_id] == ru
    assert not fr.critic_applied and not fr.critic_skipped
    assert fr.final_text.encode() == run.target_text.encode() == blob
