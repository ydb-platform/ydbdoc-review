import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = (ROOT / "entrypoint.sh").read_text()


def test_preflight_gate_is_exact_and_before_legacy_cli():
    gate = 'if [ "${MODE}" = "run" ] && [ "${INPUT_PR:-}" = "45949" ]; then'
    assert gate in ENTRYPOINT
    assert ENTRYPOINT.index("run_ng_real_ydb_preflight.py") < ENTRYPOINT.index('CLI="python -m ydbdoc_review"')


def test_verify_continue_and_other_pr_bypass_by_closed_gate():
    assert 'MODE="${INPUT_MODE:-run}"' in ENTRYPOINT
    assert ENTRYPOINT.count('"45949"') == 1
    assert '"${MODE}" = "run"' in ENTRYPOINT
    assert '"${MODE}" = "verify"' not in ENTRYPOINT
    assert '"${MODE}" = "continue"' not in ENTRYPOINT


def _invoke(tmp_path, mode, pr, preflight_code=0):
    log = tmp_path / "calls"
    log.unlink(missing_ok=True)
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\necho "preflight:$*" >> "$CALL_LOG"\nexit "$PREFLIGHT_CODE"\n')
    cli = tmp_path / "ydbdoc-review"
    cli.write_text('#!/bin/sh\necho "legacy:$*" >> "$CALL_LOG"\n')
    python.chmod(0o755); cli.chmod(0o755)
    environment = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", CALL_LOG=str(log), PREFLIGHT_CODE=str(preflight_code), INPUT_MODE=mode, INPUT_PR=str(pr), INPUT_REPO="ydb-platform/ydb")
    result = subprocess.run(["sh", str(ROOT / "entrypoint.sh")], env=environment, text=True, capture_output=True)
    return result, log.read_text().splitlines() if log.exists() else []


def test_run_45949_preflight_precedes_legacy_and_failure_blocks_it(tmp_path):
    result, calls = _invoke(tmp_path, "run", 45949)
    assert result.returncode == 0
    assert calls[0].startswith("preflight:") and calls[1].startswith("legacy:run")
    failed, calls = _invoke(tmp_path, "run", 45949, 7)
    assert failed.returncode == 7
    assert len(calls) == 1 and calls[0].startswith("preflight:")


def test_other_routes_bypass_preflight(tmp_path):
    for index, (mode, pr) in enumerate((("run", 1), ("verify", 45949), ("continue", 45949))):
        case = tmp_path / str(index); case.mkdir()
        result, calls = _invoke(case, mode, pr)
        assert result.returncode == 0
        assert len(calls) == 1 and calls[0].startswith(f"legacy:{mode if mode != 'run' else 'run'}")
