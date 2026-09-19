"""Real child CLI → fetch → runner/quality/publish → HTTP reports → YDB SDK SQL.

Only boundaries in cli_boundary are substituted. Actual YFM covered independently.
"""

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ydbdoc_review.store import YDBStore

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.timeout(120)


@pytest.fixture
def process(tmp_path, git_repo, monkeypatch):
    repo, git = git_repo
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("upload-pack", "receive-pack"):
        path = tools / ("git-" + name)
        path.write_text(
            "#!/bin/sh\nexec " + shlex.quote(shutil.which("git")) + " " + name + ' "$@"\n'
        )
        path.chmod(0o755)
    wrapper = tools / "git"
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(shutil.which("git"))
        + " -c "
        + shlex.quote(f"url.{remote}.insteadOf=https://x-access-token:dummy@github.com/up/docs.git")
        + ' "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    config = tmp_path / "gitconfig"
    config.write_text(
        f'[url "{remote}"]\n    insteadOf = https://x-access-token:dummy@github.com/up/docs.git\n'
    )
    for lang in ("ru", "en"):
        p = repo / f"ydb/docs/{lang}/a.md"
        p.parent.mkdir(parents=True)
        p.write_text("# Hello\n\nHello world.\n")
    git("add", ".")
    git("commit", "-m", "CLI source")
    source = git("rev-parse", "HEAD").decode().strip()
    git("push", str(remote), source + ":refs/heads/topic")
    state = dict(
        pulls={"1": dict(branch="topic", base="main", draft=False)},
        changes=[dict(filename="ydb/docs/ru/a.md", status="modified")],
        model=[],
        comments=[],
        http=[],
        builds=[],
        instructions=[
            dict(
                body='/ydbdoc continue In a.md use greeting "Hello, world."',
                created_at=datetime.now(UTC).isoformat(),
                user=dict(login="writer", type="User"),
            )
        ],
    )
    state_path = tmp_path / "http.json"
    state_path.write_text(json.dumps(state))
    endpoint = dict(provider="eliza", base_url="https://model.invalid", token_env="MODEL_TOKEN")
    models = tmp_path / "models.json"
    models.write_text(
        json.dumps(
            dict(
                models={
                    r: dict(main=dict(endpoint, model=r))
                    for r in ("translation", "critic", "repair")
                },
                context_tokens=100000,
                max_output_tokens=20000,
                tariffs_rub_per_million=[
                    dict(provider="eliza", model=r, input="10000", output="30000")
                    for r in ("translation", "critic", "repair")
                ],
            )
        )
    )
    env = os.environ | dict(
        PATH=str(tools) + os.pathsep + os.environ["PATH"],
        T15_FIXTURE=str(tmp_path),
        PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT),
        GIT_CONFIG_GLOBAL=str(config),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GITHUB_TOKEN="dummy",
        MODEL_TOKEN="dummy",
        GITHUB_ACTOR="writer",
        YDB_SA_KEY="{}",
        YDBDOC_ALLOWED_ACTORS="writer",
        YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE="20",
        YDBDOC_MAX_SOURCE_CHARACTERS="250000",
        YDBDOC_DAILY_BUDGET_RUB="100",
    )

    def invoke(mode, pr=1, **extra):
        # Primary translation fixture adds a genuinely absent target.
        target = repo / "ydb/docs/en/a.md"
        if mode == "doc_translate" and target.exists():
            target.unlink()
            git("add", ".")
            git("commit", "-m", "source only")
            git("push", str(remote), "HEAD:refs/heads/topic")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.contract.cli_boundary",
                mode,
                "--repo",
                "up/docs",
                "--pr",
                str(pr),
                *([] if extra.get("T15_DEFAULT_RUNTIME") == "1" else ["--config", str(models)]),
            ],
            env={key: value for key, value in (env | extra).items() if value is not None},
            capture_output=True,
            text=True,
            timeout=90,
            cwd=ROOT,
        )
        return result

    def update(**values):
        state = json.loads(state_path.read_text())
        state.update(values)
        state_path.write_text(json.dumps(state))

    def read():
        return json.loads(state_path.read_text())

    def rows():
        with sqlite3.connect(tmp_path / "database.sqlite") as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at")]

    return type(
        "Process",
        (),
        dict(
            invoke=staticmethod(invoke),
            update=staticmethod(update),
            read=staticmethod(read),
            rows=staticmethod(rows),
            repo=repo,
            git=staticmethod(git),
            remote=remote,
            root=tmp_path,
            env=env,
            source=source,
        ),
    )


def assert_saved(p, mode, status):
    summaries = [r for r in p.rows() if r["entry_id"] == "summary" and r["mode"] == mode]
    assert summaries
    row = summaries[-1]
    assert row["status"] == status
    # Read context through real YDBStore API with the same persisted SDK boundary.
    from tests.contract.test_t13_independent import SQLBoundary

    sql = SQLBoundary(p.root / "database.sqlite")
    store = object.__new__(YDBStore)
    store.pool = sql
    store.clock = lambda: datetime.now(UTC)
    context = store.context(row["run_id"])
    assert context["result"]["status"] == status
    attempts = [r for r in p.rows() if r["run_id"] == row["run_id"] and r["entry_id"] != "summary"]
    known = [Decimal(r["cost_rub"]) for r in attempts if r["cost_rub"] is not None]
    costs = context["result"]["cost_breakdown"]
    assert costs["total"] == (sum(known, Decimal(0)) if len(known) == len(attempts) else None)
    sql.connection.close()
    return context, attempts


