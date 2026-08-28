#!/usr/bin/env python3
"""Derive the closed PR 45949 provenance documents from authoritative captures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


DOMAIN = b"ydbdoc-review-ng/pr-45949/provenance-root/v2\0"
REPOSITORY = "ydb-platform/ydb"
PR_NUMBER = 45949
REFS = {
    "base": "7dcbddd3cc3906c12330b4b41dddd48da8ee1815",
    "head": "0da32bb95ae025681e74f98654262dc992fb0c35",
    "merge": "2f6dc867f5121b3c81ccc30707d95fb15916baf2",
    "current_main": "5cf1b7f85ab79c9a40b829ce23cbce0fbbe200e9",
}
SOURCE_FILES = [
    ("ydb/docs/redirects.yaml", "modified", "1c1cf59beefbb2776c7321b17ec5949e73ac6a09", 2, 0, 2),
    ("ydb/docs/ru/core/devops/concepts/index.md", "modified", "41de76b6b584c3caa27603b9e6452c14bbd752b9", 1, 0, 1),
    ("ydb/docs/ru/core/devops/concepts/node-authorization.md", "added", "3188f0d86055731f1abb8ee8ff2dce973a7483a3", 119, 0, 119),
    ("ydb/docs/ru/core/devops/concepts/toc_p.yaml", "modified", "503afb61f1cc9bb498c0bed26620600b0acde94f", 2, 0, 2),
    ("ydb/docs/ru/core/devops/deployment-options/manual/node-authorization.md", "removed", "dfc0dd195d850920f6645e5afe2a8ec063870025", 0, 95, 95),
    ("ydb/docs/ru/core/devops/deployment-options/manual/toc_p.yaml", "modified", "a14712be51bcd1d087b4597c513b5480da86a56b", 0, 2, 2),
    ("ydb/docs/ru/core/maintenance/manual/dynamic-config.md", "modified", "f2244f64a776d6b4599ae7f97c6c527b27a15ec5", 1, 1, 2),
    ("ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md", "modified", "1c6719b01ea8d6a2d3db940380cf7b4686cc16ec", 1, 1, 2),
]
SOURCE_PATHS = [row[0] for row in SOURCE_FILES]
TARGET_PATHS = [path.replace("/ru/", "/en/") if "/ru/" in path else path for path in SOURCE_PATHS]
COMMIT_OBJECTS = {
    "base": ("803561a08e3855479d7f7dbb04a508216912ad3f", ["a03c495f0658320b5884834ff4f2a2d6dff963b2"]),
    "head": ("e7fa882fc053bbf9d1cd19e8401f85317ee093a0", ["7e4c0138fea8d4bf73cb5cd40fb9d74f499d79e5"]),
    "merge": ("f6a261dd161b233d79f219209a97174031bfffba", ["d9fc9f993eb7fbade94da40c7c666178abb93170"]),
    "current_main": ("0280c4a66caeb64caf6b858aee966d24996c554e", ["01ffb66224d3f8eb572e6155d186a31421c17534", "547713dd9c37f0e461d77247947fb05b9bbbd38f"]),
}
SAFE_RELATIVE = re.compile(r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]{1,300}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9:-]{1,128}\Z")
FORBIDDEN_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
SECRET_NAME = re.compile(r"(?:^|[-_])(secret|password|passwd|private[-_]?key|access[-_]?token|auth[-_]?token)(?:$|[-_])", re.I)
SECRET_VALUE = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|bearer\s+[A-Za-z0-9._~+/=-]{10,})", re.I)
ALLOWED_AUTH_METADATA = {"github-authentication-token-expiration"}


class DerivationError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def strict_json_bytes(data: bytes, source: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise DerivationError(f"duplicate JSON key {key!r} in {source}")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DerivationError(f"invalid JSON in {source}: {exc}") from exc


def strict_b64(path: Path) -> bytes:
    try:
        encoded = b"".join(path.read_bytes().split())
        decoded = base64.b64decode(encoded, validate=True)
    except (OSError, ValueError) as exc:
        raise DerivationError(f"invalid base64 {path}: {exc}") from exc
    if base64.b64encode(decoded) != encoded:
        raise DerivationError(f"non-canonical base64 {path}")
    return decoded


@dataclass(frozen=True)
class HttpCapture:
    status: int
    headers: dict[str, str]
    body: Any
    raw: bytes


def parse_http(path: Path) -> HttpCapture:
    raw = strict_b64(path)
    if len(raw) > 2_000_000:
        raise DerivationError(f"HTTP capture too large: {path}")
    separator = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
    try:
        head, body_bytes = raw.split(separator, 1)
        lines = head.decode("ascii").splitlines()
    except (ValueError, UnicodeDecodeError) as exc:
        raise DerivationError(f"invalid HTTP framing {path}: {exc}") from exc
    match = re.fullmatch(r"HTTP/\S+ (200|404) (OK|Not Found)", lines[0] if lines else "")
    if not match:
        raise DerivationError(f"unexpected HTTP status in {path}")
    status = int(match.group(1))
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            raise DerivationError(f"malformed HTTP header in {path}")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        value = value.strip()
        if name in headers:
            raise DerivationError(f"duplicate HTTP header {name} in {path}")
        if name in FORBIDDEN_HEADERS or (SECRET_NAME.search(name) and name not in ALLOWED_AUTH_METADATA):
            raise DerivationError(f"credential-bearing header {name} in {path}")
        if SECRET_VALUE.search(value):
            raise DerivationError(f"credential-like header value in {path}")
        headers[name] = value
    if headers.get("x-github-api-version-selected") != "2022-11-28":
        raise DerivationError(f"wrong GitHub API version in {path}")
    request_id = headers.get("x-github-request-id", "")
    if not REQUEST_ID.fullmatch(request_id):
        raise DerivationError(f"invalid request ID in {path}")
    try:
        parsedate_to_datetime(headers["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DerivationError(f"invalid GitHub Date in {path}") from exc
    body = strict_json_bytes(body_bytes, path.as_posix())
    return HttpCapture(status, headers, body, raw)


def requests() -> list[dict[str, Any]]:
    rows: list[tuple[str, str, str]] = []
    for scope in ("base", "head", "merge", "current_main"):
        rows.extend((scope, REFS[scope], path) for path in SOURCE_PATHS)
    rows.extend(("current_main_target", REFS["current_main"], path) for path in TARGET_PATHS)
    result = []
    for index, (scope, ref_sha, path) in enumerate(rows, 1):
        if not SAFE_RELATIVE.fullmatch(path) or not HEX40.fullmatch(ref_sha):
            raise DerivationError("unsafe fixed request")
        request_id = f"lookup-{index:03d}"
        endpoint = f"repos/ydb-platform/ydb/contents/{path}?ref={ref_sha}"
        result.append({"id": request_id, "method": "GET", "scope": scope, "ref_sha": ref_sha, "path": path, "endpoint": endpoint, "response_artifact": f"raw/lookups/{request_id}.http.b64"})
    return result


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _selected_headers(capture: HttpCapture) -> dict[str, str]:
    names = ("date", "etag", "content-type", "link", "x-github-api-version-selected", "x-github-request-id")
    return {name: capture.headers[name] for name in names if name in capture.headers}


def _page_rows(root: Path) -> tuple[list[dict[str, Any]], list[HttpCapture]]:
    pages = [parse_http(root / f"raw/files-page-{i:03d}.http.b64") for i in (1, 2, 3)]
    relations = [
        {"next": 2, "last": 3},
        {"prev": 1, "next": 3, "last": 3, "first": 1},
        {"prev": 2, "first": 1},
    ]
    rows: list[dict[str, Any]] = []
    for number, (page, expected_links, expected_count) in enumerate(zip(pages, relations, (3, 3, 2), strict=True), 1):
        if page.status != 200 or not isinstance(page.body, list) or len(page.body) != expected_count:
            raise DerivationError(f"bad files page {number}")
        observed: dict[str, int] = {}
        for part in page.headers.get("link", "").split(","):
            match = re.fullmatch(r'\s*<https://api\.github\.com/repositories/456549280/pulls/45949/files\?per_page=3&page=(\d+)>; rel="(first|prev|next|last)"\s*', part)
            if not match or match.group(2) in observed:
                raise DerivationError(f"bad Link header on page {number}")
            observed[match.group(2)] = int(match.group(1))
        if observed != expected_links:
            raise DerivationError(f"incomplete pagination at page {number}")
        for item in page.body:
            if not isinstance(item, dict):
                raise DerivationError("non-object file row")
            rows.append({key: item.get(key) for key in ("filename", "status", "sha", "additions", "deletions", "changes")})
    expected = [dict(zip(("filename", "status", "sha", "additions", "deletions", "changes"), row, strict=True)) for row in SOURCE_FILES]
    if rows != expected:
        raise DerivationError("PR 45949 file rows were substituted")
    return rows, pages


def _display_template(capture: dict[str, Any], counts: dict[str, int]) -> bytes:
    return ("# PR 45949 capture provenance\n\n"
            "> Display-only rendering. `PROVENANCE.json`, `manifest.json` and the raw captures are authoritative.\n\n"
            f"- Repository: `{REPOSITORY}`.\n- Pull request: `{PR_NUMBER}`.\n"
            f"- GitHub response interval: `{capture['started_at_utc']}` through `{capture['completed_at_utc']}`.\n"
            f"- Immutable path lookups: `{counts['lookups']}`, including `{counts['not_found']}` HTTP 404 responses.\n"
            f"- Unique verified Git blobs: `{counts['blobs']}`.\n"
            "- Provenance root: `<PROVENANCE_ROOT>`.\n"
            "- Local capture tools are informational only; GitHub Date and request IDs are authoritative.\n").encode("utf-8")


def _tuple_digest(kind: str, path: str, data: bytes) -> bytes:
    if not SAFE_RELATIVE.fullmatch(path):
        raise DerivationError(f"unsafe tuple path {path}")
    digest = hashlib.sha256(data).hexdigest()
    return f"{kind}\0{path}\0{len(data)}\0{digest}\0".encode("utf-8")


def derive(root: Path) -> dict[str, Any]:
    reqs = requests()
    inventory_rows: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    captures: dict[str, HttpCapture] = {}
    for request in reqs:
        artifact = request["response_artifact"]
        capture = parse_http(root / artifact)
        captures[artifact] = capture
        base = {key: request[key] for key in ("id", "scope", "ref_sha", "path", "response_artifact")}
        if capture.status == 404:
            if capture.body != {"message": "Not Found", "documentation_url": "https://docs.github.com/rest/repos/contents#get-repository-content", "status": "404"}:
                raise DerivationError(f"unexpected 404 body: {request['id']}")
            inventory_rows.append({**base, "http_status": 404, "present": False})
            continue
        body = capture.body
        if capture.status != 200 or not isinstance(body, dict):
            raise DerivationError(f"unexpected lookup response: {request['id']}")
        allowed = {"name", "path", "sha", "size", "url", "html_url", "git_url", "download_url", "type", "content", "encoding", "_links"}
        if set(body) != allowed or body.get("path") != request["path"] or body.get("type") != "file" or body.get("encoding") != "base64":
            raise DerivationError(f"invalid lookup body: {request['id']}")
        if not isinstance(body.get("size"), int) or not 0 <= body["size"] <= 1_000_000 or not HEX40.fullmatch(body.get("sha", "")):
            raise DerivationError(f"invalid blob metadata: {request['id']}")
        try:
            data = base64.b64decode("".join(body["content"].split()), validate=True)
        except (AttributeError, ValueError) as exc:
            raise DerivationError(f"invalid lookup content: {request['id']}") from exc
        if len(data) != body["size"] or git_blob_sha(data) != body["sha"]:
            raise DerivationError(f"lookup blob mismatch: {request['id']}")
        blobs.setdefault(body["sha"], data)
        if blobs[body["sha"]] != data:
            raise DerivationError(f"same blob SHA has different bytes: {body['sha']}")
        inventory_rows.append({**base, "http_status": 200, "present": True, "blob_sha": body["sha"], "size": body["size"], "fixture": f"blobs/{body['sha']}.b64"})

    rows, pages = _page_rows(root)
    fixed_raw = ["raw/pr.http.b64", "raw/files-page-001.http.b64", "raw/files-page-002.http.b64", "raw/files-page-003.http.b64", "raw/main-ref.http.b64"]
    fixed_raw += [f"raw/{name}-commit.http.b64" for name in ("base", "head", "merge", "current_main")]
    for artifact in fixed_raw:
        captures[artifact] = parse_http(root / artifact)
    pr = captures["raw/pr.http.b64"].body
    if not isinstance(pr, dict) or pr.get("number") != PR_NUMBER or pr.get("changed_files") != 8 or pr.get("merged_at") is None:
        raise DerivationError("wrong PR metadata")
    if pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY or pr.get("base", {}).get("sha") != REFS["base"] or pr.get("head", {}).get("sha") != REFS["head"] or pr.get("merge_commit_sha") != REFS["merge"]:
        raise DerivationError("wrong PR identity/refs")
    for name, sha in REFS.items():
        commit = captures[f"raw/{name}-commit.http.b64"].body
        tree, parents = COMMIT_OBJECTS[name]
        if commit.get("sha") != sha or commit.get("tree", {}).get("sha") != tree or [x.get("sha") for x in commit.get("parents", [])] != parents:
            raise DerivationError(f"wrong {name} commit object")
    main_ref = captures["raw/main-ref.http.b64"].body
    if main_ref.get("ref") != "refs/heads/main" or main_ref.get("object", {}).get("sha") != REFS["current_main"]:
        raise DerivationError("wrong current-main ref")

    dates = [parsedate_to_datetime(c.headers["date"]).astimezone(timezone.utc) for c in captures.values()]
    request_ids = sorted(c.headers["x-github-request-id"] for c in captures.values())
    if len(request_ids) != len(set(request_ids)):
        raise DerivationError("duplicate GitHub request ID")
    capture = {
        "started_at_utc": min(dates).isoformat().replace("+00:00", "Z"),
        "completed_at_utc": max(dates).isoformat().replace("+00:00", "Z"),
        "request_count": len(captures),
        "request_ids_sha256": hashlib.sha256(canonical(request_ids)).hexdigest(),
        "github_api_version": "2022-11-28",
        "github_cli": "2.92.0",
        "git": "2.50.1 (Apple Git-155)",
        "python": "3.14.6",
    }
    inventory = {"schema_version": "pr-fixture-inventory/v2", "repository": REPOSITORY, "pull_request": PR_NUMBER, "lookups": inventory_rows}
    headers = {"schema_version": "github-response-headers/v2", "responses": {path: _selected_headers(captures[path]) for path in sorted(captures)}}
    manifest_no_root = {
        "schema_version": "pr-fixture-manifest/v2",
        "specification": {"repository": "ydbdoc-review", "commit": "4040e14aac7", "section": "25.12.3 M-1"},
        "subject": {"repository": REPOSITORY, "pull_request": PR_NUMBER},
        "capture": capture,
        "refs": {name: {"sha": REFS[name], "tree": COMMIT_OBJECTS[name][0], "parents": COMMIT_OBJECTS[name][1], "artifact": f"raw/{name}-commit.http.b64"} for name in REFS},
        "main_ref_artifact": "raw/main-ref.http.b64",
        "raw_pr_artifact": "raw/pr.http.b64",
        "files_pages": [{"number": i, "artifact": f"raw/files-page-{i:03d}.http.b64", "items": count} for i, count in ((1, 3), (2, 3), (3, 2))],
        "files": rows,
        "lookup_requests_artifact": "requests.json",
        "inventory_artifact": "inventory.json",
        "headers_artifact": "headers.json",
        "authoritative_raw_artifacts": sorted(captures),
        "authoritative_blob_artifacts": [f"blobs/{sha}.b64" for sha in sorted(blobs)],
        "bounds": {"lookup_count": 40, "file_count": 8, "page_count": 3, "max_http_bytes": 2_000_000, "max_blob_bytes": 1_000_000},
        "provenance_algorithm": "sha256-domain-tuples/v2",
    }
    counts = {"lookups": len(reqs), "not_found": sum(not row["present"] for row in inventory_rows), "blobs": len(blobs), "raw_responses": len(captures)}
    display_template = _display_template(capture, counts)
    parts = [DOMAIN]
    parts.append(_tuple_digest("manifest", "manifest-without-root.json", canonical(manifest_no_root)))
    for path in sorted(captures):
        parts.append(_tuple_digest("raw", path, captures[path].raw))
    for sha in sorted(blobs):
        parts.append(_tuple_digest("blob", f"blobs/{sha}.b64", blobs[sha]))
    for path, value in (("requests.json", reqs), ("inventory.json", inventory), ("headers.json", headers)):
        parts.append(_tuple_digest("derived", path, canonical(value)))
    parts.append(_tuple_digest("derived", "PROVENANCE.md.template", display_template))
    provenance_root = hashlib.sha256(b"".join(parts)).hexdigest()
    manifest = {**manifest_no_root, "provenance_root": provenance_root}
    provenance = {"schema_version": "pr-fixture-provenance/v2", "provenance_root": provenance_root, "capture": capture, "counts": counts, "domain": DOMAIN.decode("ascii").rstrip("\0")}
    display = display_template.replace(b"<PROVENANCE_ROOT>", provenance_root.encode("ascii"))
    return {"requests": reqs, "inventory": inventory, "headers": headers, "manifest": manifest, "provenance": provenance, "display": display, "blobs": blobs}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path, nargs="?", default=Path("fixtures/pr-45949"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = derive(args.fixture)
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("requests", "inventory", "headers", "manifest", "provenance"):
        (args.output / ("PROVENANCE.json" if name == "provenance" else f"{name}.json")).write_bytes(json.dumps(result[name], ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    (args.output / "PROVENANCE.md").write_bytes(result["display"])
    blob_dir = args.output / "blobs"
    blob_dir.mkdir(exist_ok=True)
    for sha, data in result["blobs"].items():
        (blob_dir / f"{sha}.b64").write_bytes(base64.b64encode(data) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
