from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from verify_pr_fixture import FixtureError, verify_fixture  # noqa: E402


FIXTURE = ROOT / "fixtures" / "pr-45949"


def refresh_checksum(root: Path, relative: str) -> None:
    path = root / relative
    checksums_path = root / "checksums.json"
    checksums = json.loads(checksums_path.read_text())
    checksums["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    checksums_path.write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n")


def mutate_http(root: Path, relative: str, transform) -> None:
    path = root / relative
    raw = base64.b64decode(b"".join(path.read_bytes().split()), validate=True)
    path.write_bytes(base64.b64encode(transform(raw)) + b"\n")
    refresh_checksum(root, relative)


class FixtureVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "fixture"
        shutil.copytree(FIXTURE, self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def assert_killed(self) -> None:
        with self.assertRaises(FixtureError):
            verify_fixture(self.root)

    def test_frozen_fixture_passes(self) -> None:
        verify_fixture(self.root)
        requests = json.loads((self.root / "requests.json").read_text())
        self.assertEqual(40, len(requests))
        self.assertEqual(40, len(list((self.root / "raw" / "lookups").glob("*.http.b64"))))
        self.assertEqual(24, len(list((self.root / "blobs").glob("*.b64"))))

    def test_capture_tool_substitution_is_killed(self) -> None:
        path = self.root / "manifest.json"
        value = json.loads(path.read_text())
        value["capture"]["github_cli"] = "malicious 0.0"
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "manifest.json")
        self.assert_killed()

    def test_capture_time_substitution_is_killed(self) -> None:
        path = self.root / "manifest.json"
        value = json.loads(path.read_text())
        value["capture"]["started_at_utc"] = "1900-01-01T00:00:00Z"
        value["capture"]["completed_at_utc"] = "1900-01-01T00:00:01Z"
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "manifest.json")
        self.assert_killed()

    def test_display_provenance_substitution_is_killed(self) -> None:
        path = self.root / "PROVENANCE.md"
        path.write_text("fabricated provenance\n")
        refresh_checksum(self.root, "PROVENANCE.md")
        self.assert_killed()

    def test_cookie_header_injection_is_killed(self) -> None:
        def inject(raw: bytes) -> bytes:
            return raw.replace(b"Content-Type:", b"Cookie: session=SECRET\r\nContent-Type:", 1)
        mutate_http(self.root, "raw/pr.http.b64", inject)
        self.assert_killed()

    def test_each_credential_header_family_is_killed(self) -> None:
        headers = (
            b"Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            b"Proxy-Authorization: Basic abcdefghijklmnop",
            b"Cookie: session=abcdefghijklmnopqrstuvwxyz",
            b"Set-Cookie: session=abcdefghijklmnopqrstuvwxyz",
            b"X-Api-Key: abcdefghijklmnopqrstuvwxyz",
            b"X-Auth-Token: abcdefghijklmnopqrstuvwxyz",
        )
        for injected in headers:
            with self.subTest(header=injected):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory) / "fixture"
                    shutil.copytree(FIXTURE, root)
                    def transform(raw: bytes, line: bytes = injected) -> bytes:
                        return raw.replace(b"Content-Type:", line + b"\r\nContent-Type:", 1)
                    mutate_http(root, "raw/pr.http.b64", transform)
                    with self.assertRaises(FixtureError):
                        verify_fixture(root)

    def test_lookup_response_swap_is_killed_after_checksum_refresh(self) -> None:
        first = "raw/lookups/lookup-001.http.b64"
        second = "raw/lookups/lookup-002.http.b64"
        a, b = (self.root / first).read_bytes(), (self.root / second).read_bytes()
        (self.root / first).write_bytes(b)
        (self.root / second).write_bytes(a)
        refresh_checksum(self.root, first)
        refresh_checksum(self.root, second)
        self.assert_killed()

    def test_two_404_response_swap_is_killed_by_provenance_root(self) -> None:
        inventory = json.loads((self.root / "inventory.json").read_text())
        absent = [row["response_artifact"] for row in inventory["lookups"] if not row["present"]]
        self.assertGreaterEqual(len(absent), 2)
        first, second = absent[:2]
        a, b = (self.root / first).read_bytes(), (self.root / second).read_bytes()
        (self.root / first).write_bytes(b)
        (self.root / second).write_bytes(a)
        refresh_checksum(self.root, first)
        refresh_checksum(self.root, second)
        self.assert_killed()

    def test_404_replaced_by_200_is_killed(self) -> None:
        inventory = json.loads((self.root / "inventory.json").read_text())
        missing = next(row["response_artifact"] for row in inventory["lookups"] if not row["present"])
        present = next(row["response_artifact"] for row in inventory["lookups"] if row["present"])
        (self.root / missing).write_bytes((self.root / present).read_bytes())
        refresh_checksum(self.root, missing)
        self.assert_killed()

    def test_deleted_raw_lookup_is_killed_even_if_checksum_entry_removed(self) -> None:
        relative = "raw/lookups/lookup-040.http.b64"
        (self.root / relative).unlink()
        checksums_path = self.root / "checksums.json"
        checksums = json.loads(checksums_path.read_text())
        del checksums["files"][relative]
        checksums_path.write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n")
        self.assert_killed()

    def test_unknown_manifest_field_is_killed(self) -> None:
        path = self.root / "manifest.json"
        value = json.loads(path.read_text())
        value["unexpected"] = True
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "manifest.json")
        self.assert_killed()

    def test_duplicate_manifest_key_is_killed(self) -> None:
        path = self.root / "manifest.json"
        raw = path.read_text()
        path.write_text(raw.replace("{", '{"schema_version":"shadow",', 1))
        refresh_checksum(self.root, "manifest.json")
        self.assert_killed()

    def test_unknown_lookup_body_field_is_killed(self) -> None:
        def inject(raw: bytes) -> bytes:
            sep = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
            head, body = raw.split(sep, 1)
            value = json.loads(body)
            value["unexpected"] = True
            return head + sep + json.dumps(value, separators=(",", ":")).encode()
        mutate_http(self.root, "raw/lookups/lookup-001.http.b64", inject)
        self.assert_killed()

    def test_inventory_authored_claim_is_killed(self) -> None:
        path = self.root / "inventory.json"
        value = json.loads(path.read_text())
        value["lookups"][0]["blob_sha"] = value["lookups"][1]["blob_sha"]
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "inventory.json")
        self.assert_killed()

    def test_path_traversal_is_killed(self) -> None:
        path = self.root / "requests.json"
        value = json.loads(path.read_text())
        value[0]["path"] = "../secret"
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "requests.json")
        self.assert_killed()

    def test_secret_query_is_killed(self) -> None:
        path = self.root / "requests.json"
        value = json.loads(path.read_text())
        value[0]["endpoint"] += "&token=SECRET"
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "requests.json")
        self.assert_killed()

    def test_userinfo_endpoint_is_killed(self) -> None:
        path = self.root / "requests.json"
        value = json.loads(path.read_text())
        value[0]["endpoint"] = "https://user:secret@api.github.com/repos/ydb-platform/ydb"
        path.write_text(json.dumps(value, indent=2) + "\n")
        refresh_checksum(self.root, "requests.json")
        self.assert_killed()

    def test_blob_swap_is_killed_after_checksum_refresh(self) -> None:
        blobs = sorted((self.root / "blobs").glob("*.b64"))
        first, second = blobs[:2]
        first.write_bytes(second.read_bytes())
        refresh_checksum(self.root, first.relative_to(self.root).as_posix())
        self.assert_killed()

    def test_raw_date_substitution_is_killed(self) -> None:
        def replace_date(raw: bytes) -> bytes:
            return raw.replace(b"Fri, 28 Aug 2026", b"Fri, 21 Aug 2026", 1)
        mutate_http(self.root, "raw/pr.http.b64", replace_date)
        self.assert_killed()

    def test_raw_request_id_deletion_is_killed(self) -> None:
        def delete_request_id(raw: bytes) -> bytes:
            lines = raw.splitlines(keepends=True)
            return b"".join(line for line in lines if not line.lower().startswith(b"x-github-request-id:"))
        mutate_http(self.root, "raw/pr.http.b64", delete_request_id)
        self.assert_killed()


if __name__ == "__main__":
    unittest.main()
