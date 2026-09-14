"""One read-only, validated projection of Task 3 evidence onto immutable K."""
from dataclasses import dataclass, replace
import re
import subprocess

from ydbdoc_review.pipeline import final_candidate
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.translation.review_blocks import validate_payloads


@dataclass(frozen=True)
class CandidateReport:
    candidate_sha: str
    status: str
    items: tuple[str, ...]

    def render(self, *, summary: bool = False) -> str:
        header = f"{self.status}. Candidate K: `{self.candidate_sha}`\n\n"
        if summary:
            return header
        if self.items:
            return header + "\n\n".join(self.items) + "\n\n"
        if self.status == "GREEN":
            return header + "Проверка завершена: проблем, требующих исправления, нет.\n\n"
        return header


def legacy_without_final(result: PRTranslationResult) -> PRTranslationResult:
    """A rendering copy; never change verdicts or publication policy on the input."""
    runs = []
    for run in result.pair_results:
        fr = run.file_result
        if fr and (fr.final_review_plan is not None or fr.final_review_response is not None):
            fr = replace(fr, critic_initial=None, critic_unresolved=None,
                         critic_applied=[], critic_skipped=[], final_review_plan=None,
                         final_review_response=None, verdict="ok")
        runs.append(replace(run, file_result=fr))
    return replace(result, pair_results=runs, final_candidate=None, candidate_repo_path=None)


def project_candidate_report(result: PRTranslationResult, *, link: ReportLinkContext | None = None,
                             refs: tuple[str | None, ...] = (), independent_status: str = "GREEN",
                             ) -> CandidateReport | None:
    candidate = result.final_candidate
    files = [r for r in result.pair_results if r.file_result is not None
             and (r.file_result.final_review_plan is not None or r.file_result.final_review_response is not None
                  or (candidate is not None and not r.skipped and not r.deleted
                      and r.plan.target_path in candidate.en_paths))]
    if candidate is None and not files:
        return None
    sha = candidate.commit_sha if candidate else next(
        (r.file_result.final_review_plan.candidate.commit_sha for r in files
         if r.file_result.final_review_plan is not None), "недоступен")
    items = []
    integrity = False
    warning = False
    repo = link.github_repo if link and link.github_repo else "ydb-platform/ydb"

    def fail(path, reason, problem=""):
        nonlocal integrity
        integrity = True
        items.append(f"RED: невозможно подтвердить evidence отчёта. `{path}`, K `{sha}`. "
                     f"EN-строка недоступна.\nПроблема: {problem or reason}\n"
                     f"Недоступное evidence: {reason}.\n"
                     "Ожидаемое исправление: получить полное semantic evidence для exact K и повторить отчёт.")

    supplied_refs = (*refs, link.ref if link else None, result.publication_candidate_sha)
    global_error = None
    if candidate is None or not result.candidate_repo_path:
        global_error = "missing immutable candidate reader"
    elif not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) or any(ref is not None and ref != sha for ref in supplied_refs):
        global_error = "SHA mismatch: report references must equal full K"
    if not files:
        fail(", ".join(candidate.en_paths) if candidate else "EN path unavailable", global_error or "missing completed semantic review")
    for run in files:
        fr = run.file_result
        plan, response = fr.final_review_plan, fr.final_review_response
        path = run.plan.target_path
        active = [i for i in response.issues if i.severity != "info"] if response else []
        try:
            if global_error:
                raise ValueError(global_error)
            if plan is None or response is None:
                raise ValueError("missing review plan or response")
            if plan.candidate != candidate:
                raise ValueError("SHA mismatch: reviewed candidate differs from K")
            if path != plan.en.path or path not in candidate.en_paths:
                raise ValueError("EN path does not belong to reviewed candidate")
            raw = final_candidate.read_candidate_bytes(result.candidate_repo_path, candidate, path)
            if raw is None or raw != plan.en.text.encode("utf-8"):
                raise ValueError("immutable K bytes differ from reviewed EN evidence")
            manifest = validate_payloads(plan, (plan.units,))
            if not manifest.complete:
                raise ValueError("invalid block inventory/span or review payload")
        except (ValueError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
            for issue in active or (None,):
                fail(path, str(exc), issue.comment if issue else "")
            continue
        if response._review_incomplete or (response.verdict != "ok" and not response.issues):
            fail(path, "semantic review did not complete")
        units = {u.id: u for u in plan.units}
        blocks = {b.id: b for b in plan.en.blocks}
        text = raw.decode("utf-8")
        for issue in active:
            try:
                if not issue.comment.strip() or not (issue.suggested_text or "").strip():
                    raise ValueError("missing problem explanation or expected correction")
                unit = units.get(issue.segment_id)
                if unit is None:
                    raise ValueError("unknown stable review unit/block ID")
                trusted = [blocks[i] for i in unit.en_block_ids]
                start, end = trusted[0].span.start, trusted[-1].span.end
                if text[start:end] != unit.en_text or not unit.en_text.strip():
                    raise ValueError("missing or invalid raw EN block evidence")
                excerpt = fr.segment_excerpts.get(unit.id, unit.en_text)
                if not excerpt or not excerpt.strip():
                    raise ValueError("empty EN citation")
                occurrences = [start + m.start() for m in re.finditer(f"(?={re.escape(excerpt)})", text[start:end])]
                if len(occurrences) > 1:
                    hint = fr.segment_lines.get(unit.id)
                    if hint:
                        occurrences = [offset for offset in occurrences if
                            hint[0] <= text.count("\n", 0, offset) + 1 and
                            text.count("\n", 0, offset + len(excerpt.rstrip("\r\n"))) + 1 <= hint[1]]
                if len(occurrences) != 1:
                    raise ValueError("missing or ambiguous EN citation within trusted block")
                offset = occurrences[0]
                first = text.count("\n", 0, offset) + 1
                last = text.count("\n", 0, offset + len(excerpt.rstrip("\r\n"))) + 1
                label = f"строка {first}" if first == last else f"строки {first}-{last}"
                anchor = f"#L{first}" if first == last else f"#L{first}-L{last}"
                quote = text[offset:offset + min(120, len(excerpt))]
                warning = True
                items.append(f"YELLOW. `{path}`, [{label}](https://github.com/{repo}/blob/{sha}/{path}{anchor}), K `{sha}`\n"
                             f"EN: «{quote}»\nПроблема: {issue.comment}\n"
                             f"Ожидаемое исправление: {issue.suggested_text}")
            except (ValueError, KeyError, IndexError) as exc:
                fail(path, str(exc), issue.comment)
    status = "RED" if integrity or independent_status == "RED" else "YELLOW" if warning or independent_status == "YELLOW" else "GREEN"
    return CandidateReport(sha, status, tuple(items))
