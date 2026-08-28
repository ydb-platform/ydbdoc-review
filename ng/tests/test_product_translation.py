from __future__ import annotations

import re
import unittest
from pathlib import Path

from ydbdoc_review_ng.fixture import Pr45949Fixture
from ydbdoc_review_ng.pipeline import Blocked, Gates, Op, TranslationPipeline


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "fixtures" / "pr-45949"


class RecordingModel:
    def __init__(self, name: str, behavior):
        self.name = name
        self.behavior = behavior
        self.calls = []

    def invoke(self, role, request):
        self.calls.append((role, request))
        return self.behavior(role, request)


def translated(role, request):
    source = request["source_utf8"]
    if request["target_path"].endswith("toc_p.yaml"):
        names = iter([
            "System requirements", "CPU settings for production", "Storage capacity planning",
            "Versioning", "Maintenance", "Database node authentication and authorization",
            "Initial deployment", "Updating configuration", "Cluster maintenance",
            "Updating executable", "Managing a cluster in bridge mode",
            "Managing a cluster's disk subsystem", "Federated queries",
        ])
        source = re.sub(r"(?m)(name: ).*$", lambda match: match.group(1) + next(names), source)
    else:
        parts = re.split(r"(?ms)(^[ \t]*```+[^\n]*\n.*?^[ \t]*```+\s*$|^[ \t]*~~~+[^\n]*\n.*?^[ \t]*~~~+\s*$)", source)
        source = "".join(
            part if re.match(r"^[ \t]*(?:```|~~~)", part) else re.sub(r"[А-Яа-яЁё]+", "translated", part)
            for part in parts
        )
    return {"candidate_utf8": source}


def passes(role, request):
    return {"verdict": "PASS", "issues": []}


class ProductTranslationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = Pr45949Fixture(FIXTURE)
        self.translator = RecordingModel("translator-A", translated)
        self.critic = RecordingModel("critic-B", passes)
        self.pipeline = TranslationPipeline(self.translator, self.critic)
        self.gates = Gates("sintjuri", frozenset({"sintjuri"}), True, 10, 1000)

    def test_real_pr_45949_composes_exact_seven_file_overlay(self):
        result = self.pipeline.run(
            pr_number=45949,
            gates=self.gates,
            manifest=self.fixture.manifest,
            read_current_main=self.fixture.read,
        )
        operations = {(entry.path, entry.op) for entry in result.overlay}
        self.assertEqual(
            operations,
            {
                ("ydb/docs/en/core/devops/concepts/index.md", Op.WRITE),
                ("ydb/docs/en/core/devops/concepts/node-authorization.md", Op.WRITE),
                ("ydb/docs/en/core/devops/concepts/toc_p.yaml", Op.WRITE),
                ("ydb/docs/en/core/devops/deployment-options/manual/node-authorization.md", Op.DELETE),
                ("ydb/docs/en/core/devops/deployment-options/manual/toc_p.yaml", Op.WRITE),
                ("ydb/docs/en/core/maintenance/manual/dynamic-config.md", Op.WRITE),
                ("ydb/docs/en/core/reference/configuration/client_certificate_authorization.md", Op.WRITE),
            },
        )
        self.assertEqual(len(self.translator.calls), 6)
        self.assertEqual(len(self.critic.calls), 6)
        self.assertIn("Проверено операций исходного PR: 8", result.report)
        self.assertIn("Редирект", result.report)

    def test_critic_receives_actual_source_target_and_candidate_bytes(self):
        self.pipeline.run(
            pr_number=45949,
            gates=self.gates,
            manifest=self.fixture.manifest,
            read_current_main=self.fixture.read,
        )
        _, request = next(
            call for call in self.critic.calls
            if call[1]["target_path"].endswith("concepts/node-authorization.md")
        )
        self.assertEqual(request["source_utf8"].encode(), self.fixture.read(
            "ydb/docs/ru/core/devops/concepts/node-authorization.md"
        ))
        self.assertIsNone(request["current_target_utf8"])
        self.assertTrue(request["candidate_utf8"])

    def test_red_bundle_is_omitted_instead_of_returned(self):
        critic = RecordingModel("critic-B", lambda role, req: {
            "verdict": "BLOCKED", "issues": [{"file": req["target_path"], "line": 1, "message": "потерян текст"}]
        })
        pipeline = TranslationPipeline(self.translator, critic)
        with self.assertRaisesRegex(Blocked, "не опубликованы"):
            pipeline.run(
                pr_number=45949, gates=self.gates, manifest=self.fixture.manifest,
                read_current_main=self.fixture.read,
            )

    def test_repair_response_is_rechecked(self):
        attempts = {}
        def repair(role, request):
            path = request["target_path"]
            attempts[path] = attempts.get(path, 0) + 1
            if attempts[path] == 1:
                fixed = request["candidate_utf8"].replace("translated", "fixed", 1)
                return {"verdict": "BLOCKED", "candidate_utf8": fixed, "issues": ["кириллица"]}
            return {"verdict": "PASS", "issues": []}
        initial = RecordingModel("translator-A", translated)
        critic = RecordingModel("critic-B", repair)
        result = TranslationPipeline(initial, critic).run(
            pr_number=45949, gates=self.gates, manifest=self.fixture.manifest,
            read_current_main=self.fixture.read,
        )
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(len(critic.calls), 12)

    def test_gates_precede_model_calls(self):
        cases = [
            Gates("stranger", frozenset({"sintjuri"}), True, 0, 100),
            Gates("sintjuri", frozenset({"sintjuri"}), False, 0, 100),
            Gates("sintjuri", frozenset({"sintjuri"}), True, 100, 100),
        ]
        for gates in cases:
            with self.subTest(gates=gates), self.assertRaises(Blocked):
                self.pipeline.run(
                    pr_number=45949, gates=gates, manifest=self.fixture.manifest,
                    read_current_main=self.fixture.read,
                )
        self.assertEqual(self.translator.calls, [])
        self.assertEqual(self.critic.calls, [])

    def test_toc_or_redirect_inconsistency_blocks_whole_overlay(self):
        def bad_read(path):
            if path == "ydb/docs/redirects.yaml":
                return b"redirects: []\n"
            return self.fixture.read(path)
        with self.assertRaisesRegex(Blocked, "редирект"):
            self.pipeline.run(
                pr_number=45949, gates=self.gates, manifest=self.fixture.manifest,
                read_current_main=bad_read,
            )

    def test_empty_truncated_and_one_line_candidates_are_red_before_critic(self):
        for bad in ("", "English", "# English\n"):
            translator = RecordingModel("translator-A", lambda role, req, value=bad: {"candidate_utf8": value})
            critic = RecordingModel("critic-B", passes)
            with self.subTest(candidate=bad), self.assertRaises(Blocked):
                TranslationPipeline(translator, critic).run(
                    pr_number=45949, gates=self.gates, manifest=self.fixture.manifest,
                    read_current_main=self.fixture.read,
                )
            self.assertEqual(critic.calls, [])

    def test_markdown_structure_code_and_links_are_deterministic_red(self):
        source_path = "ydb/docs/ru/core/devops/concepts/node-authorization.md"
        source = self.fixture.read(source_path).decode()
        mutants = [
            source.replace("## Что нужно знать перед тем как начать\n", "", 1),
            source.replace("https://grpc.io/", "https://wrong.invalid/", 1),
            source.replace("    ```bash", "", 1),
        ]
        for mutant in mutants:
            with self.subTest(mutant=len(mutant)), self.assertRaises(Blocked):
                self.pipeline._validate_candidate(source_path, source_path.replace("/ru/", "/en/"), source.encode(), mutant.encode())

    def test_toc_only_allows_meaningful_name_values(self):
        path = "ydb/docs/ru/core/devops/concepts/toc_p.yaml"
        source = self.fixture.read(path)
        for replacement in ("name: English", "name: Translated title", "href: wrong.md"):
            candidate = translated("translator", {"source_utf8": source.decode(), "target_path": path})["candidate_utf8"]
            if replacement.startswith("name"):
                candidate = re.sub(r"(?m)name: .*$", replacement, candidate, count=1)
            else:
                candidate = re.sub(r"(?m)href: .*$", replacement, candidate, count=1)
            with self.subTest(replacement=replacement), self.assertRaises(Blocked):
                self.pipeline._validate_candidate(path, path.replace("/ru/", "/en/"), source, candidate.encode())

    def test_provider_exception_is_sanitized_for_every_model_role(self):
        secret = "Authorization: Bearer TOP_SECRET"
        exploding = RecordingModel("provider", lambda role, req: (_ for _ in ()).throw(RuntimeError(secret)))
        with self.assertRaises(Blocked) as caught:
            TranslationPipeline(exploding, self.critic).run(
                pr_number=45949, gates=self.gates, manifest=self.fixture.manifest,
                read_current_main=self.fixture.read,
            )
        self.assertNotIn("TOP_SECRET", str(caught.exception))

        with self.assertRaises(Blocked) as caught:
            TranslationPipeline(self.translator, exploding).run(
                pr_number=45949, gates=self.gates, manifest=self.fixture.manifest,
                read_current_main=self.fixture.read,
            )
        self.assertNotIn("TOP_SECRET", str(caught.exception))

    def test_toc_scalar_preserves_yaml_lexical_style_and_escaping(self):
        valid = [
            ("- name: 'Авторизация узлов'\r\n", "- name: 'Node authorization'\r\n"),
            ('- name: "Авторизация узлов" # note\n', '- name: "Node \\"authorization\\"" # note\n'),
            ("- name: !title &node Авторизация узлов\n", "- name: !title &node Node authorization\n"),
        ]
        for source, candidate in valid:
            with self.subTest(candidate=candidate):
                self.pipeline._validate_toc(source, candidate, "toc_p.yaml")

        invalid = [
            ("- name: Авторизация\n", "- name: Node: authorization\n"),
            ("- name: Авторизация\n", "- name: Node # authorization\n"),
            ("- name: Авторизация\n", "- name: true\n"),
            ("- name: 'Авторизация'\n", '- name: "Authorization"\n'),
            ('- name: "Авторизация"\n', '- name: "Node \\q auth"\n'),
            ("- name: !title &node Авторизация\n", "- name: !other &node Authorization\n"),
        ]
        for source, candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(Blocked):
                self.pipeline._validate_toc(source, candidate, "toc_p.yaml")

    def test_toc_comment_translates_content_but_preserves_lexical_frame(self):
        self.pipeline._validate_toc("  # Русский комментарий\r\n", "  # English comment\r\n", "toc_p.yaml")
        self.pipeline._validate_toc(
            "- name: 'Авторизация'  # Русский комментарий\r\n",
            "- name: 'Authorization'  # English comment\r\n",
            "toc_p.yaml",
        )
        for candidate in (" # English comment\r\n", "  #English comment\r\n", "  # English comment\n"):
            with self.subTest(candidate=candidate), self.assertRaises(Blocked):
                self.pipeline._validate_toc("  # Русский комментарий\r\n", candidate, "toc_p.yaml")

if __name__ == "__main__":
    unittest.main()
