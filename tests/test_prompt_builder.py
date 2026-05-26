"""PromptBuilder, strip_code_fence, composed translate instructions."""

from pathlib import Path

from ydbdoc_review.llm import strip_code_fence
from ydbdoc_review.prompt_builder import PromptBuilder


def test_strip_code_fence_removes_outer_wrapper():
    raw = "```markdown\n# Title\n\nBody\n```"
    assert strip_code_fence(raw) == "# Title\n\nBody"


def test_strip_code_fence_keeps_doc_starting_with_fence():
    raw = "```bash\necho hi\n```\n\nMore prose."
    assert strip_code_fence(raw) == raw


def test_strip_code_fence_mismatched_closers():
    raw = "```yaml\na: 1\n```extra"
    assert strip_code_fence(raw) == raw


def test_prompt_builder_includes_quality_and_rules(tmp_path: Path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "translate_quality_hierarchy.md").write_text(
        "## Quality\n\nBest only.\n", encoding="utf-8"
    )
    (prompts / "en_style_guide.md").write_text("## Style\n\nUse you.\n", encoding="utf-8")
    (prompts / "08_translate_segment.txt").write_text(
        "## Rules\n\nOutput fragment only.\n", encoding="utf-8"
    )
    builder = PromptBuilder(prompts_dir=prompts)
    out = builder.build_translate_segment_instructions("Russian", "English")
    assert "Quality" in out
    assert "Style" in out
    assert "Rules" in out
    assert "один фрагмент" in out


def test_prompt_builder_omits_style_guide_for_non_en(tmp_path: Path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "translate_quality_hierarchy.md").write_text("Q\n", encoding="utf-8")
    (prompts / "en_style_guide.md").write_text("EN STYLE ONLY\n", encoding="utf-8")
    (prompts / "08_translate_segment.txt").write_text("R\n", encoding="utf-8")
    builder = PromptBuilder(prompts_dir=prompts)
    out = builder.build_translate_segment_instructions("English", "Russian")
    assert "EN STYLE ONLY" not in out


def test_prompt_builder_glossary_section(tmp_path: Path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    glossary = tmp_path / "glossary.md"
    glossary.write_text("| RU | EN |\n| кластер | cluster |\n", encoding="utf-8")
    (prompts / "translate_quality_hierarchy.md").write_text("", encoding="utf-8")
    (prompts / "en_style_guide.md").write_text("", encoding="utf-8")
    (prompts / "08_translate_segment.txt").write_text("R\n", encoding="utf-8")
    builder = PromptBuilder(
        prompts_dir=prompts,
        glossary_path=str(glossary),
    )
    out = builder.build_translate_segment_instructions("Russian", "English")
    assert "Глоссарий" in out
    assert "cluster" in out
