"""All three CLI modes with the approved defaults and real local Flash tokenizer.

Generation, YC network tokenization, GitHub, YDB SDK and YFM are offline boundaries.
These tests establish wiring/accounting/publication, not live provider compatibility.
"""

import json
from decimal import Decimal

import pytest

from tests.contract.test_t15_cli import assert_saved, process  # noqa: F401
from ydbdoc_review.config.defaults import default_runtime_data


@pytest.mark.parametrize("runtime_source", ["defaults", "json"])
@pytest.mark.parametrize("mode", ["doc_translate", "doc_verify", "doc_continue"])
def test_v4_runtime_all_modes(process, monkeypatch, runtime_source, mode):  # noqa: F811
    p = process
    # A chat/CI environment can contain operator overrides: this fixture tests
    # shipped defaults explicitly, with fake credentials and sockets disabled.
    overrides = dict.fromkeys((
        "YDBDOC_MODEL_PROVIDER", "YDBDOC_MODEL_TRANSLATE", "YDBDOC_MODEL_CHECK",
    ), "")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("YANDEX_CLOUD_FOLDER_DOC_REVIEW", "fixture-folder")
    (p.root / "models.json").write_text(json.dumps(default_runtime_data()))
    overrides.update(
        T15_REAL_V4="1",
        T15_DEFAULT_RUNTIME="1" if runtime_source == "defaults" else "0",
        YANDEX_CLOUD_FOLDER_DOC_REVIEW="fixture-folder",
        YANDEX_CLOUD_API_KEY_DOC_REVIEW="dummy",
        TOKENIZERS_PARALLELISM="false",
        RAYON_NUM_THREADS="1",
    )
    if mode == "doc_continue":
        seed = p.invoke("doc_translate", **overrides)
        assert seed.returncode == 0, seed.stdout + seed.stderr
        p.update(model=[], comments=[], http=[], builds=[], local_v4_counts=[], yc_tokenizers=[])
    result = p.invoke(mode, 2 if mode == "doc_continue" else 1, **overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    state = p.read()
    context, attempts = assert_saved(p, mode, "GREEN")
    expected_roles = {
        "doc_translate": ["translation", "critic"],
        "doc_verify": ["critic"],
        "doc_continue": ["critic", "repair", "critic"],
    }[mode]
    assert [role for role, _ in state["model"]] == expected_roles
    assert len(attempts) == len(expected_roles)
    for role, payload in state["model"]:
        model = "yandexgpt-5.1" if role == "critic" else "deepseek-v4-flash"
        assert payload["model"] == f"gpt://fixture-folder/{model}"
        assert 0 < payload["max_tokens"] <= 8000
        assert "reasoning_effort" not in payload
    # Verification uses only the critic model; primary translate and continuation
    # repair must run both message and raw output counting with real local Flash.
    counts = state.get("local_v4_counts", [])
    if mode != "doc_verify":
        assert {name for name, _ in counts} == {"count_messages", "count_output"}
        assert all(count > 0 for _, count in counts)
    assert state["yc_tokenizers"]
    assert all("deepseek" not in uri for uri in state["yc_tokenizers"])
    assert context["result"]["checked_sha"] == context["result_sha"] == state["builds"][-1]
    expected = b"# Hello\n\nHello, world.\n" if mode == "doc_continue" else b"# Hello\n\nHello world.\n"
    assert context["final_files"]["ydb/docs/en/a.md"] == expected
    assert len(state["pulls"]) == (1 if mode == "doc_verify" else 2)
    assert len(state["comments"]) == (1 if mode == "doc_verify" else 2)
    for number, body in state["comments"]:
        if number == (1 if mode == "doc_verify" else 2):
            assert context["result_sha"] in body
        for label in ("Перевод:", "Критик:", "Исправления:", "Итого:"):
            assert label in body
    # Boundary usage is 10 input + 5 output tokens per paid response. Prices
    # below independently check default Flash (300/500) and critic (800/800).
    flash, critic = Decimal(".0055"), Decimal(".012")
    assert context["result"]["cost_breakdown"] == dict(
        translation=flash if mode == "doc_translate" else 0,
        critic=critic * (2 if mode == "doc_continue" else 1),
        repair=flash if mode == "doc_continue" else 0,
        total={"doc_translate": flash + critic, "doc_verify": critic,
               "doc_continue": flash + 2 * critic}[mode],
    )
