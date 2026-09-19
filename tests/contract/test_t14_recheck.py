"""Fresh T14 recheck: visible Git excerpts and bounded HTTP finalization, offline."""
# ruff: noqa: F811, F401 -- reusable pytest fixtures and Russian labels.
from dataclasses import replace
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from markdown_it import MarkdownIt

from tests.contract.test_continue_t13 import (
    assert_changed_greeting,
    continued,
    request_greeting,
)
from tests.contract.test_t14_independent import ROOT, evidence, http_comments, wire
from tests.contract.test_translate_t10 import system as translate_system
from tests.contract.test_verify_t11 import system
from tests.model_clock import model_clock
from tests.unit.test_store_t12 import db
from ydbdoc_review.publication import freeze
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.report import create_reporter, render_reports
from ydbdoc_review.runner import RunHooks

pytestmark = pytest.mark.timeout(60)


class Rendered(HTMLParser):
    """Read browser text nodes of literal blocks, without stripping whitespace."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.in_pre = False
        self.links = []
        self.outside = []
        self.feed(MarkdownIt('commonmark', {'html': True}).render(body))

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.in_pre = True
            self.blocks.append('')
        if tag == 'a':
            self.links.append(dict(attrs)['href'])

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.in_pre = False

    def handle_data(self, text):
        if self.in_pre:
            self.blocks[-1] += text
        else:
            self.outside.append(text)


CASES = [
    ('entities', '&lt;node&gt; &amp; &#x41; &#65; &quot; &copy;'),
    ('html', '<div>text</div><script>alert("x")</script><!-- comment -->'),
    ('markdown', '[label](https://example.invalid/a?q=1&x=2) **bold** `code`'),
    ('ticks', 'before ' + '`'*41 + ' after'),
    ('fences', '`'*37 + '\n~~~\n> nested\n```\n**literal**\n' + '`'*48),
    ('spaces', '  leading  \n\t tabs\t\ntrailing   '),
    ('blank-lines', '\n\nfirst\n\n\nlast\n\n'),
    ('multiline', 'one &amp;\n[second](url)\n<b>third</b>\n`fourth`'),
    ('http-doc', 'HTTP 503 means service unavailable; **retain** this explanation'),
]


def literal_result(evidence, excerpt, side):
    source_path, target_path = ROOT+'ru/a.md', ROOT+'en/a.md'
    text = 'prefix\n'+excerpt+'\nsuffix\n'
    original = freeze(evidence.candidate, {source_path: text.encode()})
    candidate = freeze(original, {target_path: text.encode()})
    snapshot = replace(evidence.snapshot, source_sha=original.sha)
    plan = replace(evidence.plan, snapshot=snapshot, files={source_path: text})
    location = Location(2, 2+excerpt.count('\n'), excerpt)
    result = replace(evidence, snapshot=snapshot, plan=plan, candidate=candidate,
                     checked_sha=candidate.sha,
                     publication=replace(evidence.publication, pushed_sha=candidate.sha),
                     issues=(Issue(target_path, 'Literal evidence', 'Review linked lines',
                                   **{side: location}),))
    path, sha = (source_path, original.sha) if side == 'source' else (target_path, candidate.sha)
    return result, location, path, sha, text


@pytest.mark.parametrize('side', ['target', 'source'])
@pytest.mark.parametrize('name,excerpt', CASES, ids=[x[0] for x in CASES])
def test_visible_literal_and_git_coordinates(evidence, side, name, excerpt):
    result, location, path, sha, text = literal_result(evidence, excerpt, side)
    parsed = Rendered(render_reports(result, current_pr='up/docs/1')[-1].body)
    # A final code-block line terminator is layout; no strip() of excerpt bytes.
    assert parsed.blocks == [excerpt if excerpt.endswith('\n') else excerpt+'\n']
    anchor = '#L2' + (f'-L{location.end}' if location.end != 2 else '')
    assert f'https://github.com/up/docs/blob/{sha}/{path}{anchor}' in parsed.links
    assert result.candidate.text(path) == text
    assert result.checked_sha == result.candidate.sha == result.result_sha
    outside = ''.join(parsed.outside)
    assert result.checked_sha in outside and 'Итого:' in outside
    assert 'Review linked lines' in outside


@pytest.mark.parametrize('excerpt', ['', ' ', '\n\n', '  \t\n  '])
def test_empty_evidence_is_not_fabricated(evidence, excerpt):
    # Empty excerpts cannot localize an issue; do not invent a new Markdown policy.
    with pytest.raises(ValueError, match='nonempty quote'):
        Location(1, 1, excerpt)
    result = replace(evidence, issues=(Issue(ROOT+'en/a.md', 'Missing block', 'Locate manually'),))
    parsed = Rendered(render_reports(result, current_pr='up/docs/1')[-1].body)
    assert parsed.blocks == []
    assert 'место не установлено' in ''.join(parsed.outside)
    assert not any('/blob/' in link for link in parsed.links)


@pytest.mark.parametrize('side', ['target', 'source'])
def test_redaction_preserves_nonsecret_literal_and_original_coordinates(evidence, side):
    secret = 'private<&>\nmultiline-secret'
    excerpt = '  '+secret+'\nBearer fixture-bearer\ntoken=fixture-token\nghp_fixture\n**literal** &amp;  '
    result, location, path, sha, text = literal_result(evidence, excerpt, side)
    reports = render_reports(result, current_pr='up/docs/1', secrets=(secret,))
    parsed = Rendered(reports[-1].body)
    assert parsed.blocks == ['  [REDACTED]\n[REDACTED]\n[REDACTED]\n[REDACTED]\n**literal** &amp;  \n']
    assert f'https://github.com/up/docs/blob/{sha}/{path}#L2-L{location.end}' in parsed.links
    assert result.candidate.text(path) == text
    for report in reports:
        assert all(value not in report.body for value in (secret, 'fixture-bearer', 'fixture-token', 'ghp_fixture'))


@pytest.mark.parametrize('mode', ['translate', 'verify', 'continue'])
@pytest.mark.parametrize('fail_at', [None, 1, 2], ids=['success', 'first-report-failure', 'second-report-failure'])
def test_actual_runner_store_http_final_status_and_dedup(request, db, monkeypatch, mode, fail_at):
    if mode == 'continue':
        c = request.getfixturevalue('continued')
        request_greeting(c, 'Hello, world.')
        model_clock(monkeypatch, c.now)
        store, boundary, state = c.store, c.boundary, c.state
        before = store.daily_cost()
        sent = http_comments(monkeypatch, fail_at=fail_at)
        reporter = create_reporter(c.publisher.github, current_pr='up/docs/1', authorized=True)
        def run():
            return c.run(hooks=RunHooks(report=reporter))
        expected_cost, expected_calls = Decimal('.75'), ['critic', 'repair', 'critic']
        def remote_sha():
            return c.remote_sha('topic')
    else:
        fixture = request.getfixturevalue('translate_system' if mode == 'translate' else 'system')
        state, runner, _, _, _, remote, publisher, _ = fixture
        store, boundary = db[:2]
        before = store.daily_cost()
        adapter, sent, args = wire(fixture, db, monkeypatch, fail_at=fail_at)
        if mode == 'verify':
            # Associated original PR makes both deliveries meaningful for verify.
            reporter = create_reporter(publisher.github, current_pr='up/docs/1',
                                       source_pr='up/docs/2', authorized=True)
            args['hooks'] = replace(args['hooks'], report=reporter)
        def run():
            return runner(**args)
        expected_cost = Decimal('.250') if mode == 'translate' else Decimal('.125')
        expected_calls = ['translation', 'critic'] if mode == 'translate' else ['critic']
        def remote_sha():
            return remote('translation' if mode == 'translate' else 'topic')
    summaries = []
    def observe(query, params):
        if 'UPSERT INTO runs' in query and params.get('entry_id') == 'summary' and 'status' in params:
            summaries.append(params.copy())
        return False
    boundary.fail = observe
    result = run()
    if mode == 'continue':
        adapter = c.adapters[-1]
        assert_changed_greeting(result, c.seeded.candidate_sha, 'Hello, world.')
    status = 'GREEN' if fail_at is None else 'RED'
    assert result.status == status, result.errors
    assert result.publication.draft == (fail_at is not None)
    assert result.checked_sha == result.result_sha == remote_sha()
    assert [op for op, _ in state['calls']] == expected_calls
    assert state['made'] == 1 and len(result.attempts) == len(expected_calls)
    assert len(sent) == 2  # both independent report destinations are attempted
    assert len(summaries) == (1 if fail_at is None else 2)
    assert [row['status'] for row in summaries] == (['GREEN'] if fail_at is None else ['GREEN', 'RED'])
    context = store.context(adapter.run_id)
    assert context['result']['status'] == boundary.runs[adapter.run_id, 'summary']['status'] == status
    assert context['result']['errors'] == list(result.errors)
    assert context['cost_breakdown'] == result.cost_breakdown
    assert result.cost_breakdown['total'] == expected_cost == store.daily_cost()-before
    entries = [key for key in boundary.runs if key[0] == adapter.run_id and key[1] != 'summary']
    assert len(entries) == len(result.attempts)
    for _, body in sent:
        visible = ''.join(Rendered(body).outside)
        assert all(label in visible for label in ('Перевод:', 'Критик:', 'Исправления:', 'Итого:'))
        assert f'Итого: {expected_cost:f} ₽' in visible
    if fail_at:
        assert any('report:' in error for error in result.errors)
