"""Proof contracts for conservative source-coverage plans."""

# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.ops.translation_checkpoint import (
    CheckpointIdentity,
    UnitReceipt,
    VerifiedUnit,
    translation_unit_key,
    translation_unit_key_for_segment,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.coverage import (
    CoveragePlan,
    CoverageUnit,
    decode_coverage_plan,
    encode_coverage_plan,
    plan_source_coverage,
)


@pytest.fixture
def current_authority() -> RuAuthority:
    return RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=51079,
        source_base_sha="1" * 40,
        source_head_sha="2" * 40,
        baseline_sha="3" * 40,
        ru_sha="3" * 40,
        mode=RuAuthorityMode.CURRENT,
    )


@pytest.mark.parametrize("stem", ["user-token", "user-token-lifecycle"])
def test_mermaid_labels_are_not_materialize_protected_units(current_authority, stem):
    fixtures = Path(__file__).parents[1] / "fixtures" / "pr51079-mermaid"
    source = (fixtures / f"{stem}.ru.md").read_text()
    target = (fixtures / f"{stem}.en.md").read_text()
    result = plan_source_coverage(
        source_path=f"ydb/docs/ru/core/security/_assets/{stem}.md",
        source_text=source, existing_en=target, authority=current_authority,
    )
    assert result.units
    assert all(unit.action == "translate_required" for unit in result.units)
    assert len(result.units) == (16 if stem == "user-token" else 22)
    assert result.source_hash == hashlib.sha256(source.encode()).hexdigest()


def test_mermaid_coverage_protects_syntax_but_not_labels():
    from ydbdoc_review.translation.coverage import _document_atoms

    fixtures = Path(__file__).parents[1] / "fixtures" / "pr51079-mermaid"
    source = (fixtures / "user-token-lifecycle.ru.md").read_text()
    target = (fixtures / "user-token-lifecycle.en.md").read_text()
    assert _document_atoms(parse_markdown(source)) == _document_atoms(parse_markdown(target))
    changed = target.replace("node->>cache", "cache->>node")
    assert _document_atoms(parse_markdown(source)) != _document_atoms(parse_markdown(changed))


def test_stale_whole_file_mermaid_receipt_cannot_waive_label_obligations(current_authority):
    fixtures = Path(__file__).parents[1] / "fixtures" / "pr51079-mermaid"
    source = (fixtures / "user-token.ru.md").read_text()
    target = (fixtures / "user-token.en.md").read_text()
    source_path = "ydb/docs/ru/core/security/_assets/user-token.md"
    identity = _identity(current_authority)
    old_key = translation_unit_key(
        source=source.encode(), source_path=source_path, target_locale="en",
        atom_signature=(), parent_context="coverage:protected-only",
    )
    target_hash = hashlib.sha256(target.encode()).hexdigest()
    stale = VerifiedUnit(
        receipt=UnitReceipt(
            identity=identity, unit_key=old_key,
            source_hash=hashlib.sha256(source.encode()).hexdigest(), target_hash=target_hash,
            object_key=f"translation/v1/objects/{target_hash}", validated=True,
        ), source=source.encode(), target=target.encode(),
    )
    result = plan_source_coverage(
        source_path=source_path, source_text=source, existing_en=target,
        authority=current_authority, checkpoint_identity=identity, verified_units=(stale,),
    )
    assert len(result.units) == 16
    assert all(unit.action == "translate_required" and unit.key != old_key for unit in result.units)


def test_mermaid_checkpoint_keys_bind_original_label_text():
    source = "```mermaid\nsequenceDiagram\nactor a as Пользователь\n```\n"
    changed = source.replace("Пользователь", "Новый пользователь")
    path = "ydb/docs/ru/core/security/_assets/user-token.md"
    first = extract_segments(parse_markdown(source))[0]
    second = extract_segments(parse_markdown(changed))[0]
    assert translation_unit_key_for_segment(first, source_path=path, target_locale="en") != (
        translation_unit_key_for_segment(second, source_path=path, target_locale="en")
    )