@pytest.mark.parametrize("mode", ["doc_translate", "doc_verify", "doc_continue"])
def test_all_modes_publication_comments_context_all_costs(process, mode):
    p = process
    if mode == "doc_continue":
        seed = p.invoke("doc_translate")
        assert seed.returncode == 0, seed.stdout + seed.stderr
        p.update(model=[], comments=[], http=[], builds=[])
    result = p.invoke(mode, 2 if mode == "doc_continue" else 1)
    assert result.returncode == 0, result.stdout + result.stderr
    state = p.read()
    context, attempts = assert_saved(p, mode, "GREEN")
    assert attempts and state["model"]
    assert context["source_sha"] and context["result_sha"]
    assert context["result"]["checked_sha"] == context["result_sha"] == state["builds"][-1]
    expected = b"# Hello\n\nHello, world.\n" if mode == "doc_continue" else b"# Hello\n\nHello world.\n"
    assert context["final_files"]["ydb/docs/en/a.md"] == expected
    assert len(state["comments"]) == (1 if mode == "doc_verify" else 2)
    for _, body in state["comments"]:
        for label in ("Перевод:", "Критик:", "Исправления:", "Итого:"):
            assert label in body
    assert context["result"]["cost_breakdown"] == dict(
        translation=Decimal(".25") if mode == "doc_translate" else 0,
        critic=Decimal(".5") if mode == "doc_continue" else Decimal(".25"),
        repair=Decimal(".25") if mode == "doc_continue" else 0,
        total=Decimal(".75")
        if mode == "doc_continue"
        else Decimal(".5")
        if mode == "doc_translate"
        else Decimal(".25"),
    )


@pytest.mark.parametrize("mode", ["doc_translate", "doc_verify", "doc_continue"])
@pytest.mark.parametrize("failure", ["model_error", "report_error", "cancel"])
def test_errors_and_cancel_persist_final_red_no_double_cost(process, mode, failure):
    p = process
    if mode == "doc_continue":
        result = p.invoke("doc_translate")
        assert result.returncode == 0, result.stdout + result.stderr
    p.update(**{failure: True}, model=[], comments=[])
    result = p.invoke(mode, 2 if mode == "doc_continue" else 1)
    assert result.returncode == 1, result.stdout + result.stderr
    context, attempts = assert_saved(p, mode, "RED")
    assert attempts
    if failure == "cancel":
        assert context["result"]["cancelled"]
    if failure == "report_error":
        assert any("report" in e for e in context["result"]["errors"])
        assert context["result"]["publication"]["draft"]
        assert len(attempts) == len(p.read()["model"])


@pytest.mark.parametrize("mode", ["doc_translate", "doc_verify", "doc_continue"])
def test_acl_and_config_before_effects(process, mode):
    p = process
    invalid = [
        ("YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE", None),
        ("YDBDOC_MAX_SOURCE_CHARACTERS", None),
        ("YDBDOC_ALLOWED_ACTORS", None),
        ("YDBDOC_DAILY_BUDGET_RUB", None),
        ("YDB_SA_KEY", None),
        ("YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE", "-1"),
        ("YDBDOC_MAX_SOURCE_CHARACTERS", "1.5"),
        ("YDBDOC_ALLOWED_ACTORS", " , "),
        ("YDBDOC_DAILY_BUDGET_RUB", "sNaN"),
        ("GITHUB_ACTOR", "outsider"),
        ("GITHUB_ACTOR", None),
    ]
    for variable, value in invalid:
        result = p.invoke(mode, **{variable: value, "YDBDOC_SKIP_OPS_GATES": "1",
                                 "YDBDOC_OPS_SKIP_GATES": "1"})
        assert result.returncode == 1
        expected = "YDBDOC_ALLOWED_ACTORS" if variable == "GITHUB_ACTOR" else variable
        assert expected in result.stdout
        assert p.read()["comments"]
        assert all(method == "POST" and path == "/repos/up/docs/issues/1/comments"
                   for method, path in p.read()["http"])
        assert not p.read()["model"]
        assert len(p.read()["pulls"]) == 1
        assert not p.read().get("factories", 0)
        assert not (p.root / "database.sqlite").exists()


def test_twenty_one_dependencies_before_model_factory(process):
    p = process
    text = "# Hello\n\n" + "\n\n".join(f"[D{i}](dep{i}.md)" for i in range(21)) + "\n"
    (p.repo / "ydb/docs/ru/a.md").write_text(text)
    for i in range(21):
        (p.repo / f"ydb/docs/ru/dep{i}.md").write_text("# Dependency\n")
    p.git("add", ".")
    p.git("commit", "-m", "21 dependencies")
    p.git("push", str(p.remote), "HEAD:refs/heads/topic")
    result = p.invoke("doc_translate")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "21" in result.stdout and "YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE" in result.stdout
    assert not p.read()["model"]
    assert not p.read().get("factories", 0) and len(p.read()["pulls"]) == 1
    _context, attempts = assert_saved(p, "doc_translate", "RED")
    assert not attempts


