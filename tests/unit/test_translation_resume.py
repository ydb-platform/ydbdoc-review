"""Exact durable translation resume contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.github.workflow import (
    _resolve_translation_resume_parent,
    _translation_checkpoint_fingerprint,
    run_doc_translate,
)
from ydbdoc_review.harness.context import HarnessContext, checkpoint_scope
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.ops.translation_checkpoint import (
    CheckpointIdentity,
    CheckpointWriter,
    load_verified_unit,
    translation_unit_key_for_segment,
)
from ydbdoc_review.parsing.ast_types import InlineCode
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translation_preflight import PreflightResult
from ydbdoc_review.pipeline.types import (
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind
from ydbdoc_review.translation.glossary import Glossary, load_glossary
from ydbdoc_review.translation.translator import translate_segments


class _DiskTranscriptStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: str, object_key: str) -> Path:
        return self.root / run_id / object_key

    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        path = self._path(run_id, object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

    def get(self, run_id: str, object_key: str) -> bytes | None:
        path = self._path(run_id, object_key)
        return path.read_bytes() if path.is_file() else None

    def exists_run(self, run_id: str) -> bool:
        return (self.root / run_id).is_dir()

    def list_keys(self, run_id: str) -> list[str]:
        base = self.root / run_id
        if not base.is_dir():
            return []
        return sorted(str(path.relative_to(base)) for path in base.rglob("*") if path.is_file())


def _identity() -> CheckpointIdentity:
    return CheckpointIdentity(
        RuAuthority(
            source_repo="ydb-platform/ydb",
            source_pr=51079,
            source_base_sha="1" * 40,
            source_head_sha="2" * 40,
            baseline_sha="3" * 40,
            ru_sha="2" * 40,
            mode=RuAuthorityMode.SOURCE_PRESERVING,
        ),
        "4" * 64,
    )


def _segment(segment_id: str, text: str) -> Segment:
    return Segment(
        id=segment_id,
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text=text,
        placeholders=[],
        ast_path=[int(segment_id[-1])],
    )


def _client(responses: list[str]) -> YandexLLMClient:
    completions = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        for response in responses
    ]
    openai = MagicMock()
    openai.chat.completions.create.side_effect = completions
    config = load_config(env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"})
    return YandexLLMClient(
        folder_id="folder",
        api_key="key",
        llm=config.llm,
        client=openai,
    )


def _workflow_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo,
        check=True,
    )
    ru = repo / "ydb" / "docs" / "ru"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "source"], cwd=repo, check=True)
    return str(repo)


def test_recreated_loader_reads_exact_completed_bytes(tmp_path: Path) -> None:
    identity = _identity()
    unit_key = "5" * 64
    source = b"source"
    store = _DiskTranscriptStore(tmp_path / "store")
    CheckpointWriter(store, "run-a", identity).save_unit(
        unit_key,
        source,
        b"target",
        validated=True,
    )

    loaded = load_verified_unit(store, "run-a", identity, unit_key, source)
    assert loaded is not None
    assert loaded.source == source
    assert loaded.target == b"target"

    script = """
import sys
from pathlib import Path
from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.ops.translation_checkpoint import CheckpointIdentity, load_verified_unit

class DiskStore:
    def __init__(self, root): self.root = Path(root)
    def _path(self, run_id, key): return self.root / run_id / key
    def put(self, run_id, key, data): raise AssertionError("read-only child")
    def get(self, run_id, key):
        path = self._path(run_id, key)
        return path.read_bytes() if path.is_file() else None
    def exists_run(self, run_id): return (self.root / run_id).is_dir()
    def list_keys(self, run_id):
        base = self.root / run_id
        return sorted(str(path.relative_to(base)) for path in base.rglob("*") if path.is_file())

identity = CheckpointIdentity(
    RuAuthority("ydb-platform/ydb", 51079, "1" * 40, "2" * 40, "3" * 40, "2" * 40, RuAuthorityMode.SOURCE_PRESERVING),
    "4" * 64,
)
loaded = load_verified_unit(DiskStore(sys.argv[1]), "run-a", identity, "5" * 64, b"source")
assert loaded is not None
sys.stdout.buffer.write(loaded.target)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script, str(store.root)],
        check=False,
        capture_output=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stdout == b"target"


