from tests.unit.test_final_review_block_coverage import api as coverage_api
from tests.unit.test_final_review_block_coverage import prepare

api = coverage_api


def test_long_block_uses_whole_ru_en_payload_and_hard_limit(api):
    plan = prepare(api, "Source. " * 200, "Much longer target with `code`. " * 200)
    unit = plan.units[0]
    size = len(api.serialize_review_batch((unit,)).encode())
    accepted = api.batch_review_units(plan, budget_bytes=100, hard_limit_bytes=size)
    assert accepted.complete
    assert accepted.batches == ((unit,),)
    assert len(accepted.batches[0][0].en_text) > 1200
    rejected = api.batch_review_units(plan, budget_bytes=100, hard_limit_bytes=size - 1)
    assert not rejected.complete
    assert not rejected.batches
    assert rejected.issues[0].en_block_ids == unit.en_block_ids


def test_empty_inventory_does_not_mask_failed_preparation(api):
    plan = prepare(api, "Source", "")
    assert not api.batch_review_units(plan, budget_bytes=100, hard_limit_bytes=200).complete


def test_unrelated_document_text_is_not_repeated_in_each_unit(api):
    text = "# Title {#title}\n\n" + "Paragraph. " * 200 + "\n\n- Item.\n\n" + "Other. " * 200 + "\n"
    plan = prepare(api, text)
    manifest = api.batch_review_units(plan, budget_bytes=10000, hard_limit_bytes=10000)
    assert manifest.complete, manifest.issues
    item = next(u for u in plan.units if u.en_text.startswith("- Item"))
    assert len(api.serialize_review_batch((item,)).encode()) < 2000
    assert all("Paragraph." not in context and "Other." not in context for context in item.context)


def test_hard_limit_issue_uses_frozen_block_offset(api):
    text = "# Title {#title}\n\n" + "Long paragraph. " * 150 + "\n"
    plan = prepare(api, text)
    result = api.batch_review_units(plan, budget_bytes=1500, hard_limit_bytes=1500)
    issue = next(i for i in result.issues if i.reason == "whole unit exceeds reviewer hard limit"
                 and plan.en.blocks[1].id in i.en_block_ids)
    assert issue.span == plan.en.blocks[1].span
