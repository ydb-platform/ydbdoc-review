"""Retention contracts for completed translation work."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.github.workflow import (
    _translation_checkpoint_fingerprint,
    run_doc_translate,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.transcripts import (
    InMemoryTranscriptStore,
    NullTranscriptStore,
)
from ydbdoc_review.ops.translation_checkpoint import (
    CheckpointIdentity,
    CheckpointWriter,
    TranslationCheckpointError,
    translation_unit_key,
)
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult, PublicationImpact
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.translator import translate_segments


def _authority() -> RuAuthority:
    return RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=51079,
        source_base_sha="1" * 40,
        source_head_sha="2" * 40,
        baseline_sha="3" * 40,
        ru_sha="2" * 40,
        mode=RuAuthorityMode.SOURCE_PRESERVING,
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
    config = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
    )
    return YandexLLMClient(
        folder_id="folder",
        api_key="key",
        llm=config.llm,
        client=openai,
    )


def test_completed_unit_is_durable_before_candidate_finishes() -> None:
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(
        store,
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )

    receipt = writer.save_unit(
        "5" * 64,
        b"source",
        b"target",
        validated=True,
    )

    assert store.get("run-a", receipt.object_key) == b"target"
    assert receipt.validated


def test_checkpoint_identity_and_receipt_are_frozen() -> None:
    identity = CheckpointIdentity(_authority(), "4" * 64)
    store = InMemoryTranscriptStore()
    receipt = CheckpointWriter(store, "run-a", identity).save_unit(
        "5" * 64,
        b"source",
        b"target",
        validated=True,
    )

    with pytest.raises(FrozenInstanceError):
        identity.translation_fingerprint = "6" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        receipt.validated = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("source", b"changed"),
        ("source_path", "ydb/docs/ru/other.md"),
        ("target_locale", "de"),
        ("atom_signature", (("link", "b"), ("code", "a"))),
        ("parent_context", "Other heading"),
    ],
)
def test_unit_key_binds_every_canonical_input(override: str, value: object) -> None:
    base = {
        "source": b"source",
        "source_path": "ydb/docs/ru/a.md",
        "target_locale": "en",
        "atom_signature": (("code", "a"), ("link", "b")),
        "parent_context": "Intro",
    }
    original = translation_unit_key(**base)  # type: ignore[arg-type]
    changed = dict(base)
    changed[override] = value

    assert translation_unit_key(**changed) != original  # type: ignore[arg-type]


def test_second_different_receipt_for_same_unit_key_is_rejected() -> None:
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(
        store,
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )
    writer.save_unit("5" * 64, b"source-a", b"target-a", validated=True)

    with pytest.raises(TranslationCheckpointError, match="immutable conflict"):
        writer.save_unit("5" * 64, b"source-b", b"target-b", validated=True)


def test_null_store_cannot_claim_checkpoint_durability() -> None:
    writer = CheckpointWriter(
        NullTranscriptStore(),
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )

    with pytest.raises(TranslationCheckpointError, match="write verification failed"):
        writer.save_unit("5" * 64, b"source", b"target", validated=True)


def test_translation_fingerprint_binds_effective_continue_feedback() -> None:
    config = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
    )
    client = _client([])
    glossary = load_glossary()

    first = _translation_checkpoint_fingerprint(
        config,
        glossary,
        client,
        effective_continue_feedback="Preserve the requested terminology.",
    )
    second = _translation_checkpoint_fingerprint(
        config,
        glossary,
        client,
        effective_continue_feedback="Use the revised heading.",
    )

    assert first != second


def test_manifest_is_written_last_with_files_navigation_scope_and_units() -> None:
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(
        store,
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )
    writer.save_unit("5" * 64, b"source", b"target", validated=True)
    writer.save_unit("6" * 64, b"bad-source", b"bad-target", validated=False)
    writer.save_file(
        "ydb/docs/en/a.md",
        b"source-file",
        b"target-file",
        blockers=(),
    )
    writer.save_navigation(
        "ydb/docs/en/toc_p.yaml",
        None,
        blockers=("scope_not_applied: nested child",),
    )
    pre_manifest_keys = store.list_keys("run-a")
    assert len(
        [key for key in pre_manifest_keys if key.startswith("translation/v1/files/")]
    ) == 1
    assert len(
        [
            key
            for key in pre_manifest_keys
            if key.startswith("translation/v1/navigation/")
        ]
    ) == 1

    writer.finish(
        status="WITHHOLD_INCOMPLETE",
        blockers=("ydb/docs/en/missing.md",),
        scope={
            "ydb/docs/ru/a.md": ("doc_from_diff",),
            "ydb/docs/ru/dependency.md": ("doc_from_main", "include_dependency"),
        },
    )

    raw = store.get("run-a", "translation/v1/manifest.json")
    assert raw is not None
    manifest = json.loads(raw)
    assert manifest["stage"] == "retained"
    assert manifest["status"] == "WITHHOLD_INCOMPLETE"
    assert manifest["blockers"] == ["ydb/docs/en/missing.md"]
    assert manifest["scope"] == {
        "ydb/docs/ru/a.md": ["doc_from_diff"],
        "ydb/docs/ru/dependency.md": ["doc_from_main", "include_dependency"],
    }
    assert manifest["files"][0]["path"] == "ydb/docs/en/a.md"
    assert manifest["files"][0]["target_object_key"].startswith(
        "translation/v1/objects/"
    )
    assert manifest["navigation"] == [
        {
            "baseline_noop": True,
            "blockers": ["scope_not_applied: nested child"],
            "path": "ydb/docs/en/toc_p.yaml",
            "target_hash": None,
            "target_object_key": None,
        }
    ]
    assert [unit["validated"] for unit in manifest["units"]] == [True, False]
    assert "receipt_hash" in manifest["units"][0]
    assert "receipt_hash" in manifest["units"][1]


class _DropManifestStore(InMemoryTranscriptStore):
    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        if object_key == "translation/v1/manifest.json":
            return
        super().put(run_id, object_key, data)


class _FailingStore(InMemoryTranscriptStore):
    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        raise OSError("backend unavailable")


def test_store_backend_failure_is_an_explicit_retention_error() -> None:
    writer = CheckpointWriter(
        _FailingStore(),
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )

    with pytest.raises(TranslationCheckpointError, match="store failure") as exc_info:
        writer.save_unit("5" * 64, b"source", b"target", validated=True)

    assert isinstance(exc_info.value.__cause__, OSError)


def test_manifest_readback_failure_is_explicit() -> None:
    writer = CheckpointWriter(
        _DropManifestStore(),
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )
    writer.save_file("ydb/docs/en/a.md", b"source", b"target", blockers=())

    with pytest.raises(TranslationCheckpointError, match="write verification failed"):
        writer.finish(status="PUBLISH_NORMAL", blockers=(), scope={})


def test_completed_batches_emit_validated_units_before_later_batch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[tuple[str, str]] = []

    def translate_batch(_client, batch, _glossary, **_kwargs):  # type: ignore[no-untyped-def]
        segment = batch.segments[0]
        if segment.id == "s3":
            time.sleep(0.08)
            raise RuntimeError("third batch failed")
        time.sleep(0.01 if segment.id == "s1" else 0.02)
        return {segment.id: f"Target {segment.id}"}

    monkeypatch.setattr(
        "ydbdoc_review.translation.translator.translate_batch",
        translate_batch,
    )
    segments = [
        _segment("s1", "Source one"),
        _segment("s2", "Source two"),
        _segment("s3", "Source three"),
    ]

    with pytest.raises(RuntimeError, match="third batch failed"):
        translate_segments(
            segments,
            object(),  # type: ignore[arg-type]
            load_glossary(),
            file_path="ydb/docs/ru/a.md",
            max_chars=1,
            max_parallel_batches=3,
            on_validated_segment=lambda segment, target: retained.append(
                (segment.id, target)
            ),
        )

    assert retained == [("s1", "Target s1"), ("s2", "Target s2")]


def test_invalid_diagnostic_segment_is_not_announced_as_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[tuple[str, str]] = []
    segment = _segment("s1", "Russian source")

    monkeypatch.setattr(
        "ydbdoc_review.translation.translator.translate_batch",
        lambda *_args, **_kwargs: {"s1": "Непроверенный результат"},
    )

    result = translate_segments(
        [segment],
        object(),  # type: ignore[arg-type]
        load_glossary(),
        file_path="ydb/docs/ru/a.md",
        on_validated_segment=lambda completed, target: retained.append(
            (completed.id, target)
        ),
    )

    assert result == {"s1": "Непроверенный результат"}
    assert retained == []


def test_pr_boundary_saves_validated_file_and_unit_before_navigation() -> None:
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(
        store,
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    response = json.dumps(
        {"segments": [{"id": "s0001", "text": "Hello."}]},
        ensure_ascii=False,
    )
    critic_ok = json.dumps({"verdict": "ok", "issues": []})

    result = run_pr_translation(
        [PairContent(pair=pair, ru_text="Привет.\n")],
        _client([response, critic_ok]),
        checkpoint=writer,
    )

    assert result.pair_results[0].target_text == "Hello.\n"
    keys = store.list_keys("run-a")
    assert len([key for key in keys if key.startswith("translation/v1/units/")]) == 1
    assert len([key for key in keys if key.startswith("translation/v1/files/")]) == 1
    writer.finish(status="PUBLISH_NORMAL", blockers=(), scope={})
    raw = store.get("run-a", "translation/v1/manifest.json")
    assert raw is not None
    manifest = json.loads(raw)
    assert [record["path"] for record in manifest["files"]] == [
        "ydb/docs/en/a.md"
    ]


def test_all_protected_file_is_saved_without_model_calls() -> None:
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(
        store,
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )
    pair = DocPair(
        ru_path="ydb/docs/ru/_assets/diagram.md",
        en_path="ydb/docs/en/_assets/diagram.md",
        ru_changed=True,
    )
    source = "```mermaid\ngraph TD\n  A --> B\n```\n"
    client = _client([])

    result = run_pr_translation(
        [PairContent(pair=pair, ru_text=source)],
        client,
        checkpoint=writer,
    )

    assert result.pair_results[0].target_text == source
    assert client.usage_tracker.records == []
    writer.finish(status="PUBLISH_NORMAL", blockers=(), scope={})
    raw = store.get("run-a", "translation/v1/manifest.json")
    assert raw is not None
    manifest = json.loads(raw)
    assert manifest["units"] == []
    assert manifest["files"][0]["path"] == "ydb/docs/en/_assets/diagram.md"


def _workflow_repo(tmp_path) -> tuple[str, str]:  # type: ignore[no-untyped-def]
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
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    return str(repo), sha


def _workflow_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    from ydbdoc_review.pipeline.analyze import PairPlan

    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="Hello.\n",
                source_text="Привет.\n",
            )
        ]
    )


def _run_checkpoint_workflow(
    tmp_path,  # type: ignore[no-untyped-def]
    writer: CheckpointWriter,
    result: PRTranslationResult,
    *,
    gaps: list[str],
    identity_mismatch: bool = False,
    publication_mocks: tuple[MagicMock, MagicMock, MagicMock] | None = None,
):  # type: ignore[no-untyped-def]
    repo, sha = _workflow_repo(tmp_path)
    config = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )
    client = _client([])
    if not identity_mismatch:
        writer.identity = CheckpointIdentity(
            RuAuthority(
                source_repo="ydb-platform/ydb",
                source_pr=51079,
                source_base_sha=sha,
                source_head_sha=sha,
                baseline_sha=sha,
                ru_sha=sha,
                mode=RuAuthorityMode.SOURCE_PRESERVING,
            ),
            _translation_checkpoint_fingerprint(
                config,
                load_glossary(),
                client,
                effective_continue_feedback=None,
            ),
        )
    pull = SimpleNamespace(
        owner="ydb-platform",
        repo="ydb",
        number=51079,
        title="docs",
        head_ref="docs",
        head_sha=sha,
        head_repo_full_name="ydb-platform/ydb",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        base_ref="main",
        base_sha=sha,
        body="",
        merged=False,
        state="open",
        merge_commit_sha=None,
        labels=frozenset({"doc_translate_source_preserving"}),
    )
    scope = TranslationScopePlan(
        doc_ru_paths=frozenset({"ydb/docs/ru/a.md"}),
        doc_from_diff=frozenset({"ydb/docs/ru/a.md"}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
    )
    gh = MagicMock()
    gh.get_branch_sha.return_value = None
    gh.find_open_pull_by_head.return_value = None
    gh.post_issue_comment.return_value = "comment-url"
    prepare, commit, push = publication_mocks or (MagicMock(), MagicMock(), MagicMock())

    def retained_result(*_args, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["checkpoint"] is writer
        writer.save_file(
            "ydb/docs/en/a.md",
            b"source-file",
            b"target-file",
            blockers=(),
        )
        return result

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(None, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.pull_request_context", return_value=pull),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
        patch("ydbdoc_review.github.workflow.plan_translation_scope", return_value=scope),
        patch(
            "ydbdoc_review.github.workflow.load_pair_contents",
            return_value=[
                PairContent(
                    pair=DocPair(
                        ru_path="ydb/docs/ru/a.md",
                        en_path="ydb/docs/en/a.md",
                        ru_changed=True,
                    ),
                    ru_text="Привет.\n",
                )
            ],
        ),
        patch(
            "ydbdoc_review.github.workflow.build_en_toc_reachable_from_repo",
            return_value=frozenset(),
        ),
        patch(
            "ydbdoc_review.github.workflow.redirect_source_repo_md_paths",
            return_value=frozenset(),
        ),
        patch("ydbdoc_review.github.workflow.create_llm_client", return_value=client),
        patch("ydbdoc_review.github.workflow.run_pr_translation", side_effect=retained_result),
        patch(
            "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
            return_value=[],
        ),
        patch("ydbdoc_review.github.workflow.completeness_gaps", return_value=gaps),
        patch(
            "ydbdoc_review.github.workflow.build_later_ru_drift_report",
            return_value="",
        ),
        patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base", prepare),
        patch("ydbdoc_review.github.workflow.git_commit_paths", commit),
        patch("ydbdoc_review.github.workflow.push_branch", push),
    ):
        job = run_doc_translate(
            repo_path=repo,
            github_repo="ydb-platform/ydb",
            pr_number=51079,
            merge_base_with="HEAD",
            config=config,
            checkpoint=writer,
        )
    return job, prepare, commit, push


@pytest.mark.parametrize("has_artifact", [True, False])
def test_workflow_finishes_checkpoint_before_publication_calls(tmp_path, has_artifact) -> None:
    from ydbdoc_review.github.git_ops import git_commit_paths

    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(
        store,
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )

    result = _workflow_result()
    if not has_artifact:
        result.pair_results[0].target_text = None
        result.pair_results[0].error = "translation failed before producing a candidate"

    def assert_retained(*_args, **_kwargs):
        raw = store.get("run-a", "translation/v1/manifest.json")
        assert raw is not None
        manifest = json.loads(raw)
        # Retention precedes K, so its status still records incomplete QA.
        assert manifest["status"] == "WITHHOLD_INCOMPLETE"
        assert "ydb/docs/en/missing.md" in manifest["blockers"]

    def commit_retained(*args, **kwargs):
        assert_retained()
        return git_commit_paths(*args, **kwargs)

    class PublicationBoundaryReached(Exception):
        pass

    def observe_push(repo_path, *_args, **kwargs):
        assert_retained()
        assert result.publication_impact == PublicationImpact.PUBLISH_RED
        assert subprocess.check_output(
            ["git", "show", f"{kwargs['source_sha']}:ydb/docs/en/a.md"],
            cwd=repo_path,
        ) == b"Hello.\n"
        raise PublicationBoundaryReached("checkpoint-publication-boundary")

    prepare = MagicMock(side_effect=assert_retained)
    commit = MagicMock(side_effect=commit_retained)
    push = MagicMock(side_effect=observe_push)

    def run_workflow():
        return _run_checkpoint_workflow(
            tmp_path,
            writer,
            result,
            gaps=["ydb/docs/en/missing.md"],
            publication_mocks=(prepare, commit, push),
        )

    if has_artifact:
        # D-001 publishes the real K as RED despite the missing dependent file.
        # Stop at the remote boundary; this test owns retention ordering only.
        with pytest.raises(RuntimeError, match="checkpoint-publication-boundary") as exc:
            run_workflow()
        assert isinstance(exc.value.__cause__, PublicationBoundaryReached)
        prepare.assert_called_once()
        commit.assert_called_once()
        push.assert_called_once()
        return

    job, _, _, _ = run_workflow()
    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
    assert job.pr_result.publication_failure == "no_publishable_artifact"
    assert_retained()
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()


def test_retention_failure_is_explicit_without_replacing_publication_verdict(
    tmp_path,
) -> None:
    result = _workflow_result()
    writer = CheckpointWriter(
        _DropManifestStore(),
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )

    with pytest.raises(TranslationCheckpointError, match="write verification failed"):
        _run_checkpoint_workflow(tmp_path, writer, result, gaps=[])

    assert result.publication_impact == PublicationImpact.PUBLISH_NORMAL


def test_workflow_rejects_checkpoint_for_different_frozen_authority(tmp_path) -> None:
    writer = CheckpointWriter(
        InMemoryTranscriptStore(),
        "run-a",
        CheckpointIdentity(_authority(), "4" * 64),
    )

    with pytest.raises(TranslationCheckpointError, match="authority mismatch"):
        _run_checkpoint_workflow(
            tmp_path,
            writer,
            _workflow_result(),
            gaps=[],
            identity_mismatch=True,
        )
