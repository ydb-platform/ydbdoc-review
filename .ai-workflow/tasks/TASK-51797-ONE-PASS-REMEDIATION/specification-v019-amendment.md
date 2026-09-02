## Exact deletion and Ruff closure v019

This amendment changes only the two mechanical blockers observed after the
externally approved v018 implementation. Focused tests are 128 GREEN, full
pytest is 1354 GREEN and diff-check is GREEN. Product behavior is unchanged.

### R-021 exact clean-at-capture deletions

The base commit contains both paths below. Both were clean at capture, therefore
neither appears as a modified entry in the immutable snapshot. Their current
final state is deleted, exactly as FINAL-009 and v017 require:

- `src/ydbdoc_review/pipeline/skip_paths.py`;
- `tests/unit/test_translate_skip_paths.py`.

Extend the resolved exact-deletion allowlist with only those two literal paths,
mapped to R-021 and FINAL-009. A clean-at-capture path is authorized only when
its base state is present and its final state is deleted. Do not generalize to a
directory, glob, all clean-at-capture files, all R-021 files or an inferred
deletion list. The gate must still reject every other undeclared deletion.

### Exact Ruff corrections in touched navigation code

In `src/ydbdoc_review/pipeline/navigation_merge.py::_toc_translate_scope`, make
only these three character-level prose replacements:

1. docstring line containing `RU base→PR diff`: replace exactly
   `RU base→PR diff` with `RU base-to-PR diff`;
2. docstring line containing `every RU−EN missing href`: replace exactly
   `RU−EN` with `RU-EN`;
3. comment line containing `every RU−EN missing`: replace exactly `RU−EN` with
   `RU-EN`.

These are ASCII-only comment/docstring changes for existing Ruff findings
RUF002, RUF002 and RUF003. Do not add `noqa`, a Ruff waiver, configuration,
formatting, refactor or runtime change. Run Ruff on this exact file and the
approved repository-wide Ruff contract. The three named findings must be gone
and no new finding may appear.

v019 resolves exact predecessor v018 and self-seals exactly its own four
protocol files. The predecessor already seals `response-v018.yaml`. After
materialization and implementation, run v019 refresh-manifest and immediately
run validate with no intervening write.