def test_identity_drift_is_cache_miss() -> None:
    identity = _identity()
    store = InMemoryTranscriptStore()
    unit_key = "5" * 64
    source = b"source"
    CheckpointWriter(store, "run-a", identity).save_unit(
        unit_key,
        source,
        b"target",
        validated=True,
    )

    changed = replace(identity, translation_fingerprint="f" * 64)
    assert load_verified_unit(store, "run-a", changed, unit_key, source) is None


@pytest.mark.parametrize(
    "identity_change",
    [
        {"source_repo": "other/ydb"},
        {"source_pr": 51080},
        {"source_base_sha": "a" * 40},
        {"source_head_sha": "b" * 40},
        {"baseline_sha": "c" * 40},
        {"ru_sha": "d" * 40},
        {"mode": RuAuthorityMode.CURRENT},
    ],
)
def test_each_authority_identity_drift_is_cache_miss(
    identity_change: dict[str, object],
) -> None:
    identity = _identity()
    store = InMemoryTranscriptStore()
    unit_key = "5" * 64
    CheckpointWriter(store, "run-a", identity).save_unit(
        unit_key,
        b"source",
        b"target",
        validated=True,
    )
    changed = replace(
        identity,
        authority=replace(identity.authority, **identity_change),
    )

    assert load_verified_unit(store, "run-a", changed, unit_key, b"source") is None


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("schema", 2),
        ("validated", 1),
        ("validated", "true"),
        ("validated", False),
        ("unit_key", "6" * 64),
        ("object_key", "translation/v1/objects/" + "0" * 64),
    ],
)
def test_receipt_schema_and_object_corruption_are_cache_misses(
    mutation: str,
    value: object,
) -> None:
    identity = _identity()
    store = InMemoryTranscriptStore()
    unit_key = "5" * 64
    CheckpointWriter(store, "run-a", identity).save_unit(
        unit_key,
        b"source",
        b"target",
        validated=True,
    )
    receipt_key = f"translation/v1/units/{unit_key}.json"
    payload = json.loads(store.get("run-a", receipt_key) or b"{}")
    payload[mutation] = value
    store.put("run-a", receipt_key, json.dumps(payload))

    assert load_verified_unit(store, "run-a", identity, unit_key, b"source") is None


def test_missing_malformed_and_hash_inconsistent_checkpoint_data_are_misses() -> None:
    identity = _identity()
    unit_key = "5" * 64
    receipt_key = f"translation/v1/units/{unit_key}.json"
    store = InMemoryTranscriptStore()

    assert load_verified_unit(store, "run-a", identity, unit_key, b"source") is None
    store.put("run-a", receipt_key, b"\xff")
    assert load_verified_unit(store, "run-a", identity, unit_key, b"source") is None

    receipt = CheckpointWriter(store, "run-b", identity).save_unit(
        unit_key,
        b"source",
        b"target",
        validated=True,
    )
    assert load_verified_unit(store, "run-b", identity, unit_key, b"changed") is None
    store.put("run-b", receipt.object_key, b"corrupt target")
    assert load_verified_unit(store, "run-b", identity, unit_key, b"source") is None

    missing_hash = "0" * 64
    payload = json.loads(store.get("run-b", receipt_key) or b"{}")
    payload["target_hash"] = missing_hash
    payload["object_key"] = f"translation/v1/objects/{missing_hash}"
    store.put("run-b", receipt_key, json.dumps(payload))
    assert load_verified_unit(store, "run-b", identity, unit_key, b"source") is None


def test_all_exact_resume_avoids_repeated_translation_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segments = [_segment("s1", "Source one"), _segment("s2", "Source two")]
    retained: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "ydbdoc_review.translation.translator.translate_batch",
        lambda *_args, **_kwargs: pytest.fail("exact resumed units reached the model"),
    )
    result = translate_segments(
        segments,
        object(),  # type: ignore[arg-type]
        load_glossary(),
        file_path="ydb/docs/ru/a.md",
        load_validated_segment=lambda segment: f"Target {segment.id}",
        on_validated_segment=lambda segment, target: retained.append((segment.id, target)),
    )

    assert result == {"s1": "Target s1", "s2": "Target s2"}
    assert retained == [("s1", "Target s1"), ("s2", "Target s2")]


