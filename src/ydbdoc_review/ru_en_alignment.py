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


def ru_authority_resync_enabled() -> bool:
    raw = os.environ.get("YDBDOC_RU_AUTHORITY_RESYNC", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def critical_ru_en_mismatches(
    ru_full: str,
    en_text: str,
    *,
    en_reference: str | None = None,
) -> list[str]:
    """
    Semantic mismatches that require full re-translation from RU (not grammar-only).

    RU from the source PR is the authority; stale EN merge artifacts are bugs.
    """
    issues: list[str] = []
    if len(ru_full) < 500:
        return issues
    ref = en_reference or en_text
    if ref and _diff_en_update_looks_truncated(en_text, ref):
        issues.append("truncated")
    if len(ru_full) > 8000 and len(en_text) < int(len(ru_full) * 0.72):
        issues.append("too_short_vs_ru")

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

    ru_tokens = _cli_token_filenames(ru_full)
    en_tokens = _cli_token_filenames(en_text)
    if ru_tokens and en_tokens and ru_tokens != en_tokens:
        issues.append("token_file_name")
    if len(ru_tokens) == 1 and len(en_tokens) > 1:
        issues.append("token_file_name")

    return issues


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
        }
    )
