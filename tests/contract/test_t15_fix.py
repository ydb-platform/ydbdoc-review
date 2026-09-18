"""T15 regressions: refusal-only effects, cleanup outcome, observable YFM behavior."""
import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.contract.test_t15_cli import assert_saved, process  # noqa: F401
from tests.contract.test_t15_independent import (  # noqa: F401
    MODES,
    independent,
    source_only,
    successful,
)
from ydbdoc_review.document import protect, restore
from ydbdoc_review.links import references
from ydbdoc_review.parsing.inline_locations import prose_source_spans
from ydbdoc_review.parsing.markdown_parser import create_parser, parse_review_blocks
from ydbdoc_review.quality import deterministic_checks, structure_counts
from ydbdoc_review.store import StorageError, YDBStore


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('red', [False, True], ids=['completed', 'model-failed'])
def test_cleanup_preserves_reported_saved_result_and_cost(independent, mode, red):  # noqa: F811
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    p.update(close_error=True, model_error=red, model=[], comments=[])
    result = p.run(mode, 2 if mode == 'doc_continue' else 1)
    status = 'RED' if red else 'GREEN'
    assert result.returncode == int(red), result.stdout + result.stderr
    assert result.stdout.startswith(status + ':')
    assert 'Cleanup warning' in result.stderr and 'independent SDK close failure' in result.stderr
    context, attempts = assert_saved(p, mode, status)
    assert len(attempts) == len(p.read()['model'])
    assert all(status in body for _, body in p.read()['comments'])
    assert len(p.read()['comments']) == (1 if mode == 'doc_verify' or (red and mode == 'doc_translate') else 2)
    assert context['result']['status'] == status


@pytest.mark.parametrize('failed', [('pool',), ('driver',), ('pool', 'driver')])
def test_store_close_attempts_both_resources_and_reports_each_failure(failed):
    calls = []

    def stop(name):
        calls.append(name)
        if name in failed:
            raise RuntimeError(name + ' failed')

    store = object.__new__(YDBStore)
    store.pool = SimpleNamespace(stop=lambda: stop('pool'))
    store.driver = SimpleNamespace(stop=lambda: stop('driver'))
    with pytest.raises(StorageError) as error:
        store.close()
    assert calls == ['pool', 'driver']
    for name in failed:
        assert name + ' failed' in str(error.value)


@pytest.mark.parametrize('mode', MODES)
def test_denial_delivery_failure_still_has_no_work_effects(independent, mode):  # noqa: F811
    p = independent
    before = p.git('rev-parse', 'HEAD')
    p.update(report_error=True)
    result = p.run(mode, GITHUB_ACTOR='outsider')
    assert result.returncode == 1
    assert 'RED:' in result.stdout and 'YDBDOC_ALLOWED_ACTORS' in result.stdout
    assert 'report' in result.stdout
    state = p.read()
    assert len(state['http']) == 1
    assert state['http'][0] == ['POST', '/repos/up/docs/issues/1/comments']
    assert not state['model'] and not state.get('factories', 0)
    assert len(state['pulls']) == 1
    assert not (p.root / 'database.sqlite').exists()
    assert p.git('rev-parse', 'HEAD') == before
    assert p.git('status', '--porcelain') == b''


CASES = json.loads((Path(__file__).parents[1] / 'fixtures/yfm_survivors.json').read_text())

