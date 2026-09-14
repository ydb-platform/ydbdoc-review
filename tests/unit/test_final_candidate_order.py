"""Real doc_translate assembly, Git commit, and final review ordering."""

from contextlib import ExitStack, nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.test_github_workflow import (
    _bind_fixture_artifact,
    _env,
    _head_sha,
    _wire_translation_publication,
)
from tests.unit.test_github_workflow import (
    git_repo as git_repo,
)
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.types import PublicationImpact
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


@pytest.mark.parametrize("scenario", [
    "publish", "dry_run", "no_commit", "drift", "mismatch",
    "early_incomplete", "early_unsafe", "late_unsafe",
])
def test_all_files_and_late_repairs_precede_critic(git_repo, scenario):
    nonpublishing = scenario if scenario in {"dry_run", "no_commit"} else None
    import subprocess
    root = Path(git_repo)
    (root / "ydb/docs/ru/b.md").write_text("Мир.\n")
    en = root / "ydb/docs/en"
    (en / "core").mkdir(parents=True)
    (en / "core/toc_p.yaml").write_text("items:\n- name: A\n  href: ../a.md\n- name: B\n  href: ../b.md\n")
    (en / "a.md").write_text("Old.\n")
    (en / "b.md").write_text("Old.\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "two pages"], cwd=root, check=True, capture_output=True)
    baseline_sha = _head_sha(git_repo)
    (root / "ydb/docs/ru/a.md").write_text("Привет новый.\n")
    (root / "ydb/docs/ru/b.md").write_text("Мир новый.\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change both"], cwd=root, check=True, capture_output=True)
    source_sha = _head_sha(git_repo)
    pull = {"title": "docs", "head": {"ref": "feature/docs", "sha": source_sha,
        "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"}},
        "base": {"ref": "main", "sha": baseline_sha}}
    changes = [("ydb/docs/ru/a.md", "modified"), ("ydb/docs/ru/b.md", "modified")]
    events = []
    reviewed = []
    published = {"exists": False}
    client = MagicMock()
    client.usage_tracker = UsageTracker()
    candidate_a = "Hello мир.\r\n" if scenario == "late_unsafe" else "Hello final.\r\n"

    def translate(segments, *_args, **kwargs):
        assert not reviewed, "translator called after final critic"
        events.append("translate:" + kwargs["file_path"])
        client.usage_tracker.add(LLMUsage("yandexgpt-5.1", 10, 5, 0, 0, True, "translate"))
        return {segment.id: "Hello." for segment in segments}

    original_late = workflow._repair_en_fragments_after_apply
    def late(*args, **kwargs):
        assert not reviewed, "late repair after critic"
        result = original_late(*args, **kwargs)
        events.append("late_repair")
        if not kwargs["dry_run"]:
            (en / "a.md").write_bytes(candidate_a.encode())
            for run in kwargs["result"].pair_results:
                if run.plan.target_path.endswith("/a.md"):
                    run.target_text = candidate_a
                    run.file_result.final_text = run.target_text
        return result

    original_orphans = workflow.apply_orphan_toc_page_checks
    def orphan_check(pr_result, **kwargs):
        orphans = original_orphans(pr_result, **kwargs)
        if scenario == "early_incomplete":
            # A deterministic scope diagnostic must survive publication.
            return [*orphans, "ydb/docs/en/b.md"]
        if scenario == "early_unsafe":
            fr = pr_result.pair_results[0].file_result
            fr.segment_alignment_error = "Unmatched source block"
            fr.heuristic_blocking.append("segment_alignment: Unmatched source block")
            fr.verdict = "blocked"
        return orphans

    def critic(*_args, **kwargs):
        assert published["exists"], "critic ran before candidate PR exists"
        assert events.count("late_repair") == 1
        assert sum(e.startswith("translate:") for e in events) == 2
        sha = push.call_args.kwargs["source_sha"]
        target = kwargs["file_path"].replace("/ru/", "/en/")
        blob = subprocess.check_output(["git", "-C", git_repo, "cat-file", "blob", f"{sha}:{target}"])
        assert kwargs["translated_text"].encode() == blob
        reviewed.append((sha, target, blob))
        events.append("critic:" + target)
        if scenario == "drift" and len(reviewed) == 1:
            subprocess.run(["git", "commit", "--allow-empty", "-m", "unrelated HEAD drift"],
                           cwd=root, check=True, capture_output=True)
        return CriticResponse(verdict="warnings", issues=[CriticIssueOut(
            segment_id="s0001", severity="warning", category="meaning",
            comment="Review wording", suggested_text="Do not apply this suggestion.")])

    def create(*_args, **_kwargs):
        events.append("create_pr")
        published["exists"] = True
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
        stub("prepare_translation_branch_on_base", side_effect=lambda *a, **k: events.append("prepare"))
        stub("list_pr_file_changes_git", return_value=changes)
        stub("list_pr_file_changes_api", return_value=changes)
        stub("bind_translation_artifact", side_effect=_bind_fixture_artifact)
        stub("_repair_en_fragments_after_apply", side_effect=late)
        stub("apply_orphan_toc_page_checks", side_effect=orphan_check)
        stack.enter_context(patch("ydbdoc_review.harness.steps.translate_segments", side_effect=translate))
        stack.enter_context(patch("ydbdoc_review.harness.steps.run_critic_pass", side_effect=critic))
        stack.enter_context(patch("ydbdoc_review.translation.critic.run_critic", side_effect=critic))
        for target in ("ydbdoc_review.harness.steps.FinalizeEnStep.run",
                       "ydbdoc_review.harness.steps._render_translated_from_source",
                       "ydbdoc_review.github.workflow.write_text",
                       "ydbdoc_review.github.workflow.git_commit_paths"):
            module, _, attr = target.rpartition(".")
            from importlib import import_module
            if module.endswith("FinalizeEnStep"):
                original = import_module("ydbdoc_review.harness.steps").FinalizeEnStep.run
            else:
                original = getattr(import_module(module), attr)
            def guarded(*args, _original=original, **kwargs):
                assert not reviewed, "content mutation after critic"
                return _original(*args, **kwargs)
            stack.enter_context(patch(target, autospec=True, side_effect=guarded))
        if scenario == "mismatch":
            original_review = workflow.review_final_candidate
            def mismatched(*args, **kwargs):
                return replace(original_review(*args, **kwargs), candidate_tree_sha="0" * 40)
            stub("review_final_candidate", side_effect=mismatched)
        with pytest.raises(ValueError, match="candidate_review_mismatch") if scenario == "mismatch" else nullcontext():
            result = workflow.run_doc_translate(repo_path=git_repo, github_repo="o/r", pr_number=7,
                merge_base_with=baseline_sha, config=load_config(env=_env()),
                **({nonpublishing: True} if nonpublishing else {}))

    if scenario == "mismatch":
        assert published["exists"]
        assert gh.update_pull_body.call_count == 0
        assert push.call_count == 1
        return

    if nonpublishing:
        assert not reviewed
        assert not published["exists"]
        assert _head_sha(git_repo) == source_sha
    else:
        assert len(reviewed) == 2
        assert result.translation_pr_number == 99
        sha = reviewed[0][0]
        assert sha != source_sha
        assert (_head_sha(git_repo) != sha) if scenario == "drift" else (_head_sha(git_repo) == sha)
        assert push.call_count == 1
        assert reviewed[0][2] == candidate_a.encode()
        assert result.pr_result.pair_results[0].file_result.critic_unresolved.issues[0].suggested_text
        assert "Do not apply" not in result.pr_result.pair_results[0].target_text
        assert events.index("late_repair") < events.index("prepare") < events.index("create_pr")
        if scenario in {"early_incomplete", "early_unsafe", "late_unsafe"}:
            assert result.pr_result.publication_impact == PublicationImpact.PUBLISH_RED
            if scenario == "early_incomplete":
                assert "ydb/docs/en/b.md" in result.pr_result.completeness_gaps
            else:
                fr = result.pr_result.pair_results[0].file_result
                assert fr.verdict == "blocked"
                assert fr.heuristic_blocking
                if scenario == "early_unsafe":
                    assert fr.segment_alignment_error == "Unmatched source block"
                else:
                    assert any(message.startswith("en_language:") for message in fr.heuristic_blocking)
