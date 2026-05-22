"""Detect when EN translation must be replaced from RU (source of truth)."""

from __future__ import annotations

import os
import re

from ydbdoc_review.llm import _diff_en_update_looks_truncated

_PREREQ_KAFKA_RU_RE = re.compile(
    r"9092,\s*9093.*[Kk]afka|порт.*Kafka\s*API",
    re.IGNORECASE,
)
_PREREQ_KAFKA_EN_RE = re.compile(
    r"9092,\s*9093.*[Kk]afka|ports?\s+for.*[Kk]afka",
    re.IGNORECASE,
)
_CONFIG_DIR_RE = re.compile(r"--config-dir\s+/\S")
_YAML_CONFIG_RE = re.compile(r"--yaml-config\s+/\S")
_KAFKA_PORT_FLAG_RE = re.compile(r"--kafka-port\s+\d+")
_SSD_GROUP_RE = re.compile(r"ssd:(\d+)")
_H3_HEADING_RE = re.compile(r"^###\s+\S", re.MULTILINE)


def ru_authority_text(ru_pr: str, ru_at_base: str | None) -> str:
    """Prefer longer RU from merge base when the PR branch RU is not ahead yet."""
    if ru_at_base and len(ru_at_base.strip()) > len(ru_pr.strip()):
        return ru_at_base
    return ru_pr


def en_coverage_behind_ru(ru_full: str, en_text: str) -> bool:
    """
    True when EN is missing structural blocks present in RU (not just shorter prose).

    Typical on ``main``: RU article was extended but EN was never fully synced.
  """
    if len(ru_full) < 600:
        return False
    ru_h3 = len(_H3_HEADING_RE.findall(ru_full))
    en_h3 = len(_H3_HEADING_RE.findall(en_text))
    if ru_h3 >= 4 and en_h3 < ru_h3:
        return True
    if len(ru_full) >= 1200 and len(en_text) < int(len(ru_full) * 0.58):
        return True
    return False


def ru_authority_resync_enabled() -> bool:
    raw = os.environ.get("YDBDOC_RU_AUTHORITY_RESYNC", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def critical_ru_en_mismatches(
    ru_full: str,
    en_text: str,
    *,
    en_reference: str | None = None,
    ru_authority: str | None = None,
) -> list[str]:
    """
    Semantic mismatches that require full re-translation from RU (not grammar-only).

    RU from the source PR is the authority; stale EN merge artifacts are bugs.
    """
    issues: list[str] = []
    if len(ru_full) < 500:
        return issues
    ru_ref = ru_authority_text(ru_full, ru_authority)
    ref = en_reference or en_text
    if ref and _diff_en_update_looks_truncated(en_text, ref):
        issues.append("truncated")
    if len(ru_ref) > 8000 and len(en_text) < int(len(ru_ref) * 0.72):
        issues.append("too_short_vs_ru")
    if en_coverage_behind_ru(ru_ref, en_text):
        issues.append("en_behind_ru")

    from ydbdoc_review.ru_en_structure import (
        index_bullets_behind_ru,
        tab_items_missing_vs_source,
    )

    if tab_items_missing_vs_source(ru_ref, en_text):
        issues.append("missing_tab_items")
    if index_bullets_behind_ru(ru_ref, en_text):
        issues.append("index_bullets_behind")

    ru_has_cfg = bool(_CONFIG_DIR_RE.search(ru_full))
    en_has_cfg = bool(_CONFIG_DIR_RE.search(en_text))
    en_has_yaml = bool(_YAML_CONFIG_RE.search(en_text))
    if ru_has_cfg and en_has_yaml and not en_has_cfg:
        issues.append("config_dir_vs_yaml_config")

    ru_kafka_flags = set(_KAFKA_PORT_FLAG_RE.findall(ru_full))
    en_kafka_flags = set(_KAFKA_PORT_FLAG_RE.findall(en_text))
    if ru_kafka_flags and ru_kafka_flags != en_kafka_flags:
        issues.append("kafka_port_flags")

    if "9092" in ru_full and "9093" in ru_full:
        if "9092" not in en_text or "9093" not in en_text:
            issues.append("missing_kafka_prereq_ports")

    ru_ssd = _SSD_GROUP_RE.findall(ru_full)
    en_ssd = _SSD_GROUP_RE.findall(en_text)
    if ru_ssd and en_ssd and ru_ssd != en_ssd:
        issues.append("ssd_group_count")

    canon = canonical_token_filename_from_ru(ru_full)
    en_tokens = _cli_token_filenames(en_text)
    if canon:
        if en_tokens and en_tokens != {canon}:
            issues.append("token_file_name")
    else:
        ru_tokens = _cli_token_filenames(ru_full)
        if ru_tokens and en_tokens and ru_tokens != en_tokens:
            issues.append("token_file_name")
        if len(ru_tokens) == 1 and len(en_tokens) > 1:
            issues.append("token_file_name")

    return issues


def canonical_token_filename_from_ru(ru_full: str) -> str | None:
    """Single basename EN should use when RU mentions token-file or auth_token."""
    names = _cli_token_filenames(ru_full)
    if not names:
        return None
    if len(names) == 1:
        return next(iter(names))
    if "auth_token" in names:
        return "auth_token"
    return "token-file"


def _cli_token_filenames(text: str) -> set[str]:
    names: set[str] = set()
    for pat in (
        r">\s*(token-file|auth_token)\b",
        r"[-\s]f\s+(token-file|auth_token)\b",
        r"--token-file\s+(token-file|auth_token)\b",
    ):
        names.update(re.findall(pat, text))
    return names


def mismatch_gate_codes() -> frozenset[str]:
    return frozenset(
        {
            "config_dir_vs_yaml_config",
            "kafka_port_flags",
            "missing_kafka_prereq_ports",
            "ssd_group_count",
            "token_file_name",
            "en_behind_ru",
            "missing_tab_items",
            "index_bullets_behind",
        }
    )
