"""Load and format the translation glossary for LLM prompts."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GlossaryEntry(BaseModel):
    """One glossary row: bilingual pair or a literal term."""

    model_config = ConfigDict(extra="forbid")

    ru: str | None = None
    en: str | None = None
    term: str | None = None
    aliases_ru: list[str] = Field(default_factory=list)
    do_not_translate: bool = False
    notes: str | None = None
    context: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> GlossaryEntry:
        if self.term is not None:
            if self.ru is not None or self.en is not None:
                raise ValueError("term entries must not also set ru/en")
            return self
        if not self.ru or not self.en:
            raise ValueError("bilingual entries require both ru and en")
        return self


class Glossary(BaseModel):
    """In-memory glossary loaded from YAML."""

    model_config = ConfigDict(extra="forbid")
    entries: list[GlossaryEntry]

    @classmethod
    def from_yaml_text(cls, text: str) -> Glossary:
        data = yaml.safe_load(text)
        if not isinstance(data, list):
            raise ValueError("glossary YAML must be a list of entries")
        entries = [GlossaryEntry.model_validate(item) for item in data]
        return cls(entries=entries)

    @classmethod
    def from_yaml_path(cls, path: Path) -> Glossary:
        return cls.from_yaml_text(path.read_text(encoding="utf-8"))

    @classmethod
    def load_default(cls) -> Glossary:
        """Load the packaged ``prompts/glossary.yaml``."""
        pkg = resources.files("ydbdoc_review.prompts")
        text = (pkg / "glossary.yaml").read_text(encoding="utf-8")
        return cls.from_yaml_text(text)

    def to_prompt_dicts(self) -> list[dict[str, Any]]:
        """Serialize entries for injection into prompts (stable key order)."""
        out: list[dict[str, Any]] = []
        for entry in self.entries:
            row: dict[str, Any] = {}
            if entry.term is not None:
                row["term"] = entry.term
            else:
                row["ru"] = entry.ru
                row["en"] = entry.en
            if entry.aliases_ru:
                row["aliases_ru"] = entry.aliases_ru
            if entry.do_not_translate:
                row["do_not_translate"] = True
            if entry.notes:
                row["notes"] = entry.notes
            if entry.context:
                row["context"] = entry.context
            out.append(row)
        return out

    def to_prompt_yaml(self) -> str:
        """YAML block for translator/critic prompts (MVP: full glossary)."""
        return yaml.dump(
            self.to_prompt_dicts(),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).strip()


def load_glossary(path: Path | None = None) -> Glossary:
    """Load glossary from ``path`` or the packaged default."""
    if path is None:
        return Glossary.load_default()
    return Glossary.from_yaml_path(path)
