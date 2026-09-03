"""§6.184: low-magnitude EN patch for tiny RU glossary-style additions."""

from __future__ import annotations

# ruff: noqa: RUF001
from textwrap import dedent

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.differential import (
    DifferentialTranslationConfig,
    analyze_ru_diff,
    patch_en_with_added_translations,
    patch_en_with_source_added_autotitle_lines,
    prepare_differential_seed,
    slim_pending_for_low_magnitude_patch,
)

_DIFF_ON = DifferentialTranslationConfig(enabled=True)


def test_pr_50904_autotitle_addition_preserves_unrelated_en_bytes():
    ru_base = dedent(
        """\
        # Концепции администрирования кластеров

        * [{#T}](./maintenance-without-downtime.md)

        * [{#T}](../backup-and-recovery/index.md)
        """
    )
    ru_pr = ru_base.replace(
        "* [{#T}](./maintenance-without-downtime.md)\n",
        "* [{#T}](./maintenance-without-downtime.md)\n* [{#T}](./node-authorization.md)\n",
    )
    en = dedent(
        """\
        # Concepts for Cluster Administration

        * [{#T}](./maintenance-without-downtime.md)

        * [{#T}](../backup-and-recovery.md)
        """
    )

    assert patch_en_with_source_added_autotitle_lines(ru_base, ru_pr, en) == en.replace(
        "* [{#T}](./maintenance-without-downtime.md)\n",
        "* [{#T}](./maintenance-without-downtime.md)\n* [{#T}](./node-authorization.md)\n",
    )


def test_pr_50904_retry_rebuilds_patch_from_clean_en_base():
    """A retry must discard unrelated rewrites left by an earlier run."""
    ru_base = "# Концепции\n\n* [{#T}](./old.md)\n"
    ru_pr = ru_base + "* [{#T}](./node-authorization.md)\n"
    en_base = "# Concepts for Cluster Administration\n\n* [{#T}](./old.md)\n"
    polluted_en = "# Concepts for DevOps engineers\n\n* [{#T}](./old.md)\n"

    result = patch_en_with_source_added_autotitle_lines(ru_base, ru_pr, en_base)

    assert polluted_en not in result
    assert result == en_base + "* [{#T}](./node-authorization.md)\n"


def test_glossary_zero_diff_seeds_most_segments():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    segs = extract_segments(parse_markdown(ru))
    _strategy, seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru,
        en_current_text=en,
        ru_base_text=ru,
        config=_DIFF_ON,
    )
    assert len(seeded) > len(pending)
    assert len(pending) < 100


def test_slim_pending_activates_even_when_pending_equals_changes():
    """Regression #49578: slim==pending must still patch, not reconstruct."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru_base = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    ru_pr = ru_base.replace(
        "### Board {#board}",
        "Подробнее — в разделе [Сервисы](architecture/metadata-services.md).\n\n### Board {#board}",
        1,
    )
    segs = extract_segments(parse_markdown(ru_pr))
    _strategy, _seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru_pr,
        en_current_text=en,
        ru_base_text=ru_base,
        config=_DIFF_ON,
    )
    # Pretend all unseeded segments were already filtered to the change set.
    analysis = analyze_ru_diff(ru_base, ru_pr)
    change_ids = analysis.added_segment_ids | analysis.modified_segment_ids
    pending_only_changes = [s for s in pending if s.id in change_ids]
    assert pending_only_changes
    slim = slim_pending_for_low_magnitude_patch(
        pending_only_changes, ru_base_text=ru_base, ru_pr_text=ru_pr
    )
    assert slim is not None
    slim_pending, slim_analysis = slim
    assert slim_analysis.change_magnitude < 0.05
    assert len(slim_pending) == len(pending_only_changes)


def test_slim_pending_keeps_only_added_for_tiny_glossary_edit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru_base = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    # Mimic #45667: three new cross-links under StateStorage / Board / SchemeBoard.
    ru_pr = ru_base.replace(
        "### Board {#board}",
        "Подробнее — в разделе [Сервисы](architecture/metadata-services.md).\n\n### Board {#board}",
        1,
    )
    segs = extract_segments(parse_markdown(ru_pr))
    _strategy, _seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru_pr,
        en_current_text=en,
        ru_base_text=ru_base,
        config=_DIFF_ON,
    )
    slim = slim_pending_for_low_magnitude_patch(pending, ru_base_text=ru_base, ru_pr_text=ru_pr)
    assert slim is not None
    slim_pending, analysis = slim
    assert analysis.change_magnitude < 0.05
    assert 1 <= len(slim_pending) <= 5
    assert len(slim_pending) < len(pending)


def test_patch_en_replaces_plain_crossref_with_linked():
    """Main EN already has plain 'see … section'; RU adds a markdown link."""
    en = dedent(
        """\
        ### State Storage {#state-storage}

        **State Storage** is a distributed service.

        For more details about the StateStorage architecture and related \
