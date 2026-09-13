"""Authorized late edits must advance QA bytes without blessing unrelated drift."""

from copy import deepcopy
from dataclasses import replace
from functools import partial

import pytest

from ydbdoc_review.github import workflow
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.validation.final_language import apply_final_en_language_gate
from ydbdoc_review.validation.link_contract import LinkContractIssue
from ydbdoc_review.validation.redirect_impacts import retarget_redirect_inbound_links

EN = "ydb/docs/en/core/page.md"
BEFORE = "See [page](old.md).\r\n\r\n"
AFTER = "See [page](new.md).\r\n\r\n"


def _run(text=BEFORE, path=EN):
    ru = path.replace("/en/", "/ru/")
    return PairRunResult(
        plan=PairPlan(
            pair=DocPair(ru_path=ru, en_path=path), action="translate_to_en",
            source_path=ru, target_path=path, source_lang="ru", target_lang="en",
        ),
        target_text=text,
        file_result=FileTranslationResult(
            file_path=path, final_text=text, segments_count=1, verdict="ok", prompt_version="test",
        ),
    )


@pytest.fixture
def candidate(tmp_path):
    path = tmp_path / EN
    path.parent.mkdir(parents=True)
    path.write_bytes(BEFORE.encode())
    result = PRTranslationResult(pair_results=[_run()])
    return tmp_path, path, result


def _apply(candidate, **kwargs):
    repo, _path, result = candidate
    return workflow._apply_authorized_late_repair(
        str(repo), EN, BEFORE, AFTER, result=result, dry_run=False, **kwargs,
    )


def test_authorized_repair_preserves_exact_bytes_and_every_existing_finding(candidate):
    _repo, path, result = candidate
    run = result.pair_results[0]
    fr = run.file_result
    issue = LinkContractIssue("href_mismatch", "independent contract", file_path=EN)
    run.validation_issues = (issue,)
    run.source_text = "Source authority"
    run.soft_keep_reason = "retained provenance"
    fr.critic_initial = CriticResponse(verdict="blocked", issues=[])
    fr.critic_unresolved = CriticResponse(verdict="blocked", issues=[])
    fr.segment_alignment_error = "independent alignment"
    fr.link_contract_issues = (issue,)
    fr.manual_actions = [ManualAction("s1", "line 1", "independent manual work")]
    fr.heuristic_blocking = ["independent blocker"]
    fr.heuristic_warnings = ["independent warning"]
    fr.verdict = "blocked"
    result.final_tree_blockers = [FinalTreeBlocker(EN, "en_link_target", "typed blocker")]
    before = deepcopy(result)

    assert _apply(candidate)

    assert path.read_bytes() == AFTER.encode()
    assert run.target_text == fr.final_text == AFTER
    assert run.file_result is fr  # Deferred findings retain this object by identity.
    assert replace(fr, final_text=BEFORE) == before.pair_results[0].file_result
    assert replace(run, target_text=BEFORE, file_result=before.pair_results[0].file_result) == before.pair_results[0]
    assert result.final_tree_blockers == before.final_tree_blockers
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_INCOMPLETE


@pytest.mark.parametrize("drift", ["target", "file_result", "disk", "second_run"])
def test_authorized_repair_rejects_any_preimage_drift_before_mutating(candidate, drift):
    _repo, path, result = candidate
    if drift == "target":
        result.pair_results[0].target_text = "unexpected"
    elif drift == "file_result":
        result.pair_results[0].file_result.final_text = "unexpected"
    elif drift == "disk":
        path.write_bytes(b"unexpected")
    else:
        result.pair_results.append(_run("unexpected"))
    state = deepcopy(result)
    disk = path.read_bytes()

    with pytest.raises(ValueError, match="late_repair_preimage_mismatch"):
        _apply(candidate)

    assert result == state
    assert path.read_bytes() == disk


@pytest.mark.parametrize("mode", ["dry_run", "unchanged"])
def test_authorized_repair_noop_does_not_adopt_or_write(candidate, mode):
    repo, path, result = candidate
    state = deepcopy(result)
    assert not workflow._apply_authorized_late_repair(
        str(repo), EN, BEFORE, BEFORE if mode == "unchanged" else AFTER,
        result=result, dry_run=mode == "dry_run",
    )
    assert result == state
    assert path.read_bytes() == BEFORE.encode()


