"""Translation pipeline: glossary, prompts, translator, critic."""

from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry, load_glossary

__all__ = ["Glossary", "GlossaryEntry", "load_glossary"]