# Hand-reviewed observable expectations for the 116 literal inputs, in fixture
# order. P/Y/H/L/T/R/C = paragraphs, YFM containers, headings, list items,
# tables, rows, code. No expected AST classes or renderer output is retained.
# Each entry also chooses existing prose INSIDE the input, never an appended probe.
EXPECTATIONS = [
    # Images 0..8
    (1, 0, 'alt'), (1, 0, 'alt'), (1, 0, 'alt'), (1, 0, 'alt'),
    (1, 0, 'alt'), (1, 0, 'alt'), (1, 0, 'alt'), (1, 0, 'alt'), (1, 0, 'alt'),
    # Conditionals 9..23 (if + each branch are containers)
    (1, 2, 'OSS only'), (1, 2, 'Enterprise text'), (2, 3, 'Enterprise'),
    (3, 4, 'Other'), (2, 4, 'Inner text'), (1, 2, 'community edition'),
    (3, 0, 'Some content'), (1, 2, 'OSS only'), (1, 2, 'Ent'),
    (2, 3, 'Ent'), (3, 4, 'Other'), (3, 2, 'OSS section'),
    (2, 4, 'Inner'), (0, 2, None), (1, 3, 'OSS-only note'),
    # Includes 24..39: directives do not create prose paragraphs
    (0, 0, None), (0, 0, None), (0, 0, None), (0, 0, None),
    (2, 0, 'Intro paragraph'), (0, 0, None), (0, 0, None),
    (0, 0, None), (0, 0, None), (0, 0, None), (3, 0, 'Middle'),
    (0, 1, None), (1, 1, 'Python'), (0, 0, None),
    (1, 0, None), (1, 0, 'Use'),
    # Variables 40..64
    (1, 0, 'Hello'), (1, 0, 'Use'), (1, 0, None), (1, 0, None), (1, 0, None),
    (0, 0, 'Run'), (1, 0, 'glossary'), (1, 0, 'glossary'), (1, 0, 'alt'),
    (1, 0, 'for terms'), (1, 0, 'docs'), (1, 0, 'literally'), (0, 0, None),
    (1, 0, None), (1, 0, None), (1, 0, 'Hello'), (0, 0, 'Title with'),
    (1, 0, 'Use'), (1, 0, 'Multiple'), (1, 0, 'Link'), (1, 0, 'Link text'),
    (2, 0, 'another'), (0, 0, 'col'), (1, 0, 'This is'), (1, 0, None),
    # Notes 65..81
    (1, 1, 'Hello'), (1, 1, 'Danger'), (1, 1, 'Some text'), (1, 1, 'Run this'),
    (3, 2, 'Inner warning'), (1, 1, 'Use'), (3, 0, 'Some text'),
    (1, 1, 'Hello'), (1, 1, 'Danger'), (1, 1, 'output'), (1, 1, 'Text'),
    (3, 1, 'Warning text'), (2, 1, 'Second para'), (1, 1, 'for queries'),
    (2, 2, 'Inner'), (1, 1, 'Run'), (3, 1, 'two'),
    # Cuts 82..94
    (1, 1, 'Hidden text'), (1, 1, 'Text'), (0, 1, None), (2, 2, 'Inner text'),
    (1, 2, 'Hidden'), (1, 1, 'to begin'), (3, 0, 'Text'), (1, 1, 'Hidden'),
    (2, 1, 'Second'), (3, 1, 'Inside cut'), (0, 1, None), (2, 2, 'Inner.'),
    (2, 2, 'Inside'),
    # Terms 95..102
    (0, 0, 'A set of nodes'), (1, 0, 'for details'), (1, 0, 'consists of nodes'),
    (1, 0, 'docs'), (0, 0, 'A set of nodes'), (0, 0, 'A unit of distribution'),
    (1, 0, 'for details'), (1, 0, 'Tablet definition'),
    # Tabs 103..115: one YFM container, ordinary list titles/body paragraphs
    (4, 1, 'Go text'), (2, 1, 'Content one'), (4, 1, 'Python body'),
    (2, 1, 'Body'), (1, 1, 'Bash'), (2, 1, 'Content'),
    (3, 0, 'Content'), (4, 1, 'Python text'), (2, 1, 'Run this command'),
    (2, 1, 'Content'), (6, 1, 'Text B with'), (2, 2, 'Be careful'),
    (3, 1, 'Context shared by every tab'),
]
assert len(EXPECTATIONS) == len(CASES) == 116
EXTRA_COUNTS = {
    22: dict(code=1), 45: dict(headings=1), 52: dict(code=1), 56: dict(headings=1),
    61: dict(list_items=2), 62: dict(tables=1, rows=2), 67: dict(headings=1),
    68: dict(code=1), 80: dict(code=1), 81: dict(list_items=2),
    84: dict(code=1), 92: dict(code=1),
    36: dict(list_items=1), 94: dict(list_items=1),
    103: dict(list_items=2), 104: dict(list_items=1), 105: dict(list_items=2),
    106: dict(list_items=1), 107: dict(list_items=1, code=1),
    108: dict(list_items=1), 109: dict(list_items=1), 110: dict(list_items=2),
    111: dict(list_items=1, code=1), 112: dict(list_items=1),
    113: dict(list_items=2), 114: dict(list_items=1), 115: dict(list_items=1),
}
LINKS = {
    **{index: [('asset', 'image.png')] for index in range(9)},
    24: [('include', '../_includes/foo.md')], 25: [('include', 'path/to/x.md')],
    26: [('include', '/ydb/docs/_includes/auth.md')], 27: [('include', 'path.md')],
    28: [('include', '../inc.md')], 29: [('include', 'path.md')], 30: [('include', 'path.md')],
    31: [('include', '../_includes/foo.md')], 32: [('include', '/abs/path/to/file.md')],
    33: [('include', 'empty-text.md')], 34: [('include', 'one.md'), ('include', 'two.md')],
    35: [('include', '../inc.md')], 36: [('include', '../py.md')], 37: [('include', 'path.md')],
    46: [('link', '{{ link-glossary }}')], 47: [('link', '{{ link-glossary }}')],
    48: [('asset', '{{ image-path }}')], 49: [('link', '{{ link-glossary }}')],
    50: [('link', 'http://x')], 59: [('link', '{{ url-var }}')],
    60: [('link', 'http://x')], 98: [('link', 'http://x')],
}


