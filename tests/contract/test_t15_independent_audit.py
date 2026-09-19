"""Independent boundary and parser invariants retained after cleanup."""
import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from ydbdoc_review.document import MarkerError, protect, restore
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError
from ydbdoc_review.parsing.markdown_parser import parse_review_blocks
from ydbdoc_review.quality import structure_counts

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/yfm_survivors.json').read_text())


@pytest.mark.parametrize('endpoint', ['files', 'comments'])
@pytest.mark.parametrize('last', ['success', 'error'])
def test_real_http_pagination_reaches_second_page(monkeypatch, endpoint, last):
    seen = []

    def send(session, request, **kwargs):
        page = int(parse_qs(urlsplit(request.url).query)['page'][0])
        seen.append(page)
        response = requests.Response()
        response.status_code = 503 if page == 2 and last == 'error' else 200
        response._content = json.dumps([{'id': i} for i in range(100)] if page == 1 else [{'id': 100}]).encode()
        return response

    monkeypatch.setattr(requests.Session, 'send', send)
    github = GitHubClient('dummy')
    iterator = (github.iter_pull_files('up', 'docs', 1) if endpoint == 'files'
                else github.iter_issue_comments('up', 'docs', 1))
    if last == 'error':
        with pytest.raises(GitHubAPIError, match='503'):
            list(iterator)
    else:
        rows = list(iterator)
        assert [r['id'] for r in rows] == list(range(101))
    assert seen == [1, 2]


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_corpus_markers_reject_loss_and_external_prose_changes(case):
    # Appended independent prose proves restore does not return a cached source.
    source = case['source'] + '\n\nIndependent prose sentinel.\n'
    protected = protect(source)
    assert 'Independent prose sentinel.' in protected.text
    translated = protected.text.replace('Independent prose sentinel.', 'Translated sentence.')
    expected = source.replace('Independent prose sentinel.', 'Translated sentence.')
    assert restore(protected, translated) == expected
    if protected.atoms:
        marker = protected.atoms[0].marker
        with pytest.raises(MarkerError):
            restore(protected, translated.replace(marker, '', 1))


@pytest.mark.parametrize('source,expected', [
    ('{% if oss %}\n\nFirst\nwrapped.\n\n{% else %}\n\nSecond.\n\n{% endif %}\n',
     dict(paragraphs=2, yfm=3)),
    ('{% cut "Title" %}\n\n# Heading\n\n- One\n- Two\n\n{% endcut %}\n',
     dict(headings=1, paragraphs=2, list_items=2, yfm=1)),
    ('| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n', dict(tables=1, rows=3)),
    ('```python\nx = 1\n```\n\n    y = 2\n', dict(code=2)),
])
def test_parser_counts_and_source_spans_not_renderer_identity(source, expected):
    counts = asdict(structure_counts(source))
    assert counts == {key: expected.get(key, 0) for key in counts}
    blocks = parse_review_blocks(source)
    assert blocks
    for block in blocks:
        assert source[block.span.start:block.span.end]
        assert block.line_start > 0 and block.line_end >= block.line_start


@pytest.mark.parametrize('mode', ['translate', 'verify', 'continue'])
def test_examples_supply_default_provider_credentials(mode):
    import yaml

    from ydbdoc_review.config.defaults import default_runtime_data

    root = Path(__file__).resolve().parents[2]
    path = root / f'examples/ydb-github-doc-{mode}-on-label.yml'
    data = yaml.safe_load(path.read_text())
    step = data['jobs']['document']['steps'][-1]

    assert not step['with'].get('config')
    for role in default_runtime_data()['models'].values():
        for endpoint in role.values():
            name = endpoint['token_env']
            assert step['env'][name] == '${{ secrets.' + name + ' }}'
    folder = 'YANDEX_CLOUD_FOLDER_DOC_REVIEW'
    assert step['env'][folder] == '${{ secrets.' + folder + ' }}'
