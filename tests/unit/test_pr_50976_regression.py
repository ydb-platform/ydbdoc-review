"""Decision-level replay for ydb-platform/ydb#50976 (source PR #40385)."""

import pytest

from ydbdoc_review.pipeline.analyze import PairPlan, PairProvenance
from ydbdoc_review.pipeline.completeness import (
    CompletenessState,
    evaluate_completeness_states,
)
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.href_parity import is_href_only_change
from ydbdoc_review.validation.structural_delta import historical_operations_survive

ROOT = "ydb/docs/en/core/"
# Read-only GitHub provenance captured 2026-08-26.
SOURCE_PR = 40385
SOURCE_BASE = "391b7a3c3936936af46b0bdbb570cf9c926c3376"
SOURCE_HEAD = "99845ebe3ab4ebbfd7521cfd8acec4c905433adb"
TRANSLATION_PR = 50976
TRANSLATION_BASE = "8dfb3ec066b5d84befa777a53379fb26dd816c33"
TRANSLATION_HEAD = "244454398dbf3c50c0ab9310284190bd25d9a877"
EXPECTED = (
    "reference/configuration/client_certificate_authorization.md",
    "reference/configuration/monitoring_config.md",
    "reference/configuration/tls.md",
    "security/authentication.md",
    "security/index.md",
)


def test_pr_50976_exact_five_file_scope():
    assert (SOURCE_PR, TRANSLATION_PR) == (40385, 50976)
    assert all(
        len(oid) == 40 for oid in (SOURCE_BASE, SOURCE_HEAD, TRANSLATION_BASE, TRANSLATION_HEAD)
    )
    assert len(EXPECTED) == 5
    assert len(set(EXPECTED)) == 5


def test_pr_50976_client_inline_code_and_link_delta_is_not_href_only():
    before = "[Узлы](node.md#auth): `Имя=Значение`.\n"
    after = "[Узлы](node.md#auth): `Name=Value`.\n"

    assert not is_href_only_change(before, after)


@pytest.mark.parametrize(
    "relative_path,operation",
    [
        ("reference/configuration/monitoring_config.md", "Новая секция мониторинга.\n"),
        ("reference/configuration/tls.md", "Ссылка на monitoring_config#tls.\n"),
        ("security/authentication.md", "Обновлённая операция аутентификации.\n"),
        ("security/index.md", "Обновлённый индекс безопасности.\n"),
    ],
)
def test_pr_50976_surviving_operations_are_not_called_superseded(
    relative_path: str, operation: str
):
    proof = historical_operations_survive("До.\n", operation, operation + "Позже.\n")

    assert relative_path in EXPECTED
    assert proof.survives


def test_pr_50976_all_required_paths_end_in_satisfied_states():
    runs = []
    for relative_path in EXPECTED:
        en_path = ROOT + relative_path
        ru_path = en_path.replace("/en/", "/ru/")
        pair = DocPair(ru_path, en_path, ru_changed=True)
        plan = PairPlan(
            pair,
            "translate_to_en",
            ru_path,
            en_path,
            "ru",
            "en",
            provenance=PairProvenance.CURRENT_PAIR,
        )
        runs.append(
            PairRunResult(
                plan,
                skipped=True,
                historical_disposition="already_translated",
            )
        )

    states = evaluate_completeness_states(PRTranslationResult(pair_results=runs))
    assert set(states) == {ROOT + path for path in EXPECTED}
    assert set(states.values()) == {CompletenessState.EXISTING_SATISFIED}