@pytest.mark.parametrize("changed", ["source", "result"])
def test_both_sha_changes_before_continue(process, changed):
    p = process
    seed = p.invoke("doc_translate")
    assert seed.returncode == 0, seed.stdout + seed.stderr
    state = p.read()
    branch = "topic" if changed == "source" else state["pulls"]["2"]["branch"]
    p.git("fetch", str(p.remote), branch)
    p.git("checkout", "--detach", "FETCH_HEAD")
    p.git("commit", "--allow-empty", "-m", "unrelated")
    p.git("push", str(p.remote), "HEAD:refs/heads/" + branch)
    p.update(model=[], factories=0)
    result = p.invoke("doc_continue", 2)
    assert result.returncode == 1
    assert "После предыдущего запуска появились новые коммиты" in result.stdout
    assert not p.read()["model"]
    assert not p.read().get("factories", 0)


def test_three_continuations_then_refusal(process):
    p = process
    seed = p.invoke("doc_translate")
    assert seed.returncode == 0, seed.stdout + seed.stderr
    for greeting in ('Hello, world.', 'Hello world!', 'Hello, world!'):
        instructions = p.read()['instructions']
        instructions[0]['body'] = f'/ydbdoc continue In a.md use greeting "{greeting}"'
        p.update(instructions=instructions)
        result = p.invoke("doc_continue", 2)
        assert result.returncode == 0, result.stdout + result.stderr
    p.update(model=[], factories=0)
    result = p.invoke("doc_continue", 2)
    assert result.returncode == 1
    assert not p.read()["model"]
    assert not p.read().get("factories", 0)


@pytest.mark.parametrize("mode", ["doc_translate", "doc_verify", "doc_continue"])
def test_budget_gate_before_factory_and_all_modes_accounting(process, mode):
    p = process
    if mode == "doc_continue":
        seed = p.invoke("doc_translate")
        assert seed.returncode == 0, seed.stdout + seed.stderr
    p.update(model=[], factories=0)
    result = p.invoke(mode, 2 if mode == "doc_continue" else 1, YDBDOC_DAILY_BUDGET_RUB="0")
    assert result.returncode == 1
    assert "YDBDOC_DAILY_BUDGET_RUB" in result.stdout
    assert not p.read()["model"] and not p.read().get("factories", 0)
    context, attempts = assert_saved(p, mode, "RED")
    assert not attempts and context["result"]["cost_breakdown"]["total"] == 0


def test_admitted_overrun_finishes_then_next_process_is_blocked(process):
    p = process
    first = p.invoke("doc_translate", YDBDOC_DAILY_BUDGET_RUB="0.1")
    assert first.returncode == 0, first.stdout + first.stderr
    context, attempts = assert_saved(p, "doc_translate", "GREEN")
    assert context["result"]["cost_breakdown"]["total"] == Decimal(".5") and len(attempts) == 2
    p.update(model=[], factories=0)
    second = p.invoke("doc_verify", 2, YDBDOC_DAILY_BUDGET_RUB="0.1")
    assert second.returncode == 1
    assert not p.read()["model"] and p.read()["factories"] == 0


def test_unknown_tariff_is_not_zero_and_blocks_next_admission(process):
    p = process
    models = p.root / "models.json"
    data = json.loads(models.read_text())
    del data["tariffs_rub_per_million"]
    models.write_text(json.dumps(data))
    result = p.invoke("doc_translate")
    assert result.returncode == 0, result.stdout + result.stderr
    context, attempts = assert_saved(p, "doc_translate", "GREEN")
    assert context["result"]["cost_breakdown"]["total"] is None
    assert all(row["cost_rub"] is None for row in attempts)
    assert all("неизвестно" in body for _, body in p.read()["comments"])
    p.update(model=[], factories=0)
    result = p.invoke("doc_verify", 2)
    assert result.returncode == 1 and "неизвестна" in result.stdout
    assert not p.read()["model"] and p.read()["factories"] == 0


def test_continue_noop_keeps_sha_red_without_duplicate_check(process):
    p = process
    seed = p.invoke('doc_translate')
    assert seed.returncode == 0, seed.stdout + seed.stderr
    p.update(repair_noop=True, model=[], builds=[])
    result = p.invoke('doc_continue', 2)
    assert result.returncode == 1, result.stdout + result.stderr
    context, attempts = assert_saved(p, 'doc_continue', 'RED')
    state = p.read()
    assert [role for role, _ in state['model']] == ['critic', 'repair']
    assert len(attempts) == 2 and len(state['builds']) == 1
    assert context['result']['checked_sha'] == context['result_sha'] == state['builds'][0]
    assert context['final_files']['ydb/docs/en/a.md'] == b'# Hello\n\nHello world.\n'
