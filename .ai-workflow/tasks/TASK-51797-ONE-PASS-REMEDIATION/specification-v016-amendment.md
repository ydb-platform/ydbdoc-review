## Chunker real-fixture batch-count contract v016

This amendment changes only the obsolete fixed batch-count assertion in
`tests/unit/test_chunker.py::test_chunker_reasonable_batch_count_on_real_files`.
No production or fixture change is authorized.

### Root cause and decision

The glossary fixtures are large and contain many segments with at least eight
protected placeholders. The existing approved chunker contract deliberately
places each such dense segment in its own batch. Current measured facts are:

- RU glossary: 437 segments, 175 batches;
- EN glossary: 421 segments, 116 batches;
- no glossary batch is oversized.

The legacy universal assertion `batch_count < 100` is unrelated to document
size, total segment count, character budget or mandatory dense-segment
isolation. Its failure is therefore an obsolete performance threshold, not a
parser or chunker regression. Replacing `100` with another number would be an
arbitrary invention and is forbidden.

### Exact contract-based replacement

Keep the v014 exact negative-fixture handling. For every other real fixture,
replace only the `< 100` assertion with verification that every batch boundary
is required by the documented greedy algorithm:

1. A singleton segment with at least eight placeholders is a mandatory dense
   singleton and its following boundary is valid.
2. A singleton segment longer than the 4000-character budget is a mandatory
   oversized singleton and its following boundary is valid.
3. Otherwise, when a following batch exists:
   - if the following batch's first segment is dense, the boundary is required;
   - else the current batch total plus that next segment length must exceed
     4000, proving greedy packing could not legally merge it.
4. Batch order, non-emptiness, budget and segment conservation remain covered by
   the adjacent real-fixture test and existing unit tests.

This exact maximal-packing property scales with fixture size and directly tests
the production contract without inventing a new global count threshold.

v016 resolves exact predecessor v015, self-seals its four protocol files, and
requires the v015 `refresh-manifest` plus validate sequence after materializing
v016 and implementing this test change.
