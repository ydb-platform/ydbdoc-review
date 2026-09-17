"""Decoded front matter uses the same references, closure and candidate gate."""
import json
from urllib.parse import unquote

import pytest
import yaml

from tests.contract import test_t10_independent as fixtures
from tests.contract.test_links_build import commit
from ydbdoc_review.links import check_links, prepare_assets, references
from ydbdoc_review.plan import (
    ChangedFile,
    Snapshot,
    build_plan,
    discover_dependencies,
    markdown_dependencies,
)

rig = fixtures.rig

ROOT = 'ydb/docs/'
LINK = r'[Read](/ru/topic\).md)'


def scalar(value, style):
    if style == 'single':
        return "'" + value.replace("'", "''") + "'"
    if style == 'double':
        return json.dumps(value)
    if style == 'escaped':
        return json.dumps(value).replace('/ru/', r'\u002fru/')
    if style in {'literal', 'folded'}:
        return ('|-\n' if style == 'literal' else '>-\n') + '  ' + value
    return value


def document(key, value, style):
    return f'---\n{key}: {scalar(value, style)}\nconfig: "[opaque](/ru/config.md)"\n---\n# Article\n'


@pytest.mark.parametrize('key', ['title', 'description'])
@pytest.mark.parametrize('style', ['single', 'double', 'escaped', 'literal', 'folded', 'plain'])
def test_decoded_references_and_dependency_paths(key, style):
    text = document(key, 'Read ' + LINK, style)
    refs = references(text)
    assert [(unquote(r.href), r.kind, r.location) for r in refs] == [('/ru/topic).md', 'link', None)]
    assert markdown_dependencies(ROOT + 'ru/a.md', text) == [ROOT + 'ru/topic).md']


@pytest.mark.parametrize('key', ['title', 'description'])
@pytest.mark.parametrize('style', ['single', 'double', 'escaped', 'literal', 'folded'])
def test_actual_runner_decoded_url_repair(rig, key, style):
    state, run, put, *_ = rig
    source = document(key, LINK, style)
    put({'ru/a.md': source, 'ru/topic).md': '# Topic\n', 'en/topic).md': '# Topic\n'})
    result = run()
    target = result.candidate.text(ROOT + 'en/a.md')
    value = yaml.safe_load(target.split('---', 2)[1])[key]
    assert [unquote(r.href) for r in references(value)] == ['/en/topic).md']
    assert 'config: "[opaque](/ru/config.md)"' in target
    assert result.candidate.text(ROOT + 'ru/a.md') == source
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.checked_sha == result.result_sha
    assert result.publication.draft is False
    assert sum(op == 'repair' for op, _ in state['calls']) == 1


@pytest.mark.parametrize('key', ['title', 'description'])
@pytest.mark.parametrize('target', [None, '', '# Topic\n'])
def test_whole_tree_gate_catches_unselected_frontmatter(git_repo, key, target):
    files = {'en/unselected.md': document(key, LINK, 'escaped'), 'ru/topic).md': '# Topic\n'}
    if target is not None:
        files['en/topic).md'] = target
    candidate = commit(git_repo, files)
    result = check_links(candidate)
    assert not result.ok
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == ROOT + 'en/unselected.md'
    assert '/ru/topic' in issue.problem
    assert ('replace with' if target else 'specify a replacement') in issue.problem
    assert issue.target is None


@pytest.mark.parametrize('key', ['title', 'description'])
def test_ru_to_en_allowed_and_missing_target_red(git_repo, key):
    candidate = commit(git_repo, {'ru/a.md': document(key, '[Read](/en/topic.md)', 'double'),
                                  'en/topic.md': '# Topic\n'})
    assert check_links(candidate).ok
    missing = commit(git_repo, {'en/topic.md': None})
    result = check_links(missing)
    assert not result.ok
    assert 'Missing link target: /en/topic.md' in result.issues[0].problem


@pytest.mark.parametrize('lang,other', [('ru', 'en'), ('en', 'ru')])
def test_assets_use_decoded_references(git_repo, lang, other):
    candidate = commit(git_repo, {f'{lang}/a.md': document('description', '![Image](p.png)', 'double'),
                                  f'{lang}/p.png': b'\x89PNG\r\n'})
    prepared = prepare_assets(candidate, {ROOT + f'{lang}/a.md': ROOT + f'{other}/a.md'})
    assert not prepared.issues
    assert dict(prepared.copies) == {ROOT + f'{other}/p.png': b'\x89PNG\r\n'}


def test_code_config_aliases_duplicates_and_comments_stay_opaque():
    text = '''---
config: &config '[opaque](/ru/config.md)'
title: *config
description: '[first](/ru/first.md)'
description: '[second](/ru/second.md)'
---
`[inline](/ru/inline.md)`

```yaml
title: '[fenced](/ru/fenced.md)'
```
<!-- [hidden](/ru/hidden.md) -->
'''
    assert references(text) == ()
    value = '`[code](/ru/code.md)` <!-- [hidden](/ru/hidden.md) --> ' + LINK
    refs = references(document('description', value, 'double') + '\n[Body](body.md)\n')
    assert [unquote(r.href) for r in refs] == ['/ru/topic).md', 'body.md']
    assert refs[0].location is None
    assert refs[1].location is not None
    refs[1].location.validate(document('description', value, 'double') + '\n[Body](body.md)\n')


def test_folded_multiline_value_does_not_invent_coordinates():
    text = '---\ndescription: >-\n  [Read\n  more](/ru/topic.md)\n---\n'
    refs = references(text)
    assert [(r.href, r.location) for r in refs] == [('/ru/topic.md', None)]


@pytest.mark.parametrize('lang', ['ru', 'en'])
def test_recursive_frontmatter_closure_deduplicates_cycles(lang):
    sha = 'a' * 40
    snapshot = Snapshot('up', 'docs', 1, sha, sha, 'topic', 'up/docs')
    prefix = ROOT + lang + '/'
    files = {
        prefix + 'a.md': document('title', '[B](b.md)', 'double'),
        prefix + 'b.md': document('description', '[C](c.md) [A](a.md)', 'folded'),
        prefix + 'c.md': document('title', '[A](a.md) [B](b.md)', 'single'),
    }
    def read(requested_sha, path):
        assert requested_sha == sha
        return files.get(path)
    plan = build_plan(snapshot, [ChangedFile(prefix + 'a.md', 'added')], read)
    closure = discover_dependencies(plan, read)
    assert closure.dependencies == [prefix + 'b.md', prefix + 'c.md']
    assert len(closure.operations) == 3
    assert closure.files == files
