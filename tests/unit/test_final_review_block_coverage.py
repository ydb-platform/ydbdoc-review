"""Whole frozen input must survive preparation and payload validation."""

import importlib
import importlib.util
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from ydbdoc_review.pipeline.final_candidate import FinalCandidate


@pytest.fixture
def api():
    class API:
        def __getattr__(self, attr):
            name = "ydbdoc_review.translation.review_blocks"
            assert importlib.util.find_spec(name) is not None, "whole-block coverage API is missing"
            return getattr(importlib.import_module(name), attr)
    name = "ydbdoc_review.translation.review_blocks"
    return importlib.import_module(name) if importlib.util.find_spec(name) else API()


def prepare(api, ru, en=None, sha="a" * 40):
    candidate = FinalCandidate(sha, "b" * 40, ("en.md",), ())
    source = api.AuthoritativeDocument("ru.md", "authority-commit", ru.encode())
    return api.prepare_review_document(candidate, source, "en.md", (ru if en is None else en).encode())


@pytest.mark.parametrize("text,kinds", [
    ("First. Second **sentence** with [link](url).\nStill same paragraph.\n", ("paragraph",)),
    ("#|\n|| H | J ||\n|| A | B | EXTRA ||\n|#\n", ("table",)),
    ("Before.\n\n#|\n|| H | J ||\n|| A | B ||\n|#\n\nAfter.\n", ("paragraph", "table", "paragraph")),
    ('{% note info "Title" %}\nBody\n{% endnote %}\n', ("yfm_note",)),
    ('{% cut "Title" %}\nBody\n{% endcut %}\n', ("yfm_cut",)),
    ("{% list tabs %}\n- Title\n\n  Body\n{% endlist %}\n", ("yfm_tabs",)),
    ("```python\n# Human comment\nprint('message')\n```\n", ("fence",)),
    ('```mermaid\ngraph LR\nA[Human label] --> B[Other label]\n```\n', ("fence",)),
    ("- First paragraph\n\n  Second paragraph\n  - Nested item\n", ("bullet_list",)),
    ("> First\n>\n> Second\n", ("blockquote",)),
    ("| H | J |\n|---|---|\n| A | B |\n", ("table",)),
    ("---\ntitle: Human title\n---\n# Heading {#id}\n\n![Alt](image \"Title\")\n", ("front_matter", "heading", "paragraph")),
])
def test_all_whole_containers_are_exact_primary_payload(api, text, kinds):
    plan = prepare(api, text)
    assert plan.complete, plan.issues
    assert tuple(b.kind for b in plan.en.blocks) == kinds
    assert len(plan.units) == len(kinds)
    assert tuple(u.en_text for u in plan.units) == tuple(b.text for b in plan.en.blocks)
    assert tuple(u.ru_text for u in plan.units) == tuple(b.text for b in plan.ru.blocks)
    assert all(text[b.span.start:b.span.end] == b.text for b in plan.en.blocks)
    assert api.validate_payloads(plan, (plan.units,)).complete


@pytest.mark.parametrize("en", ["", "One.\n\nExtra.\n", "# Heading\n"])
def test_missing_extra_or_changed_structure_cannot_silently_align(api, en):
    plan = prepare(api, "One.\n", en)
    assert not plan.complete
    assert plan.issues[0].code == "incomplete-review"
    assert plan.issues[0].candidate_sha == "a" * 40


def test_empty_is_proven_and_distinct_from_failed_extraction(api, monkeypatch):
    plan = prepare(api, " \n")
    assert plan.complete and plan.en.empty_reason == "whitespace-only"
    monkeypatch.setattr(api, "parse_review_blocks", lambda text: ())
    failed = prepare(api, "Readable tail\n")
    assert not failed.complete
    assert failed.issues[0].span.start == 0


def test_omissions_duplicates_mutation_and_other_snapshot_fail(api):
    plan = prepare(api, "First.\n\nSecond.\n")
    unit = plan.units[0]
    for batches in [(), ((unit, unit),), ((replace(unit, en_text="First."),),),
                    ((replace(unit, en_block_ids=unit.en_block_ids[:1]),),)]:
        manifest = api.validate_payloads(plan, batches)
        assert not manifest.complete
        assert manifest.issues
    next_plan = prepare(api, "First.\n", "New.\n", "c" * 40)
    assert not api.validate_payloads(next_plan, (plan.units,)).complete
    assert next_plan.units[0].en_text == "New.\n"


def test_same_kind_reordering_and_repeated_text_keep_document_context(api):
    plan = prepare(api, "# A\n\nSame.\n\n# B\n\nSame.\n", "# B\n\nSame.\n\n# A\n\nSame.\n")
    assert not plan.complete
    assert len({b.id for b in plan.en.blocks}) == 4
    assert plan.issues[0].reason == "ambiguous whole-block correspondence"


def test_unanchored_translated_multiblock_alignment_is_red(api):
    plan = prepare(api, "First source.\n\nSecond source.\n", "First target.\n\nSecond target.\n")
    assert not plan.complete
    assert len(plan.en.blocks) == 2


def test_explicit_heading_anchors_pair_independent_whole_blocks(api):
    plan = prepare(api, "# Источник {#one}\n\n# Другой {#two}\n", "# Source {#one}\n\n# Other {#two}\n")
    assert plan.complete
    assert len(plan.units) == 2


def test_missing_candidate_bytes_is_located_red(api):
    candidate = FinalCandidate("a" * 40, "b" * 40, ("en.md",), ())
    source = api.AuthoritativeDocument("ru.md", "authority", b"Source")
    plan = api.prepare_review_document(candidate, source, "en.md", None)
    assert not plan.complete
    assert plan.issues[0].path == "en.md"


def test_pr53007_exact_whole_paragraph(api):
    fixtures = Path(__file__).parents[1] / "fixtures/final-review-block-coverage"
    ru = (fixtures / "53007.ru.md").read_bytes()
    en = (fixtures / "53007.en.md").read_bytes()
    assert sha256(ru).hexdigest() == "5167eeed69a0b593fff3d3708192ed0806ef32fe1bf5db8df656a7578db4ac5b"
    assert sha256(en).hexdigest() == "eb8d640034c9d8ee23b2050fea0a68912284909edc8b32de276a4f02a8cd63d9"
    plan = prepare(api, ru.decode(), en.decode(), "432f16a5749cc40dc9233545b66d8e98f5288efc")
    assert plan.complete
    assert len(plan.en.blocks) == 1
    assert plan.units[0].ru_text.encode() == ru
    assert plan.units[0].en_text.encode() == en
    assert not api.validate_payloads(plan, ((replace(plan.units[0], en_text="bind_dn or bind_password"),),)).complete
