"""Corpus round trips through the actual production atom encoder/decoder."""
from ydbdoc_review.document import protect, restore


def roundtrip(text):
    protected = protect(text)
    result = restore(protected, protected.text)
    assert result.encode('utf-8') == text.encode('utf-8')
    return result
