## R-006 strict parser real-fixture classification v010

This amendment changes only the R-006 integration-test classification for one
existing malformed EN fixture. Every parser rule and every other approved
requirement remains unchanged.

### Observed conflict and classification

`tests/fixtures/markdown_files/en/core/reference/ydb-sdk/topic.md` contains, at
line 253, nonblank direct-depth prose after `{% list tabs group=lang %}` and
before the first `- C++` tab header. The corresponding RU fixture places the
same prose before the tabs opener. Under the approved R-006 contract the EN
shape is genuinely ambiguous: there is no tab that can own the prose.

`AmbiguousYfmStructureError` is therefore the required parser result for this
exact EN fixture. The legacy integration tests incorrectly classify every real
fixture as valid input. The parser must not be weakened and the prose must not
be silently attached, moved, discarded or converted into a synthetic tab.

### Exact test-only adjustment

In `tests/integration/test_real_files_round_trip.py` define one exact relative
path set containing only:

`en/core/reference/ydb-sdk/topic.md`

For that path:

- `test_parse_does_not_crash` must assert
  `AmbiguousYfmStructureError` with message matching `unowned direct-depth`;
- `test_round_trip_stable` must assert the same parse failure and return without
  attempting render or a second parse.

Every other fixture retains the existing must-parse and stable-round-trip
expectations. The exact set is asserted in a dedicated test so no new fixture
can be silently added. The EN or RU fixture bytes are not changed.

### Acceptance

- all 52 strict R-006 parser tests remain GREEN;
- the real RU `topic.md` zero-loss/count/deterministic round-trip remains GREEN;
- both generic integration parametrizations explicitly pass for the one EN
  negative fixture by observing the required exception;
- every other real fixture continues to parse and round-trip under the existing
  assertions;
- no parser or fixture change is made.

Broad `ValueError` acceptance, `xfail`, `skip`, wildcard path matching, parser
fallback, prose attachment, fixture editing and unrelated test changes are
forbidden.
