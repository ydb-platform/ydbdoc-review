"""TOC and redirect YAML — scoped merge (only PR-changed entries)."""

from ydbdoc_review.navigation.redirects import (
    merge_en_redirects_yaml,
    parse_redirect_entries,
    redirect_translate_scope,
    validate_redirect_merge,
)
from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    parse_toc_items,
    toc_translate_scope,
    validate_toc_merge,
)

__all__ = [
    "merge_en_redirects_yaml",
    "merge_en_toc_yaml",
    "parse_redirect_entries",
    "parse_toc_items",
    "redirect_translate_scope",
    "toc_translate_scope",
    "validate_redirect_merge",
    "validate_toc_merge",
]