@pytest.mark.parametrize("flag", ["deleted", "skipped", "error"])
def test_authorized_repair_does_not_change_inactive_result_or_file(candidate, flag):
    _repo, path, result = candidate
    setattr(result.pair_results[0], flag, "failure" if flag == "error" else True)
    state = deepcopy(result)
    assert not _apply(candidate)
    assert result == state
    assert path.read_bytes() == BEFORE.encode()


def test_authorized_repair_does_not_change_unrelated_results(candidate):
    _repo, path, result = candidate
    other = _run("Unrelated", "ydb/docs/en/core/other.md")
    result.pair_results.append(other)
    state = deepcopy(other)
    assert _apply(candidate)
    assert path.read_bytes() == AFTER.encode()
    assert other == state


def test_authorized_repair_write_failure_keeps_disk_and_qa_preimage(candidate, monkeypatch):
    _repo, path, result = candidate
    state = deepcopy(result)

    def failed_replace(*_args):
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(workflow.os, "replace", failed_replace)
    with pytest.raises(OSError, match="atomic replacement failure"):
        _apply(candidate)
    assert result == state
    assert path.read_bytes() == BEFORE.encode()
    assert list(path.parent.iterdir()) == [path]


def test_authorized_repair_cannot_clear_late_russian_publication_veto(candidate):
    repo, path, result = candidate
    russian = "<!-- Русский текст -->\n"
    workflow._apply_authorized_late_repair(
        str(repo), EN, BEFORE, russian, result=result, dry_run=False,
    )
    assert apply_final_en_language_gate(
        result, en_paths=[EN], read_text=lambda _: path.read_bytes().decode(),
    ) == [EN]
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE


def test_redirect_writer_routes_exact_preimage_through_authorized_repair(candidate):
    repo, path, result = candidate
    apply_repair = partial(
        workflow._apply_authorized_late_repair, str(repo), result=result, dry_run=False,
    )
    assert retarget_redirect_inbound_links(
        str(repo), {"/old.md": "/new.md"}, allowed_paths=frozenset({EN}),
        apply_repair=apply_repair,
    ) == [EN]
    assert path.read_bytes() == AFTER.encode()
    assert result.pair_results[0].target_text == AFTER
    assert result.pair_results[0].file_result.final_text == AFTER


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_exact_anchor_declaration_updates_matching_owner_qa(tmp_path, newline):
    page = "ydb/docs/en/core/page.md"
    owner = "ydb/docs/en/core/owner.md"
    before = "## User token\n\nBody.\n".replace("\n", newline)
    after = "## User token {#token}\n\nBody.\n".replace("\n", newline)
    values = {
        page: "See [token](owner.md#token).\n",
        page.replace("/en/", "/ru/"): "См. [токен](owner.md#token).\n",
        owner: before,
        owner.replace("/en/", "/ru/"): "## Токен пользователя {#token}\n\nОписание.\n",  # noqa: RUF001 - RU fixture
    }
    for rel, text in values.items():
        local = tmp_path / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(text.encode())
    result = PRTranslationResult(pair_results=[_run(before, owner)])
    assert workflow._declare_exact_ascii_fragment_targets_after_apply(
        str(tmp_path), [page], dry_run=False, result=result,
    ) == [owner]
    assert (tmp_path / owner).read_bytes() == after.encode()
    assert result.pair_results[0].target_text == result.pair_results[0].file_result.final_text == after


def test_generic_fragment_repair_updates_matching_qa(tmp_path):
    before = "See [token](owner.md#токен-пользователя).\n"
    after = "See [token](owner.md#user-token).\n"
    values = {
        EN: before,
        EN.replace("/en/", "/ru/"): "См. [токен](owner.md#токен-пользователя).\n",
        "ydb/docs/en/core/owner.md": "## User token {#user-token}\n",
        "ydb/docs/ru/core/owner.md": "## Токен пользователя {#токен-пользователя}\n",
    }
    for rel, text in values.items():
        local = tmp_path / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(text.encode())
    result = PRTranslationResult(pair_results=[_run(before)])
    assert workflow._repair_en_fragments_after_apply(
        str(tmp_path), [EN], dry_run=False, result=result,
    ) == [EN]
    assert (tmp_path / EN).read_bytes() == after.encode()
    assert result.pair_results[0].target_text == result.pair_results[0].file_result.final_text == after
