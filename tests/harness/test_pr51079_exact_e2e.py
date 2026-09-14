"""Offline replay of immutable upstream PR 51079 translation authorities."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import replace
from functools import cache, lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.pr import load_pair_contents
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.github.workflow import (
    _apply_results_to_disk,
    _attach_source_coverage_plans,
    _defer_proven_outbound_fragments,
    _enforce_report_checkout_bytes,
    _recheck_deferred_outbound_fragments,
    _reconcile_final_en_same_fragment_paths_after_apply,
    _repair_en_fragments_after_apply,
)
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.navigation.scope_planner import (
    doc_pairs_from_plan,
    navigation_pairs_from_plan,
    plan_translation_scope,
)
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.navigation_merge import run_navigation_merges
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import PRTranslationResult, PublicationImpact
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.editorial import check_en_editorial
from ydbdoc_review.validation.en_link_targets import apply_en_link_target_checks
from ydbdoc_review.validation.final_language import (
    apply_final_en_language_gate,
    check_final_en_language,
)
from ydbdoc_review.validation.toc_targets import apply_orphan_toc_page_checks

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pr51079-exact"
SOURCE_PATHS = (
    "ydb/docs/ru/core/reference/configuration/auth_config.md",
    "ydb/docs/ru/core/security/authentication.md",
)
EXPECTED_MD = (
    "ydb/docs/en/core/concepts/glossary.md",
    "ydb/docs/en/core/reference/configuration/auth_config.md",
    "ydb/docs/en/core/security/_assets/user-token-lifecycle.md",
    "ydb/docs/en/core/security/_assets/user-token.md",
    "ydb/docs/en/core/security/authentication.md",
    "ydb/docs/en/core/security/caching-authentication-results.md",
)
EXPECTED_TOC = "ydb/docs/en/core/security/toc_p.yaml"
EXPECTED_OUTPUT = (*EXPECTED_MD, EXPECTED_TOC)
pytestmark = pytest.mark.timeout(180)


def _install_offline_external_read_adapters(monkeypatch):
    from ydbdoc_review.github import git_ops
    from ydbdoc_review.validation.wikipedia_links import WikipediaResolver

    def langlink(self, source_lang, title, target_lang):
        return {
            ("ru", "LDAP", "en"): "Lightweight_Directory_Access_Protocol",
            ("ru", "Argon2", "en"): "Argon2",
        }[(source_lang, title, target_lang)]

    monkeypatch.setattr(WikipediaResolver, "_fetch_langlink", langlink)
    audit = SimpleNamespace(snapshots=_load_fixture().snapshots, refs={}, reads=set(), unknown=set())
    original_popen = subprocess.Popen

    def admit(repo, ref, path):
        key = (str(Path(repo).resolve()), ref, path)
        audit.reads.add(key)
        snapshot = audit.refs.get(key[:2])
        # The real upstream helper retries origin/<full SHA> after an absent
        # path. Admit only that registered authority's explicit null records.
        remote_spelling = re.fullmatch(r"(?:origin/|refs/remotes/origin/)([0-9a-f]{40})", ref)
        if snapshot is None and remote_spelling:
            fallback = audit.refs.get((key[0], remote_spelling[1]))
            if fallback is not None and path in audit.snapshots[fallback]:
                if audit.snapshots[fallback][path] is None:
                    probe = subprocess.run(
                        ["git", "-C", key[0], "rev-parse", "--verify", ref], capture_output=True,
                    )
                    if probe.returncode != 0:
                        return
        if snapshot is None or path not in audit.snapshots[snapshot]:
            audit.unknown.add(key)
            raise AssertionError(f"Unrecorded Git context read: {key}")

    def audited_popen(command, *args, **kwargs):
        # Guard below run/check_output and all pre-imported Git reader aliases.
        if isinstance(command, (list, tuple)) and Path(str(command[0])).name == "git":
            command = [str(arg) for arg in command]
            repo = kwargs.get("cwd", Path.cwd())
            index = 1
            while command[index] in {"-C", "-c"}:
                if command[index] == "-C":
                    repo = command[index + 1]
                index += 2
            operation = command[index]
            operands = command[index + 1:]
            def unsupported():
                audit.unknown.add((str(Path(repo).resolve()), "unsupported", repr(command)))
                raise AssertionError(f"Unsupported Git context read: {command}")

            if operation in {"show", "cat-file"}:
                specs = [arg for arg in operands if ":" in arg and not arg.startswith("-")]
                if not specs and "-s" not in operands and "--no-patch" not in operands:
                    unsupported()
                for spec in specs:
                    ref, path = spec.split(":", 1)
                    admit(repo, ref, path)
            elif operation == "ls-tree" and "--" in operands:
                separator = operands.index("--")
                for path in operands[separator + 1:]:
                    admit(repo, operands[separator - 1], path)
            else:
                if operation not in {
                    "init", "config", "add", "commit", "rev-parse", "rev-list",
                    "diff", "merge-base", "ls-files", "ls-tree", "status", "log",
                    "describe", "symbolic-ref",
                }:
                    unsupported()
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", audited_popen)
    original_read = git_ops.read_text_at_upstream_tip

    @cache
    def read_known(repo, ref, path):
        admit(repo, ref, path)
        return original_read(repo, ref, path)

    monkeypatch.setattr(git_ops, "read_text_at_upstream_tip", read_known)
    return audit


@pytest.fixture(autouse=True)
def offline_external_read_adapters(monkeypatch):
    audit = _install_offline_external_read_adapters(monkeypatch)
    yield audit
    # Callers may catch reader errors. Such reads must still fail the test.
    assert not audit.unknown, sorted(audit.unknown)


def _snapshot_reader(snapshots, snapshot_name):
    def read(path):
        assert snapshot_name in snapshots, f"Unknown snapshot: {snapshot_name}"
        assert path in snapshots[snapshot_name], (snapshot_name, path)
        return snapshots[snapshot_name][path]

    return read


def _load_fixture():
    assert (FIXTURE / "manifest.json").is_file(), "Checked-in exact fixture is missing"
    manifest = json.loads((FIXTURE / "manifest.json").read_bytes())
    assert set(manifest["artifact_sha256"]) == {
        "snapshots.json", "source-pr-files.json", "model-translations.json",
    }
    for filename, digest in manifest["artifact_sha256"].items():
        assert hashlib.sha256((FIXTURE / filename).read_bytes()).hexdigest() == digest
    snapshots = json.loads((FIXTURE / "snapshots.json").read_bytes())
    source_files = json.loads((FIXTURE / "source-pr-files.json").read_bytes())
    recorded = set()
    for entry in manifest["entries"]:
        key = (entry["snapshot"], entry["path"])
        assert key not in recorded, key
        recorded.add(key)
        value = _snapshot_reader(snapshots, key[0])(key[1])
        if entry.get("absent"):
            assert value is None
            assert "sha256" not in entry
        else:
            assert isinstance(value, str)
            assert hashlib.sha256(value.encode("utf-8")).hexdigest() == entry["sha256"]
    assert recorded == {(name, path) for name, paths in snapshots.items() for path in paths}
    source_paths = tuple(item["filename"] for item in source_files)
    assert source_paths == SOURCE_PATHS
    assert [item["status"] for item in source_files] == ["modified", "modified"]
    return SimpleNamespace(
        manifest=manifest,
        snapshots=snapshots,
        source_paths=source_paths,
        changes=[(item["filename"], item["status"]) for item in source_files],
        reader=lambda name: _snapshot_reader(snapshots, name),
    )


def test_pr51079_exact_fixture_identity_and_scope():
    case = _load_fixture()
    assert case.manifest["source_pr"] == 51079
    assert case.manifest["source_base"] == "0aa50f3ab4688eb53d04888eba9fb3c36968ff14"
    assert case.manifest["source_merge"] == "673b924813e35646535e20d917b014094bf7de14"
    assert case.manifest["baseline"] == "7886ff84e31c2f52c99adf8fbeb908fda38e4192"
    assert case.manifest["ru_sha"] == case.manifest["baseline"]
    assert case.manifest["ru_authority_kind"] == "current"
    assert case.manifest["defective_translation_pr"] == 52948
    assert case.manifest["defective_translation_head"] == "a1be7c4f2521da74f759a4a2277f9a9e442ae840"
    assert case.source_paths == SOURCE_PATHS
    scope = plan_translation_scope(
        case.changes,
        read_ru=case.reader("B"),
        read_en_base=case.reader("B"),
        read_ru_base=case.reader("H0"),
        docs_root="ydb/docs",
    )
    assert scope.doc_from_diff == frozenset(SOURCE_PATHS)
    assert scope.doc_ru_paths == frozenset(p.replace("/en/", "/ru/", 1) for p in EXPECTED_MD)
    assert scope.nav_ru_paths == frozenset({"ydb/docs/ru/core/security/toc_p.yaml"})


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@lru_cache(maxsize=8192)
def _read_at(repo: Path, sha: str, path: str) -> str | None:
    assert re.fullmatch(r"[0-9a-f]{40}", sha)
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{path}"], capture_output=True,
    )
    if result.returncode:
        assert not _git(repo, "ls-tree", sha, "--", path), result.stderr
        return None
    return result.stdout.decode("utf-8")


def _init_frozen_repo(case, tmp_path, audit):
    repo = tmp_path / "frozen"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "offline-fixture@example.invalid")
    _git(repo, "config", "user.name", "Offline fixture")
    _git(repo, "config", "commit.gpgsign", "false")
    shas = {}
    for name in ("H0", "H", "B"):
        for path, value in case.snapshots[name].items():
            local = repo / path
            if value is None:
                if local.is_file():
                    local.unlink()
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(value.encode("utf-8"))
        _git(repo, "add", ".")
        _git(repo, "commit", "--allow-empty", "-qm", name)
        shas[name] = _git(repo, "rev-parse", "HEAD")
        audit.refs[(str(repo.resolve()), shas[name])] = name
    assert tuple(_git(repo, "diff", "--name-only", shas["H0"], shas["H"]).splitlines()) == SOURCE_PATHS
    authority = RuAuthority(
        source_repo="ydb-platform/ydb", source_pr=51079,
        source_base_sha=shas["H0"], source_head_sha=shas["H"],
        baseline_sha=shas["B"], ru_sha=shas["B"], mode=RuAuthorityMode.CURRENT,
    )
    return repo, shas, authority


def _fake_model_client(case, *, refusal_path=None):
    oracle = json.loads((FIXTURE / "model-translations.json").read_bytes())
    translations = {(row["file_path"], row["source"]): row["english"] for row in oracle["segments"]}
    assert len(translations) == len(oracle["segments"])
    config = load_config(env={"YDBDOC_YC_FOLDER_ID": "offline", "YDBDOC_YC_API_KEY": "offline"})
    requested = []
    unknown_requests = []

    def completion(**kwargs):
        message = kwargs["messages"][-1]["content"]
        if "Spans JSON:" in message:
            path = message.splitlines()[0].removeprefix("File: ")
            payload = json.loads(message.split("Spans JSON:")[1])
            response = {"spans": []}
            for span in payload["spans"]:
                key = (path, span["text"])
                assert key in translations, key
                response["spans"].append({"id": span["id"], "text": translations[key]})
        elif message.startswith('{"labels":'):
            labels = json.loads(message)["labels"]
            response = {"translations": [{"ru": label, "en": oracle["menu_labels"][label]} for label in labels]}
        else:
            match = re.search(r"File: `([^`]+)`", message)
            assert match, message[:200]
            path = match[1]
            assert path.replace("/ru/", "/en/", 1) in EXPECTED_MD
            if message.startswith(("Review ", "Re-verify ")):
                response = {"verdict": "ok", "issues": []}
                if path == refusal_path:
                    response = "I cannot discuss this topic."
            else:
                start = re.search(r'\{\s*"segments"\s*:', message)
                assert start, message[:200]
                payload, _ = json.JSONDecoder().raw_decode(message[start.start():])
                response = {"segments": []}
                for segment in payload["segments"]:
                    key = (path, segment["text"])
                    assert key in translations, key
                    requested.append(key)
                    response["segments"].append({"id": segment["id"], "text": translations[key]})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=response if isinstance(response, str) else json.dumps(response),
            ))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    transport = MagicMock()

    def checked_completion(**kwargs):
        try:
            return completion(**kwargs)
        except (AssertionError, KeyError, ValueError) as exc:
            unknown_requests.append(str(exc))
            raise AssertionError(f"Unknown offline model request: {exc}") from exc

    transport.chat.completions.create.side_effect = checked_completion
    client = YandexLLMClient(folder_id="offline", api_key="offline", llm=config.llm, client=transport)
    client._offline_unknown_requests = unknown_requests
    return client, config, requested


def _run_exact_candidate(tmp_path, audit):
    case = _load_fixture()
    repo, shas, authority = _init_frozen_repo(case, tmp_path, audit)
    scope = plan_translation_scope(
        case.changes, read_ru=case.reader("B"), read_en_base=case.reader("B"),
        read_ru_base=case.reader("H0"), docs_root="ydb/docs",
    )
    contents = load_pair_contents(
        str(repo), doc_pairs_from_plan(scope), merge_base_with=shas["B"], authority=authority,
    )
    contents = _attach_source_coverage_plans(
        contents, scope_plan=scope, authority=authority, checkpoint=None, resume_parent_run_id=None,
    )
    client, config, requested = _fake_model_client(case)
    result = run_pr_translation(
        contents, client, load_glossary(), config=config,
        docs_text_reader=case.reader("B"), docs_repo_path=str(repo),
    )
    result.navigation_results = run_navigation_merges(
        navigation_pairs_from_plan(scope), repo_path=str(repo), merge_base_with=shas["B"],
        client=client, glossary=load_glossary(), config=config,
        scope_plan=scope, authority=authority, active_doc_ru_paths=scope.doc_ru_paths,
    )
    assert not client._offline_unknown_requests
    assert not [run.error for run in result.pair_results if run.error]
    assert not apply_orphan_toc_page_checks(result, repo_path=str(repo), baseline_ref=shas["B"])
    deferred = _defer_proven_outbound_fragments(
        result, repo_path=str(repo), baseline_ref=shas["B"],
    )
    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_NORMAL
    touched = _apply_results_to_disk(str(repo), result, dry_run=False)
    assert set(touched.written) == set(EXPECTED_OUTPUT)
    assert not touched.deleted
    _repair_en_fragments_after_apply(
        str(repo), touched.written, dry_run=False,
        merge_base_with=shas["B"], ru_content_ref=shas["B"],
        result=result,
    )
    _reconcile_final_en_same_fragment_paths_after_apply(
        str(repo), contents, result, touched.written, dry_run=False,
        merge_base_with=shas["B"], ru_content_ref=shas["B"],
    )

    def candidate_read(path):
        case.reader("B")(path)
        local = repo / path
        return local.read_bytes().decode() if local.is_file() else None

    assert not apply_en_link_target_checks(
        result, repo_path=str(repo), baseline_read=case.reader("B"), docs_read=candidate_read,
    )
    _recheck_deferred_outbound_fragments(
        result, deferred, read_final_docs=candidate_read, baseline_read_text=case.reader("B"),
    )
    assert not apply_final_en_language_gate(result, en_paths=EXPECTED_OUTPUT, read_text=candidate_read)
    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_NORMAL
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Accepted offline translation candidate")
    candidate_sha = _git(repo, "rev-parse", "HEAD")
    audit.refs[(str(repo.resolve()), candidate_sha)] = "B"
    assert not _enforce_report_checkout_bytes(str(repo), candidate_sha, result)
    for run in result.pair_results:
        committed = _read_at(repo, candidate_sha, run.plan.target_path)
        assert run.file_result is not None
        assert committed == run.target_text == run.file_result.final_text
    return SimpleNamespace(
        case=case, repo=repo, shas=shas, authority=authority, contents=contents,
        result=result, requested=requested, candidate_sha=candidate_sha, audit=audit,
    )


def _verify_local_candidate(candidate, sha, *, refusal_path=None):
    candidate.audit.refs[(str(candidate.repo.resolve()), sha)] = "B"
    client, config, _ = _fake_model_client(candidate.case, refusal_path=refusal_path)

    def read(path):
        candidate.case.reader("B")(path)
        return _read_at(candidate.repo, sha, path)

    ctx = HarnessContext.from_options(client, config=config, docs_text_reader=read)
    verified = PRTranslationResult()
    for content in candidate.contents:
        pair = content.pair
        plan = PairPlan(
            pair=pair, action="critic_only", source_path=pair.ru_path, target_path=pair.en_path,
            source_lang="ru", target_lang="en",
        )
        verify_content = replace(
            content, en_text=read(pair.en_path), force_full_overwrite=True,
        )
        verified.pair_results.append(run_pair_plan(verify_content, plan, ctx, {}))
    assert not client._offline_unknown_requests
    apply_en_link_target_checks(
        verified, repo_path=str(candidate.repo), baseline_read=candidate.case.reader("B"), docs_read=read,
    )
    apply_final_en_language_gate(verified, en_paths=EXPECTED_OUTPUT, read_text=read)
    _enforce_report_checkout_bytes(str(candidate.repo), sha, verified)
    meta = ReportMeta(mode="doc_verify", report_number=1, elapsed_s=0, checkout_ref=sha)
    assert meta.checkout_ref == sha
    report = build_full_report(verified, meta=meta, config=config)
    assert f"Checkout: `{sha[:12]}`" in report
    return verified, report


@pytest.fixture(scope="module")
def _exact_candidate_base(tmp_path_factory):
    with pytest.MonkeyPatch.context() as monkeypatch:
        audit = _install_offline_external_read_adapters(monkeypatch)
        candidate = _run_exact_candidate(tmp_path_factory.mktemp("pr51079-exact-base"), audit)
    assert not audit.unknown, sorted(audit.unknown)
    return candidate


@pytest.fixture
def exact_candidate(tmp_path, offline_external_read_adapters, _exact_candidate_base):
    repo = tmp_path / "frozen"
    shutil.copytree(_exact_candidate_base.repo, repo)
    candidate = copy.deepcopy(_exact_candidate_base)
    candidate.repo = repo
    candidate.case = _load_fixture()
    candidate.audit = offline_external_read_adapters
    resolved_repo = str(repo.resolve())
    for snapshot_name, sha in candidate.shas.items():
        candidate.audit.refs[(resolved_repo, sha)] = snapshot_name
    candidate.audit.refs[(resolved_repo, candidate.candidate_sha)] = "B"
    return candidate


def test_pr51079_exact_candidate_translates_all_six_markdown_and_toc(exact_candidate):
    candidate = exact_candidate
    assert {run.plan.target_path for run in candidate.result.pair_results} == set(EXPECTED_MD)
    assert {path for path, _ in candidate.requested} == {p.replace("/en/", "/ru/", 1) for p in EXPECTED_MD}
    for path in EXPECTED_OUTPUT:
        text = _read_at(candidate.repo, candidate.candidate_sha, path)
        assert text is not None
        assert check_final_en_language(text) == []
        assert check_en_editorial(text) == []
    authentication = _read_at(candidate.repo, candidate.candidate_sha, EXPECTED_MD[4])
    assert "Name=Value,...@<domain>" in authentication
    configuration = _read_at(candidate.repo, candidate.candidate_sha, EXPECTED_MD[1])
    for text in (authentication, configuration):
        assert "`ldaps` schema" not in text
        assert "[ section `use_tls`" not in text
        assert "[ using the `StartTls` request ]" not in text
    glossary = _read_at(candidate.repo, candidate.candidate_sha, EXPECTED_MD[0])
    assert glossary.count("{#user-token}") == 1
    unchanged, count = re.subn(r"\n### User token \{#user-token\}\n\n[^\n]+\n", "", glossary)
    assert count == 1
    assert unchanged == candidate.case.reader("B")(EXPECTED_MD[0])


def test_pr51079_exact_assets_are_reachable_through_real_include_chain(exact_candidate):
    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.segmentation.mermaid import mermaid_skeleton
    from ydbdoc_review.validation.fence_integrity import check_fence_body_copy

    candidate = exact_candidate
    assert not apply_orphan_toc_page_checks(
        candidate.result, repo_path=str(candidate.repo), baseline_ref=candidate.shas["B"],
    )
    for path in EXPECTED_MD[2:4]:
        source = candidate.case.reader("B")(path.replace("/en/", "/ru/", 1))
        target = _read_at(candidate.repo, candidate.candidate_sha, path)
        previous_fixture = FIXTURE.parent / "pr51079-mermaid" / f"{Path(path).stem}.ru.md"
        assert source.encode() == previous_fixture.read_bytes()
        expected_english = previous_fixture.with_name(f"{Path(path).stem}.en.md").read_bytes().decode()
        assert target.strip() == expected_english.strip()
        source_fence, = parse_markdown(source).children
        target_fence, = parse_markdown(target).children
        assert mermaid_skeleton(source_fence.content) == mermaid_skeleton(target_fence.content)
        assert not check_fence_body_copy(source, target)
    caching = next(run for run in candidate.result.pair_results if run.plan.target_path == EXPECTED_MD[5])
    original = caching.file_result.final_text
    assert original.count("{% include") == 2
    caching.file_result.final_text = "\n".join(line for line in original.splitlines() if "{% include" not in line)
    assert apply_orphan_toc_page_checks(
        candidate.result, repo_path=str(candidate.repo), baseline_ref=candidate.shas["B"],
    ) == list(EXPECTED_MD[2:4])


def _assert_qa_status(report, expected):
    assert report.count("## Статус QA (K):") == 1
    assert f"## Статус QA (K): {expected}" in report
    if expected == "🟢 GREEN":
        assert "## Статус QA (K): 🔴" not in report
        assert "## Статус QA (K): 🟡" not in report
    else:
        assert "## Статус QA (K): 🟢" not in report
    assert "Рекомендация:" not in report
    assert "можно мержить" not in report


def test_pr51079_defective_original_k_is_non_green(tmp_path, offline_external_read_adapters):
    case = _load_fixture()
    for path in EXPECTED_MD[2:5]:
        assert check_final_en_language(case.reader("K")(path)), path
    text = case.reader("K")(EXPECTED_MD[4])
    assert "Имя=Значение,...@<domain>" in text
    assert check_en_editorial(text)
    audit = offline_external_read_adapters
    repo, shas, authority = _init_frozen_repo(case, tmp_path, audit)
    scope = plan_translation_scope(
        case.changes, read_ru=case.reader("B"), read_en_base=case.reader("B"),
        read_ru_base=case.reader("H0"),
    )
    contents = load_pair_contents(
        str(repo), doc_pairs_from_plan(scope), merge_base_with=shas["B"], authority=authority,
    )
    for path in EXPECTED_OUTPUT:
        local = repo / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(case.reader("K")(path).encode())
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Immutable historical defective K replay")
    local_sha = _git(repo, "rev-parse", "HEAD")
    candidate = SimpleNamespace(case=case, repo=repo, contents=contents, audit=audit)
    verified, report = _verify_local_candidate(candidate, local_sha)
    assert f"Checkout: `{local_sha[:12]}`" in report
    assert local_sha != case.manifest["defective_translation_head"]
    assert {b.path for b in verified.final_tree_blockers if b.code == "en_language"} == set(EXPECTED_MD[2:5])
    _assert_qa_status(report, "🔴 RED")


@pytest.mark.parametrize("path,original,mutation,qa_status", [
    (EXPECTED_MD[3], "actor user as User", "actor user as Пользователь", "🔴 RED"),
    (EXPECTED_MD[2], "Note right of cache:", "Note right of cache: Русская метка ", "🔴 RED"),
    (EXPECTED_MD[4], "Name=Value,...@<domain>", "Имя=Значение,...@<domain>", "🔴 RED"),
    (EXPECTED_MD[4], "`ldaps` scheme", "`ldaps` schema", "🟡 YELLOW"),
    (EXPECTED_MD[4], "[section `use_tls`]", "[ section `use_tls` ]", "🟡 YELLOW"),
    (EXPECTED_MD[1], "[using the `StartTls` request]", "[ using the `StartTls` request ]", "🟡 YELLOW"),
    (EXPECTED_MD[4], None, None, "🔴 RED"),
])
def test_pr51079_single_regression_injection_is_non_green(exact_candidate, path, original, mutation, qa_status):
    candidate = exact_candidate
    accepted, accepted_report = _verify_local_candidate(candidate, candidate.candidate_sha)
    assert all(run.file_result and run.file_result.verdict == "ok" for run in accepted.pair_results)
    _assert_qa_status(accepted_report, "🟢 GREEN")
    if original is not None:
        text = (candidate.repo / path).read_bytes().decode()
        assert original in text
        mutated = text.replace(original, mutation, 1)
        assert mutated != text
        assert mutation in mutated
        assert (check_final_en_language(mutated) if "Русская" in mutation or "Пользователь" in mutation or "Имя=" in mutation else check_en_editorial(mutated))
        (candidate.repo / path).write_bytes(mutated.encode())
    _git(candidate.repo, "add", ".")
    _git(candidate.repo, "commit", "--allow-empty", "-qm", "Single regression K2")
    sha = _git(candidate.repo, "rev-parse", "HEAD")
    assert sha != candidate.candidate_sha
    verified, report = _verify_local_candidate(
        candidate, sha, refusal_path=path.replace("/en/", "/ru/", 1) if original is None else None,
    )
    affected = next(run.file_result for run in verified.pair_results if run.plan.target_path == path)
    assert affected is not None and affected.verdict != "ok"
    if original is None:
        assert affected.critic_unresolved is not None
        assert affected.critic_unresolved.verdict == "blocked"
        assert any(issue.category == "critic_model_refusal" for issue in affected.critic_unresolved.issues)
        assert affected.verdict == "blocked"
    _assert_qa_status(report, qa_status)


def test_pr51079_report_checks_exact_local_candidate_sha(exact_candidate):
    candidate = exact_candidate
    verified, report = _verify_local_candidate(candidate, candidate.candidate_sha)
    assert f"Checkout: `{candidate.candidate_sha[:12]}`" in report
    assert f"Checkout: `{candidate.shas['B'][:12]}`" not in report
    _assert_qa_status(report, "🟢 GREEN")
    assert not _enforce_report_checkout_bytes(str(candidate.repo), candidate.candidate_sha, verified)
    for run in verified.pair_results:
        assert run.target_text == _read_at(candidate.repo, candidate.candidate_sha, run.plan.target_path)
        assert run.file_result is not None
        assert run.file_result.final_text == run.target_text
        assert run.file_result.verdict == "ok"
    first = verified.pair_results[0]
    exact = first.target_text
    first.target_text += "\nUncommitted content.\n"
    assert _enforce_report_checkout_bytes(str(candidate.repo), candidate.candidate_sha, verified) == [EXPECTED_MD[0]]
    first.target_text = exact
    first.file_result.final_text += "\nUncommitted final content.\n"
    assert _enforce_report_checkout_bytes(str(candidate.repo), candidate.candidate_sha, verified) == [EXPECTED_MD[0]]
    _, config, _ = _fake_model_client(candidate.case)
    meta = ReportMeta(mode="doc_verify", report_number=1, elapsed_s=0, checkout_ref=candidate.candidate_sha)
    _assert_qa_status(build_full_report(verified, meta=meta, config=config), "🔴 RED")


def test_pr51079_snapshot_unknown_read_fails_closed():
    case = _load_fixture()
    with pytest.raises(AssertionError):
        case.reader("B")("ydb/docs/en/core/not-recorded.md")
    with pytest.raises(AssertionError):
        case.reader("unknown")(EXPECTED_MD[0])
    absent = next(path for path, value in case.snapshots["B"].items() if value is None)
    assert case.reader("B")(absent) is None
    client, _, _ = _fake_model_client(case)
    with pytest.raises(AssertionError):
        client._client.chat.completions.create(messages=[{"content": (
            f"File: `{SOURCE_PATHS[0]}`\n"
            '{"segments": [{"id": "s1", "text": "Unknown source must not fall back"}]}'
        )}])


def test_pr51079_e2e_never_calls_github_or_network(tmp_path, monkeypatch, offline_external_read_adapters):
    from ydbdoc_review.github.client import GitHubClient

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline fixture attempted GitHub access")

    monkeypatch.setattr(GitHubClient, "_request", forbidden)
    candidate = _run_exact_candidate(tmp_path, offline_external_read_adapters)
    _verify_local_candidate(candidate, candidate.candidate_sha)


def test_pr51079_all_git_context_reads_are_recorded(tmp_path, monkeypatch, offline_external_read_adapters):
    """A sparse checkout must not turn an unrecorded upstream file into absence."""
    original_run = subprocess.run
    recorded = _load_fixture().snapshots["B"]
    unknown = set()
    actual_reads = set()

    def audit_run(command, *args, **kwargs):
        if command[0] == "git":
            paths = [str(arg).split(":", 1)[1] for arg in command if ":ydb/docs/" in str(arg)]
            if "ls-tree" in command and "--" in command:
                paths.extend(command[command.index("--") + 1:])
            for path in paths:
                actual_reads.add(path)
                if path not in recorded:
                    unknown.add(path)
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", audit_run)
    candidate = _run_exact_candidate(tmp_path, offline_external_read_adapters)
    _verify_local_candidate(candidate, candidate.candidate_sha)
    assert "ydb/docs/ru/core/reference/configuration/auth_config.md" in actual_reads
    assert {
        "ydb/docs/ru/core/concepts/query_execution/execution_process.md",
        "ydb/docs/ru/core/reference/ydb-sdk/error_handling.md",
    }.isdisjoint(actual_reads)
    assert unknown == set()
    assert not candidate.audit.unknown


def test_pr51079_late_reconciliation_unknown_read_fails_closed(exact_candidate):
    candidate = exact_candidate
    en_path = EXPECTED_MD[4]
    local = candidate.repo / en_path
    repaired = local.read_bytes()
    historical_href = b"../reference/configuration/security_config.md#security-auth"
    stale_href = b"../reference/configuration/auth_config.md#security-auth"
    assert historical_href in repaired
    assert stale_href not in repaired
    local.write_bytes(repaired.replace(historical_href, stale_href, 1))
    path = "ydb/docs/ru/core/reference/configuration/auth_config.md"
    expected = candidate.audit.snapshots["B"].pop(path)
    assert expected is not None
    assert (candidate.repo / path).read_bytes() == expected.encode()
    try:
        with pytest.raises(AssertionError, match="Unrecorded Git context read"):
            _reconcile_final_en_same_fragment_paths_after_apply(
                str(candidate.repo), candidate.contents, candidate.result, EXPECTED_OUTPUT,
                dry_run=False, merge_base_with=candidate.shas["B"], ru_content_ref=candidate.shas["B"],
            )
        expected_key = (str(candidate.repo.resolve()), candidate.shas["B"], path)
        assert candidate.audit.unknown == {expected_key}
        # A caught reader exception remains auditable, not a fabricated None.
        candidate.audit.unknown.remove(expected_key)
    finally:
        candidate.audit.snapshots["B"][path] = expected
        local.write_bytes(repaired)