def test_partial_resume_dispatches_only_missing_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segments = [
        _segment("s1", "Source one"),
        _segment("s2", "Source two"),
        _segment("s3", "Source three"),
    ]
    dispatched: list[tuple[str, ...]] = []
    retained: list[str] = []

    def translate_batch(_client: Any, batch: Any, _glossary: Any, **_kwargs: Any):
        ids = tuple(segment.id for segment in batch.segments)
        dispatched.append(ids)
        return {segment.id: f"Fresh {segment.id}" for segment in batch.segments}

    monkeypatch.setattr(
        "ydbdoc_review.translation.translator.translate_batch",
        translate_batch,
    )
    result = translate_segments(
        segments,
        object(),  # type: ignore[arg-type]
        load_glossary(),
        file_path="ydb/docs/ru/a.md",
        load_validated_segment=lambda segment: (
            f"Retained {segment.id}" if segment.id in {"s1", "s2"} else None
        ),
        on_validated_segment=lambda segment, _target: retained.append(segment.id),
    )

    assert result == {
        "s1": "Retained s1",
        "s2": "Retained s2",
        "s3": "Fresh s3",
    }
    assert dispatched == [("s3",)]
    assert retained == ["s1", "s2", "s3"]


def test_resume_revalidates_loaded_translation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    segment = _segment("s1", "Русский исходник")
    dispatched: list[str] = []

    def translate_batch(_client: Any, batch: Any, _glossary: Any, **_kwargs: Any):
        dispatched.extend(item.id for item in batch.segments)
        return {"s1": "Fresh English target"}

    monkeypatch.setattr(
        "ydbdoc_review.translation.translator.translate_batch",
        translate_batch,
    )
    result = translate_segments(
        [segment],
        object(),  # type: ignore[arg-type]
        load_glossary(),
        file_path="ydb/docs/ru/a.md",
        load_validated_segment=lambda _segment: "Невалидный сохранённый текст",
    )

    assert result == {"s1": "Fresh English target"}
    assert dispatched == ["s1"]
    assert "rejected by current validation" in caplog.text


def test_runtime_resume_reloads_and_retains_current_receipt() -> None:
    store = InMemoryTranscriptStore()
    parent = CheckpointWriter(store, "run-a", _identity())
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    contents = [PairContent(pair=pair, ru_text="Привет.\n")]
    response = json.dumps(
        {"segments": [{"id": "s0001", "text": "Hello."}]},
        ensure_ascii=False,
    )
    first = run_pr_translation(contents, _client([response]), checkpoint=parent)
    assert first.pair_results[0].target_text == "Hello.\n"

    current = CheckpointWriter(store, "run-b", _identity())
    resumed_client = _client([])
    second = run_pr_translation(
        contents,
        resumed_client,
        checkpoint=current,
        resume_parent_run_id="run-a",
    )

    assert second.pair_results[0].target_text == "Hello.\n"
    assert resumed_client.usage_tracker.records == []
    assert (
        len([key for key in store.list_keys("run-b") if key.startswith("translation/v1/units/")])
        == 1
    )


def _record(
    run_id: str,
    *,
    repo: str = "ydb-platform/ydb",
    status: str = "failed",
    hour: int,
) -> RunRecord:
    return RunRecord(
        run_day="2026-09-07",
        run_id=run_id,
        actor="u",
        mode="translate",
        repo=repo,
        source_pr=51079,
        status=status,
        started_at=datetime(2026, 9, 7, hour, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 7, hour, 1, tzinfo=UTC),
    )


def _ops_context(
    ledger: InMemoryRunsLedger,
    store: InMemoryTranscriptStore,
) -> SimpleNamespace:
    return SimpleNamespace(
        ledger=ledger,
        store=store,
        run_id="current",
        repo="ydb-platform/ydb",
        source_pr=51079,
        parent_run_id=None,
    )


