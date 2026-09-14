"""Report evidence must survive stale hints and reject unproved candidate citations."""
from dataclasses import replace
from pathlib import Path

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.final_candidate import FinalCandidate
from ydbdoc_review.pipeline.types import FileTranslationResult, PairRunResult, PRTranslationResult
from ydbdoc_review.reporting import builder
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.translation.review_blocks import AuthoritativeDocument, prepare_review_document
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.parsing.inline_locations import SourceSpan

K = "432f16a5749cc40dc9233545b66d8e98f5288efc"
PATH = "ydb/docs/en/core/security/authentication.md"
QUOTE = "login and password in `bind_dn` or `bind_password`, or a certificate"
PROBLEM = "The two required credentials became alternatives."
FIX = "Use `bind_dn` and `bind_password`; keep the certificate alternative."


def sample(monkeypatch, text=None):
    if text is None:
        paragraph = (Path(__file__).parents[1] / "fixtures/final-review-block-coverage/53007.en.md").read_text()
        text = "\n" * 103 + paragraph
    candidate = FinalCandidate(K, "b" * 40, (PATH,), ())
    plan = prepare_review_document(candidate, AuthoritativeDocument("ydb/docs/ru/a.md", "source", text.encode()), PATH, text.encode())
    issue = CriticIssueOut(segment_id=plan.units[-1].id, severity="warning", category="translation_quality", comment=PROBLEM, suggested_text=FIX)
    response = CriticResponse(verdict="warnings", issues=[issue])
    fr = FileTranslationResult(PATH, "untrusted worktree", 1, "warnings", "v1", critic_unresolved=response)
    fr.final_review_plan = plan
    fr.final_review_response = response
    fr.segment_excerpts[issue.segment_id] = QUOTE if QUOTE in text else plan.units[-1].en_text
    fr.segment_lines[issue.segment_id] = (104, 104)
    pair = DocPair(ru_path="ydb/docs/ru/a.md", en_path=PATH, ru_changed=True)
    result = PRTranslationResult(pair_results=[PairRunResult(PairPlan(pair=pair, action="translate_to_en", source_path=pair.ru_path, target_path=PATH, source_lang="ru", target_lang="en", summary="changed"), target_text=text, file_result=fr)])
    result.final_candidate = candidate
    result.candidate_repo_path = "/frozen-reader"
    # Mock only immutable Git I/O; assertions bind every read to K and its exact path.
    def read(repo, actual, path):
        assert repo == "/frozen-reader" and actual == candidate and path == PATH
        return text.encode()
    from ydbdoc_review.pipeline import final_candidate
    monkeypatch.setattr(final_candidate, "read_candidate_bytes", read)
    return result, fr


def body(result, **kwargs):
    return builder.build_translation_pr_body(53007, "ydb-platform/ydb", publication_result=result, **kwargs)


def test_exact_53007_yellow_line104_searchable_quote(monkeypatch):
    result, fr = sample(monkeypatch)
    rendered = body(result)
    assert rendered.startswith("YELLOW")
    assert f"/blob/{K}/{PATH}#L104)" in rendered
    assert "строка 104" in rendered and f"EN: «{QUOTE}»" in rendered
    assert PROBLEM in rendered and FIX in rendered
    assert "YELLOW." in rendered and fr.final_text == "untrusted worktree"


def test_shifted_final_location_ignores_prefinal_hint(monkeypatch):
    result, _ = sample(monkeypatch, "\n" * 109 + QUOTE + "\n")
    rendered = body(result)
    assert "строка 110" in rendered and "#L110)" in rendered
    assert "#L104" not in rendered