def test_empty_current_ru_diff_does_not_prove_en_coverage(
    current_authority: RuAuthority,
) -> None:
    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/security/authentication.md",
        source_text="# Authentication\n\nNew provider requirements.\n",
        existing_en="# Authentication\n\nOld provider requirements.\n",
        authority=current_authority,
    )

    assert any(unit.action == "translate_required" for unit in result.units)
    assert all(unit.action != "reuse_verified" for unit in result.units)


def _identity(authority: RuAuthority, fingerprint: str = "4" * 64) -> CheckpointIdentity:
    return CheckpointIdentity(authority, fingerprint)


def _verified_unit(
    *,
    source_text: str,
    target: str,
    source_path: str,
    identity: CheckpointIdentity,
) -> VerifiedUnit:
    segment = extract_segments(parse_markdown(source_text))[0]
    source = segment.text.encode("utf-8")
    target_bytes = target.encode("utf-8")
    key = translation_unit_key_for_segment(
        segment,
        source_path=source_path,
        target_locale="en",
    )
    target_hash = hashlib.sha256(target_bytes).hexdigest()
    receipt = UnitReceipt(
        identity=identity,
        unit_key=key,
        source_hash=hashlib.sha256(source).hexdigest(),
        target_hash=target_hash,
        object_key=f"translation/v1/objects/{target_hash}",
        validated=True,
    )
    return VerifiedUnit(receipt=receipt, source=source, target=target_bytes)


