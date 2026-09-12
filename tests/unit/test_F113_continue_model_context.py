"""F-113: continue sends the complete saved context to the model."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import DocJobResult, run_doc_continue
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.ops.feedback_ctx import continue_feedback_scope
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.job_state import mark_continuable
from ydbdoc_review.ops.lifecycle import (
    OpsContext,
    compose_continue_feedback,
    load_parent_run_context,
)
from ydbdoc_review.ops.recorder import LlmTranscriptRecorder
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.pipeline.analyze import PairContent, run_analyze_batch
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translation_preflight import PreflightResult
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.translation.glossary import load_glossary

RU_PATH = "ydb/docs/ru/security.md"
EN_PATH = "ydb/docs/en/security.md"
SAVED_SCOPE_PATH = "ydb/docs/ru/previous-noop.md"


def test_F113_prompt_payload() -> None:
    """Removing any saved decision input makes the next Analyze prompt incomplete."""
    store = InMemoryTranscriptStore()
    parent_run = "parent"
    store.put(
        parent_run,
        "job/continuability.json",
        '{"fixed_shas":{"merge_base":"FIXED-S-FROM-PARENT"}}',
    )
    store.put(
        parent_run,
        "translation/v1/manifest.json",
        json.dumps(
            {
                "files": [{"retained_result": "x" * 13_000}],
                "scope": {SAVED_SCOPE_PATH: ["doc_from_diff"]},
            },
            sort_keys=True,
        ),
    )
    store.put(parent_run, "report.md", "Open question: confirm the security heading.")
    store.put(
        parent_run,
        "llm/001-analyze-req.json",
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Analyze evidence: authentication meaning changed."
                            + "x" * 2_000
                            + "LATE ANALYZE EVIDENCE FOR SAVED PAIR"
                        ),
                    }
                ]
            }
        ),
    )
    store.put(
        parent_run,
        "llm/001-analyze-resp.json",
        json.dumps(
            {
                "role": "assistant",
                "content": (
                    "Previous decision: no_translation_needed."
                    + "y" * 2_000
                    + "LATE ANALYZE DECISION FOR SAVED PAIR"
                ),
            }
        ),
    )
    for seq in range(2, 7):
        store.put(
            parent_run,
            f"llm/{seq:03d}-critic-resp.json",
            json.dumps({"role": "assistant", "content": f"Later dialogue {seq}"}),
        )

    parent_context = load_parent_run_context(
        SimpleNamespace(parent_run_id=parent_run, store=store)
    )
    feedback = compose_continue_feedback(
        "New instruction: translate the security heading.", parent_context
    )
    model = MagicMock()
    model.chat.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "results": [
                    {
                        "ru_path": RU_PATH,
                        "en_path": EN_PATH,
                        "ru_present": True,
                        "en_present": True,
                        "semantically_aligned": False,
                        "needs_generation_for": "en",
                        "summary": "The requested heading differs.",
                    }
                ]
            }
        )
    )
    current = PairContent(
        pair=DocPair(ru_path=RU_PATH, en_path=EN_PATH, ru_changed=True),
        ru_text="Текущий исходный текст.",
        en_text="CURRENT TARGET FROM NEW B",
    )

    with continue_feedback_scope(feedback):
        run_analyze_batch(model, [current], load_glossary())

    messages = model.chat.call_args.args[0]
    payload = "\n".join(str(message["content"]) for message in messages)
    for expected in (
        "New instruction: translate the security heading.",
        "FIXED-S-FROM-PARENT",
        SAVED_SCOPE_PATH,
        "Analyze evidence: authentication meaning changed.",
        "LATE ANALYZE EVIDENCE FOR SAVED PAIR",
        "Previous decision: no_translation_needed.",
        "LATE ANALYZE DECISION FOR SAVED PAIR",
        "Later dialogue 6",
        "Open question: confirm the security heading.",
        "CURRENT TARGET FROM NEW B",
    ):
        assert expected in payload


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _no_branch_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / RU_PATH).parent.mkdir(parents=True)
    (repo / EN_PATH).parent.mkdir(parents=True)
    (repo / RU_PATH).write_text("Исходный текст.\n", encoding="utf-8")
    (repo / EN_PATH).write_text("OLD TARGET BEFORE NEW B\n", encoding="utf-8")
    (repo / "ydb/docs/en/toc_p.yaml").write_text(
        "items:\n- name: Security\n  href: security.md\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    subprocess.run(["git", "switch", "-c", "feature/security"], cwd=repo, check=True)
    (repo / RU_PATH).write_text("Обновлённый исходный текст.\n", encoding="utf-8")
    subprocess.run(["git", "add", RU_PATH], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "source"], cwd=repo, check=True)
    source_sha = _git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "switch", "main"], cwd=repo, check=True)
    (repo / EN_PATH).write_text("CURRENT TARGET FROM NEW B\n", encoding="utf-8")
    subprocess.run(["git", "add", EN_PATH], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "new baseline"], cwd=repo, check=True)
    new_base_sha = _git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "switch", "feature/security"], cwd=repo, check=True)
    return repo, source_sha, new_base_sha


def _model(config) -> YandexLLMClient:
    return YandexLLMClient(
        folder_id="folder",
        api_key="key",
        llm=config.llm,
        client=MagicMock(),
    )


def test_F113_no_branch(tmp_path: Path) -> None:
    """A no-op without an artifact reruns the original pair against the new B."""
    source_pr = 113
    instruction = "Translate the previously aligned security pair."
    repo, source_sha, new_base_sha = _no_branch_repo(tmp_path)
    mark_continuable(
        repo,
        source_pr=source_pr,
        unfinished_stage="analyze",
        fixed_shas={"merge_base": new_base_sha, "head": source_sha},
        translation_pr=None,
    )
    pull = {
        "title": "docs: security",
        "body": "",
        "state": "open",
        "merged": False,
        "head": {
            "ref": "feature/security",
            "sha": source_sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": new_base_sha},
    }
    store = InMemoryTranscriptStore()
    store.put("parent", "report.md", f"no_translation_needed: {RU_PATH}")
    ops_ctx = OpsContext(
        actor="test",
        run_id="child",
        run_day="2026-09-12",
        ledger=InMemoryRunsLedger(),
        store=store,
        recorder=LlmTranscriptRecorder(),
        budget_rub=5000.0,
        parent_run_id="parent",
        mode="continue",
        repo="o/r",
        source_pr=source_pr,
        continue_feedback=instruction,
    )
    cfg = load_config(
        env={
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
        }
    )
    github = MagicMock()
    github.get_pull.return_value = pull
    github.get_branch_sha.return_value = None

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ),
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            return_value=_model(cfg),
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[(RU_PATH, "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.preflight_translation",
            return_value=PreflightResult(blockers=(), deferred_checks=()),
        ),
        patch("ydbdoc_review.github.workflow._analyzed_noop_result", return_value=None),
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=PRTranslationResult(),
        ) as translate,
    ):
        result = run_doc_continue(
            repo_path=str(repo),
            github_repo="o/r",
            pr_number=source_pr,
            merge_base_with=new_base_sha,
            dry_run=True,
            no_commit=True,
            config=cfg,
            instruction=instruction,
        )

    assert isinstance(result, DocJobResult)
    translate.assert_called_once()
    contents = translate.call_args.args[0]
    assert [(item.pair.ru_path, item.pair.en_path) for item in contents] == [
        (RU_PATH, EN_PATH)
    ]
    assert contents[0].en_text == "CURRENT TARGET FROM NEW B\n"
