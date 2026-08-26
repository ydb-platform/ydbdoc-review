from ydbdoc_review.validation.hard_file_validator import HardValidationCode, validate_whole_file


def test_whole_file_gate_accepts_localized_prose_with_same_structure():
    ru = "# RU {#same}\n\n{% list tabs %}\n- Python\n  ```py\n  x\n  ```\n{% endlist %}\n"
    en = ru.replace("# RU", "# EN")
    assert validate_whole_file(path="a.md", authoritative_ru=ru, candidate_en=en) == []


def test_whole_file_gate_blocks_fence_and_placeholder():
    errors = validate_whole_file(path="a.md", authoritative_ru="```py\nx\n```\n", candidate_en="⟦SEG⟧\n")
    codes = {error.code for error in errors}
    assert HardValidationCode.FENCE_STRUCTURE in codes
    assert HardValidationCode.PLACEHOLDER_RESIDUE in codes
