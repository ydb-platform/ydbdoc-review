"""Pinned #40385 scope oracle for the frozen prefix-redirect baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ydbdoc_review.navigation.scope_planner import plan_translation_scope

FIXTURE = Path(__file__).parents[1] / "fixtures/pr40385-prefix-redirect-snapshots.json"


def test_pr40385_pinned_scope_oracle() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    paths = data["paths"]
    assert data["schema_version"] == 2
    catalog = {
        f"{role}:{path}"
        for role in ("ru_h", "ru_h0", "en_b")
        for path in data[role]
    }
    assert set(data["expected_reader_lookups"]) == catalog
    assert set(data["baseline_reader_lookups"]) <= catalog
    for role in ("ru_h", "ru_h0", "en_b"):
        texts = data[role]
        oids = data["objects"]["blob_oids"][role]
        assert texts.keys() == oids.keys()
        for path, text in texts.items():
            oid = oids[path]
            if text is None:
                assert oid is None
                continue
            raw = text.encode("utf-8")
            blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
            assert hashlib.sha1(blob).hexdigest() == oid

    source_equivalence = data["objects"]["source_equivalence"]
    assert set(paths["required_ru_scope"]) <= source_equivalence.keys()
    for path in paths["required_ru_scope"]:
        evidence = source_equivalence[path]
        assert evidence["H"] == evidence["H_pr"]
        assert evidence["H"] == data["objects"]["blob_oids"]["ru_h"][path]
    expected_image = "5c797aa32bb88f0523d118bc07d6dd450085279c"
    assert set(data["objects"]["image_blobs"].values()) == {expected_image}
    reads: set[str] = set()

    def reader(tree: str):
        mapping = data[tree]

        def read(path: str) -> str | None:
            normalized = path.replace("\\", "/")
            reads.add(f"{tree}:{normalized}")
            if normalized not in mapping:
                raise AssertionError(f"unknown frozen fixture lookup: {tree}:{normalized}")
            return mapping[normalized]

        return read

    plan = plan_translation_scope(
        [(path, "modified") for path in paths["source_roots"]],
        read_ru=reader("ru_h"),
        read_en_base=reader("en_b"),
        read_ru_base=reader("ru_h0"),
    )

    assert sorted(plan.doc_ru_paths) == paths["required_ru_scope"]
    assert sorted(plan.doc_from_diff) == paths["source_roots"]
    assert sorted(plan.doc_from_main) == paths["synthetic_ru"]
    assert plan.nav_ru_paths == frozenset()
    assert plan.link_dep_warnings == ()
    state = plan.dependency_budget.snapshot()
    assert state.limit == 20
    assert state.root_ru_paths == frozenset(paths["source_roots"])
    assert state.admitted_ru_paths == frozenset(paths["synthetic_ru"])
    assert len(state.admitted_ru_paths) == 3
    assert state.denied_ru_paths == frozenset()
    assert state.uncertain_ru_paths == frozenset()
    assert not any("embedded-ui" in path for path in plan.doc_ru_paths)
    assert not any("ydb-ui" in path for path in plan.doc_ru_paths)
    assert all(path not in plan.doc_ru_paths for path in paths["negative_ru"])
    assert reads <= set(data["expected_reader_lookups"])
    assert "en_b:ydb/docs/redirects.yaml" in reads
    assert all(f"ru_h:{path}" in reads for path in paths["source_roots"])
    assert data["objects"]["security_overview_image"] == expected_image