@pytest.mark.parametrize('index,case', list(enumerate(CASES)), ids=[c['id'] for c in CASES])
def test_yfm_corpus_observable_invariants(index, case):
    source = case['source']
    paragraphs, yfm, prose = EXPECTATIONS[index]
    expected = dict(headings=0, paragraphs=paragraphs, list_items=0, tables=0, rows=0, code=0, yfm=yfm)
    expected.update(EXTRA_COUNTS.get(index, {}))
    assert asdict(structure_counts(source)) == expected
    refs = references(source)
    assert [(r.kind, r.href) for r in refs] == LINKS.get(index, [])
    for ref in refs:
        assert ref.location is not None
        ref.location.validate(source)
        assert ref.href in ref.location.quote
    blocks = parse_review_blocks(source)
    lines = source.splitlines(keepends=True)
    for block in blocks:
        # Exact physical source offsets, not merely nonempty slices.
        assert block.span.start == len(''.join(lines[:block.line_start - 1]))
        assert block.span.end == len(''.join(lines[:block.line_end]))
        assert source[block.span.start:block.span.end] == ''.join(lines[block.line_start - 1:block.line_end])
    protected = protect(source)
    assert restore(protected, protected.text) == source
    if prose:
        assert prose in protected.text
        # Check actual inline provenance at each physical character of this input.
        start = source.index(prose)
        spans = prose_source_spans(create_parser(source_locations=True).parse(source))
        covered = {(span.start, span.end) for span in spans}
        assert all((pos, pos + 1) in covered for pos in range(start, start + len(prose)))
        assert restore(protected, protected.text.replace(prose, 'Translated prose', 1)) == source.replace(prose, 'Translated prose', 1)
    issues = deterministic_checks(source, source, path='x.md', target_lang='en')
    syntax = [issue for issue in issues if issue.code == 'syntax']
    assert bool(syntax) == (index in {15, 71, 88, 109})
    for variable in re.findall(r'\{\{\s*[^{}\s][^{}]*\}\}', source):
        assert variable not in protected.text
    for ref in refs:
        assert ref.href not in protected.text
    if index in {95, 99, 100}:
        assert [block.kind for block in blocks] == ['term_definition']
    if index in {39, 51, 52}:
        literal = '{% include [x](y.md) %}' if index == 39 else '{{ name }}'
        assert literal not in protected.text
        assert not refs


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'], ids=['LF', 'CRLF', 'CR'])
def test_nested_physical_spans_and_yfm_table(newline):
    lines = ['# Title', '', '{% note info "Label" %}', '', '> - Repeated text.',
             '>   Wrapped text.', '', '{% endnote %}', '', '#|',
             '|| Name | Value ||', '|| Text | `Text` ||', '|#', '']
    source = newline.join(lines)
    blocks = parse_review_blocks(source)
    assert [(b.kind, b.line_start, b.line_end) for b in blocks] == [
        ('heading', 1, 1), ('yfm_note', 3, 8), ('table', 10, 13)]
    assert asdict(structure_counts(source)) == dict(headings=1, paragraphs=1,
        list_items=1, tables=1, rows=2, code=0, yfm=1)
    for block, first, last in zip(blocks, (0, 2, 9), (1, 8, 13), strict=True):
        assert source[block.span.start:block.span.end] == newline.join(lines[first:last]) + newline
    tokens = create_parser(source_locations=True).parse(source)
    origins = {(s.start, s.end) for s in prose_source_spans(tokens)}
    for word in ('Repeated text.', 'Wrapped text.', 'Name', 'Value', 'Text'):
        start = source.index(word)
        assert all((i, i + 1) in origins for i in range(start, start + len(word)))
    code_start = source.index('`Text`') + 1
    assert all((i, i + 1) not in origins for i in range(code_start, code_start + 4))
    p = protect(source)
    changed = p.text.replace('Text', 'Translated').replace('Label', 'Caption')
    expected = source.replace('|| Text |', '|| Translated |').replace('"Label"', '"Caption"')
    assert restore(p, changed) == expected
