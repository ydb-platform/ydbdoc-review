"""Configuration wiring only; no claim of live model support."""
import json

from ydbdoc_review.config.defaults import default_runtime_data
from ydbdoc_review.config.runtime import load_runtime


def test_reasoning_control_is_explicit_endpoint_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('YANDEX_CLOUD_FOLDER_DOC_REVIEW', 'folder')
    monkeypatch.setenv('YANDEX_CLOUD_API_KEY_DOC_REVIEW', 'test-key')
    data = default_runtime_data()
    # The legacy default is retained pending model approval, with no guessed control.
    assert data['models']['translation']['main']['model'] == 'deepseek-v32'
    assert 'reasoning_effort' not in data['models']['translation']['main']
    data['models']['translation']['main']['reasoning_effort'] = 'none'
    data['max_output_tokens'] = 7000
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(data))
    runtime = load_runtime(path)
    assert runtime.choices['translation'].main.reasoning_effort == 'none'
    assert runtime.choices['translation'].alternative.reasoning_effort is None
    assert runtime.budget.max_output_tokens == 7000
