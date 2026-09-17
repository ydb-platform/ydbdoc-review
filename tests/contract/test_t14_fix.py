"""Literal excerpts verified through Git, CommonMark and actual report delivery."""
# ruff: noqa: F811, RUF001 -- fixture imports and bilingual documents.
from dataclasses import replace
from html.parser import HTMLParser

import pytest
from markdown_it import MarkdownIt

from tests.contract.test_t14_independent import ROOT, evidence, wire  # noqa: F401
from tests.contract.test_translate_t10 import system as translate_system  # noqa: F401
from tests.unit.test_store_t12 import db  # noqa: F401
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.publication import freeze
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.report import create_reporter, render_reports
from ydbdoc_review.runner import finalize


class CodeText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == 'code':
            self.active = True
            self.blocks.append('')

    def handle_endtag(self, tag):
        if tag == 'code':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.blocks[-1] += data


def code_text(body):
    html = MarkdownIt('commonmark', {'html': True}).render(body)
    parser = CodeText()
    parser.feed(html)
    assert '<script>' not in html
    assert '<li>Итого:' in html
    return parser.blocks


@pytest.mark.parametrize('excerpt', [
    '<script>alert("x")</script> &lt;node&gt; &#38; &amp;',
    '[visible](https://example.invalid) **bold** `code` \\*escaped*',
    '````````\n```\n~~~\n> # heading\n- list\n</blockquote></code></pre>',
    '  leading\tspace  \n\n\tindented\ntrailing  ',
    'first\n\nlast\n\n',
    'long ' + 'literal &amp; **text** ' * 60,
    'HTTP 503 is documentation, retain this whole line',
])
@pytest.mark.parametrize('side', ['source', 'target'])
def test_literal_git_excerpt_with_exact_whitespace_and_coordinates(evidence, excerpt, side):
    source_path, target_path = ROOT+'ru/a.md', ROOT+'en/a.md'
    original = freeze(evidence.candidate, {source_path: ('prefix\n'+excerpt).encode()})
    candidate = freeze(original, {target_path: ('prefix\n'+excerpt).encode()})
    snapshot = replace(evidence.snapshot, source_sha=original.sha)
    plan = replace(evidence.plan, snapshot=snapshot, files={source_path: 'prefix\n'+excerpt})
    location = Location(2, 1+len(excerpt.splitlines()), excerpt)
    issue = Issue(target_path, 'Review literal excerpt', 'Correct at linked lines',
                  **{side: location})
    result = replace(evidence, snapshot=snapshot, plan=plan, candidate=candidate,
                     checked_sha=candidate.sha, issues=(issue,),
                     publication=replace(evidence.publication, pushed_sha=candidate.sha))
    body = render_reports(result, current_pr='up/docs/1')[-1].body
    displayed = excerpt[:500]
    assert code_text(body) == [displayed + ('' if displayed.endswith('\n') else '\n')]
    assert ('цитата сокращена' in body) == (len(excerpt) > 500)
    path, sha = (source_path, original.sha) if side == 'source' else (target_path, candidate.sha)
    assert f'/blob/{sha}/{path}#L2' in body
    assert f'-L{location.end}' in body if location.end > 2 else '#L2)' in body


def test_secrets_redacted_before_fencing_and_without_changing_git(evidence, monkeypatch, db):
    from tests.contract.test_t14_independent import http_comments
    from ydbdoc_review.store import RunStore

    secret = 'private\nmultiline<&>value'
    excerpt = f'{secret}\ntoken=fixture-secret\nBearer fixture-bearer\nghp_fixture\nhttps://user:pass@example.invalid\n**visible** &amp;'
    candidate = freeze(evidence.candidate, {ROOT+'en/a.md': excerpt.encode()})
    result = replace(evidence, candidate=candidate, checked_sha=candidate.sha,
                     publication=replace(evidence.publication, pushed_sha=candidate.sha, draft=True),
                     issues=(Issue(ROOT+'en/a.md', 'Literal', 'Review',
                                   target=Location(1, len(excerpt.splitlines()), excerpt)),))
    sent = http_comments(monkeypatch)
    adapter = RunStore(db[0], mode='doc_translate', source_pr='up/docs/1')
    final = finalize(result, adapter.hooks(report=create_reporter(
        GitHubClient('dummy'),
        current_pr='up/docs/1', authorized=True, secrets=(secret,))))
    assert final is result and len(sent) == 2
    text = code_text(sent[-1][1])[0]
    assert text == ('[REDACTED]\n[REDACTED]\n[REDACTED]\n[REDACTED]\n'
                    'https://[REDACTED]@example.invalid\n**visible** &amp;\n')
    assert all(value not in sent[-1][1] for value in (secret, 'fixture-secret', 'fixture-bearer', 'ghp_fixture'))
    assert candidate.text(ROOT+'en/a.md') == excerpt
    assert db[0].context(adapter.run_id)['result']['status'] == final.status


def test_published_warning_quote_survives_real_run_store_and_http(translate_system, db, monkeypatch):
    state, run, commit, _, _, remote_sha, *_ = translate_system
    source = '# Code\n\n```text\nПривет **bold** &amp; <node>\n```\n'
    commit({'ru/a.md': source})
    adapter, sent, args = wire(translate_system, db, monkeypatch)
    result = run(**args)
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.result_sha == remote_sha('translation') == result.checked_sha
    assert result.candidate.text(ROOT+'en/a.md') == source
    assert [op for op, _ in state['calls']] == ['translation', 'critic']
    assert len(sent) == 2
    excerpts = code_text(sent[-1][1])
    assert excerpts and any('Привет' in text for text in excerpts)
    for issue in result.issues:
        if issue.target:
            assert issue.target.quote + ('' if issue.target.quote.endswith('\n') else '\n') in excerpts
    saved = db[0].context(adapter.run_id)
    assert saved['final_files'][ROOT+'en/a.md'] == source.encode()
    assert saved['result']['status'] == 'GREEN'
    assert saved['cost_breakdown'] == result.cost_breakdown
