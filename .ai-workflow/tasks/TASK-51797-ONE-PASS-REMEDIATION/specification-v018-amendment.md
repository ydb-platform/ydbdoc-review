## Close v017 reviewer findings v018

This amendment changes only the three choices identified in
`response-v017.yaml`. Every other v017 instruction remains exact and unchanged.

### R-011 navigation role and model binding

Extend both closed role types, and only those types, from
`Literal["translate", "critic", "repair"]` to
`Literal["translate", "critic", "repair", "navigation"]`:

- `translation/model_policy.py::TranslationChatOnce.chat_once.role`;
- `translation/acquisition.py::AcquisitionRole`.

`navigation` is request metadata only. It never becomes a key in
`TranslationModelPolicy`, `_ROLES`, YAML configuration or
`TranslationJobManifest`; no model slug or pair is added. The navigation
controller is constructed exactly with `model_pair=manifest.model_policy.translate`
and `role="navigation"`. `TranslationChatOnce.chat_once` receives the explicit
slug from that translate pair and the metadata role `navigation`. Add type and
runtime tests proving the attempted slug sequence is exactly translate-primary,
translate-fallback under the existing four-request table and that no seventh
slug/config key exists.

### R-016 immutable complete-document validation context

Add in `translation/one_pass.py` exactly these two frozen, slotted dataclasses:

```python
@dataclass(frozen=True, slots=True)
class ValidationAtomRecord:
    block_id: str
    atom_id: str
    sha256: str

@dataclass(frozen=True, slots=True)
class CompleteDocumentValidationContext:
    source_text: str
    source_file: str
    source_container_signature: tuple[tuple[str, int, int], ...]
    source_atoms: tuple[ValidationAtomRecord, ...]
    source_fence_config_signature: tuple[tuple[str, int, int, str], ...]
    expected_links: tuple[tuple[str, str], ...]
    expected_anchor_map: tuple[tuple[str, str], ...]
    en_toc_reachable: frozenset[str] | None
    residual_cyrillic_allowed_ranges: tuple[tuple[int, int, str], ...]
```

The tuple semantics are closed:

- container tuple: parser node kind, source start byte, source end byte, in
  parser traversal order;
- atom record: segment/block ID, placeholder/atom ID, SHA-256 of
  `placeholder.node.model_dump_json()`, in segment then placeholder order;
- fence/config tuple: parser node kind, source start byte, source end byte and
  SHA-256 of the exact source slice, in source order;
- expected link tuple: exact parser-owned source destination and the sole
  allowed EN destination after absolute `/ru/` to `/en/` mapping, in parser
  order;
- anchor map: exact source-to-target explicit anchor pairs returned by the
  existing anchor localization step;
- residual-Cyrillic tuple: only parser-classified source-owned non-prose ranges,
  with start/end UTF-8 byte offsets and node kind. Cyrillic outside these ranges
  is invalid.

Add exactly one constructor
`build_complete_document_validation_context(source_text, source_file,
segments, expected_anchor_map, en_toc_reachable=None)` in `one_pass.py`. It
parses only `source_text`, derives all tuples from the existing parser nodes,
source spans and protected segment records, performs no model call and returns
the frozen context. Construct it once immediately after anchor localization and
before first complete-document validation. Pass the same object unchanged to
base validation, local critic/repair, post-repair validation and the transaction
pre-stage boundary. The validator reparses only the candidate document and
compares it to these immutable records. No dictionary context, lazy field,
callback, mutable AST or context reconstruction after a repair is allowed.

### R-001 alternate fence-test final state

Move every test case that exists only in
`tests/unit/test_fence_comments_read_only.py` into the restored
`tests/unit/test_fence_comments.py`, preserving each original function name or
using that exact name as a parametrized case ID. Run the combined original-path
file GREEN. Then delete `tests/unit/test_fence_comments_read_only.py`. The final
tree contains exactly `tests/unit/test_fence_comments.py`; the migration report
records the alternate file as deleted only after its unique case set is present
and GREEN in the required file. Duplicate copies, empty alternate files and
coverage loss are forbidden.

v018 resolves exact predecessor v017, seals its own four protocol files plus
`response-v017.yaml`, then requires v018 refresh-manifest and immediate validate.

