"""Redact known credentials in public diagnostics, not stored model transcripts."""
import re


def redact_known(text: str, secrets: tuple[str, ...]) -> str:
    values = sorted({value for value in secrets if value}, key=len, reverse=True)
    if not values:
        return text
    # One pass handles overlapping credentials without rewriting the replacement.
    return re.sub('|'.join(map(re.escape, values)), lambda _: '[REDACTED]', text)
