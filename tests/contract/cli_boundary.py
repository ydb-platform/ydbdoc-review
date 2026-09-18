"""Child-process external boundaries only: HTTP, YDB SDK and the YFM executable.

Loaded explicitly by test_t15_cli.py, never by production. No live sockets allowed.
"""

import json
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import requests
import ydb
from pytest_socket import disable_socket

from tests.contract.test_t13_independent import SQLBoundary
from ydbdoc_review import build


def main():
    disable_socket(allow_unix_socket=True)
    root = Path(os.environ["T15_FIXTURE"])
    state_path = root / "http.json"
    sql = None

    def pool(driver):
        nonlocal sql
        sql = SQLBoundary(root / "database.sqlite")
        sql.stop = lambda: sql.connection.close()
        return sql

    driver = SimpleNamespace(wait=lambda **kw: None, stop=lambda: None)
    ydb.Driver = lambda **kw: driver
    ydb.iam.ServiceAccountCredentials.from_content = lambda raw: object()
    ydb.SessionPool = pool

    def read():
        return json.loads(state_path.read_text())

    def write(state):
        state_path.write_text(json.dumps(state))

    def sha(branch):
        proc = subprocess.run(
            [
                "git",
                "--git-dir",
                str(root / "remote.git"),
                "rev-parse",
                "--verify",
                "refs/heads/" + branch,
            ],
            capture_output=True,
            timeout=15,
        )
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    def response(data, status=200):
        value = requests.Response()
        value.status_code = status
        value._content = json.dumps(data).encode()
        return value

    def send(session, request, **kwargs):
        state = read()
        path = unquote(urlsplit(request.url).path)
        method = request.method
        state["http"].append([method, path])
        write(state)
        if "model.invalid" in request.url:
            role = path.split("/internal/")[1].split("/")[0]
            payload = json.loads(request.body)
            # Pending request and transcript exist BEFORE every paid HTTP attempt.
            row = sql.connection.execute(
                "SELECT * FROM runs WHERE entry_id <> 'summary' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            assert row is not None and row["operation"] == role and row["status"] == "pending"
            assert sql.connection.execute("SELECT COUNT(*) FROM run_objects").fetchone()[0] > 0
            state["model"].append([role, payload])
            write(state)
            if state.get("cancel"):
                os.kill(os.getpid(), signal.SIGTERM)
            if state.get("model_error"):
                return response(
                    {
                        "error": {"message": "paid error"},
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    },
                    400,
                )
            text = payload["messages"][-1]["content"]
            result = (
                json.dumps(dict(complete=True, verdict="correct", issues=[]))
                if role == "critic"
                else text.split("\n\n", 1)[1]
                if role == "translation"
                else json.loads(text)["source"]
            )
            return response(
                {
                    "choices": [{"message": {"content": result}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            )
        if "/git/ref/heads/" in path:
            current = sha(path.split("/heads/")[1])
            return response(
                {"object": {"sha": current}} if current else {}, 200 if current else 404
            )
        if path.endswith("/files"):
            return response(state["changes"])
        if path.endswith("/comments"):
            number = int(path.split("/issues/")[1].split("/")[0])
            if method == "GET":
                return response(state.get("instructions", []))
            if state.get("report_error"):
                return response({"message": "report unavailable"}, 503)
            body = json.loads(request.body)["body"]
            state["comments"].append([number, body])
            write(state)
            return response({"html_url": "https://github.com/up/docs/issues/1#comment"})
        if path == "/graphql":
            number = json.loads(request.body)["variables"]["id"].removeprefix("PR_")
            state["pulls"][number]["draft"] = True
            write(state)
            return response(
                {"data": {"convertPullRequestToDraft": {"pullRequest": {"isDraft": True}}}}
            )
        if path.endswith("/pulls"):
            if method == "GET":
                return response([])
            data = json.loads(request.body)
            state["pulls"]["2"] = dict(branch=data["head"], base=data["base"], draft=data["draft"])
            write(state)
            return response({"html_url": "https://github.com/up/docs/pull/2", "number": 2})
        if "/pulls/" in path:
            number = path.rsplit("/", 1)[1]
            pull = state["pulls"][number]
            return response(
                dict(
                    state="open",
                    merged=False,
                    draft=pull["draft"],
                    node_id="PR_" + number,
                    head=dict(
                        sha=sha(pull["branch"]), ref=pull["branch"], repo=dict(full_name="up/docs")
                    ),
                    base=dict(ref=pull["base"]),
                )
            )
        raise AssertionError((method, path))

    requests.Session.send = send
    from ydbdoc_review.model import ModelClient

    original_init = ModelClient.__init__

    def observe_factory(client, **kwargs):
        state = read()
        state["factories"] = state.get("factories", 0) + 1
        write(state)
        original_init(client, **kwargs)

    ModelClient.__init__ = observe_factory

    def builder(candidate):
        state = read()
        state["builds"].append(candidate.sha)
        write(state)
        return build.BuildResult(candidate.sha, "success", returncode=0)

    # Set boundary before importing runners so their default build argument is bound here.
    # Test helper imports above load runners; update only their external build default.
    from ydbdoc_review import continuation, runner, verify

    for function in (runner.run_translate, verify.run_verify, continuation.run_continue):
        function.__kwdefaults__["build"] = builder
    sys.argv = ["ydbdoc-review", *sys.argv[1:]]
    runpy.run_module("ydbdoc_review", run_name="__main__")


if __name__ == "__main__":
    main()
