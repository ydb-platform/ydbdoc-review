"""YAML-driven harness regression cases (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import MagicMock

import yaml

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.profiles import TRANSLATE_PROFILE, VERIFY_PROFILE
from ydbdoc_review.harness.runner import FileHarness
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.types import FileTranslationResult, FileVerdict
from ydbdoc_review.translation.glossary import load_glossary

HarnessProfileName = Literal["translate", "verify"]

_PROFILES = {
    "translate": TRANSLATE_PROFILE,
    "verify": VERIFY_PROFILE,
}


@dataclass(frozen=True)
class SegmentExpectation:
    segment_id: str
    placeholders: list[str] = field(default_factory=list)
    text_contains: list[str] = field(default_factory=list)
    text_excludes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HarnessCaseExpectation:
    verdict: FileVerdict
    critic_unresolved_verdict: str | None = None
    critic_unresolved_issues: int | None = None
    final_text_contains: list[str] = field(default_factory=list)
    final_text_excludes: list[str] = field(default_factory=list)
    segments: list[SegmentExpectation] = field(default_factory=list)


@dataclass(frozen=True)
class HarnessCase:
    """One regression fixture: inputs + expected harness outcome."""

    id: str
    case_dir: Path
    description: str
    profile: HarnessProfileName
    file_path: str
    source_text: str
    target_text: str | None
    enable_critic: bool
    source_lang: str
    target_lang: str
    llm_responses: list[str]
    expect: HarnessCaseExpectation


@dataclass
class HarnessCaseResult:
    case: HarnessCase
    state: FileRunState
    result: FileTranslationResult


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _mock_client(responses: list[str]) -> YandexLLMClient:
    mock_openai = MagicMock()
    if responses:
        mock_openai.chat.completions.create.side_effect = [
            _completion(r) for r in responses
        ]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )


def _read_case_text(case_dir: Path, value: str) -> str:
    path = case_dir / value
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return value


def _parse_segment_expectations(raw: Any) -> list[SegmentExpectation]:
    if not raw:
        return []
    if isinstance(raw, dict):
        out: list[SegmentExpectation] = []
        for segment_id, spec in raw.items():
            if not isinstance(spec, dict):
                raise ValueError(f"segment {segment_id!r}: expected mapping")
            out.append(
                SegmentExpectation(
                    segment_id=segment_id,
                    placeholders=list(spec.get("placeholders") or []),
                    text_contains=list(spec.get("text_contains") or []),
                    text_excludes=list(spec.get("text_excludes") or []),
                )
            )
        return out
    raise ValueError("expect.segments must be a mapping segment_id → spec")


def _parse_expectation(raw: dict[str, Any]) -> HarnessCaseExpectation:
    return HarnessCaseExpectation(
        verdict=raw["verdict"],
        critic_unresolved_verdict=raw.get("critic_unresolved_verdict"),
        critic_unresolved_issues=raw.get("critic_unresolved_issues"),
        final_text_contains=list(raw.get("final_text_contains") or []),
        final_text_excludes=list(raw.get("final_text_excludes") or []),
        segments=_parse_segment_expectations(raw.get("segments")),
    )


def load_harness_case(case_yaml: Path) -> HarnessCase:
    """Load ``case.yaml`` and sibling markdown inputs."""
    case_dir = case_yaml.parent
    raw = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{case_yaml}: root must be a mapping")

    profile = raw.get("profile", "verify")
    if profile not in _PROFILES:
        raise ValueError(f"{case_yaml}: unknown profile {profile!r}")

    source_key = raw.get("source")
    if not source_key:
        raise ValueError(f"{case_yaml}: missing source")

    target_key = raw.get("target")
    if profile == "verify" and not target_key:
        raise ValueError(f"{case_yaml}: verify profile requires target")

    llm_block = raw.get("llm") or {}
    responses = list(llm_block.get("responses") or [])

    return HarnessCase(
        id=str(raw.get("id") or case_dir.name),
        case_dir=case_dir,
        description=str(raw.get("description") or ""),
        profile=profile,
        file_path=str(raw.get("file_path") or f"docs/ru/{case_dir.name}.md"),
        source_text=_read_case_text(case_dir, str(source_key)),
        target_text=_read_case_text(case_dir, str(target_key)) if target_key else None,
        enable_critic=bool(raw.get("enable_critic", False)),
        source_lang=str(raw.get("source_lang") or "ru"),
        target_lang=str(raw.get("target_lang") or "en"),
        llm_responses=responses,
        expect=_parse_expectation(raw["expect"]),
    )


def discover_harness_cases(cases_root: Path) -> list[Path]:
    """Return ``case.yaml`` paths under ``cases_root`` (one level deep)."""
    if not cases_root.is_dir():
        return []
    return sorted(cases_root.glob("*/case.yaml"))


def run_harness_case(case: HarnessCase) -> HarnessCaseResult:
    """Execute a fixture through ``FileHarness`` with optional mocked LLM."""
    profile = _PROFILES[case.profile]
    state = FileRunState(
        mode=case.profile,
        file_path=case.file_path,
        raw_source_text=case.source_text,
        source_text=case.source_text,
        existing_target_text=case.target_text if case.profile == "verify" else None,
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        _mock_client(case.llm_responses),
        glossary=load_glossary(),
        config=cfg,
        source_lang=case.source_lang,
        target_lang=case.target_lang,
        enable_critic=case.enable_critic,
    )
    result = FileHarness(profile).run(state, ctx)
    return HarnessCaseResult(case=case, state=state, result=result)


def assert_harness_case(result: HarnessCaseResult) -> None:
    """Raise ``AssertionError`` when the run does not match ``case.expect``."""
    case = result.case
    expect = case.expect
    got = result.result

    assert got.verdict == expect.verdict, (
        f"{case.id}: verdict {got.verdict!r} != {expect.verdict!r}"
    )

    if expect.critic_unresolved_verdict is not None:
        unresolved = result.state.critic_unresolved
        got_verdict = unresolved.verdict if unresolved else None
        assert got_verdict == expect.critic_unresolved_verdict, (
            f"{case.id}: critic_unresolved.verdict {got_verdict!r} "
            f"!= {expect.critic_unresolved_verdict!r}"
        )

    if expect.critic_unresolved_issues is not None:
        count = len(result.state.critic_unresolved.issues) if result.state.critic_unresolved else 0
        assert count == expect.critic_unresolved_issues, (
            f"{case.id}: critic_unresolved issues {count} != {expect.critic_unresolved_issues}"
        )

    for needle in expect.final_text_contains:
        assert needle in got.final_text, (
            f"{case.id}: final_text missing {needle!r}"
        )
    for needle in expect.final_text_excludes:
        assert needle not in got.final_text, (
            f"{case.id}: final_text must not contain {needle!r}"
        )

    for seg_expect in expect.segments:
        translation = result.state.translations.get(seg_expect.segment_id)
        if translation is None:
            raise AssertionError(
                f"{case.id}: no translation for segment {seg_expect.segment_id!r}"
            )
        for placeholder in seg_expect.placeholders:
            assert placeholder in translation, (
                f"{case.id}: {seg_expect.segment_id} missing placeholder {placeholder!r} "
                f"in {translation!r}"
            )
        for needle in seg_expect.text_contains:
            assert needle in translation, (
                f"{case.id}: {seg_expect.segment_id} missing {needle!r} in {translation!r}"
            )
        for needle in seg_expect.text_excludes:
            assert needle not in translation, (
                f"{case.id}: {seg_expect.segment_id} must not contain {needle!r} "
                f"in {translation!r}"
            )


def case_id_from_path(case_yaml: Path) -> str:
    raw = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("id"):
        return str(raw["id"])
    return case_yaml.parent.name
