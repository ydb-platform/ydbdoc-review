import hashlib
import json
from pathlib import Path

from ydbdoc_review.validation.hard_file_validator import validate_whole_file


def test_exact_pr_51142_fixture_decisions():
    root = Path(__file__).parents[1] / "fixtures/pr_51142_decisions"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["ref"]) == 40
    for name, expected in manifest["files"].items():
        ru = (root / f"{name}.ru.md").read_bytes()
        en = (root / f"{name}.en.md").read_bytes()
        assert hashlib.sha256(ru).hexdigest() == expected["ru_sha256"]
        assert hashlib.sha256(en).hexdigest() == expected["en_sha256"]
        errors = validate_whole_file(
            path=name,
            authoritative_ru=ru.decode(),
            candidate_en=en.decode(),
        )
        assert (not errors) == (expected["decision"] == "preserve"), (name, errors)
