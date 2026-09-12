"""Guard: Docker action must forward ops secrets into the container (§6.143)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "action-docker.sh").read_text(encoding="utf-8")


def test_action_docker_forwards_ydb_sa_key():
    assert "YDB_SA_KEY" in SCRIPT
    assert "YDBDOC_TRANSCRIPT_BACKEND" in SCRIPT
    assert "YDBDOC_ALLOWED_ACTORS" in SCRIPT
    assert "GITHUB_ACTOR" in SCRIPT


def test_action_docker_passes_env_by_name_not_inline_value():
    # Multiline JSON secrets break with -e VAR=$value; use -e VAR.
    assert '-e "${var}=${!var}"' not in SCRIPT
    assert '-e "${var}"' in SCRIPT


def test_action_docker_can_mount_sa_key_file():
    assert "/run/secrets/ydb-sa.json" in SCRIPT
