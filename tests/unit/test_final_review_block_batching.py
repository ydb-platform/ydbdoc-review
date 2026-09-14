from tests.unit.test_final_review_block_coverage import api, prepare


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
