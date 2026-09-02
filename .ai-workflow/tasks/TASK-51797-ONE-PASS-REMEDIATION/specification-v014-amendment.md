## Approved-contract legacy test alignment v014

This amendment updates five legacy test expectations to already approved
R-006, R-015 and R-012 behavior. It changes no production behavior or fixture.

### R-006 chunker fixture propagation

Both real-fixture loops in `tests/unit/test_chunker.py` parse every fixture as
valid. Propagate the exact v010 negative classification for only
`en/core/reference/ydb-sdk/topic.md`: import
`AmbiguousYfmStructureError`, use a one-value exact set with an exact-set guard,
and in each loop assert the exception matching `unowned direct-depth`, then
continue. Every other fixture retains all existing chunking assertions.

### R-015 image atom assertion

In
`tests/unit/test_image_alt_protect.py::test_image_alt_is_translatable_src_protected`,
replace only the obsolete `⟦S1⟧` wrapper expectations and simulated translation
with the approved single-image atom boundaries:
`⟦IMGBEGIN_1⟧<translatable alt>⟦IMGEND_1⟧`. Assert both exact boundaries are
present, `⟦S1⟧` is absent, translated alt is placed between the same boundaries,
and existing rendered-alt/source preservation assertions remain unchanged.

### R-012 acquisition exhaustion assertion

In
`tests/unit/test_pipeline_orchestrator.py::test_run_pr_translation_isolates_validation_failure`,
replace only the obsolete `protect-token sequence` error substring with exact
approved diagnostic substring
`translation_acquisition_exhausted: role=translate`. Existing result-count
assertions remain unchanged.

### Environment-only failures

The three Eliza TLS failures are `PermissionError` while writing a user cache
outside the writable sandbox. They require rerun with a writable isolated
`XDG_CACHE_HOME` or equivalent allowed cache directory. They authorize no source
or test semantic change and must be reported separately from product failures.

v014 resolves exact predecessor v013 and self-seals only its four protocol
artifacts. No wildcard classification, skip/xfail, broad exception acceptance,
production edit, fixture edit or unrelated expectation change is authorized.
