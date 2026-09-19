
"""Quality loop returns the final durable map for translate/verify/continue."""
from dataclasses import replace

from tests.contract.test_quality_loop_t09 import PATH
from tests.unit.test_quality_parts_f07 import mapped


def test_missing_map_repair_is_frozen_and_returned(setup):
    run, calls, freezes, _, _, *_ = setup
    source, initial = mapped((0, 1, 3))
    initial = replace(initial, path=PATH)
    _, result = run(source=source, target=initial.text, initial=initial)
    assert result.status == 'GREEN'
    assert len(freezes) == 1
    assert len([call for op, call in calls if op == 'repair']) == 3
    assert result.files[0].text == result.candidate.text(PATH) == source
    assert not result.files[0].unfinished
    assert len(result.rounds) == 2
    assert len(result.rounds[0].checks[0].parts) == 4


def test_verify_saves_map_without_initial_translation(setup):
    run, calls, *_ = setup
    _, result = run()
    assert result.status == 'GREEN'
    assert result.files[0].text == result.candidate.text(PATH)
    assert result.files[0].chunks
    assert all(op == 'critic' for op, _ in calls)


def test_long_verify_fixes_map_before_first_check_and_keeps_ids(setup, monkeypatch):
    import tests.contract.test_quality_loop_t09 as fixture_module
    import ydbdoc_review.quality_loop as loop_module
    from ydbdoc_review.document import RequestBudget
    monkeypatch.setattr(fixture_module, 'BUDGET', RequestBudget(5000, 350, lambda m: len(str(m))))
    prepared = []
    actual = loop_module.repair_document

    def capture(*args, **kwargs):
        result = actual(*args, **kwargs)
        if kwargs.get('prepare_only'):
            prepared.append(result.file_result)
        return result

    monkeypatch.setattr(loop_module, 'repair_document', capture)
    run, calls, *_ = setup
    source = '\n\n'.join(f'Section {i} has exact facts.' for i in range(30))
    target = '\n\n'.join(f'Раздел {i} содержит факты.' for i in range(30))
    _, result = run(source=source, target=target)
    assert result.status == 'GREEN', result.issues
    assert len(prepared) == 1 and len(prepared[0].chunks) > 1
    assert [slot.chunk for slot in result.files[0].chunks] == [slot.chunk for slot in prepared[0].chunks]
    assert len(result.rounds[0].checks[0].parts) == len(prepared[0].chunks)
    assert all(operation != 'translation' for operation, _ in calls)
