"""T13 filename selection regressions; independent acceptance stays unchanged."""
from dataclasses import asdict

import pytest

from ydbdoc_review.continuation import select_files as select_files
from ydbdoc_review.plan import PlanError
from ydbdoc_review.quality import Issue
from ydbdoc_review.quality_loop import SelectedFile

ROOT = 'ydb/docs/'


def context(*paths, issues=(), unfinished=()):
    files = [asdict(SelectedFile(ROOT + path, '# Source\n', path.split('/')[0])) for path in paths]
    return {'known_files': files, 'result': {
        'selected_files': files, 'issues': [asdict(issue) for issue in issues],
        'unfinished_files': list(unfinished)}}


@pytest.mark.parametrize('lang', ['en', 'ru'])
@pytest.mark.parametrize('mention', ['good.md', 'ydb/docs/en/good.md', 'ydb/docs/ru/good.md'])
def test_unique_pair_name_and_source_alias(lang, mention):
    selected = select_files(context(lang + '/good.md', lang + '/other.md'), 'Improve `' + mention + '`')
    assert [f.path for f in selected] == [ROOT + lang + '/good.md']


def test_ambiguous_name_lists_all_target_paths_in_stable_order():
    saved = context('en/other/a.md', 'en/a.md')
    with pytest.raises(PlanError) as exc:
        select_files(saved, 'Improve a.md')
    assert str(exc.value) == ('Неоднозначный выбор файла. Уточните путь: '
                              'a.md: ydb/docs/en/a.md, ydb/docs/en/other/a.md')
    # Either full language path disambiguates the same pair.
    for mention in ('ydb/docs/en/other/a.md', 'ydb/docs/ru/other/a.md'):
        assert [f.path for f in select_files(saved, 'Improve ' + mention)] == [ROOT + 'en/other/a.md']


def test_exact_mentions_keep_each_files_instruction_minimal():
    selected = select_files(context('en/a.md', 'en/data.md'),
                            'Use glossary\nFix a.md: FIRST_ONLY\nFix ydb/docs/ru/data.md: SECOND_ONLY')
    first, second = selected
    assert first.instruction == 'Use glossary\nFix a.md: FIRST_ONLY'
    assert second.instruction == 'Use glossary\nFix ydb/docs/ru/data.md: SECOND_ONLY'


def test_selection_unions_problems_unfinished_and_explicit_without_good_siblings():
    saved = context('en/bad.md', 'en/pending.md', 'en/good.md', 'en/untouched.md',
                    issues=(Issue(ROOT + 'en/bad.md', 'Saved problem', 'Fix it'),),
                    unfinished=(ROOT + 'en/pending.md',))
    selected = select_files(saved, 'Improve good.md and ydb/docs/ru/good.md')
    assert [f.path for f in selected] == [ROOT + 'en/' + name for name in ('bad.md', 'good.md', 'pending.md')]
    assert selected[0].requested_findings[0].problem == 'Saved problem'
    assert selected[0].instruction == selected[2].instruction == ''


@pytest.mark.parametrize('mention', ['missing.md', 'unknown/good.md'])
def test_unknown_reference_never_falls_back_to_basename(mention):
    with pytest.raises(PlanError, match=mention):
        select_files(context('en/good.md'), 'Improve ' + mention)