def test_latest_eligible_failed_run_selection() -> None:
    store = InMemoryTranscriptStore()
    identity = _identity()
    eligible = CheckpointWriter(store, "older-eligible", identity)
    eligible.save_unit("6" * 64, b"source", b"target", validated=True)
    corrupt = CheckpointWriter(store, "newer-corrupt", identity)
    receipt = corrupt.save_unit("7" * 64, b"source", b"target", validated=True)
    store.put("newer-corrupt", receipt.object_key, b"corrupt")
    ledger = InMemoryRunsLedger()
    ledger.records = [
        _record("older-eligible", hour=9),
        _record("newer-corrupt", hour=10),
        _record("newer-denied", status="denied_quota", hour=11),
        _record("foreign", repo="other/ydb", status="ok", hour=12),
    ]
    current = CheckpointWriter(store, "current", identity)

    selected = _resolve_translation_resume_parent(
        ops_ctx=_ops_context(ledger, store),
        checkpoint=current,
        explicit_parent_run_id=None,
    )

    assert selected == "older-eligible"


def test_explicit_invalid_parent_never_falls_back() -> None:
    store = InMemoryTranscriptStore()
    identity = _identity()
    CheckpointWriter(store, "older-eligible", identity).save_unit(
        "6" * 64,
        b"source",
        b"target",
        validated=True,
    )
    store.put("explicit-corrupt", "manifest.json", b"{}")
    ledger = InMemoryRunsLedger()
    ledger.records = [
        _record("older-eligible", hour=9),
        _record("explicit-corrupt", hour=10),
    ]
    current = CheckpointWriter(store, "current", identity)

    selected = _resolve_translation_resume_parent(
        ops_ctx=_ops_context(ledger, store),
        checkpoint=current,
        explicit_parent_run_id="explicit-corrupt",
    )

    assert selected is None


def test_workflow_propagates_selected_resume_parent(tmp_path: Path) -> None:
    git_repo = _workflow_repo(tmp_path)
    sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"],
        text=True,
    ).strip()
    config = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )
    identity = CheckpointIdentity(
        RuAuthority(
            source_repo="o/r",
            source_pr=51079,
            source_base_sha=sha,
            source_head_sha=sha,
            baseline_sha=sha,
            ru_sha=sha,
            mode=RuAuthorityMode.CURRENT,
        ),
        "4" * 64,
    )
    store = InMemoryTranscriptStore()
    CheckpointWriter(store, "parent", identity).save_unit(
        "5" * 64,
        b"source",
        b"target",
        validated=True,
    )
    current = CheckpointWriter(store, "current", identity)
    ledger = InMemoryRunsLedger()
    ledger.records = [_record("parent", repo="o/r", status="failed", hour=9)]
    ops_ctx = SimpleNamespace(
        ledger=ledger,
        store=store,
        run_id="current",
        repo="o/r",
        source_pr=51079,
        parent_run_id="parent",
        continue_feedback=None,
        recorder=None,
    )
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                source_text="Привет.\n",
                target_text="Hello.\n",
            )
        ]
    )
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": sha},
    }

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as github,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.finish_ops_job"),
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            return_value=_client([]),
        ),
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=result,
        ) as translate,
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
    ):
        github.return_value.get_pull.return_value = pull
        run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=51079,
            merge_base_with="HEAD",
            dry_run=True,
            config=config,
            parent_run_id="parent",
            checkpoint=current,
        )

    assert translate.call_args.kwargs["resume_parent_run_id"] == "parent"