subsystems, see the Metadata distribution services section.

        ### Board {#board}

        **Board** stores key-value metadata.
        """
    )
    ru = dedent(
        """\
        ### Хранилище состояния {#state-storage}

        **Хранилище состояния** — сервис.

        Подробнее об устройстве StateStorage — в разделе \
[Сервисы распространения метаданных](architecture/metadata-services.md).

        ### Board {#board}

        **Board** хранит метаданные.
        """
    )
    segs = extract_segments(parse_markdown(ru))
    analysis = analyze_ru_diff(
        dedent(
            """\
            ### Хранилище состояния {#state-storage}

            **Хранилище состояния** — сервис.

            ### Board {#board}

            **Board** хранит метаданные.
            """
        ),
        ru,
    )
    change_ids = analysis.added_segment_ids | analysis.modified_segment_ids
    added_id = next(iter(change_ids))
    translated = (
        "For more details about the StateStorage architecture and related "
        "subsystems, see the section "
        "⟦L1⟧Metadata distribution services⟦L1⟧."
    )
    out = patch_en_with_added_translations(
        en,
        pr_segments=segs,
        translations={added_id: translated},
        added_segment_ids=analysis.added_segment_ids,
        modified_segment_ids=analysis.modified_segment_ids,
    )
    assert out.count("Metadata distribution services") == 1
    assert "[Metadata distribution services](architecture/metadata-services.md)" in out
    assert out.index("State Storage") < out.index("Metadata distribution")
    assert out.index("Metadata distribution") < out.index("### Board")


def test_patch_en_inserts_after_anchor():
    en = dedent(
        """\
        ### State Storage {#state-storage}

        **State Storage** is a distributed service.

        ### Board {#board}

        **Board** stores key-value metadata.
        """
    )
    ru = dedent(
        """\
        ### Хранилище состояния {#state-storage}

        **Хранилище состояния** — сервис.

        Подробнее — в разделе [Сервисы](architecture/metadata-services.md).

        ### Board {#board}

        **Board** хранит метаданные.
        """
    )
    segs = extract_segments(parse_markdown(ru))
    analysis = analyze_ru_diff(
        dedent(
            """\
            ### Хранилище состояния {#state-storage}

            **Хранилище состояния** — сервис.

            ### Board {#board}

            **Board** хранит метаданные.
            """
        ),
        ru,
    )
    assert analysis.added_segment_ids or analysis.modified_segment_ids
    change_ids = analysis.added_segment_ids | analysis.modified_segment_ids
    added_id = next(iter(change_ids))
    translations = {
        added_id: (
            "For more details, see the section "
            "⟦L1⟧Metadata services⟦L1⟧."
        )
    }
    out = patch_en_with_added_translations(
        en,
        pr_segments=segs,
        translations=translations,
        added_segment_ids=analysis.added_segment_ids,
        modified_segment_ids=analysis.modified_segment_ids,
    )
    assert "Metadata services" in out
    assert "[Metadata services](architecture/metadata-services.md)" in out
    assert out.index("Metadata services") < out.index("### Board")


def test_pr51853_low_magnitude_patch_renders_link_boundaries():
    cases = [
        (
            "# Client certificate authorization {#client-certificate-authorization}\n\n"
            "Опцию рекомендуется использовать при "
            "[регистрации динамических узлов]"
            "(../../devops/concepts/node-authorization.md"
            "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov).\n",
            "The option is advisable to use when registering dynamic nodes.",
            "The option is advisable to use when "
            "⟦L1⟧registering dynamic nodes⟦L1⟧.",
            [
                "[registering dynamic nodes]"
                "(../../devops/concepts/node-authorization.md"
                "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov)"
            ],
        ),
        (
            "# Monitoring {#monitoring}\n\n"
            "Секция задаёт параметры "
            "[YDB Monitoring](../ydb-ui/ydb-monitoring.md).\n",
            "The section sets the parameters for YDB Monitoring.",
            "The section sets the parameters for "
            "⟦L1⟧YDB Monitoring⟦L1⟧.",
            ["[YDB Monitoring](../ydb-ui/ydb-monitoring.md)"],
        ),
        (
            "# Authentication {#authentication}\n\n"
            "- **gRPC** и **YDB Monitoring** — можно включить запрос сертификата "
            "клиента для аутентификации устройств, а также отдельно включить его "
            "обязательную проверку (недоверенный сертификат отклоняется всегда). "
            "Настройка gRPC описана в секциях "
            "[grpc_config](../reference/configuration/tls.md#grpc) и "
            "[client_certificate_authorization]"
            "(../reference/configuration/client_certificate_authorization.md), а "
            "подключение клиента — в разделе "
            "[Параметры TLS-соединения](../reference/ydb-cli/connect.md#tls); "
            "настройка мониторинга YDB Monitoring описана в секции "
            "[monitoring_config]"
            "(../reference/configuration/monitoring_config.md#tls).\n",
            "Existing authentication overview.",
            "- **gRPC** and **YDB Monitoring** can request a client certificate and "
            "require its validation. gRPC is configured in the "
            "⟦L1⟧grpc_config⟦L1⟧ and "
            "⟦L2⟧client_certificate_authorization⟦L2⟧ sections, while "
            "the client connection is covered in "
            "⟦L3⟧TLS connection parameters⟦L3⟧; YDB Monitoring is "
            "configured in ⟦L4⟧monitoring_config⟦L4⟧.",
            [
                "[grpc_config](../reference/configuration/tls.md#grpc)",
                "[client_certificate_authorization]"
                "(../reference/configuration/client_certificate_authorization.md)",
                "[TLS connection parameters](../reference/ydb-cli/connect.md#tls)",
                "[monitoring_config]"
                "(../reference/configuration/monitoring_config.md#tls)",
            ],
        ),
    ]

    for ru, existing_paragraph, translated, expected_links in cases:
        segments = extract_segments(parse_markdown(ru))
        segment = segments[-1]
        prefix = "UNCHANGED PREFIX\n\n"
        suffix = "\n\n# Unrelated {#unrelated}\n\nUNCHANGED SUFFIX\n"
        en = prefix + ru.splitlines()[0] + "\n\n" + existing_paragraph + suffix
        out = patch_en_with_added_translations(
            en,
            pr_segments=segments,
            translations={segment.id: translated},
            added_segment_ids=frozenset({segment.id}),
        )

        for link in expected_links:
            assert out.count(link) == 1
        assert "⟦L" not in out
        assert "%E2%9F%A6" not in out
        assert out.startswith(prefix)
        assert "# Unrelated {#unrelated}" in out
        assert out.endswith("UNCHANGED SUFFIX\n")
