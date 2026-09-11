"""F-006 contracts for local translation and read-only CLI commands."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

import ydbdoc_review.cli as cli
from ydbdoc_review.ops.gates import GateResult

runner = CliRunner()


class FakeUsage:
    total_input_tokens = 11
    total_output_tokens = 7

    def estimate_cost_rub(self) -> float:
        return 1.25


def test_F006_translate_file_uses_local_pipeline_and_accounting(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.md"
    output = tmp_path / "target.md"
    source.write_text("# Heading\n\nText.\n", encoding="utf-8")
    ops_ctx = object()
    finished: list[dict[str, object]] = []
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "begin_ops_job",
        lambda **kwargs: (ops_ctx, GateResult(ok=True), None),
    )
    monkeypatch.setattr(
        cli,
        "create_llm_client",
        lambda config: SimpleNamespace(usage_tracker=FakeUsage()),
    )

    def fake_translate(text, client, glossary, **kwargs):
        calls["source"] = text
        calls.update(kwargs)
        return SimpleNamespace(final_text="# Heading\n\nText.\n", verdict="ok")

    monkeypatch.setattr(cli, "translate_file", fake_translate)
    monkeypatch.setattr(
        cli,
        "finish_ops_job",
        lambda ctx, **kwargs: finished.append({"ctx": ctx, **kwargs}),
    )

    result = runner.invoke(
        cli.app,
        [
            "translate-file",
            str(source),
            "--output",
            str(output),
            "--source-lang",
            "ru",
            "--target-lang",
            "en",
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.read_text(encoding="utf-8") == "# Heading\n\nText.\n"
    assert calls["source"] == source.read_text(encoding="utf-8")
    assert calls["source_lang"] == "ru"
    assert calls["target_lang"] == "en"
    assert calls["file_path"] == str(source)
    assert finished == [
        {
            "ctx": ops_ctx,
            "status": "ok",
            "cost_rub": 1.25,
            "input_tokens": 11,
            "output_tokens": 7,
        }
    ]


def test_F006_translate_file_denies_paid_work_before_client(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.md"
    source.write_text("Текст.\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "begin_ops_job",
        lambda **kwargs: (
            None,
            GateResult(ok=False, status="denied_accounting"),
            "accounting unavailable",
        ),
    )
    monkeypatch.setattr(
        cli,
        "create_llm_client",
        lambda _config: (_ for _ in ()).throw(AssertionError("LLM must not start")),
    )

    result = runner.invoke(cli.app, ["translate-file", str(source)])

    assert result.exit_code == 1
    assert "accounting unavailable" in result.output


def test_F006_list_models_and_extract_do_not_start_llm(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    source.write_text("# Title\n\nText.\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "OpenAI",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM must not start")),
    )
    monkeypatch.setattr(
        cli,
        "create_llm_client",
        lambda _config: (_ for _ in ()).throw(AssertionError("LLM must not start")),
    )

    models = runner.invoke(cli.app, ["list-models"])
    extracted = runner.invoke(cli.app, ["extract", str(source), "--format", "json"])

    assert models.exit_code == 0, models.output
    assert "Configured model chains" in models.output
    assert extracted.exit_code == 0, extracted.output
    assert '"text": "Title"' in extracted.output