@pytest.mark.parametrize("damage", ["sha", "path", "block", "span", "quote", "problem", "correction", "missing", "incomplete"])
def test_unproved_evidence_is_explicit_red(monkeypatch, damage):
    result, fr = sample(monkeypatch)
    plan = fr.final_review_plan
    if damage == "sha": fr.final_review_plan = replace(plan, candidate=replace(plan.candidate, commit_sha="c" * 40))
    if damage == "path": fr.final_review_plan = replace(plan, en=replace(plan.en, path="other.md"))
    if damage == "block": fr.final_review_response.issues[0].segment_id = "unknown"
    if damage == "span": fr.final_review_plan = replace(plan, en=replace(plan.en, blocks=(replace(plan.en.blocks[0], span=SourceSpan(0, 999999)),)))
    if damage == "quote": fr.segment_excerpts[plan.units[0].id] = "RU or suggested text only"
    if damage == "problem": fr.final_review_response.issues[0].comment = ""
    if damage == "correction": fr.final_review_response.issues[0].suggested_text = None
    if damage == "missing": result.final_candidate = None
    if damage == "incomplete": fr.final_review_response._review_incomplete = True
    rendered = body(result)
    assert rendered.startswith("RED")
    assert "невозможно подтвердить evidence отчёта" in rendered
    assert K in rendered and PATH in rendered
    if damage != "incomplete":
        assert "EN-строка недоступна" in rendered and "#L" not in rendered
    assert result.publication_impact.value == "PUBLISH_NORMAL"


def test_repeated_excerpt_inside_block_requires_unambiguous_hint(monkeypatch):
    result, fr = sample(monkeypatch, "same\nsame\n")
    fr.segment_excerpts[fr.final_review_plan.units[0].id] = "same"
    assert body(result).startswith("RED")
    fr.segment_lines[fr.final_review_plan.units[0].id] = (2, 2)
    rendered = body(result)
    assert rendered.startswith("YELLOW") and "#L2)" in rendered


def test_repeated_blocks_resolve_by_stable_unit(monkeypatch):
    result, fr = sample(monkeypatch, "same\n\nsame\n")
    fr.segment_lines.clear()
    rendered = body(result)
    assert rendered.startswith("YELLOW") and "#L3)" in rendered


def test_crlf_multiline_quote_keeps_raw_newlines_and_visible_range(monkeypatch):
    result, _ = sample(monkeypatch, "\r\nalpha\r\nbeta\r\n")
    rendered = body(result)
    assert "строки 2-3" in rendered and "#L2-L3)" in rendered
    assert "EN: «alpha\r\nbeta\r\n»" in rendered


def test_two_problems_on_same_line_are_not_deduplicated(monkeypatch):
    result, fr = sample(monkeypatch)
    fr.final_review_response.issues.append(fr.final_review_response.issues[0].model_copy(update={"comment": "Second problem", "suggested_text": "Second correction"}))
    rendered = body(result)
    assert PROBLEM in rendered and "Second problem" in rendered and "Second correction" in rendered


def test_surfaces_share_status_candidate_items_and_are_readonly(monkeypatch):
    result, fr = sample(monkeypatch)
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    meta = builder.ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1, checkout_ref=K)
    before = repr(result)
    full = builder.build_full_report(result, meta=meta, config=cfg, link=ReportLinkContext("ydb-platform/ydb", K))
    handoff = builder.build_source_pr_comment(result, translation_pr_number=42, meta=meta, config=cfg)
    for rendered in (body(result), full, handoff):
        assert rendered.startswith("YELLOW") and K in rendered
    for rendered in (body(result), full):
        assert QUOTE in rendered and PROBLEM in rendered and FIX in rendered and "#L104)" in rendered
    assert "body" in handoff and "#42" in handoff
    assert repr(result) == before


@pytest.mark.parametrize("ref", ["main", "c" * 40])
def test_full_report_rejects_conflicting_link_and_checkout_sha(monkeypatch, ref):
    result, _ = sample(monkeypatch)
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    rendered = builder.build_full_report(result, meta=builder.ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1, checkout_ref=K), config=cfg, link=ReportLinkContext("ydb-platform/ydb", ref))
    assert rendered.startswith("RED") and "SHA" in rendered and "#L" not in rendered


def test_completed_clean_is_green_but_legacy_input_stays_legacy(monkeypatch):
    result, fr = sample(monkeypatch)
    fr.final_review_response.issues.clear()
    fr.final_review_response.verdict = "ok"
    fr.verdict = "ok"
    assert body(result).startswith("GREEN")
    assert "проблем, требующих исправления, нет" in body(result)
    result.final_candidate = None
    fr.final_review_plan = None
    fr.final_review_response = None
    legacy = body(result)
    assert legacy.startswith("Auto-generated translation") and "QA K:" in legacy
