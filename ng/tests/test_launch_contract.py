from __future__ import annotations

import unittest
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ydbdoc_review_ng.fixture import Pr45949Fixture
from ydbdoc_review_ng.launch import CORE_SHA, DryState, GitHubApi, OpenAiCompatibleModel, YdbState, execute
from ydbdoc_review_ng.state import ModelCallIdentity, ModelCallReservation, ModelState, RepoIdentity, schema_statements, new_claim_nonce
from ydbdoc_review_ng.pipeline import Blocked

from test_product_translation import FIXTURE, RecordingModel, passes, translated


class FakeGitHub:
    def __init__(self, fixture, old=None):
        self.fixture = fixture
        self.old = list(old or [])
        self.calls = []
        self.published = None
        self.comments = []

    def remove_label(self, pr, label): self.calls.append(("remove_label", pr, label))
    def pull(self, pr):
        self.calls.append(("pull", pr))
        return {"number": pr, "merged": True, "merge_commit_sha": "a" * 40, "base": {"sha": "b" * 40}, "head": {"sha": "c" * 40}}
    def files(self, pr): self.calls.append(("files", pr)); return self.fixture.manifest
    def main_sha(self): self.calls.append(("main",)); return "d" * 40
    def content(self, path, sha):
        self.calls.append(("content", path, sha))
        self.assert_sha = sha
        return self.fixture.read(path)
    def active_drafts(self, branch): self.calls.append(("drafts", branch)); return list(self.old)
    def close_pr(self, pr, comment): self.calls.append(("close", pr)); self.old = []
    def delete_branch(self, branch): self.calls.append(("delete_branch", branch))
    def publish(self, branch, base_sha, result, title):
        self.calls.append(("publish", branch, base_sha)); self.published = result; return 70001
    def report(self, pr, body): self.calls.append(("report", pr)); self.comments.append(body)


class RecordingState(DryState):
    def __init__(self, repository, clock, events):
        super().__init__(repository, clock)
        self.events = events

    def put_effect_checkpoints(self, receipt_identity, effects):
        self.events.append(("state", tuple((item.kind, item.state) for item in effects)))
        return super().put_effect_checkpoints(receipt_identity, effects)


class OrderingGitHub(FakeGitHub):
    def __init__(self, fixture, events):
        super().__init__(fixture)
        self.events = events

    def publish(self, branch, base_sha, result, title):
        self.events.append(("publish",))
        return super().publish(branch, base_sha, result, title)


class LaunchContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = Pr45949Fixture(FIXTURE)
        self.event = {
            "pull_request": {"number": 45949}, "sender": {"login": "sintjuri"},
            "delivery_id": "delivery-1", "payload_sha256": "e" * 64,
            "github_run_id": "101", "github_run_attempt": 1,
            "github_event_name": "pull_request_target", "label_timeline_event_id": 202,
        }

    def state(self):
        return DryState(RepoIdentity("ydb-platform", "ydb"), lambda: datetime(2026, 8, 28, tzinfo=timezone.utc))

    def test_production_composition_consumes_label_and_publishes_only_safe_bundle(self):
        github = FakeGitHub(self.fixture)
        state = self.state()
        result = execute(
            self.event, github, state, RecordingModel("translator-A", translated),
            RecordingModel("critic-B", passes), allowed=frozenset({"sintjuri"}), budget=100,
        )
        self.assertEqual(github.calls[0], ("remove_label", 45949, "doc_translate"))
        self.assertEqual(len(result.overlay), 7)
        self.assertIs(github.published, result)
        self.assertEqual(github.assert_sha, "d" * 40)
        self.assertIn(("publish", "ydbdoc-review/pr-45949", "d" * 40), github.calls)

    def test_repeated_translate_gates_then_closes_and_deletes_old_draft(self):
        github = FakeGitHub(self.fixture, old=[{"number": 60000, "draft": True}])
        execute(
            self.event, github, self.state(), RecordingModel("translator-A", translated),
            RecordingModel("critic-B", passes), allowed=frozenset({"sintjuri"}), budget=100,
        )
        self.assertLess(github.calls.index(("pull", 45949)), github.calls.index(("close", 60000)))
        self.assertLess(github.calls.index(("close", 60000)), github.calls.index(("delete_branch", "ydbdoc-review/pr-45949")))

    def test_publish_is_preceded_by_durable_push_and_create_intents(self):
        events = []
        github = OrderingGitHub(self.fixture, events)
        state = RecordingState(RepoIdentity("ydb-platform", "ydb"), lambda: datetime(2026, 8, 28, tzinfo=timezone.utc), events)
        execute(
            self.event, github, state, RecordingModel("translator-A", translated),
            RecordingModel("critic-B", passes), allowed=frozenset({"sintjuri"}), budget=100,
        )
        publish_at = events.index(("publish",))
        before = events[publish_at - 1][1]
        self.assertIn(("PUSH_BRANCH", "INTENT_RECORDED"), before)
        self.assertIn(("CREATE_DRAFT", "INTENT_RECORDED"), before)
        self.assertNotIn(("PUSH_BRANCH", "CONFIRMED"), before)
        after = events[publish_at + 1][1]
        self.assertIn(("PUSH_BRANCH", "CONFIRMED"), after)

    def test_rejected_actor_only_consumes_label_and_does_no_destructive_work(self):
        github = FakeGitHub(self.fixture, old=[{"number": 60000, "draft": True}])
        with self.assertRaises(Blocked):
            execute(
                self.event, github, self.state(), RecordingModel("translator-A", translated),
                RecordingModel("critic-B", passes), allowed=frozenset({"someone-else"}), budget=100,
            )
        self.assertEqual(github.calls, [("remove_label", 45949, "doc_translate"), ("pull", 45949)])

    def test_files_api_reads_every_page_without_using_link_claims(self):
        api = GitHubApi("owner/repo", "token", dry_run=True)
        pages = [[{"filename": f"ydb/docs/ru/{i}.md", "status": "modified"} for i in range(100)], [{"filename": "ydb/docs/ru/last.md", "status": "added"}]]
        calls = []
        def request(method, path, body=None):
            calls.append(path)
            return pages[len(calls) - 1], {}
        api._request = request
        files = api.files(45949)
        self.assertEqual(len(files), 101)
        self.assertEqual(calls, ["/pulls/45949/files?per_page=100&page=1", "/pulls/45949/files?per_page=100&page=2"])

    def test_github_dry_run_performs_no_mutating_request(self):
        api = GitHubApi("owner/repo", "token", dry_run=True)
        calls = []
        api._request = lambda *args, **kwargs: calls.append((args, kwargs))
        api.remove_label(1, "doc_translate")
        api.delete_branch("ydbdoc-review/pr-1")
        api.close_pr(2, "closed")
        api.report(1, "report")
        self.assertEqual(api.publish("branch", "a" * 40, object(), "title"), 0)
        self.assertEqual(calls, [])

    def test_model_adapter_calls_provider_once_and_records_unknown_billing(self):
        state = self.state()
        model = OpenAiCompatibleModel("translator-model", "https://provider.invalid/chat", "secret", state, 1, 100, "folder")
        model.run_id = "delivery"
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Authorization: secret")) as call:
            with self.assertRaises(RuntimeError) as caught:
                model.invoke("translator", {"source_utf8": "bytes"})
        self.assertEqual(call.call_count, 1)
        self.assertNotIn("secret", str(caught.exception))
        record = state.get_model_call(ModelCallIdentity("delivery:translator:1:translator-model"))
        self.assertEqual(record.state, ModelState.UNKNOWN_BILLED)

    def test_existing_model_reservation_never_dispatches(self):
        state = self.state()
        identity = ModelCallIdentity("delivery:translator:1:translator-model")
        state.reserve_model_call(ModelCallReservation(
            identity, new_claim_nonce(), "delivery", "delivery", "openai-compatible",
            "translator-model", "TRANSLATOR_A", 0, 1,
        ))
        model = OpenAiCompatibleModel("translator-model", "https://provider.invalid/chat", "secret", state, 1, 100, "folder")
        model.run_id = "delivery"
        with patch("urllib.request.urlopen") as call:
            with self.assertRaises(Blocked):
                model.invoke("translator", {"source_utf8": "bytes"})
        call.assert_not_called()

    def test_clean_wheel_installs_without_editable_source(self):
        uv = shutil.which("uv")
        if not uv:
            self.skipTest("uv is unavailable")
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory(prefix="ng-wheel-contract-") as directory:
            output, environment = Path(directory, "dist"), dict(os.environ)
            environment["UV_CACHE_DIR"] = "/private/tmp/ng-uv-cache"
            subprocess.run([uv, "build", "--wheel", "--out-dir", str(output)], cwd=root, env=environment, check=True, capture_output=True)
            wheel = next(output.glob("*.whl"))
            venv = Path(directory, "venv")
            subprocess.run([uv, "venv", "--python", sys.executable, str(venv)], env=environment, check=True, capture_output=True)
            python = venv / "bin" / "python"
            subprocess.run([uv, "pip", "install", "--python", str(python), str(wheel)], env=environment, check=True, capture_output=True)
            check = subprocess.run(
                [str(python), "-c", "import ydb; from ydbdoc_review_ng.state import YdbState,DryState; assert ydb.__version__ == '3.31.2'; assert ydb.iam.ServiceAccountCredentials"],
                cwd=directory, env=environment, check=True, capture_output=True, text=True,
            )
            self.assertEqual(check.stdout, "")

    def test_ydb_schema_is_exactly_four_minimal_tables(self):
        statements = schema_statements("m0_contract_test12")
        self.assertEqual(len(statements), 4)
        for table in YdbState.TABLES:
            self.assertTrue(any(table in sql for sql in statements))

    def test_workflow_uses_existing_repository_contract_and_pinned_core(self):
        workflow = (FIXTURE.parents[1] / ".github" / "workflows" / "doc-translate-ng.yml").read_text()
        self.assertEqual(CORE_SHA, "8c962d8ff5042286428038d6fe2d5c485c527dee")
        for name in (
            "GITHUB_TOKEN", "YDBDOC_ALLOWED_ACTORS", "YDBDOC_DAILY_BUDGET_RUB",
            "YDBDOC_YDB_ENDPOINT", "YDBDOC_YDB_DATABASE", "YDB_SA_KEY",
            "YANDEX_CLOUD_FOLDER_DOC_REVIEW", "YANDEX_CLOUD_API_KEY_DOC_REVIEW",
            "YDBDOC_MODEL_TRANSLATE", "YDBDOC_MODEL_CHECK",
        ):
            self.assertIn(name, workflow)
        self.assertIn("ref: v0.1.0", workflow)
        self.assertIn("Consume doc_translate label immediately", workflow)


if __name__ == "__main__":
    unittest.main()
