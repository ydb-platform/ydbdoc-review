# ydbdoc-review-ng product vertical

This branch contains the first working product path for `doc_translate`. It
builds an immutable candidate overlay and never publishes a partial or red
bundle. The PR 45949 fixture is used as a real composition test, not as a proof
platform.

Run the product tests:

```sh
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

The CLI takes separate translator and critic executables. Each executable reads
one JSON request on stdin and writes one JSON response on stdout. Translation
responses contain `candidate_utf8`; critic responses contain `verdict` and may
contain a repaired `candidate_utf8`.

```sh
YDBDOC_ALLOWED_ACTORS=sintjuri ydbdoc-review-ng doc_translate \
  --pr 45949 --merged --actor sintjuri --budget-rub 1000 \
  --fixture fixtures/pr-45949 \
  --translator-command /path/to/translator \
  --critic-command /path/to/critic
```

## Frozen input

The independently frozen PR 45949 capture remains the current-main input for
the composition test.

Run the offline verifier and its adversarial tests:

```sh
python3 tools/verify_pr_fixture.py
python3 -m unittest discover -s tests -v
```

The v2 evidence pack stores base64-wrapped byte captures of GitHub HTTP
responses, including all 40 immutable-SHA content lookups and their HTTP 404
responses. Request descriptors contain no credentials. Inventory rows and blob
fixtures are machine-derived from those captures, never accepted as authored
claims.

`manifest.json` and `PROVENANCE.json` are closed canonical documents. Their
provenance root binds the manifest without its root, every authoritative raw
response, every blob, the derived request/inventory/header documents, and the
display provenance template. `checksums.json` is only a corruption check. The
verifier independently recomputes each Git blob SHA-1 using Git's
`blob <size>\0<bytes>` object format and rejects duplicate JSON keys, unknown
fields, unsafe paths, oversized artifacts and credential-bearing headers.
