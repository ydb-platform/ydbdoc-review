## R-006 strict negative-fixture propagation to reinsert v013

This amendment changes only one legacy identity test so it respects the exact
EN negative-fixture classification approved in v010. Parser, reinsert behavior,
fixtures and every other requirement remain unchanged.

### Root cause

`tests/unit/test_reinsert.py::test_identity_on_real_fixtures` treats every
fixture as a valid parse and records every exception as an identity failure.
The exact fixture
`en/core/reference/ydb-sdk/topic.md` is already approved in v010 as invalid
under strict R-006 because it has unowned direct-depth prose before the first tab
header. The test therefore misclassifies the required
`AmbiguousYfmStructureError` as a reinsert regression.

### Exact adjustment

In `tests/unit/test_reinsert.py`:

1. Import `AmbiguousYfmStructureError` from the existing parser AST types.
2. Define an exact immutable set containing only
   `en/core/reference/ydb-sdk/topic.md`.
3. In `test_identity_on_real_fixtures`, compute each fixture's POSIX relative
   path. For that one exact path, assert that `parse_markdown(text)` raises
   `AmbiguousYfmStructureError` matching `unowned direct-depth`, then continue
   without invoking the identity pipeline.
4. Every other fixture retains the existing direct-render versus identity
   comparison and failure aggregation unchanged.
5. Add an exact-set assertion so no additional negative fixture can be added
   silently.

No broad exception acceptance, `skip`, `xfail`, wildcard, parser fallback,
fixture edit, prose attachment, identity-pipeline change or behavior expansion
is authorized.

v013 also extends only the R-004 amendment resolver from exact predecessor v012
and self-seals its own four protocol artifacts. This protocol composition does
not enlarge the R-006 implementation scope.
