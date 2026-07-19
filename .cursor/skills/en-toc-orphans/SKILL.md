---
name: en-toc-orphans
description: >-
  Find English YDB docs pages that exist on disk but are not linked from the EN
  Diplodoc toc graph (sidebar orphans). Use when the user asks about EN pages
  missing from toc/menu, unreachable translations, orphan EN recipes, toc
  orphans after observability moves, or wants an audit of EN translations
  without toc hrefs.
---

# EN toc orphans audit

## Goal

List every `ydb/docs/en/**/*.md` page that is **not reachable** from
`ydb/docs/en/core/toc_p.yaml` via `href` / `include.path` (same graph as
`orphan_toc_page` / §6.117). Skip `_includes/`.

RU and EN **menu structures must match** (§6.121): every sidebar `href` /
`include.path` in RU should appear in EN and vice versa. Orphan EN files that
are not in any menu should be **deleted** (or wired into toc), not left on disk
— especially when `redirects.yaml` already points old URLs at a new section.

## When to run

- User mentions orphan EN pages, missing toc entries, unreachable translations
- After a docs restructure (content moved to `reference/`, section removed from RU toc)
- Before/after a translation PR that touches toc yaml
- Investigating `build-docs` / Diplodoc warnings about undeclared pages

## How to audit

Prefer the packaged script from **ydbdoc-review** (this repo):

```bash
cd /path/to/ydbdoc-review
.venv/bin/python scripts/find_en_toc_orphans.py --repo-path /path/to/ydb
```

Or with the package on `PYTHONPATH` / installed editable:

```bash
python -m scripts.find_en_toc_orphans --repo-path /path/to/ydb
```

Exit code `1` if any orphans; prints one repo-relative path per line.

### Without the script (manual BFS)

1. Checkout `ydb-platform/ydb` at the ref to audit (`main` or a PR head).
2. BFS from `ydb/docs/en/core/toc_p.yaml`: collect all `href: *.md` and follow
   `include.path` child tocs.
3. Enumerate `ydb/docs/en/**/*.md` excluding `_includes/`.
4. Report set difference: files − reachable.

Equivalent library call:

```python
from ydbdoc_review.validation.toc_targets import find_en_pages_missing_from_toc
orphans = find_en_pages_missing_from_toc("/path/to/ydb")
```

## How to fix

For each orphan path:

1. Check whether RU still has a counterpart under `ydb/docs/ru/...`.
2. Check `ydb/docs/redirects.yaml` for a `from:` of the old public URL.
3. Prefer one of:
   - **Delete** the EN file if content moved (example: recipe OTel pages →
     `reference/ydb-sdk/observability/`, redirects already present) — see
     [ydb#47107](https://github.com/ydb-platform/ydb/pull/47107).
   - **Add** the `href` to the correct EN toc (and keep RU toc in sync).
   - Do **not** re-run `doc_translate` on an old source PR hoping it will clean
     orphans after a restructure — it often reintroduces stale menu items.

## Related pipeline checks

| Check | When | § |
|-------|------|---|
| `orphan_toc_page` | Translated EN `.md` in current PR not on toc graph | §6.117 |
| `toc_structure_parity` | RU/EN toc href\|include sets differ (new EN-only / RU-only) | §6.121 |
| `toc_en_only_legacy` | EN-only entry already on EN main (warning; should converge) | §6.121 |

## Output for the user

- Count + list of orphan paths
- For each: suggested action (delete vs wire into toc) with evidence (redirect /
  RU absence / reference replacement)
- Link to any open cleanup PR