def test_matching_receipt_and_current_en_are_the_only_reuse_proof(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/core/security/authentication.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="Новые требования.\n",
        target="New requirements.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="Новые требования.\n",
        existing_en="New requirements.\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "units"
    assert len(result.units) == 1
    assert result.units[0].action == "reuse_verified"
    assert result.units[0].target == "New requirements."
    assert result.units[0].en_span == (0, len("New requirements."))


def test_changed_expected_fingerprint_rejects_otherwise_valid_receipt(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/core/security/authentication.md"
    f1 = _identity(current_authority, "1" * 64)
    f2 = _identity(current_authority, "2" * 64)
    verified = _verified_unit(
        source_text="Источник.\n",
        target="Accepted target.",
        source_path=path,
        identity=f1,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="Источник.\n",
        existing_en="Accepted target.\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=f2,
    )

    assert not any(unit.action == "reuse_verified" for unit in result.units)


@pytest.mark.parametrize("changed_en", [False, True])
def test_missing_expected_identity_never_reuses_receipts(
    current_authority: RuAuthority,
    changed_en: bool,
) -> None:
    path = "ydb/docs/ru/core/security/authentication.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="Источник.\n",
        target="Accepted target.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="Источник.\n",
        existing_en="Changed target.\n" if changed_en else "Accepted target.\n",
        authority=current_authority,
        verified_units=(verified,),
    )

    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_current_en_drift_rejects_matching_receipt(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="Источник.\n",
        target="Receipt target.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="Источник.\n",
        existing_en="Newer accepted EN repair.\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "full"
    assert not any(unit.action == "reuse_verified" for unit in result.units)


@pytest.mark.parametrize(
    ("source", "existing_en"),
    [
        pytest.param(
            "Source.\n",
            "Target.\n\n```yaml\nx: y\n```\n",
            id="additional-en-atom",
        ),
        pytest.param(
            "Source.\n",
            "Target.\n\n[Additional](extra.md)\n",
            id="additional-en-inline-atom",
        ),
        pytest.param(
            "Source.\n\n```yaml\nx: y\n```\n",
            "Target.\n",
            id="missing-en-atom",
        ),
        pytest.param(
            "Source.\n\n```yaml\nx: y\n```\n\n{% include [x](required.md) %}\n",
            "Target.\n\n{% include [x](required.md) %}\n\n```yaml\nx: y\n```\n",
            id="reordered-en-atoms",
        ),
        pytest.param(
            "Source.\n\n```yaml\nx: y\n```\n",
            "Target.\n\n```yaml\nx: changed\n```\n",
            id="changed-en-atom-value",
        ),
        pytest.param(
            "Source.\n\n```yaml\nx: y\n```\n",
            "Target.\n\n<div>x: y</div>\n",
            id="changed-en-atom-type",
        ),
        pytest.param(
            "Source.\n\n```yaml\nx: y\n```\n\n```yaml\nx: y\n```\n",
            "Target.\n\n```yaml\nx: y\n```\n\n```yaml\nx: y\n```\n",
            id="ambiguous-duplicate-atoms",
        ),
    ],
)
def test_document_protected_atom_drift_rejects_reuse(
    current_authority: RuAuthority,
    source: str,
    existing_en: str,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text=source,
        target="Target.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text=source,
        existing_en=existing_en,
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "full"
    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_cross_type_protected_atom_reorder_rejects_reuse(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    source = "Source [link](required.md).\n\n```yaml\nx: y\n```\n"
    verified = _verified_unit(
        source_text=source,
        target="Target ⟦L1⟧link⟦L1⟧.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text=source,
        existing_en="```yaml\nx: y\n```\n\nTarget [link](required.md).\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "full"
    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_changed_link_atom_rejects_same_protected_target_text(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="[Источник](./required.md)\n",
        target="⟦L1⟧Target⟦L1⟧",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="[Источник](./required.md)\n",
        existing_en="[Target](./different.md)\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_matching_protected_receipt_keeps_canonical_checkpoint_target(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    protected_target = "⟦L1⟧Target⟦L1⟧ with ⟦C1⟧."
    verified = _verified_unit(
        source_text="[Источник](./required.md) с `token`.\n",
        target=protected_target,
        source_path=path,
        identity=identity,
    )
    existing = "[Target](./required.md) with `token`.\n"

    result = plan_source_coverage(
        source_path=path,
        source_text="[Источник](./required.md) с `token`.\n",
        existing_en=existing,
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "units"
    assert result.units[0].target == protected_target
    assert result.units[0].en_span == (0, len(existing.rstrip("\n")))


def test_shifted_protected_atom_ids_do_not_reuse_same_placeholder_text(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="`first` затем `second`.\n",
        target="⟦C1⟧ then ⟦C2⟧.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="`first` затем `second`.\n",
        existing_en="`second` then `first`.\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "full"
    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_ordinary_fragment_link_is_not_required_fragment_evidence(
    current_authority: RuAuthority,
) -> None:
    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/security/caching-authentication-results.md",
        source_text="[Токен](../concepts/glossary.md#user-token)\n",
        existing_en="[Token](../concepts/glossary.md#user-token)\n",
        authority=current_authority,
    )

    assert result.required_fragments == frozenset()
    assert result.mode == "full"
    assert all(unit.action == "translate_required" for unit in result.units)


def test_changed_heading_anchor_rejects_same_receipt_text(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="# Источник {#stable}\n",
        target="Target",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="# Источник {#stable}\n",
        existing_en="# Target {#shifted}\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_changed_include_identity_rejects_otherwise_matching_receipts(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    source = "Источник.\n\n{% include [x](required.md) %}\n"
    verified = _verified_unit(
        source_text=source,
        target="Target.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text=source,
        existing_en="Target.\n\n{% include [x](different.md) %}\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "full"
    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_duplicate_current_en_target_is_not_a_unique_boundary(
    current_authority: RuAuthority,
) -> None:
    path = "ydb/docs/ru/a.md"
    identity = _identity(current_authority)
    verified = _verified_unit(
        source_text="Источник.\n",
        target="Repeated.",
        source_path=path,
        identity=identity,
    )

    result = plan_source_coverage(
        source_path=path,
        source_text="Источник.\n",
        existing_en="Repeated.\n\nRepeated.\n",
        authority=current_authority,
        verified_units=(verified,),
        checkpoint_identity=identity,
    )

    assert result.mode == "full"
    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_no_existing_en_requires_translation(current_authority: RuAuthority) -> None:
    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/security/caching-authentication-results.md",
        source_text="# Кеширование\n\nНовая страница.\n",
        existing_en=None,
        authority=current_authority,
    )

    assert result.mode == "full"
    assert result.en_hash is None
    assert result.units
    assert all(unit.action == "translate_required" for unit in result.units)


def test_protected_only_asset_materializes_deterministically(
    current_authority: RuAuthority,
) -> None:
    source = "```mermaid\ngraph LR\n  A --> B\n```\n"
    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/security/_assets/user-token.md",
        source_text=source,
        existing_en=None,
        authority=current_authority,
    )

    assert len(result.units) == 1
    assert result.units[0].action == "materialize_protected"
    assert result.units[0].source == source
    assert result.units[0].target == source


def test_required_50704_fragment_preserves_unrelated_accepted_en_section(
    current_authority: RuAuthority,
) -> None:
    source = (
        "# Глоссарий {#glossary}\n\nВведение.\n\n"
        "## Предыдущий {#previous}\n\nСтарое.\n\n"
        "## Токен пользователя {#user-token}\n\n"
        "Полное определение токена.\n\nДополнительные условия.\n\n"
        "## Следующий {#next}\n\nСледующее.\n"
    )
    existing = (
        "# Glossary {#glossary}\n\nIntroduction.\n\n"
        "## Previous {#previous}\n\nExisting accepted prose.\n\n"
        "## Next {#next}\n\nUnrelated accepted EN repair.\n"
    )

    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/concepts/glossary.md",
        source_text=source,
        existing_en=existing,
        authority=current_authority,
        required_fragments=frozenset({"user-token"}),
    )

    assert result.mode == "units"
    assert result.required_fragments == frozenset({"user-token"})
    assert len(result.units) == 1
    unit = result.units[0]
    assert unit.action == "translate_required"
    assert unit.source == (
        "## Токен пользователя {#user-token}\n\n"
        "Полное определение токена.\n\nДополнительные условия.\n\n"
    )
    insertion = existing.index("## Next {#next}")
    assert unit.en_span == (insertion, insertion)
    assert existing[unit.en_span[1] :] == ("## Next {#next}\n\nUnrelated accepted EN repair.\n")
    assert unit.target is None


def test_explicit_later_ru_only_auth_config_drift_stays_outside_coverage_scope(
    current_authority: RuAuthority,
) -> None:
    source_authority = replace(
        current_authority,
        ru_sha=current_authority.source_head_sha,
        mode=RuAuthorityMode.SOURCE_PRESERVING,
    )
    source_at_h = "Use parameter auth-mode.\n"
    later_ru_only_at_b = "Use parameter `auth-mode`.\n"
    plan = plan_source_coverage(
        source_path="ydb/docs/ru/core/security/auth_config.md",
        source_text=source_at_h,
        existing_en="Use the auth-mode parameter.\n",
        authority=source_authority,
    )
    warning = "fixture attribution: later RU-only PR #52355 H-to-B drift in auth_config"
    content = PairContent(
        pair=DocPair(
            ru_path=plan.source_path,
            en_path="ydb/docs/en/core/security/auth_config.md",
            ru_changed=True,
        ),
        ru_text=source_at_h,
        tip_newer_warnings=(warning,),
        coverage_plan=plan,
    )

    assert content.tip_newer_warnings == (warning,)
    assert content.coverage_plan is not None
    assert content.coverage_plan.source_path.endswith("/auth_config.md")
    assert content.coverage_plan.source_hash == hashlib.sha256(source_at_h.encode()).hexdigest()
    assert content.coverage_plan.required_fragments == frozenset()
    assert tuple(unit.source for unit in content.coverage_plan.units) == (
        "Use parameter auth-mode.",
    )
    assert all(unit.action != "reuse_verified" for unit in content.coverage_plan.units)
    assert all(later_ru_only_at_b not in unit.source for unit in content.coverage_plan.units)


@pytest.mark.parametrize(
    "existing",
    [
        (
            "# Glossary {#glossary}\n\n"
            "## Previous {#previous}\n\nA.\n\n"
            "## Next {#next}\n\nB.\n\n"
            "## Duplicate {#next}\n\nC.\n"
        ),
        ("# Glossary {#glossary}\n\n## Previous {#previous}\n\nA.\n\n## Next {#shifted}\n\nB.\n"),
    ],
)
def test_duplicate_or_shifted_fragment_anchors_fall_back_to_full(
    current_authority: RuAuthority,
    existing: str,
) -> None:
    source = (
        "# Глоссарий {#glossary}\n\n"
        "## Предыдущий {#previous}\n\nA.\n\n"
        "## Токен {#user-token}\n\nDefinition.\n\n"
        "## Следующий {#next}\n\nB.\n"
    )

    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/concepts/glossary.md",
        source_text=source,
        existing_en=existing,
        authority=current_authority,
        required_fragments=frozenset({"user-token"}),
    )

    assert result.mode == "full"
    assert any("ambiguous" in unit.reason for unit in result.units)
    assert not any(unit.action == "reuse_verified" for unit in result.units)


def test_repeated_required_fragment_in_source_falls_back_to_full(
    current_authority: RuAuthority,
) -> None:
    source = "## One {#user-token}\n\nA.\n\n## Two {#user-token}\n\nB.\n"
    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/concepts/glossary.md",
        source_text=source,
        existing_en="## Existing {#other}\n\nC.\n",
        authority=current_authority,
        required_fragments=frozenset({"user-token"}),
    )

    assert result.mode == "full"
    assert any("ambiguous" in unit.reason for unit in result.units)


def test_required_fragment_heading_without_definition_falls_back_to_full(
    current_authority: RuAuthority,
) -> None:
    source = (
        "## Previous {#previous}\n\nA.\n\n## User token {#user-token}\n\n## Next {#next}\n\nB.\n"
    )
    existing = "## Previous {#previous}\n\nA.\n\n## Next {#next}\n\nB.\n"

    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/concepts/glossary.md",
        source_text=source,
        existing_en=existing,
        authority=current_authority,
        required_fragments=frozenset({"user-token"}),
    )

    assert result.mode == "full"
    assert any("definition" in unit.reason for unit in result.units)


def _portable_plan() -> CoveragePlan:
    en = "Existing target.\n"
    return CoveragePlan(
        source_path="ydb/docs/ru/a.md",
        source_hash="1" * 64,
        en_hash=hashlib.sha256(en.encode()).hexdigest(),
        units=(
            CoverageUnit(
                key="2" * 64,
                action="reuse_verified",
                source="Источник.",
                en_span=(0, len("Existing target.")),
                target="Existing target.",
                reason="matching validated source and EN receipt",
            ),
        ),
        required_fragments=frozenset({"user-token"}),
        mode="units",
    )


def test_coverage_types_are_frozen_and_codec_is_canonical() -> None:
    plan = _portable_plan()
    encoded = encode_coverage_plan(plan)

    assert decode_coverage_plan(encoded) == plan
    assert encode_coverage_plan(decode_coverage_plan(encoded)) == encoded
    assert encoded == json.dumps(
        json.loads(encoded),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(FrozenInstanceError):
        plan.mode = "full"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.update(schema=2), "schema"),
        (lambda p: p.update(extra=True), "malformed"),
        (lambda p: p.pop("source_hash"), "malformed"),
        (lambda p: p.update(source_hash="bad"), "source_hash"),
        (lambda p: p["units"][0].update(action="guess_reuse"), "action"),
        (lambda p: p["units"][0].update(key="bad"), "key"),
        (lambda p: p["units"][0].update(en_span=[9, 2]), "span"),
    ],
)
def test_decoder_rejects_noncanonical_or_malformed_evidence(mutate, match: str) -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    mutate(payload)

    with pytest.raises(ValueError, match=match):
        decode_coverage_plan(json.dumps(payload).encode())


def test_decoder_rejects_overlapping_en_spans() -> None:
    plan = _portable_plan()
    payload = json.loads(encode_coverage_plan(plan))
    second = dict(payload["units"][0])
    second.update(key="3" * 64, en_span=[5, 12])
    payload["units"].append(second)

    with pytest.raises(ValueError, match="overlap"):
        decode_coverage_plan(json.dumps(payload).encode())


@pytest.mark.parametrize("offset", [0, len("Existing target.")])
def test_decoder_rejects_insertion_at_replacement_boundary(offset: int) -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    insertion = dict(payload["units"][0])
    insertion.update(
        action="translate_required",
        en_span=[offset, offset],
        key="3" * 64,
        source="Вставка.",
        target=None,
    )
    payload["units"].append(insertion)

    with pytest.raises(ValueError, match="overlap"):
        decode_coverage_plan(json.dumps(payload).encode())


def test_decoder_rejects_multiple_insertions_at_same_offset() -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    payload["units"][0].update(
        action="translate_required",
        en_span=[len("Existing target.\n"), len("Existing target.\n")],
        target=None,
    )
    duplicate = dict(payload["units"][0])
    duplicate["key"] = "3" * 64
    payload["units"].append(duplicate)

    with pytest.raises(ValueError, match="overlap"):
        decode_coverage_plan(json.dumps(payload).encode())


def test_decoder_accepts_nonoverlapping_replacement_and_insertion() -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    insertion = dict(payload["units"][0])
    insertion.update(
        action="translate_required",
        en_span=[len("Existing target.\n"), len("Existing target.\n")],
        key="3" * 64,
        source="Вставка.",
        target=None,
    )
    payload["units"].append(insertion)

    decoded = decode_coverage_plan(json.dumps(payload).encode())

    assert decoded.units[1].en_span == (17, 17)


def test_decoder_requires_en_hash_and_spans_for_units_mode() -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    payload["en_hash"] = None
    payload["units"][0]["en_span"] = None

    with pytest.raises(ValueError, match="en_hash"):
        decode_coverage_plan(json.dumps(payload).encode())


def test_decoder_rejects_edit_spans_in_full_mode() -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    payload["mode"] = "full"

    with pytest.raises(ValueError, match=r"full.*span"):
        decode_coverage_plan(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "source_path",
    [
        "",
        "/absolute.md",
        "../outside.md",
        "a/../outside.md",
        "a/./b.md",
        "a//b.md",
        "a/b.md/",
        r"a\b.md",
        "C:/outside.md",
        ".",
    ],
)
def test_decoder_rejects_noncanonical_or_non_repo_relative_source_path(
    source_path: str,
) -> None:
    payload = json.loads(encode_coverage_plan(_portable_plan()))
    payload["source_path"] = source_path

    with pytest.raises(ValueError, match="source_path"):
        decode_coverage_plan(json.dumps(payload).encode())


def test_decoder_accepts_canonical_repo_relative_source_path() -> None:
    plan = _portable_plan()

    decoded = decode_coverage_plan(encode_coverage_plan(plan))

    assert decoded.source_path == "ydb/docs/ru/a.md"


def test_pair_content_carries_plan_without_changing_pair_planning() -> None:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = _portable_plan()

    content = PairContent(pair=pair, ru_text="Источник.\n", coverage_plan=plan)

    assert content.coverage_plan is plan
