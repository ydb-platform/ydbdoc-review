"""Independent source-report failure after successful paid model work."""
from decimal import Decimal

import pytest

from tests.contract.test_t15_cli import assert_saved, process  # noqa: F401
from tests.contract.test_t15_independent import independent, source_only  # noqa: F401
from tests.contract.test_t15_redaction import assert_private, credentials, invoke


@pytest.mark.parametrize('fault', ['http', 'interrupt'])
def test_first_source_report_failure_after_model_is_private_and_accounted(independent, fault):  # noqa: F811
    p = independent
    source_only(p)
    values = credentials(p)
    p.update(redaction_phase='report', redaction_fault=fault, fail_report_number=1,
             model=[], comments=[], http=[], stops=[])
    result = invoke(p, 'doc_translate')
    assert result.returncode == 1 and result.stdout.startswith('RED:')
    context, attempts = assert_saved(p, 'doc_translate', 'RED')
    state = p.read()
    assert_private(result, state, values, context)
    assert ('report: GitHubAPIError' if fault == 'http' else 'report: KeyboardInterrupt') in result.stdout
    if fault == 'http':
        assert 'HTTP 403' in result.stdout
    assert context['result']['cancelled'] == (fault == 'interrupt')
    assert [role for role, _ in state['model']] == ['translation', 'critic']
    assert len(attempts) == len({row['entry_id'] for row in attempts}) == 2
    assert context['result']['cost_breakdown']['total'] == Decimal('.50')
    assert state['report_attempts'] == 1 and state['comments'] == []
    assert [path for method, path in state['http'] if method == 'POST' and path.endswith('/comments')] == ['/repos/up/docs/issues/1/comments']
    assert state['git_pushes'] == 1 and state['pulls']['2']['draft']
    assert state['stops'] == ['pool', 'driver']
    assert context['result']['checked_sha'] == context['result_sha']
