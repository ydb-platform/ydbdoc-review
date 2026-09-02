## R-004 final manifest lifecycle v015

This amendment changes only the lifecycle of the generated implementation
manifest. The immutable baseline snapshot remains capture-once and must never be
rewritten.

### Root cause

The approved bootstrap correctly refused to overwrite the manifest, but it ran
before the remaining approved implementation and later reviewed protocol
artifacts were materialized. A manifest that must equal the entire current
worktree delta necessarily becomes stale after any later authorized change.
Validation correctly reports `manifest paths do not exactly match worktree
delta`. Treating the initial generated manifest as immutable is incompatible
with its role as final-delta evidence.

### Two-command lifecycle

1. `bootstrap-manifest` remains creation-only and refuses an existing output.
2. Add `refresh-manifest`, which requires an existing output and regenerates it
   deterministically from the complete current delta plus its own output path
   using the latest resolved v015 contract.
3. Refresh builds and validates the complete replacement in memory, writes a
   sibling temporary file, fsyncs it, and atomically replaces only the exact
   configured manifest path. On any error the previous manifest bytes remain.
4. Refresh uses the existing normalized manifest self-hash. Repeating refresh
   without any other worktree change must produce byte-identical output.
5. Refresh never changes or recaptures the snapshot. Snapshot digest/base/count
   verification happens before manifest generation.
6. Every current path must already have an approved deterministic mapping. An
   unclassified, wildcard-only, forbidden deletion or unexpected protocol path
   makes refresh RED; it is never silently omitted or automatically trusted.

### Required timing

Run refresh only at a quiescent checkpoint after all currently approved code,
tests, reports and reviewed protocol artifacts have been materialized. Run the
exact validation immediately afterward. Any subsequent worktree change makes
the manifest stale by definition and requires another deterministic refresh
followed by validation. A green result is valid only for the exact unchanged
delta that was refreshed and validated.

For this amendment, v015 resolves exact predecessor v014 and self-seals its own
four protocol paths. After the external v015 response is materialized, the
developer implements `refresh-manifest`, refreshes once, and validates. Thus the
amendment does not invalidate its own final manifest.

No hand-authored entry, snapshot mutation, best-effort generation, path
omission, generic protocol trust, non-atomic overwrite or unrelated change is
authorized.
