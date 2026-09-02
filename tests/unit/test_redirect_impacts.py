import subprocess
from pathlib import Path

from ydbdoc_review.navigation.redirects import (
    redirect_public_path_to_repo_md,
    redirect_source_repo_md_paths,
    should_skip_redirect_tombstone_en,
)
from ydbdoc_review.validation import redirect_impacts
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


def test_retarget_redirect_inbound_links_pinned_publication_sha_ignores_worktree_and_moving_ref(
    tmp_path: Path, monkeypatch
):
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, text=True, capture_output=True
        ).stdout.strip()

    def write(path: str, text: str) -> None:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    source = "ydb/docs/en/core/reference/configuration/source.md"
    ru_target = "ydb/docs/ru/core/devops/concepts/node.md"
    en_target = "ydb/docs/en/core/devops/concepts/node.md"
    missing_source = "ydb/docs/en/core/reference/configuration/missing-source.md"
    unrelated = "ydb/docs/en/core/security/unrelated.md"
    old = "../../devops/manual/node.md"
    fragment = "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    write(
        source,
        f"publication source [node]({old}{fragment}) "
        "[missing](../../devops/manual/missing.md#old-fragment)\n",
    )
    write(ru_target, "## Включение режима аутентификации и авторизации узлов\n")
    write(en_target, "## Publication fragment\n")
    write(unrelated, "publication unrelated\n")
    git("add", ".")
    git("commit", "-m", "publication")
    publication_tree_sha = git("rev-parse", "HEAD")

    git("checkout", "-b", "moving-ref")
    write(source, "moving-ref source sentinel\n")
    write(ru_target, "## Moving ref fragment\n")
    write(en_target, "## Moving ref fragment\n")
    write(missing_source, "moving-ref missing source sentinel\n")
    write("ydb/docs/ru/core/devops/concepts/missing.md", "## moving target\n")
    write("ydb/docs/en/core/devops/concepts/missing.md", "## moving target\n")
    write(unrelated, "moving-ref unrelated sentinel\n")
    git("add", ".")
    git("commit", "-m", "moving ref")
    moving_ref_sha = git("rev-parse", "HEAD")

    write(source, "worktree source sentinel\n")
    write(ru_target, "## Worktree fragment\n")
    write(en_target, "## Worktree fragment\n")
    write(missing_source, "worktree missing source sentinel\n")
    write("ydb/docs/ru/core/devops/concepts/missing.md", "## worktree target\n")
    write("ydb/docs/en/core/devops/concepts/missing.md", "## worktree target\n")
    write(unrelated, "worktree unrelated sentinel\n")

    calls: list[tuple[str, str]] = []
    original_read = redirect_impacts.read_text_at_ref

    def pinned_read(repo_path: str, ref: str, path: str) -> str | None:
        calls.append((ref, path))
        return original_read(repo_path, ref, path)

    monkeypatch.setattr(redirect_impacts, "read_text_at_ref", pinned_read)
    changed = retarget_redirect_inbound_links(
        str(tmp_path),
        {
            "/devops/manual/node.md": "/devops/concepts/node.md",
            "/devops/manual/missing.md": "/devops/concepts/missing.md",
        },
        allowed_paths=frozenset({source, missing_source}),
        publication_ref=publication_tree_sha,
    )

    assert changed == [source]
    updated = (tmp_path / source).read_text(encoding="utf-8")
    assert "publication source" in updated
    assert "../../devops/concepts/node.md#publication-fragment" in updated
    assert "../../devops/concepts/missing.md#old-fragment" in updated
    assert "moving-ref" not in updated
    assert "worktree" not in updated
    assert all(ref == publication_tree_sha for ref, _path in calls)
    assert moving_ref_sha not in {ref for ref, _path in calls}
    assert {path for _ref, path in calls} == {source, ru_target, en_target, missing_source,
                                               "ydb/docs/ru/core/devops/concepts/missing.md",
                                               "ydb/docs/en/core/devops/concepts/missing.md"}
    assert unrelated not in {path for _ref, path in calls}
    assert (tmp_path / unrelated).read_text(encoding="utf-8") == "worktree unrelated sentinel\n"
