"""Compact durable context is independent of unrelated repository entries."""
from dataclasses import dataclass, field, replace

from tests.unit.test_store_t12 import adapter, db, paid  # noqa: F401
from ydbdoc_review.continuation import select_files
from ydbdoc_review.document import ChunkResult, assemble_file, chunk_document, protect
from ydbdoc_review.quality_loop import SelectedFile
from ydbdoc_review.runner import RunResult
from ydbdoc_review.store import decode, encode


def test_context_scale_and_exact_correspondence(db):  # noqa: F811
    store, _, _ = db
    persisted = adapter(store)
    source = '# Title\n\nFirst paragraph.\n\nSecond paragraph.\n'
    files = []
    for path in ('docs/en/a.md', 'docs/en/b.md'):
        protected = protect(source, path=path)
        chunks = chunk_document(protected, lambda text: len(text) < 30)
        parts = tuple(ChunkResult(chunk, None if i == 0 else chunk.text,
                                  None if i == 0 else chunk.text,
                                  unfinished=i == 0, status='missing' if i == 0 else 'complete')
                      for i, chunk in enumerate(chunks))
        state = assemble_file(path, protected, parts)
        files.append(SelectedFile(path, source, 'en', initial=state))
    @dataclass
    class Tree:
        sha: str = 'fixed-candidate'
        entries: dict = field(default_factory=dict)
        def read(self, path):
            assert path in {file.path for file in files}
            return b'Current translation\n'
    tree = Tree()
    result = RunResult(candidate=tree, selected_files=tuple(files),
                       files=tuple(file.initial for file in files),
                       unfinished_files=tuple(file.path for file in files),
                       quality={'candidate': tree, 'rounds': ['nested runtime']})
    persisted.save(result)
    small = store.get(persisted.run_id, 'context')
    tree.entries = {f'unrelated/{i}.bin': ('100644', str(i)) for i in range(154000)}
    persisted.save(result)
    large = store.get(persisted.run_id, 'context')
    assert large == small
    context = decode(large)
    assert len(large) < 20000
    assert not {'candidate', 'quality', 'plan', 'files', 'selected_files'} & context['result'].keys()
    resumed = select_files(context, 'Fix missing parts')
    assert tuple(f.initial for f in resumed) == tuple(f.initial for f in files)
    assert resumed[0].initial.chunks[0].response is None
    assert all(f.initial.protected.atoms == original.initial.protected.atoms
               for f, original in zip(resumed, files, strict=True))
    # A later continue selects a subset but must keep the other durable file.
    persisted.save(replace(result, selected_files=(resumed[0],), files=()),
                   known_files=context['known_files'])
    latest = store.context(persisted.run_id)
    assert len(latest['known_files']) == len(latest['final_files']) == 2


def test_context_call_references_resolve_to_canonical_objects(db):  # noqa: F811
    store, _, _ = db
    persisted = adapter(store)
    attempt = paid()
    persisted.record_request(attempt.request)
    persisted.record_attempt(attempt)
    persisted.save(RunResult(attempts=(attempt,)))
    context = store.context(persisted.run_id)
    assert context['requests'] == ['request/req/0']
    assert context['attempts'] == ['attempt/req/0']
    assert decode(store.get(persisted.run_id, context['requests'][0])) == decode(encode(attempt.request))
    assert decode(store.get(persisted.run_id, context['attempts'][0])) == decode(encode(attempt))