def test_parent_discovery_never_combines_multiple_runs() -> None:
    source_text = "Первый.\n\nВторой.\n\nТретий.\n"
    segments = extract_segments(parse_markdown(source_text))
    assert len(segments) == 3
    store = InMemoryTranscriptStore()
    identity = _identity()
    selected = CheckpointWriter(store, "selected", identity)
    other = CheckpointWriter(store, "other", identity)
    for segment, target in zip(
        segments[:2],
        ("First.", "Second."),
        strict=True,
    ):
        selected.save_unit(
            translation_unit_key_for_segment(
                segment,
                source_path="ydb/docs/ru/a.md",
                target_locale="en",
            ),
            segment.text.encode("utf-8"),
            target.encode("utf-8"),
            validated=True,
        )
    missing = segments[2]
    other.save_unit(
        translation_unit_key_for_segment(
            missing,
            source_path="ydb/docs/ru/a.md",
            target_locale="en",
        ),
        missing.text.encode("utf-8"),
        b"Stale third from a different parent.",
        validated=True,
    )
    response = json.dumps(
        {"segments": [{"id": missing.id, "text": "Fresh third."}]},
        ensure_ascii=False,
    )
    client = _client([response])
    current = CheckpointWriter(store, "current", identity)
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )

    result = run_pr_translation(
        [PairContent(pair=pair, ru_text=source_text)],
        client,
        checkpoint=current,
        resume_parent_run_id="selected",
    )

    assert result.pair_results[0].target_text == "First.\n\nSecond.\n\nFresh third.\n"
    assert len(client.usage_tracker.records) == 1
    assert (
        len([key for key in store.list_keys("current") if key.startswith("translation/v1/units/")])
        == 3
    )


def test_resume_does_not_admit_denied_continue(tmp_path: Path) -> None:
    config = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )
    store = InMemoryTranscriptStore()
    checkpoint = CheckpointWriter(store, "current", _identity())
    CheckpointWriter(store, "perfect-parent", _identity()).save_unit(
        "5" * 64,
        b"source",
        b"target",
        validated=True,
    )
    create_client = MagicMock()
    prepare = MagicMock()

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient"),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(
                None,
                GateResult(ok=False, reason="denied", status="denied_acl"),
                "denied",
            ),
        ),
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            create_client,
        ),
        patch(
            "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
            prepare,
        ),
    ):
        result = run_doc_translate(
            repo_path=str(tmp_path),
            github_repo="ydb-platform/ydb",
            pr_number=51079,
            dry_run=True,
            config=config,
            ops_mode="continue",
            parent_run_id="perfect-parent",
            checkpoint=checkpoint,
        )

    assert result.mode == "doc_continue"
    create_client.assert_not_called()
    prepare.assert_not_called()


def test_zero_call_resume_runs_final_gates_and_lease_check(tmp_path: Path) -> None:
    git_repo = _workflow_repo(tmp_path)
    sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"],
        text=True,
    ).strip()
    config = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )
    identity = CheckpointIdentity(
        RuAuthority(
            source_repo="o/r",
            source_pr=51079,
            source_base_sha=sha,
            source_head_sha=sha,
            baseline_sha=sha,
            ru_sha=sha,
            mode=RuAuthorityMode.CURRENT,
        ),
        "4" * 64,
    )
    source_text = "Привет.\n"
    segment = extract_segments(parse_markdown(source_text))[0]
    store = InMemoryTranscriptStore()
    parent = CheckpointWriter(store, "parent", identity)
    parent.save_unit(
        translation_unit_key_for_segment(
            segment,
            source_path="ydb/docs/ru/a.md",
            target_locale="en",
        ),
        segment.text.encode("utf-8"),
        b"Hello.",
        validated=True,
    )
    current = CheckpointWriter(store, "current", identity)
    ledger = InMemoryRunsLedger()
    ledger.records = [_record("parent", repo="o/r", status="failed", hour=9)]
    ops_ctx = SimpleNamespace(
        ledger=ledger,
        store=store,
        run_id="current",
        repo="o/r",
        source_pr=51079,
        parent_run_id="parent",
        continue_feedback=None,
        recorder=MagicMock(),
    )
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": sha},
    }
    client = _client([])
    prepare = MagicMock()

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as github,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.finish_ops_job"),
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            return_value=client,
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
            prepare,
        ),
    ):
        github.return_value.get_pull.return_value = pull
        github.return_value.get_branch_sha.return_value = None
        job = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=51079,
            merge_base_with="HEAD",
            dry_run=False,
            no_commit=True,
            config=config,
            parent_run_id="parent",
            checkpoint=current,
        )

    assert client.usage_tracker.records == []
    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
    assert "ydb/docs/en/a.md" in job.pr_result.completeness_gaps
    github.return_value.get_branch_sha.assert_called_once()
    prepare.assert_not_called()


