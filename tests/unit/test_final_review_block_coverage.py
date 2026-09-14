"""Whole frozen input must survive preparation and payload validation."""

# Russian source fixtures intentionally contain Cyrillic characters.
# ruff: noqa: RUF001

import importlib
import importlib.util
import json
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


def test_translated_mixed_nested_document_serializes_every_primary_block(api):
    directory = Path(__file__).parents[1] / "fixtures/final-review-block-coverage"
    ru = (directory / "mixed.ru.md").read_text()
    en = (directory / "mixed.en.md").read_text()
    plan = prepare(api, ru, en)
    assert plan.complete, plan.issues
    assert tuple(b.kind for b in plan.en.blocks) == (
        "front_matter", "heading", "paragraph", "bullet_list", "blockquote",
        "table", "table", "yfm_note", "yfm_tabs", "yfm_cut", "yfm_if",
        "term_definition", "fence", "fence", "paragraph",
    )
    manifest = api.batch_review_units(plan, budget_bytes=2500, hard_limit_bytes=12000)
    assert manifest.complete, manifest.issues
    captured = [json.loads(api.serialize_review_batch(batch)) for batch in manifest.batches]
    delivered = [unit for payload in captured for unit in payload["units"]]
    assert len(delivered) >= 12
    assert sorted(i for unit in delivered for i in unit["en_block_ids"]) == sorted(b.id for b in plan.en.blocks)
    assert sorted(i for unit in delivered for i in unit["ru_block_ids"]) == sorted(b.id for b in plan.ru.blocks)
    for required in ["Extra cell", "Note body.", "First tab", "Tab body.", "Details", "Section body.",
                     "Main branch.", "Fallback branch.", "Term definition.", '# Comment\nprint("Message")',
                     "A[Start] --> B[End]", '![Description](image.png "Title")']:
        assert sum(required in unit["en_text"] for unit in delivered) == 1
    assert any("Item continuation." in u["en_text"] and "Nested item." in u["en_text"] for u in delivered)


def test_partial_parser_inventory_loss_is_red_with_prose_still_present(api, monkeypatch):
    real_parse = api.parse_review_blocks
    monkeypatch.setattr(api, "parse_review_blocks", lambda text: tuple(
        block for block in real_parse(text) if block.kind != "table"))
    plan = prepare(api, "Before.\n\n| H |\n|---|\n| V |\n\nAfter.\n")
    assert not plan.complete
    assert "unparsed readable source" in plan.issues[0].reason


@pytest.mark.parametrize("field", ["commit_sha", "tree_sha"])
def test_swapping_plan_candidate_invalidates_old_inventory_and_payload(api, field):
    plan = prepare(api, "Source", "Target")
    swapped = replace(plan, candidate=replace(plan.candidate, **{field: "c" * 40}))
    assert not api.validate_payloads(swapped, (plan.units,)).complete
    # Even changing submitted and expected unit identity cannot rebind old evidence.
    units = tuple(replace(u, candidate_sha=swapped.candidate.commit_sha,
                          candidate_tree_sha=swapped.candidate.tree_sha) for u in plan.units)
    assert not api.validate_payloads(replace(swapped, units=units), (units,)).complete


def test_translated_blocks_use_enclosing_heading_without_full_document_grouping(api):
    plan = prepare(api, "# Настройка\n\nПервый.\n\n- Пункт\n\nПоследний.\n",
                   "# Setup\n\nFirst.\n\n- Item\n\nLast.\n")
    assert plan.complete, plan.issues
    assert len(plan.units) == 4
    assert plan.units[1].context == ("# Настройка\n", "# Setup\n")
    assert plan.units[1].en_text == "First.\n"


def test_consecutive_prose_is_grouped_only_inside_proven_heading(api):
    plan = prepare(api, "# Раздел {#a}\n\nПервый.\n\nВторой.\n\n## Далее {#b}\n\nТретий.\n",
                   "# Section {#a}\n\nFirst.\n\nSecond.\n\n## Next {#b}\n\nThird.\n")
    assert plan.complete, plan.issues
    assert len(plan.units) == 4
    assert plan.units[1].en_text == "First.\n\nSecond.\n"
    assert len(plan.units[1].en_block_ids) == 2
    assert plan.units[-1].context == ("# Раздел {#a}\n", "# Section {#a}\n", "## Далее {#b}\n", "## Next {#b}\n")


