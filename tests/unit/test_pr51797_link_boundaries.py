from __future__ import annotations

# ruff: noqa: RUF001
from urllib.parse import quote

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.translation.translator import validate_segment_translation
from ydbdoc_review.validation.markers import extract_placeholders
from ydbdoc_review.validation.placeholder_repair import repair_translation_placeholders


def test_pr51797_node_authorization_wrapper_and_href_survive_translated_label():
    href = (
        "../../devops/concepts/node-authorization.md"
        "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    )
    doc = parse_markdown(f"См. [регистрации динамических узлов]({href}).\n")
    segments = extract_segments(doc)
    segment = segments[0]
    assert "⟦L1⟧регистрации динамических узлов⟦L1⟧" in segment.text
    assert href not in segment.text
    assert "[регистрации динамических узлов](" not in segment.text

    translated = "See ⟦L1⟧registering dynamic nodes⟦L1⟧."
    output = render_markdown(
        reinsert_segments(doc, segments, {segment.id: translated})
    )
    assert f"[registering dynamic nodes]({href})" in output
    assert "⟦" not in output


def test_pr51797_monitoring_wrapper_and_href_survive_translated_label():
    href = "../ydb-ui/ydb-monitoring.md"
    doc = parse_markdown(f"См. [YDB Monitoring]({href}).\n")
    segments = extract_segments(doc)
    segment = segments[0]
    assert "⟦L1⟧YDB Monitoring⟦L1⟧" in segment.text
    assert href not in segment.text
    assert "[YDB Monitoring](" not in segment.text

    translated = "See ⟦L1⟧YDB Monitoring⟦L1⟧."
    output = render_markdown(
        reinsert_segments(doc, segments, {segment.id: translated})
    )
    assert f"[YDB Monitoring]({href})" in output
    assert "⟦" not in output


def test_pr51797_four_percent_encoded_boundaries_restore_translated_labels():
    hrefs = [
        "../reference/configuration/tls.md#grpc",
        "../reference/configuration/client_certificate_authorization.md",
        "../reference/ydb-cli/connect.md#tls",
        "../reference/configuration/monitoring_config.md#tls",
    ]
    source = (
        "- **gRPC** и **YDB Monitoring** — можно включить запрос сертификата клиента "
        "для аутентификации устройств, а также отдельно включить его обязательную "
        "проверку (недоверенный сертификат отклоняется всегда). Настройка gRPC "
        f"описана в секциях [grpc_config]({hrefs[0]}) и "
        f"[client_certificate_authorization]({hrefs[1]}), а подключение клиента — "
        f"в разделе [Параметры TLS-соединения]({hrefs[2]}); настройка мониторинга "
        f"YDB Monitoring описана в секции [monitoring_config]({hrefs[3]}).\n"
    )
    doc = parse_markdown(source)
    segments = extract_segments(doc)
    segment = segments[0]
    boundaries = [marker[1:-1] for marker in extract_placeholders(segment.text) if marker[1] == "L"]
    assert boundaries == ["L1", "L1", "L2", "L2", "L3", "L3", "L4", "L4"]
    assert "⟦U" not in segment.text
    assert all(href not in segment.text for href in hrefs)
    assert "[grpc_config](" not in segment.text

    translated = (
        "- **gRPC** and **YDB Monitoring** can request a client certificate and "
        "require its validation. gRPC is configured in the "
        "⟦L1⟧grpc_config⟦L1⟧ and "
        "⟦L2⟧client_certificate_authorization⟦L2⟧ sections, while the client "
        "connection is covered in ⟦L3⟧TLS connection parameters⟦L3⟧; "
        "YDB Monitoring is configured in ⟦L4⟧monitoring_config⟦L4⟧."
    )
    for index in range(1, 5):
        marker = f"⟦L{index}⟧"
        translated = translated.replace(marker, quote(marker, safe=""))
    repaired = repair_translation_placeholders(segment, translated)
    validate_segment_translation(segment, repaired)
    output = render_markdown(
        reinsert_segments(doc, segments, {segment.id: repaired})
    )

    labels = [
        "grpc_config",
        "client_certificate_authorization",
        "TLS connection parameters",
        "monitoring_config",
    ]
    for label, href in zip(labels, hrefs, strict=True):
        assert output.count(f"[{label}]({href})") == 1
    assert "%E2%9F%A6" not in output
    assert "⟦" not in output
    assert "Параметры TLS-соединения" not in output