def test_changed_baseline_refreezes_and_does_not_inherit_approval() -> None:
    old_identity = _identity()
    store = InMemoryTranscriptStore()
    CheckpointWriter(store, "old-baseline", old_identity).save_unit(
        "5" * 64,
        b"source",
        b"target",
        validated=True,
    )
    current_identity = replace(
        old_identity,
        authority=replace(old_identity.authority, baseline_sha="a" * 40),
    )

    assert (
        load_verified_unit(
            store,
            "old-baseline",
            current_identity,
            "5" * 64,
            b"source",
        )
        is None
    )


def test_translation_inputs_invalidate_resume() -> None:
    config = load_config(env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"})
    client = _client([])
    glossary = load_glossary()
    base = _translation_checkpoint_fingerprint(
        config,
        glossary,
        client,
        effective_continue_feedback="current instruction",
    )
    changed_glossary = Glossary.from_yaml_text("- ru: термин\n  en: term\n")
    llm_config = config.model_copy(deep=True)
    llm_config.llm.max_tokens += 1
    translation_config = config.model_copy(deep=True)
    translation_config.translation.segment_max_source_chars += 1

    variants = [
        _translation_checkpoint_fingerprint(
            config,
            changed_glossary,
            client,
            effective_continue_feedback="current instruction",
        ),
        _translation_checkpoint_fingerprint(
            llm_config,
            glossary,
            client,
            effective_continue_feedback="current instruction",
        ),
        _translation_checkpoint_fingerprint(
            translation_config,
            glossary,
            client,
            effective_continue_feedback="current instruction",
        ),
        _translation_checkpoint_fingerprint(
            config,
            glossary,
            client,
            effective_continue_feedback="changed effective instruction",
        ),
    ]
    with patch(
        "ydbdoc_review.github.workflow.load_template",
        side_effect=lambda name, version: f"changed:{version}:{name}",
    ):
        variants.append(
            _translation_checkpoint_fingerprint(
                config,
                glossary,
                client,
                effective_continue_feedback="current instruction",
            )
        )
    with patch.object(
        client,
        "model_chain_for_role",
        return_value=["different-translation-model"],
    ):
        variants.append(
            _translation_checkpoint_fingerprint(
                config,
                glossary,
                client,
                effective_continue_feedback="current instruction",
            )
        )

    assert len(set(variants)) == len(variants)
    assert all(fingerprint != base for fingerprint in variants)


def test_checkpoint_scope_resets_resume_parent_between_jobs() -> None:
    checkpoint = CheckpointWriter(InMemoryTranscriptStore(), "current", _identity())
    client = _client([])

    with checkpoint_scope(checkpoint, "parent"):
        active = HarnessContext.from_options(client)
        assert active.checkpoint is checkpoint
        assert active.resume_parent_run_id == "parent"

    fresh = HarnessContext.from_options(client)
    assert fresh.checkpoint is None
    assert fresh.resume_parent_run_id is None


def test_current_segment_identity_fields_invalidate_unit_key() -> None:
    segment = Segment(
        id="s1",
        kind=SegmentKind.HEADING,
        path=["Parent"],
        text="Source ⟦C1⟧ ⟦C2⟧",
        placeholders=[
            ProtectedInline(placeholder="⟦C1⟧", node=InlineCode(content="one")),
            ProtectedInline(placeholder="⟦C2⟧", node=InlineCode(content="two")),
        ],
        ast_path=[0, "title"],
        heading_anchor="anchor",
    )

    def key(current: Segment, *, path: str = "ydb/docs/ru/a.md", locale: str = "en") -> str:
        return translation_unit_key_for_segment(
            current,
            source_path=path,
            target_locale=locale,
        )

    original = key(segment)
    variants = [
        key(segment.model_copy(update={"text": "Changed ⟦C1⟧ ⟦C2⟧"})),
        key(segment.model_copy(update={"kind": SegmentKind.PARAGRAPH})),
        key(segment.model_copy(update={"path": ["Other parent"]})),
        key(segment.model_copy(update={"ast_path": [1, "title"]})),
        key(segment.model_copy(update={"heading_anchor": "other-anchor"})),
        key(segment.model_copy(update={"placeholders": list(reversed(segment.placeholders))})),
        key(segment, path="ydb/docs/ru/other.md"),
        key(segment, locale="de"),
    ]

    assert all(candidate != original for candidate in variants)


def test_navigation_change_reuses_translations_but_recomputes_navigation(
    tmp_path: Path,
) -> None:
    git_repo = _workflow_repo(tmp_path)
    toc_path = Path(git_repo, "ydb/docs/ru/core/toc_p.yaml")
    toc_path.parent.mkdir(parents=True)
    toc_path.write_text(
        "items:\n- name: A\n  href: ../a.md\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "navigation"], cwd=git_repo, check=True)
    sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"],
        text=True,
    ).strip()
    identity = CheckpointIdentity(
        RuAuthority(
            source_repo="o/r",
            source_pr=51079,
            source_base_sha=sha,
            source_head_sha=sha,
            baseline_sha=sha,
            ru_sha=sha,
            mode=RuAuthorityMode.CURRENT,
        ),
        "4" * 64,
    )
    source_text = "Привет.\n"
    segment = extract_segments(parse_markdown(source_text))[0]
    store = InMemoryTranscriptStore()
    CheckpointWriter(store, "parent", identity).save_unit(
        translation_unit_key_for_segment(
            segment,
            source_path="ydb/docs/ru/a.md",
            target_locale="en",
        ),
        segment.text.encode("utf-8"),
        b"Hello.",
        validated=True,
    )
    current = CheckpointWriter(store, "current", identity)
    ledger = InMemoryRunsLedger()
    ledger.records = [_record("parent", repo="o/r", status="failed", hour=9)]
    ops_ctx = SimpleNamespace(
        ledger=ledger,
        store=store,
        run_id="current",
        repo="o/r",
        source_pr=51079,
        parent_run_id="parent",
        continue_feedback=None,
        recorder=MagicMock(),
    )
    scope = TranslationScopePlan(
        doc_ru_paths=frozenset({"ydb/docs/ru/a.md"}),
        doc_from_diff=frozenset({"ydb/docs/ru/a.md"}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset({"ydb/docs/ru/core/toc_p.yaml"}),
        nav_from_diff=frozenset({"ydb/docs/ru/core/toc_p.yaml"}),
        nav_from_main=frozenset(),
    )
    nav_result = NavigationRunResult(
        ru_path="ydb/docs/ru/core/toc_p.yaml",
        en_path="ydb/docs/en/core/toc_p.yaml",
        kind="toc",
        target_text="items:\n- name: A\n  href: ../a.md\n",
        verdict="ok",
    )
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": sha},
    }
    config = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )
    client = _client([])

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as github,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.finish_ops_job"),
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            return_value=client,
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[
                ("ydb/docs/ru/a.md", "modified"),
                ("ydb/docs/ru/core/toc_p.yaml", "modified"),
            ],
        ),
        patch(
            "ydbdoc_review.github.workflow.plan_translation_scope",
            return_value=scope,
        ),
        patch(
            "ydbdoc_review.github.workflow.preflight_translation",
            return_value=PreflightResult(blockers=(), deferred_checks=()),
        ),
        patch(
            "ydbdoc_review.github.workflow.run_navigation_merges",
            return_value=[nav_result],
        ) as navigation,
    ):
        github.return_value.get_pull.return_value = pull
        github.return_value.get_branch_sha.return_value = None
        run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=51079,
            merge_base_with="HEAD",
            dry_run=True,
            config=config,
            parent_run_id="parent",
            checkpoint=current,
        )

    assert client.usage_tracker.records == []
    navigation.assert_called_once()
    assert store.get("current", "translation/v1/manifest.json") is not None
