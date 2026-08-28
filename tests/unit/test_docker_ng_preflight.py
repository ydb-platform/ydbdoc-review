import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_installs_ng_real_contract_and_runner():
    docker = (ROOT / "Dockerfile").read_text()
    assert "COPY ng/pyproject.toml /app/ng/" in docker
    assert "COPY ng/src /app/ng/src" in docker
    assert "ng/tests/test_state_contract.py ng/tests/test_real_ydb_state.py" in docker
    assert "pip install --no-cache-dir /app/ng pytest==8.3.5" in docker
    assert "COPY scripts/run_ng_real_ydb_preflight.py" in docker


def test_accepted_ng_bytes_match_frozen_manifest():
    manifest = json.loads((ROOT / "ng/ACCEPTED_SOURCE.json").read_text())
    assert manifest["source_commit_sha"] == "4f713656fd4a67bbf23f7837349eae1ee51e853d"
    assert manifest["files"]
    for item in manifest["files"]:
        payload = (ROOT / item["destination_path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
