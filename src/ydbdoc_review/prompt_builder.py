"""Assemble translation prompts from templates and optional context files."""

from __future__ import annotations

import collections
import logging
from pathlib import Path

from ydbdoc_review.config import Settings
from ydbdoc_review.prompt_defaults import (
    DEFAULT_EN_STYLE_GUIDE_FILE,
    DEFAULT_QUALITY_HIERARCHY_FILE,
    DEFAULT_SEGMENT_RULES_FILE,
    SECTION_PREAMBLES,
    TRANSLATE_SYSTEM_TEMPLATE,
)

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Build translate instructions with optional glossary, project info, style guide."""

    def __init__(
        self,
        *,
        prompts_dir: str | Path,
        glossary_path: str = "",
        project_info_path: str = "",
        translate_system_template_path: str = "",
        quality_hierarchy_path: str = "",
        en_style_guide_path: str = "",
        segment_rules_path: str = "",
    ) -> None:
        self._prompts_dir = Path(prompts_dir)
        self._system_template = self._load_template(
            translate_system_template_path, TRANSLATE_SYSTEM_TEMPLATE
        )
        self._quality_hierarchy = self._load_prompt_file(
            quality_hierarchy_path or DEFAULT_QUALITY_HIERARCHY_FILE
        )
        self._en_style_guide = self._load_prompt_file(
            en_style_guide_path or DEFAULT_EN_STYLE_GUIDE_FILE
        )
        self._segment_rules = self._load_prompt_file(
            segment_rules_path or DEFAULT_SEGMENT_RULES_FILE
        )
        self._context_sections: dict[str, str] = {
            "project_info_section": self._render_section(
                "project_info", self._load_optional_file(project_info_path)
            ),
            "glossary_section": self._render_section(
                "glossary", self._load_optional_file(glossary_path)
            ),
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> PromptBuilder:
        return cls(
            prompts_dir=settings.prompts_dir,
            glossary_path=settings.glossary_path,
            project_info_path=settings.project_info_path,
            translate_system_template_path=settings.translate_system_template_path,
            quality_hierarchy_path=settings.quality_hierarchy_path,
            en_style_guide_path=settings.en_style_guide_path,
            segment_rules_path=settings.segment_rules_path,
        )

    def build_translate_segment_instructions(
        self, source_lang: str, target_lang: str
    ) -> str:
        """Full system/instructions string for one unit translation call."""
        target = target_lang.strip().lower()
        variables: dict[str, str] = {
            "quality_hierarchy_section": self._quality_hierarchy,
            "segment_rules": self._segment_rules,
            "style_guide_section": (
                self._en_style_guide if target in ("english", "en") else ""
            ),
            **self._context_sections,
        }
        return self._system_template.format_map(
            collections.defaultdict(str, variables)
        ).strip()

    @staticmethod
    def build_segment_user_input(
        *,
        source_lang: str,
        target_lang: str,
        source_path: str,
        fragment_type: str,
        fragment_label: str,
        body: str,
    ) -> str:
        return (
            f"File: `{source_path}`\n"
            f"Fragment type: `{fragment_type}`\n"
            f"Fragment label: `{fragment_label}`\n"
            f"SOURCE language: {source_lang}\n"
            f"TARGET language: {target_lang}\n\n"
            f"--- SOURCE BEGIN ---\n{body}\n--- SOURCE END ---\n"
        )

    def _resolve_path(self, path: str) -> Path:
        p = Path(path).expanduser()
        if p.is_absolute():
            return p
        return (self._prompts_dir / p).resolve()

    def _load_template(self, path: str, default: str) -> str:
        if not path.strip():
            return default
        resolved = self._resolve_path(path)
        try:
            return resolved.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "Could not load system template %s — using built-in default",
                resolved,
            )
            return default

    def _load_prompt_file(self, path: str) -> str:
        """Load a file from prompts_dir (or absolute path); empty if missing."""
        resolved = self._resolve_path(path)
        try:
            text = resolved.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("Prompt file missing: %s", resolved)
            return ""
        return f"{text}\n\n" if text else ""

    def _load_optional_file(self, path: str) -> str:
        if not path.strip():
            return ""
        resolved = self._resolve_path(path)
        try:
            return resolved.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning(
                "Could not load context file %s — section omitted", resolved
            )
            return ""

    @staticmethod
    def _render_section(name: str, content: str) -> str:
        if not content:
            return ""
        preamble = SECTION_PREAMBLES.get(name, "")
        if preamble:
            return f"{preamble}\n\n{content}\n\n"
        return f"{content}\n\n"
