# Rollback: segment-based translation

Before the **file-level translation** rewrite (line plan + 1–N requests per file), the pipeline used per-unit segment calls.

| | |
|---|---|
| **Git tag** | `pre-file-translate` |
| **Commit** | `4bf42f4a0429edcccdeeac85daabd7d0592d2d7c` |
| **Message** | Fix missing fence helper imports in tabs_translate |

## Restore segment pipeline only

```bash
git checkout pre-file-translate
# or
git checkout 4bf42f4a0429edcccdeeac85daabd7d0592d2d7c
```

## Run old pipeline on a release build

Set env `YDBDOC_TRANSLATE_LEGACY_SEGMENTS=true` on a build that still includes `_translate_document_segment_legacy` in `pipeline_v2.py`.
