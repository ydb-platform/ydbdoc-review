"""T08 exact trees, link semantics and real installed Diplodoc CLI."""
import shutil

import pytest

from ydbdoc_review.build import BuildResult, automatic_ok, build_candidate
from ydbdoc_review.links import (
    Candidate,
    check_links,
    confirmed_english_url,
    prepare_assets,
    references,
    resolve,
)
from ydbdoc_review.plan import markdown_dependencies

ROOT = 'ydb/docs/'


def commit(git_repo, files):
    repo, git = git_repo
    for path, content in files.items():
        file = repo / ROOT / path
        file.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            file.unlink(missing_ok=True)
        else:
            file.write_bytes(content.encode() if isinstance(content, str) else content)
    git('add', '.')
    git('commit', '-m', 'fixture')
    return Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())


def test_candidate_reads_commit_not_worktree_and_deleted_empty_are_authoritative(git_repo):
    old = commit(git_repo, {'en/a.md': '[b](b.md#id) ![pic](x.png)',
                            'en/b.md': '# B {#id}', 'en/x.png': b'123'})
    new = commit(git_repo, {'en/b.md': '', 'en/x.png': None})
    (new.repo / ROOT / 'en/b.md').write_text('# Dirty {#id}')
    (new.repo / ROOT / 'en/x.png').write_bytes(b'dirty')
    assert new.text(ROOT + 'en/b.md') == ''
    assert new.read(ROOT + 'en/x.png') is None
    assert old.read(ROOT + 'en/x.png') == b'123'
    build = BuildResult(new.sha, 'success', returncode=0, anchors={ROOT + 'en/b.md': ()})
    result = check_links(new, build=build)
    assert not result.ok
    assert any('Empty link target' in i.problem for i in result.issues)
    assert any('Missing asset' in i.problem for i in result.issues)
    for issue in result.issues:
        if issue.target:
            issue.target.validate(new.text(issue.path))
    with pytest.raises(ValueError, match='SHA'):
        Candidate.open(new.repo, 'HEAD')
    with pytest.raises(TypeError):
        new.entries['evil'] = ('100644', 'a')


@pytest.mark.parametrize('lang', ['en', 'ru'])
def test_shared_root_resolution_and_closure(lang):
    source = ROOT + f'{lang}/core/a.md'
    assert resolve(source, '/foo.md').path == ROOT + 'foo.md'
    assert resolve(source, f'/{lang}/core/foo.md').path == ROOT + f'{lang}/core/foo.md'
    assert markdown_dependencies(source, '[root](/foo.md) [local](foo.md)') == [ROOT + f'{lang}/core/foo.md']
    assert resolve(source, 'a%20b.md?x=1#hi%20there').fragment == 'hi there'
    assert resolve(source, '//host/path') is None
    with pytest.raises(ValueError, match='escapes'):
        resolve(source, '../../../../outside.md')


def test_reference_parser_includes_html_images_and_ignores_code_comments():
    text = '''[ref][r] ![image](pic.png)

[r]: target(a).md "Title"

{% include [frag](part.md) %}

<img src="raw.png">

<!-- [hidden](hidden.md) -->

`[no](code.md)`

```md
{% include [no](bad.md) %}
```
'''
    refs = references(text)
    assert [(r.href, r.kind) for r in refs] == [
        ('target(a).md', 'link'), ('pic.png', 'asset'), ('part.md', 'include'), ('raw.png', 'asset')]


def test_locale_policy_no_blind_remap_or_reverse_russification(git_repo):
    tree = commit(git_repo, {'en/a.md': '[ru](/ru/b.md?q=1#id)', 'ru/b.md': '# B {#id}',
                             'en/b.md': '# B {#id}', 'ru/a.md': '[en](/en/b.md)'})
    result = check_links(tree)
    assert len(result.issues) == 1
    assert 'English page links' in result.issues[0].problem
    build = BuildResult(tree.sha, 'success', returncode=0, anchors={ROOT + 'en/b.md': {'id'}})
    assert confirmed_english_url(tree, ROOT + 'en/a.md', '/ru/b.md?q=1#id', build=build) == '/en/b.md?q=1#id'
    assert confirmed_english_url(tree, ROOT + 'ru/a.md', '/en/b.md') == '/en/b.md'
    with pytest.raises(ValueError, match='anchor'):
        confirmed_english_url(tree, ROOT + 'en/a.md', '/ru/b.md#absent', build=build)
    removed = commit(git_repo, {'en/b.md': None})
    with pytest.raises(ValueError, match='Missing English target'):
        confirmed_english_url(removed, ROOT + 'en/a.md', '/ru/b.md')


