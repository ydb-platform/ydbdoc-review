import subprocess

import pytest

from ydbdoc_review.build import automatic_ok, build_candidate
from ydbdoc_review.links import Candidate, check_links, confirmed_english_url

ROOT = "ydb/docs/"


@pytest.fixture
def tree(tmp_path):
    def make(markdown, extras=None):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            return (
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
                .stdout.decode()
                .strip()
            )

        git("init")
        git("config", "user.name", "Independent tester")
        git("config", "user.email", "tester@example.invalid")
        git("config", "commit.gpgsign", "false")
        files = {
            "toc.yaml": "title: Test\nitems:\n  - name: Page\n    href: en/a.md\n",
            "en/a.md": markdown,
        }
        files.update(extras or {})
        for name, data in files.items():
            path = repo / ROOT / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        git("add", ".")
        git("commit", "-m", "fixture")
        return Candidate.open(repo, git("rev-parse", "HEAD"))

    return make


def test_encoded_external_russian_page_is_not_automatically_green(tree):
    candidate = tree("# Page\n\n[Russian](https://ydb.tech/docs/%72u/core/foo)\n")
    build = build_candidate(candidate)
    assert build.ok_for(candidate.sha), build.log
    links = check_links(candidate, build=build)
    print(
        "BUILD",
        build.status,
        build.candidate_sha,
        "LINKS",
        links,
        "GATE",
        automatic_ok(candidate.sha, links, build),
    )
    assert not automatic_ok(candidate.sha, links, build), (
        "Percent-encoded Russian URL was given successful automatic gates"
    )


def test_encoded_external_russian_url_requires_confirmation(tree):
    candidate = tree("# Page")
    with pytest.raises(ValueError, match=r"[Uu]nconfirmed"):
        confirmed_english_url(candidate, ROOT + "en/a.md", "https://ydb.tech/docs/%72u/core/foo")


def test_existing_svg_fragment_accepts_real_asset_id(tree):
    candidate = tree(
        "# Page\n\n![icon](icons.svg#ok)\n",
        {
            "en/icons.svg": '<svg xmlns="http://www.w3.org/2000/svg"><view id="ok" viewBox="0 0 10 10"/><rect width="10" height="10"/></svg>'
        },
    )
    build = build_candidate(candidate)
    assert build.ok_for(candidate.sha), build.log
    links = check_links(candidate, build=build)
    print("BUILD", build.status, "LINKS", links)
    assert links.ok, (
        "Existing SVG view fragment is rejected despite exact asset bytes and successful build"
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://ydb.tech/docs/%72u/core/foo?lang=en#ok",
        "https://ydb.tech/docs/r%75/core/foo",
        "//ydb.tech/docs/%72%75/core/foo",
        "https://ydb.tech/docs/ru",
    ],
)
def test_external_russian_path_segments_require_confirmation(tree, href):
    from ydbdoc_review.build import BuildResult

    candidate = tree(f"# Page\n\n[Russian]({href})\n", {"ru/a.md": f"[Russian]({href})"})
    build = BuildResult(candidate.sha, "success", returncode=0)
    links = check_links(candidate, build=build)
    assert not links.ok
    assert any(href in issue.problem and "replacement" in issue.problem for issue in links.issues)
    with pytest.raises(ValueError, match="Unconfirmed"):
        confirmed_english_url(candidate, ROOT + "en/a.md", href)
    assert confirmed_english_url(candidate, ROOT + "ru/a.md", href) == href
    assert check_links(candidate, paths=[ROOT + "ru/a.md"], build=build).ok


@pytest.mark.parametrize(
    "href",
    [
        "https://ydb.tech/docs/en/foo?next=/ru/foo#%72u",
        "https://ru.example/docs/ruby/foo",
    ],
)
def test_non_russian_external_path_is_unchanged(tree, href):
    candidate = tree(f"# Page\n\n[English]({href})\n")
    assert check_links(candidate).ok
    assert confirmed_english_url(candidate, ROOT + "en/a.md", href) == href


@pytest.mark.parametrize(
    ("svg", "fragment", "valid"),
    [
        ('<svg xmlns="http://www.w3.org/2000/svg"><view id="ok"/></svg>', "%6Fk", True),
        ('<svg><g id="ok"/></svg>', "ok", True),
        ('<svg><!-- <view id="ok"/> --><view id="other"/></svg>', "ok", False),
        ('<svg><view id="ok"/></svg>', "missing", False),
        ('<svg><view id="ok"></svg>', "ok", False),
    ],
)
def test_svg_fragments_use_only_exact_candidate_xml_ids(tree, svg, fragment, valid):
    from ydbdoc_review.build import BuildResult

    candidate = tree(f"# Page\n\n![icon](icons.svg#{fragment})\n", {"en/icons.svg": svg})
    # Dirty bytes must neither create nor remove the candidate's IDs.
    (candidate.repo / ROOT / "en/icons.svg").write_text('<svg><view id="missing"/></svg>')
    build = BuildResult(
        candidate.sha, "success", returncode=0, anchors={ROOT + "en/icons.svg": {"ok", "missing"}}
    )
    links = check_links(candidate, build=build)
    assert links.ok is valid
    if not valid:
        assert any(
            "anchor" in issue.problem or "Invalid SVG" in issue.problem for issue in links.issues
        )
    for unavailable in (
        None,
        BuildResult("0" * 40, "success", returncode=0),
        BuildResult(candidate.sha, "pending"),
        BuildResult(candidate.sha, "failure"),
    ):
        assert not check_links(candidate, build=unavailable).ok


def test_russian_page_may_keep_encoded_english_external_link(tree):
    href = "https://ydb.tech/docs/%65n/core/foo?x=1#ok"
    candidate = tree("# Page", {"ru/a.md": f"[English]({href})"})
    assert check_links(candidate).ok
    assert confirmed_english_url(candidate, ROOT + "ru/a.md", href) == href


def test_english_svg_replacement_requires_actual_candidate_id(tree):
    from ydbdoc_review.build import BuildResult

    candidate = tree("# Page", {"en/icons.svg": '<svg><view id="ok"/></svg>'})
    build = BuildResult(candidate.sha, "success", returncode=0)
    assert (
        confirmed_english_url(candidate, ROOT + "en/a.md", "/ru/icons.svg?x=1#%6Fk", build=build)
        == "/en/icons.svg?x=1#%6Fk"
    )
    with pytest.raises(ValueError, match="anchor"):
        confirmed_english_url(candidate, ROOT + "en/a.md", "/ru/icons.svg#missing", build=build)
