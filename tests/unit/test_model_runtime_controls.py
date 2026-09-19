"""Configuration wiring only; no claim of live model support."""
import json
from decimal import Decimal

from ydbdoc_review.config.defaults import default_runtime_data
from ydbdoc_review.config.runtime import load_runtime


def test_reasoning_control_is_explicit_endpoint_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('YANDEX_CLOUD_FOLDER_DOC_REVIEW', 'folder')
    monkeypatch.setenv('YANDEX_CLOUD_API_KEY_DOC_REVIEW', 'test-key')
    data = default_runtime_data()
    # Approved model migration does not guess a YC reasoning-off control.
    assert data['models']['translation']['main']['model'] == 'deepseek-v4-flash'
    assert 'reasoning_effort' not in data['models']['translation']['main']
    data['models']['translation']['main']['reasoning_effort'] = 'none'
    data['max_output_tokens'] = 7000
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(data))
    runtime = load_runtime(path)
    assert runtime.choices['translation'].main.reasoning_effort == 'none'
    assert runtime.choices['translation'].alternative.reasoning_effort is None
    assert runtime.budget.max_output_tokens == 7000


def test_approved_v4_default_tariff_uses_yc_cached_and_output_rates(monkeypatch):
    monkeypatch.setenv('YANDEX_CLOUD_FOLDER_DOC_REVIEW', 'folder')
    monkeypatch.setenv('YANDEX_CLOUD_API_KEY_DOC_REVIEW', 'test-key')
    monkeypatch.delenv('YDBDOC_MODEL_TRANSLATE', raising=False)
    runtime = load_runtime()
    for operation in ('translation', 'repair'):
        choice = runtime.choices[operation]
        assert choice.main.model == 'deepseek-v4-flash'
        assert choice.alternative.model == 'yandexgpt-5-pro'
        assert choice.main.reasoning_effort is None
        assert choice.alternative.reasoning_effort is None
        response = {'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                             'prompt_tokens_details': {'cached_tokens': 600}}}
        assert runtime.resolver(choice.main, response) == Decimal('0.215')
        del response['usage']['prompt_tokens_details']
        assert runtime.resolver(choice.main, response) == Decimal('0.350')
    assert runtime.budget.context_tokens == 32768
    assert runtime.budget.max_output_tokens == 8000
