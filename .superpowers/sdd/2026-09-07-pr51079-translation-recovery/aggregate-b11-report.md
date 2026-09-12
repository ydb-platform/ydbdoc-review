# Aggregate B11 developer report

## Scope

- Base: `6be3d9ab572caeb34bbc42d7e7961667b4aadd68`.
- Branch: `fix/pr51079-aggregate-b11`.
- Changed only the tabs parser/renderer contract, its focused unit test, and this report.
- Left `tests/integration/test_real_files_round_trip.py` unchanged because its fixed-point assertion is current and passes with the repair.

## Root cause

Graph discovery traced the relevant path through `_parse_yfm_tabs`, `_render_yfm_tabs`, and `_render_yfm_tab`. The parser deliberately preserves blocks outside the tabs container's bullet lists as `YfmTab(title=[], children=[block])` sentinels. The renderer previously treated every sentinel as a real list item and emitted `- `. On the next parse, that synthetic marker became another empty tab, so each parse/render cycle added untitled tabs.

The failure reproduced on the exact fixture `tests/fixtures/markdown_files/en/core/reference/ydb-sdk/topic.md`. Before the repair, its first-to-second render diff gained synthetic `- ` lines near the shared tabs paragraph and the unindented C++ body. AST inspection showed the first parse's content-bearing untitled sentinels expanding with empty sentinels after the first render.

## TDD repair

Added `test_tabs_preserve_content_outside_bullet_list_without_empty_tab_marker`. The minimal fixture contains a shared paragraph before a valid named tab. Before production code changed, the test failed because the first render inserted `- ` and indented the shared paragraph.

`_render_yfm_tabs` now renders content-bearing untitled sentinels directly at container level. Named tabs and truly empty list items retain the existing `_render_yfm_tab` path. The preserved block content therefore remains in order without creating syntax that parses as another tab. Parser and renderer comments now document the sentinel contract.

## Verification

- Focused RED: the new unit test failed before the renderer change with an unexpected `- ` marker.
- Focused GREEN: the new unit test and exact `en/core/reference/ydb-sdk/topic.md` fixed-point case passed together.
- Aggregate gate: `134 passed` for `tests/unit/test_yfm_tabs.py`, `tests/unit/test_parser_round_trip.py`, `tests/unit/test_renderer_coverage.py`, and `tests/integration/test_real_files_round_trip.py` with `XDG_CACHE_HOME=/private/tmp/pr51079-b11-cache`.
- Exact real-file semantic check: the parsed AST equals the AST after render and reparse, and the first and second rendered texts are equal.
- `git diff --check` passed.
- Focused Ruff checks passed when excluding pre-existing whole-file `I001`, `F401`, and derivative `RUF100` findings. Full Ruff formatting/checking is not clean at the base because the scoped parser, renderer, and tabs test files already contain unrelated import-order, unused-import, and formatting debt.

No push, tag, GitHub mutation, release, or production action was performed.
