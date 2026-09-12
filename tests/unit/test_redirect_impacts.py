from pathlib import Path

from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    redirect_public_path_to_repo_md,
    redirect_source_repo_md_paths,
    should_skip_redirect_tombstone_en,
)
from ydbdoc_review.validation.redirect_impacts import (
    added_redirects,
    mirror_redirects_to_en,
    retarget_redirect_inbound_links,
)


def test_redirect_source_repo_md_paths_maps_public_from():
    yaml_text = (
        "ru:\n"
        "  - from: /maintenance/manual/dynamic-config.md\n"
        "    to: /devops/configuration-management/configuration-v1/dynamic-config.md\n"
        "\n"
        "en:\n"
        "  - from: /maintenance/manual/dynamic-config.md\n"
        "    to: /devops/configuration-management/configuration-v1/dynamic-config.md\n"
    )
    paths = redirect_source_repo_md_paths(yaml_text, locale="en")
    assert paths == {
        "ydb/docs/en/core/maintenance/manual/dynamic-config.md",
    }
    assert redirect_public_path_to_repo_md(
        "/maintenance/manual/dynamic-config.md", locale="ru"
    ) == "ydb/docs/ru/core/maintenance/manual/dynamic-config.md"


def test_should_skip_redirect_tombstone_when_not_in_toc():
    en = "ydb/docs/en/core/maintenance/manual/dynamic-config.md"
    sources = frozenset({en})
    # Pending-seeded "reachable" sets must not defeat the skip (#51703).
    assert should_skip_redirect_tombstone_en(
        en, redirect_source_en_paths=sources, en_toc_reachable=frozenset({en})
    )
    assert should_skip_redirect_tombstone_en(
        en, redirect_source_en_paths=sources, en_toc_reachable=frozenset()
    )
    assert not should_skip_redirect_tombstone_en(
        "ydb/docs/en/core/other.md",
        redirect_source_en_paths=sources,
        en_toc_reachable=frozenset(),
    )


def test_added_redirects_and_inbound_retarget(tmp_path: Path):
    base = "ru:\n  - from: /old.md\n    to: /older.md\n"
    current = base + "  - from: /devops/manual/node.md\n    to: /devops/concepts/node.md\n"
    mappings = added_redirects(base, current)
    page = tmp_path / "ydb/docs/ru/core/security/authentication.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "See [nodes](../devops/manual/node.md#mode) and [other](other.md).\n",
        encoding="utf-8",
    )

    changed = retarget_redirect_inbound_links(str(tmp_path), mappings)

    assert changed == ["ydb/docs/ru/core/security/authentication.md"]
    assert page.read_text(encoding="utf-8") == (
        "See [nodes](../devops/concepts/node.md#mode) and [other](other.md).\n"
    )


def test_mirror_redirects_to_en_is_idempotent():
    text = "ru:\n  - from: /old.md\n    to: /new.md\n\nen:\n"
    mappings = {"/old.md": "/new.md"}

    mirrored = mirror_redirects_to_en(text, mappings)

    assert mirrored.endswith("en:\n  - from: /old.md\n    to: /new.md\n")
    assert mirror_redirects_to_en(mirrored, mappings) == mirrored


def test_pr_50904_retarget_is_source_scoped_and_localizes_en_fragment(tmp_path: Path):
    root = tmp_path / "ydb/docs"
    ru_target = root / "ru/core/devops/concepts/node-authorization.md"
    en_target = root / "en/core/devops/concepts/node-authorization.md"
    ru_target.parent.mkdir(parents=True)
    en_target.parent.mkdir(parents=True)
    ru_target.write_text(
        "## Включение режима аутентификации и авторизации узлов\n",
        encoding="utf-8",
    )
    en_target.write_text(
        "## Enabling node authentication and authorization\n",
        encoding="utf-8",
    )
    scoped = root / "en/core/reference/configuration/client_certificate_authorization.md"
    unrelated = root / "en/core/security/authentication.md"
    scoped.parent.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    old = "../../devops/deployment-options/manual/node-authorization.md"
    fragment = "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    scoped.write_text(f"See [nodes]({old}{fragment}).\n", encoding="utf-8")
    unrelated.write_text(
        "See [nodes](../devops/deployment-options/manual/node-authorization.md).\n",
        encoding="utf-8",
    )

    changed = retarget_redirect_inbound_links(
        str(tmp_path),
        {
            "/devops/deployment-options/manual/node-authorization.md": "/devops/concepts/node-authorization.md"
        },
        allowed_paths=frozenset({scoped.relative_to(tmp_path).as_posix()}),
    )

    assert changed == [scoped.relative_to(tmp_path).as_posix()]
    assert (
        "node-authorization.md#enabling-node-authentication-and-authorization"
        in scoped.read_text(encoding="utf-8")
    )
    assert "deployment-options/manual/node-authorization.md" in unrelated.read_text(
        encoding="utf-8"
    )