def test_swapped_inventory_with_different_shape_is_red_not_exception(api):
    plan = prepare(api, "Source", "Target")
    other = prepare(api, "Source\n\nExtra", "Target\n\nExtra")
    swapped = replace(plan, en=other.en)
    assert not api.validate_payloads(swapped, (plan.units,)).complete


def test_dropping_only_table_payload_cannot_use_context_as_coverage(api):
    plan = prepare(api, "# Источник {#a}\n\nАбзац.\n\n| Ключ |\n|---|\n| Значение |\n",
                   "# Source {#a}\n\nParagraph.\n\n| Key |\n|---|\n| Value |\n")
    assert plan.complete
    omitted = plan.en.blocks[-1].id
    manifest = api.validate_payloads(plan, (plan.units[:-1],))
    assert not manifest.complete
    assert any(omitted in issue.en_block_ids for issue in manifest.issues)


@pytest.mark.parametrize("en", [
    "# A {#b}\n\nParagraph.\n\n# B {#a}\n\nParagraph.\n",
    "# A {#a}\n\nParagraph.\n\n# B {#a}\n\nParagraph.\n",
])
def test_changed_or_duplicate_section_anchors_are_ambiguous(api, en):
    plan = prepare(api, "# А {#a}\n\nАбзац.\n\n# Б {#b}\n\nАбзац.\n", en)
    assert not plan.complete
    assert not api.validate_payloads(plan, (plan.units,)).complete


def test_distinct_unanchored_sibling_section_structure_proves_correspondence(api):
    plan = prepare(api, "# Настройка\n\n## Ключи\n\n| Ключ |\n|---|\n| Значение |\n\n## Действия\n\n- Пункт\n",
                   "# Setup\n\n## Keys\n\n| Key |\n|---|\n| Value |\n\n## Actions\n\n- Item\n")
    assert plan.complete, plan.issues
    assert len(plan.units) == 5
    assert plan.units[2].context == ("# Настройка\n", "# Setup\n", "## Ключи\n", "## Keys\n")


def test_root_mixed_translation_uses_distinct_container_boundary(api):
    plan = prepare(api, "До.\n\n| Ключ |\n|---|\n| Значение |\n\nПосле.\n",
                   "Before.\n\n| Key |\n|---|\n| Value |\n\nAfter.\n")
    assert plan.complete, plan.issues
    assert len(plan.units) == 3
    assert plan.units[0].en_text == "Before.\n"
    assert plan.units[-1].en_text == "After.\n"


@pytest.mark.parametrize("child_anchors", [("b", "a"), ("a", "a")])
def test_proven_parent_cannot_prove_swapped_or_duplicate_child_anchors(api, child_anchors):
    ru = "# Родитель {#parent}\n\n## Первый {#a}\n\n## Второй {#b}\n"
    first, second = child_anchors
    en = f"# Parent {{#parent}}\n\n## First {{#{first}}}\n\n## Second {{#{second}}}\n"
    plan = prepare(api, ru, en)
    assert not plan.complete
    child_ids = {plan.en.blocks[1].id, plan.en.blocks[2].id}
    assert child_ids <= {i for issue in plan.issues for i in issue.en_block_ids}
    assert not child_ids.intersection(i for unit in plan.units for i in unit.en_block_ids)
    assert not api.validate_payloads(plan, (plan.units,)).complete
    # Validation recomputes correspondence, even if a caller clears prior errors.
    assert not api.validate_payloads(replace(plan, issues=()), (plan.units,)).complete


def test_identical_documents_do_not_make_duplicate_explicit_child_anchors_unique(api):
    text = "# Parent {#parent}\n\n## First {#a}\n\n## Second {#a}\n"
    plan = prepare(api, text)
    assert not plan.complete
    assert not api.validate_payloads(replace(plan, issues=()), (plan.units,)).complete
