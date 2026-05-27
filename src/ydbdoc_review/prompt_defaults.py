"""Default prompt templates for translation (composed by :class:`PromptBuilder`)."""

from __future__ import annotations

TRANSLATE_SYSTEM_TEMPLATE = """\
Вы переводите **один фрагмент** технической документации YDB (не весь файл).

Вход: SOURCE-язык, TARGET-язык, путь к файлу, тип фрагмента, затем исходный markdown.

{quality_hierarchy_section}\
{project_info_section}\
{glossary_section}\
{style_guide_section}\
{segment_rules}\
"""

SECTION_PREAMBLES: dict[str, str] = {
    "project_info": "Информация о проекте:",
    "glossary": (
        "Глоссарий проекта. Используйте эти термины строго и единообразно в переводе:"
    ),
}

# Relative to ``prompts_dir`` when paths are not absolute.
DEFAULT_QUALITY_HIERARCHY_FILE = "translate_quality_hierarchy.md"
DEFAULT_EN_STYLE_GUIDE_FILE = "en_style_guide.md"
DEFAULT_SEGMENT_RULES_FILE = "08_translate_segment.txt"
DEFAULT_FILE_RULES_FILE = "10_translate_file_with_plan.txt"
DEFAULT_ANNOTATED_CHUNK_RULES_FILE = "11_annotated_chunk.txt"
DEFAULT_PLACEHOLDER_RULES_FILE = "12_translate_placeholder_json.txt"

TRANSLATE_PLACEHOLDER_TEMPLATE = """\
Вы переводите **отдельные строки** markdown-документации YDB. Ответ — только JSON.

{quality_hierarchy_section}\
{project_info_section}\
{glossary_section}\
{style_guide_section}\
{placeholder_rules}\
"""

TRANSLATE_ANNOTATED_CHUNK_TEMPLATE = """\
Вы переводите **один размеченный фрагмент** markdown-файла YDB.

В user-сообщении: карта регионов по строкам и SOURCE с префиксами `L00001|`.
Следуйте карте: COPY — дословно, TRANSLATE — на TARGET.

{quality_hierarchy_section}\
{project_info_section}\
{glossary_section}\
{style_guide_section}\
{annotated_chunk_rules}\
"""

TRANSLATE_FILE_SYSTEM_TEMPLATE = """\
Вы переводите **markdown-файл** (или его часть) технической документации YDB.

В user-сообщении: план по **номерам строк**, затем исходный текст. Следуйте плану.

{quality_hierarchy_section}\
{project_info_section}\
{glossary_section}\
{style_guide_section}\
{file_rules}\
"""