@pytest.mark.parametrize('lang,other', [('en', 'ru'), ('ru', 'en')])
def test_asset_copy_bytes_both_directions_and_explicit_overrides(git_repo, lang, other):
    tree = commit(git_repo, {f'{lang}/a.md': '![x](img/p.png)\n\n{% include [p](part.md) %}',
                             f'{lang}/part.md': '[file](download.bin)\n\n{% include [a](a.md) %}',
                             f'{lang}/img/p.png': b'\x00\xffbinary', f'{lang}/download.bin': b'\x80data'})
    pairs = {ROOT + f'{lang}/a.md': ROOT + f'{other}/a.md'}
    result = prepare_assets(tree, pairs)
    assert result.source_sha == tree.sha and not result.issues
    assert result.copies == {ROOT + f'{other}/img/p.png': b'\x00\xffbinary',
                             ROOT + f'{other}/download.bin': b'\x80data'}
    for override in (None, b''):
        result = prepare_assets(tree, pairs, overrides={ROOT + f'{other}/img/p.png': override})
        assert result.issues and ROOT + f'{other}/img/p.png' not in result.copies
        assert result.copies[ROOT + f'{other}/download.bin'] == b'\x80data'


def test_missing_and_empty_include_asset_and_unsupported_git_entry(git_repo):
    tree = commit(git_repo, {'en/a.md': '![empty](empty.png)\n\n{% include [empty](empty.md) %}',
                             'en/empty.png': b'', 'en/empty.md': ''})
    result = check_links(tree)
    assert len(result.issues) == 2
    assert all('Empty' in i.problem for i in result.issues)
    tree = commit(git_repo, {'en/a.md': '{% include [missing](gone.md) %}'})
    assert 'Missing include' in check_links(tree).issues[0].problem
    (tree.repo / ROOT / 'en/link.md').symlink_to('/etc/passwd')
    repo, git = git_repo
    git('add', '.')
    git('commit', '-m', 'symlink')
    tree = Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())
    assert any('Unsupported Git entry' in i.problem for i in check_links(tree).issues)


@pytest.mark.parametrize('status,sha,code', [('pending', 'same', None), ('failure', 'same', 1),
                                           ('success', 'other', 0), ('success', 'same', None)])
def test_build_gates_pending_failure_wrong_sha(git_repo, status, sha, code):
    tree = commit(git_repo, {'en/a.md': '# A'})
    build = BuildResult(tree.sha if sha == 'same' else 'b' * 40, status, returncode=code)
    assert not automatic_ok(tree.sha, check_links(tree), build)
    assert build.issues_for(tree.sha)


def test_missing_builder_is_failure(git_repo):
    tree = commit(git_repo, {'en/a.md': '# A'})
    result = build_candidate(tree, executable='ydbdoc-nonexistent-yfm')
    assert not result.ok_for(tree.sha) and 'missing' in result.log


@pytest.mark.timeout(60)
def test_real_builder_exact_sha_root_urls_includes_anchors_and_failure(git_repo, tmp_path):
    assert shutil.which('yfm'), 'Install @diplodoc/cli@5.61.0 and put yfm on PATH'
    tree = commit(git_repo, {
        'toc.yaml': 'title: Fixture\nitems:\n  - name: EN\n    href: en/core/a.md\n  - name: RU\n    href: ru/core/a.md\n  - name: Root\n    href: foo.md\n',
        'en/core/a.md': '# Page\n\n[root](/foo.md) [RU](/ru/core/a.md)\n\n'
                        '[local](#explicit) [included](#included)\n\n'
                        '## Heading {#explicit}\n\n{% include [part](part.md) %}\n\n![asset](pic.svg)\n',
        'en/core/part.md': '## Included\n\n[self](#included) A paragraph.\n',
        'en/core/pic.svg': '<svg xmlns="http://www.w3.org/2000/svg"/>',
        'ru/core/a.md': '# Russian\n\n[EN](/en/core/a.md#explicit)\n',
        'foo.md': '# Root\n\n## Root heading\n',
        'build.sh': 'exit 99\n',  # the untrusted entry script must never run
    })
    # Worktree/baseline changes cannot alter build inputs.
    (tree.repo / ROOT / 'en/core/a.md').write_text('[broken](gone.md)')
    result = build_candidate(tree)
    print('REAL SUCCESS BUILD:', result.log)
    assert result.ok_for(tree.sha), result.log
    assert {'explicit', 'included'} <= result.anchors[ROOT + 'en/core/a.md']
    assert '/foo.md' in result.rendered_links[ROOT + 'en/core/a.md']
    assert '/ru/core/a.md' in result.rendered_links[ROOT + 'en/core/a.md']
    assert not any('/en/core/foo' in href for href in result.rendered_links[ROOT + 'en/core/a.md'])
    ru = check_links(tree, paths=[ROOT + 'ru/core/a.md'], build=result)
    assert automatic_ok(tree.sha, ru, result), ru.issues
    checked = check_links(tree, build=result)
    assert len(checked.issues) == 1 and 'English page links' in checked.issues[0].problem
    # The include's self link is validated against the generated parent page.
    broken = commit(git_repo, {'en/core/a.md': '# Page\n\n[broken](gone.md)\n'})
    failure = build_candidate(broken)
    print('REAL FAILURE BUILD:', failure.log)
    assert not failure.ok_for(broken.sha), failure.log
    assert failure.returncode != 0
    assert not automatic_ok(broken.sha, ru, result)
    timed_out = build_candidate(tree, timeout=0.001)
    assert not timed_out.ok_for(tree.sha) and 'timed out' in timed_out.log


