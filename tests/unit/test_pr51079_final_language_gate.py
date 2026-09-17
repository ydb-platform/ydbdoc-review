"""The last language barrier must inspect authoritative, unmodified UTF-8 text."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.git_ops import read_text_at_commit
from ydbdoc_review.github.workflow import _enforce_report_checkout_bytes, _final_tree_reader
from ydbdoc_review.harness import TRANSLATE_PROFILE, TRANSLATE_WITH_QA_PROFILE, VERIFY_PROFILE
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_full_report,
    build_translation_pr_body,
    parse_final_tree_blocker_manifest,
)
from ydbdoc_review.validation.final_language import (
    apply_final_en_language_gate,
    check_final_en_language,
)

EN = "ydb/docs/en/a.md"
RU = "ydb/docs/ru/a.md"


def _result(text="Hello.\n", *, file_result=True):
    plan = PairPlan(
        pair=DocPair(ru_path=RU, en_path=EN),
        action="critic_only",
        source_path=RU,
        target_path=EN,
        source_lang="ru",
        target_lang="en",
    )
    fr = (
        FileTranslationResult(
            file_path=EN, final_text=text, segments_count=0, verdict="ok", prompt_version="test"
        )
        if file_result
        else None
    )
    return PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text=text, file_result=fr)]
    )


@pytest.mark.parametrize(
    "text",
    [
        "Русская проза\n",
        "Use `Имя=Значение,...@<domain>`.\n",
        "```mermaid\nsequenceDiagram\nactor user as Пользователь\n```\n",
        "```yaml\nkey: Русское\n```\n",
        "    Комментарий\n",
        "<!-- Русский -->\n",
        "---\nother_key: Русское\n---\n",
        "{% include [Русское](fragment.md) %}\n",
        "## Title {#русский}\n",
        "Extended Cyrillic: Ꙁ\n",
    ],
)
def test_final_language_gate_scans_every_final_representation(text):
    is_code = text.startswith(("```", "    "))
    assert bool(check_final_en_language(text, target_lang="en")) is not is_code
    assert bool(check_final_en_language(text, target_lang="English")) is not is_code
    assert check_final_en_language(text, target_lang="ru") == []


def test_detector_preserves_lines_and_bounds_previews():
    text = "ASCII\n" + ("Я" * 200 + "\n") * 15
    findings = check_final_en_language(text)
    assert findings[0] == "en_language: line 2: " + "Я" * 120
    assert findings[-1] == "en_language: 3 additional lines"
    assert len(findings) == 13
    assert check_final_en_language("Name=Value,...@<domain>\n") == []


@pytest.mark.parametrize("profile", [TRANSLATE_PROFILE, TRANSLATE_WITH_QA_PROFILE, VERIFY_PROFILE])
def test_real_harness_final_gate(profile):
    from tests.unit.test_pr51079_protected_validation import _run_protected

    result, _ = _run_protected(profile, "```yaml\nkey: Русское\n```\n")
    assert result.verdict == "warnings"
    assert not any(message.startswith("en_language:") for message in result.heuristic_blocking)


@pytest.mark.parametrize("shortcut", ["preserved", "href_only"])
def test_real_pair_shortcuts_gate_target(shortcut):
    plan = _result().pair_results[0].plan
    old = "<!-- Русский -->\n\nSee [page](old.md).\n"
    new = old.replace("old.md", "new.md")
    source = "См. [page](new.md).\n" if shortcut == "preserved" else "См. страницу.\n"
    content = PairContent(
        pair=plan.pair,
        ru_base_text="Старая страница.\n",
        ru_text=source,
        en_base_text=old,
        en_text=new,
    )
    run = run_pair_plan(content, plan, HarnessContext.from_options(MagicMock()), {})
    assert run.target_text == new
    assert run.file_result is not None
    assert run.file_result.final_text == new
    assert run.file_result.verdict == "blocked"
    assert any(m.startswith("en_language:") for m in run.file_result.heuristic_blocking)


def test_gate_covers_include_only_and_no_file_result_pairs():
    text = "{% include [Русское](fragment.md) %}\n"
    result = _result(text, file_result=False)
    nav = "ydb/docs/en/toc.yaml"
    result.navigation_results.append(NavigationRunResult(RU, nav, "toc", "name: Русское\n"))
    values = {EN: text, nav: "name: Русское\n"}
    assert apply_final_en_language_gate(result, en_paths=values, read_text=values.get) == sorted(
        values
    )
    assert result.pair_results[0].file_result.verdict == "blocked"
    assert result.navigation_results[0].verdict == "blocked"


def test_language_blocker_is_unsafe_not_publish_red():
    result = _result("<!-- Русское -->\n")
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE
    result.pair_results[0].file_result = None
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE
    apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: "<!-- Русское -->\n")
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE


@pytest.mark.parametrize("suffix", ["md", "yaml", "yml"])
def test_en_language_manifest_roundtrip_binds_exact_utf8_hash(suffix):
    text = "Русское\r\n"
    path = f"ydb/docs/en/asset.{suffix}"
    result = PRTranslationResult()
    apply_final_en_language_gate(result, en_paths=[path], read_text=lambda _: text)
    blocker = result.final_tree_blockers[0]
    assert blocker.artifact_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    body = build_translation_pr_body(7, "o/r", publication_result=result)
    assert parse_final_tree_blocker_manifest(body) == result.final_tree_blockers
    with pytest.raises(ValueError):
        replace(blocker, artifact_sha256=None)
    with pytest.raises(ValueError):
        replace(blocker, artifact_sha256="A" * 64)


def test_final_gate_is_idempotent_and_retains_other_blockers():
    result = _result("Русское\n")
    other = FinalTreeBlocker(EN, "en_link_target", "en_link_target: missing")
    result.final_tree_blockers.append(other)
    reader = {EN: "Русское\n"}.get
    apply_final_en_language_gate(result, en_paths=[EN, EN], read_text=reader)
    previous = list(result.final_tree_blockers)
    apply_final_en_language_gate(result, en_paths=[EN], read_text=reader)
    assert result.final_tree_blockers == previous
    run = result.pair_results[0]
    run.target_text = run.file_result.final_text = "Fixed\n"
    run.file_result.heuristic_blocking.append(other.message)
    apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: "Fixed\n")
    assert result.final_tree_blockers == [other]
    assert run.file_result.heuristic_blocking == [other.message]
    assert run.file_result.verdict == "blocked"


def test_gate_reads_once_and_blocks_candidate_mismatch_without_overwriting():
    result = _result()
    calls = []

    def read(path):
        calls.append(path)
        return "Русское\n"

    assert apply_final_en_language_gate(result, en_paths=[EN, EN], read_text=read) == [EN]
    assert calls == [EN]
    run = result.pair_results[0]
    assert run.target_text == run.file_result.final_text == "Hello.\n"
    assert any(
        m.startswith("report_checkout_mismatch:") for m in run.file_result.heuristic_blocking
    )


def test_empty_overlay_does_not_fall_back_to_russian_baseline():
    result = _result("")
    apply_final_en_language_gate(result, en_paths=[EN], read_text={EN: ""}.get)
    assert result.final_tree_blockers == []
    assert result.pair_results[0].target_text == ""


def test_deleted_asset_is_not_resurrected():
    result = _result("Русское\n")
    result.pair_results[0].deleted = True
    result.pair_results[0].target_text = None
    assert apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: None) == []
    assert result.final_tree_blockers == []


def test_unscanned_and_unknown_paths_retain_language_evidence():
    result = _result("Русское\n")
    apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: "Русское\n")
    before = list(result.final_tree_blockers)
    apply_final_en_language_gate(result, en_paths=["ydb/docs/en/other.md"], read_text=lambda _: "")
    apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: None)
    assert result.final_tree_blockers == before
    assert result.pair_results[0].file_result.verdict == "blocked"


def test_language_repair_clears_only_its_verdict_contribution():
    result = _result("Русское\n")
    apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: "Русское\n")
    run = result.pair_results[0]
    run.target_text = run.file_result.final_text = "English\n"
    apply_final_en_language_gate(result, en_paths=[EN], read_text=lambda _: "English\n")
    assert result.final_tree_blockers == []
    assert run.file_result.heuristic_blocking == []
    assert run.file_result.verdict == "ok"


@pytest.mark.parametrize("prior_verdict", ["warnings", "blocked"])
def test_navigation_language_repair_restores_unrelated_verdict(prior_verdict):
    path = "ydb/docs/en/toc.yaml"
    nav = NavigationRunResult(
        RU,
        path,
        "toc",
        "name: Русское\n",
        warnings=["existing navigation finding"],
        verdict=prior_verdict,
    )
    result = PRTranslationResult(navigation_results=[nav])
    apply_final_en_language_gate(result, en_paths=[path], read_text=lambda _: nav.target_text)
    assert nav.verdict == "blocked"
    nav.target_text = "name: English\n"
    apply_final_en_language_gate(result, en_paths=[path], read_text=lambda _: nav.target_text)
    assert result.final_tree_blockers == []
    assert nav.warnings == ["existing navigation finding"]
    assert nav.verdict == prior_verdict
    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_RED


@pytest.mark.parametrize("add_toc_blocker", [True, False])
def test_navigation_language_rescan_preserves_later_independent_contributions(
    tmp_path,
    add_toc_blocker,
):
    from ydbdoc_review.validation.toc_targets import apply_toc_target_checks

    path = "ydb/docs/en/core/toc_p.yaml"
    nav = NavigationRunResult(
        RU,
        path,
        "toc",
        "items:\n- name: Русское\n  href: missing.md\n",
    )
    result = PRTranslationResult(navigation_results=[nav])
    apply_final_en_language_gate(result, en_paths=[path], read_text=lambda _: nav.target_text)
    assert nav.verdict == "blocked"
    nav.target_text = "items:\n- name: English\n  href: missing.md\n"
    if add_toc_blocker:
        apply_toc_target_checks(result, repo_path=str(tmp_path))
        assert any(m.startswith("missing_toc_target:") for m in nav.warnings)
    else:
        nav.warnings.append("Reviewer should check the navigation labels")
    apply_final_en_language_gate(result, en_paths=[path], read_text=lambda _: nav.target_text)
    assert result.final_tree_blockers == []
    assert not any(m.startswith("en_language:") for m in nav.warnings)
    assert nav.verdict == ("blocked" if add_toc_blocker else "warnings")
    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_RED


def test_verify_report_hash_and_bytes_match_checked_sha(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    path = tmp_path / EN
    path.parent.mkdir(parents=True)
    path.write_text("English\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "B")
    path.write_text("<!-- Русское -->\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "K")
    k = git("rev-parse", "HEAD")
    path.write_text("Dirty worktree\n", encoding="utf-8")
    checked = read_text_at_commit(str(tmp_path), k, EN)
    result = _result(checked)
    apply_final_en_language_gate(
        result, en_paths=[EN], read_text=_final_tree_reader(str(tmp_path), k, set())
    )
    assert (
        result.final_tree_blockers[0].artifact_sha256
        == hashlib.sha256(checked.encode()).hexdigest()
    )
    assert _enforce_report_checkout_bytes(str(tmp_path), k, result) == []
    refresh_publication_impact(result)
    report = build_full_report(
        result, meta=ReportMeta("doc_verify", 1, 0, checkout_ref=k), config=load_config(env={})
    )
    assert k[:12] in report
    assert "Candidate опубликован" not in report
    result.pair_results[0].file_result.final_text = "Wrong report text\n"
    assert _enforce_report_checkout_bytes(str(tmp_path), k, result) == [EN]


def test_navigation_english_byte_mismatch_withholds_instead_of_publishing_red():
    path = "ydb/docs/en/core/toc_p.yaml"
    nav = NavigationRunResult(RU, path, "toc", "name: Stale English\n")
    result = PRTranslationResult(navigation_results=[nav])

    apply_final_en_language_gate(
        result, en_paths=[path], read_text=lambda _: "name: Actual English\n"
    )

    assert nav.target_text == "name: Stale English\n"
    assert result.final_tree_blockers == []
    assert any(message.startswith("report_checkout_mismatch:") for message in nav.warnings)
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_UNSAFE


@pytest.mark.parametrize(
    ("impact", "published"),
    [
        (PublicationImpact.PUBLISH_NORMAL, True),
        (PublicationImpact.PUBLISH_RED, True),
        (PublicationImpact.WITHHOLD_UNSAFE, False),
        (PublicationImpact.WITHHOLD_INCOMPLETE, False),
    ],
)
def test_final_tree_report_and_pr_body_agree_on_publication_state(impact, published):
    blocker = FinalTreeBlocker(EN, "en_link_target", "route conflict: existing live target")
    result = PRTranslationResult(final_tree_blockers=[blocker], publication_impact=impact)
    report = build_full_report(
        result, meta=ReportMeta("doc_verify", 1, 0), config=load_config(env={})
    )
    body = build_translation_pr_body(7, "o/r", publication_result=result)

    assert "Статус QA (K): 🔴 RED" in report
    assert "QA K: 🔴 RED" in body
    for rendered in (report, body):
        assert ("Candidate опубликован для ручного исправления" in rendered) is published
        assert ("Публикация candidate удержана" in rendered) is not published
        assert blocker.message in rendered
    assert f"Артефакт: {'опубликован' if published else 'не опубликован'}" in body
    assert result.publication_impact is impact
    assert result.final_tree_blockers == [blocker]
