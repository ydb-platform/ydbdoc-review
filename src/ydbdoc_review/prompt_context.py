"""Explicit caller-supplied terminology, shared by translation and quality prompts."""
from collections.abc import Mapping


def glossary_context(glossary: Mapping[str, str] | None) -> dict:
    # The current configuration accepts inline term pairs, not remote resources.
    # State that actual provenance; never imply that a URL has been fetched.
    return {
        "source": "caller-supplied inline glossary mapping" if glossary else None,
        "terms": dict(glossary or {}),
        "rules": [
            "Apply these explicit source-term to target-term mappings in their relevant context.",
            "Preserve protected atoms; terminology rules do not authorize changing code or URLs.",
            "A URL is a reference, not glossary contents: do not assume it was read or invent its rules.",
            "Do not infer additional mandatory terminology from unspecified external sources.",
        ] if glossary else [],
    }