def test_external_russian_url_cannot_silently_pass_or_be_rewritten(git_repo):
    tree = commit(git_repo, {'en/a.md': '[RU](https://ydb.tech/docs/ru/example)'})
    result = check_links(tree)
    assert not result.ok and 'Unconfirmed English target' in result.issues[0].problem
    with pytest.raises(ValueError, match='Unconfirmed'):
        confirmed_english_url(tree, ROOT + 'en/a.md', 'https://ydb.tech/docs/ru/example')


def test_shared_include_inherits_rendered_anchors_and_language(git_repo):
    tree = commit(git_repo, {'en/a.md': '# A\n\n{% include [shared](../_includes/p.md) %}',
                             '_includes/p.md': '## Shared {#shared}\n\n[self](#shared) [ru](/ru/b.md)',
                             'ru/b.md': '# B'})
    build = BuildResult(tree.sha, 'success', returncode=0, anchors={ROOT + 'en/a.md': {'shared'}})
    result = check_links(tree, build=build)
    assert len(result.issues) == 1
    assert 'English page links' in result.issues[0].problem
    assert result.issues[0].path == ROOT + '_includes/p.md'


def test_disconnected_include_cycle_and_missing_assets_are_not_hidden(git_repo):
    tree = commit(git_repo, {'en/a.md': '{% include [b](b.md) %}',
                             'en/b.md': '{% include [a](a.md) %}\n\n![missing](gone.png)'})
    result = check_links(tree)
    assert any('Include cycle' in i.problem for i in result.issues)
    assert any('Missing asset' in i.problem for i in result.issues)


def test_fragment_requires_same_sha_success_even_with_plausible_ids(git_repo):
    tree = commit(git_repo, {'en/a.md': '# A\n\n[link](#id)'})
    build = BuildResult('b' * 40, 'success', returncode=0, anchors={ROOT + 'en/a.md': {'id'}})
    result = check_links(tree, build=build)
    assert not result.ok and not result.complete


def test_absolute_missing_root_is_not_locale_core_fallback(git_repo):
    tree = commit(git_repo, {'en/core/a.md': '[root](/foo.md)', 'en/core/foo.md': '# Wrong root'})
    result = check_links(tree)
    assert not result.ok
    assert 'ydb/docs/foo.md' in result.issues[0].problem


def test_url_proposal_preserves_percent_encoding_query_and_fragment(git_repo):
    tree = commit(git_repo, {'en/a.md': '# A', 'ru/a b.md': '# B', 'en/a b.md': '# B'})
    assert confirmed_english_url(tree, ROOT + 'en/a.md', '/ru/a%20b.md?x=1') == '/en/a%20b.md?x=1'


def test_prepared_assets_are_committed_and_checked_even_after_other_copy_fails(git_repo):
    tree = commit(git_repo, {'en/a.md': '![missing](gone.png) ![exists](ok.png)',
                             'en/ok.png': b'\x00\xffexact'})
    prepared = prepare_assets(tree, {ROOT + 'en/a.md': ROOT + 'ru/a.md'})
    assert prepared.issues and prepared.copies[ROOT + 'ru/ok.png'] == b'\x00\xffexact'
    files = {p[len(ROOT):]: data for p, data in prepared.copies.items()}
    files['ru/a.md'] = tree.text(ROOT + 'en/a.md')
    candidate = commit(git_repo, files)
    assert candidate.read(ROOT + 'ru/ok.png') == b'\x00\xffexact'
    issues = check_links(candidate, paths=[ROOT + 'ru/a.md']).issues
    assert len(issues) == 1 and 'gone.png' in issues[0].problem


def test_builder_uses_exact_input_fixed_command_and_scrubs_credentials(git_repo, monkeypatch):
    import subprocess

    import ydbdoc_review.build as module

    tree = commit(git_repo, {'en/a.md': '# Exact', 'build.sh': 'DO NOT RUN'})
    original = subprocess.run
    monkeypatch.setenv('SECRET_SENTINEL', 'must-not-pass')
    calls = []

    def run(command, **kwargs):
        if command[0] == 'git':
            return original(command, **kwargs)
        calls.append(command)
        assert command[1:] == ['-s', '-i', '.', '-o', command[5], '--allowHTML', '--apply-presets']
        assert 'SECRET_SENTINEL' not in kwargs['env']
        source = kwargs['cwd']
        assert (source / 'en/a.md').read_text() == '# Exact'
        from pathlib import Path
        output = Path(command[5])
        assert not output.is_relative_to(source)
        (output / 'en').mkdir(parents=True)
        (output / 'en/a.html').write_text('<h1 id="exact">Exact</h1>')
        return subprocess.CompletedProcess(command, 0, 'fixture build')

    monkeypatch.setattr(module.shutil, 'which', lambda _: '/trusted/yfm')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = build_candidate(tree)
    assert result.ok_for(tree.sha) and len(calls) == 1
    assert result.anchors[ROOT + 'en/a.md'] == {'exact'}
