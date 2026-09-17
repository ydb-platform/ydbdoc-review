"""One path resolver for planning and exact-commit validation.

Absolute URLs are site-root relative, never implicitly locale/core relative.
Preparation may propose copies/replacements; final validation never overlays or
falls back to an older tree. Generated anchors come from the actual YFM build.
"""
from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.etree import ElementTree

from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import Issue, Location


def safe_path(path: str) -> str:
    if not path or path.startswith('/') or '\\' in path or '\x00' in path:
        raise ValueError(f'Invalid repository path: {path!r}')
    normalized = posixpath.normpath(path)
    if normalized == '..' or normalized.startswith('../'):
        raise ValueError(f'Path escapes repository: {path}')
    return normalized


@dataclass(frozen=True)
class Candidate:
    repo: Path
    sha: str
    # path -> (Git mode, blob oid); captured once, immutable and SHA-addressed.
    entries: Mapping[str, tuple[str, str]]

    @classmethod
    def open(cls, repo: str | Path, sha: str) -> Candidate:
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha):
            raise ValueError('A full immutable commit SHA is required')
        repo = Path(repo).resolve()
        actual = _git(repo, 'rev-parse', '--verify', sha + '^{commit}').decode().strip()
        if actual != sha:
            raise ValueError('Candidate is not the requested commit')
        entries = {}
        for record in _git(repo, 'ls-tree', '-rz', sha).split(b'\0'):
            if not record:
                continue
            metadata, raw_path = record.split(b'\t', 1)
            mode, kind, oid = metadata.decode().split()
            path = safe_path(raw_path.decode('utf-8'))
            entries[path] = (mode if kind == 'blob' else kind, oid)
        return cls(repo, sha, MappingProxyType(entries))

    def read(self, path: str) -> bytes | None:
        entry = self.entries.get(safe_path(path))
        if entry is None:
            return None
        mode, oid = entry
        if mode not in {'100644', '100755'}:
            raise ValueError(f'Unsupported Git entry {mode}: {path}')
        return _git(self.repo, 'cat-file', 'blob', oid)

    def text(self, path: str) -> str | None:
        data = self.read(path)
        return None if data is None else data.decode('utf-8')


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout


@dataclass(frozen=True)
class Reference:
    href: str
    kind: str  # link, asset, include
    location: Location | None = None


class HtmlReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[Reference] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('name'):
            self.ids.add(attrs['name'])
        for attr in ('src', 'href', 'poster'):
            if attrs.get(attr):
                self.refs.append(Reference(attrs[attr], 'html_link' if tag == 'a' else 'asset'))


def references(text: str) -> tuple[Reference, ...]:
    result = []
    lines = text.splitlines(keepends=True)
    pending = [(token, None) for token in reversed(create_parser().parse(text))]
    while pending:
        token, location = pending.pop()
        if token.map:
            start, end = token.map
            quote = ''.join(lines[start:end])
            if quote.strip():
                location = Location(start + 1, end, quote)
        if token.type == 'image':
            result.append(Reference(token.attrGet('src') or '', 'asset', location))
            continue
        pending.extend((child, location) for child in reversed(token.children or []))
        if token.type == 'link_open':
            result.append(Reference(token.attrGet('href') or '', 'link', location))
        elif token.type == 'yfm_include':
            result.append(Reference(token.meta['path'], 'include', location))
        elif token.type in {'html_inline', 'html_block'}:
            parser = HtmlReferences()
            parser.feed(token.content)
            result.extend(Reference(ref.href, ref.kind, location) for ref in parser.refs)
    return tuple(result)


@dataclass(frozen=True)
class Target:
    path: str
    fragment: str


def resolve(path: str, href: str, *, docs_root: str = 'ydb/docs') -> Target | None:
    """Resolve local URL lexically; None denotes an external URL.

    The YFM CLI preserves /foo literally at the site root. There is no
    /foo -> <locale>/core/foo or repository-prefix heuristic.
    """
    root = safe_path(docs_root).rstrip('/')
    url = urlsplit(href)
    if url.scheme or url.netloc:
        return None
    decoded = unquote(url.path)
    if decoded.startswith('/'):
        target = root + '/' + decoded.lstrip('/')
    elif decoded:
        target = posixpath.join(posixpath.dirname(safe_path(path)), decoded)
    else:
        target = safe_path(path)
    target = safe_path(target)
    if not target.startswith(root + '/'):
        raise ValueError(f'URL escapes documentation root: {href}')
    # Public HTML URLs refer to the source page, not a baseline generated file.
    if target.endswith('.html'):
        target = target[:-5] + '.md'
    elif decoded.endswith('/'):
        target += '/index.yaml'
    return Target(target, unquote(url.fragment))


def locale(path: str, docs_root: str = 'ydb/docs') -> str | None:
    prefix = docs_root.rstrip('/') + '/'
    if path.startswith(prefix):
        value = path[len(prefix):].split('/')[0]
        if value in {'ru', 'en'}:
            return value
    return None


def mirror(path: str, docs_root: str = 'ydb/docs') -> str:
    lang = locale(path, docs_root)
    if lang is None:
        return path
    prefix = docs_root.rstrip('/') + '/'
    return prefix + ('en' if lang == 'ru' else 'ru') + path[len(prefix) + 2:]


@dataclass(frozen=True)
class LinkResult:
    candidate_sha: str
    issues: tuple[Issue, ...]
    complete: bool

    @property
    def ok(self) -> bool:
        return self.complete and not any(i.severity == 'error' for i in self.issues)


def _russian_url(href: str) -> bool:
    return 'ru' in unquote(urlsplit(href).path).split('/')


def _anchor_ids(path: str, data: bytes, build) -> Iterable[str]:
    if path.lower().endswith('.svg'):
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError as error:
            raise ValueError(f'Invalid SVG: {path}: {error}') from error
        return {element.attrib['id'] for element in root.iter() if 'id' in element.attrib}
    return build.anchors.get(path, frozenset())


def check_links(candidate: Candidate, *, paths: Iterable[str] | None = None,
                docs_root: str = 'ydb/docs', build=None) -> LinkResult:
    """Check pages and recursively included fragments on this candidate only.

    Pass BuildResult to verify page fragments against emitted IDs (including
    includes, duplicate headings and YFM auto slugs). SVG IDs come from exact
    candidate XML bytes; both require a successful build of this SHA.
    """
    issues = []
    complete = True
    pages = list(paths) if paths is not None else [
        p for p in candidate.entries if p.startswith(docs_root.rstrip('/') + '/') and p.endswith('.md')]
    if paths is None:
        # Fragments render in their including page's anchor/locale context.
        included = set()
        for page in pages:
            try:
                for ref in references(candidate.text(page) or ''):
                    if ref.kind == 'include':
                        target = resolve(page, ref.href, docs_root=docs_root)
                        if target:
                            included.add(target.path)
            except (ValueError, RuntimeError, OSError):
                pass  # reported by the normal traversal below
        roots = [p for p in pages if p not in included or (build and p in build.anchors)]
        # A disconnected include cycle must not disappear from validation.
        pages = roots + [p for p in pages if p in included]
    pending = [(p, p, ()) for p in reversed(pages)]
    seen = set()
    reached = set()
    while pending:
        path, rendered_page, ancestors = pending.pop()
        if not ancestors and path in reached and not (build and path in build.anchors):
            continue
        if (path, rendered_page) in seen:
            continue
        seen.add((path, rendered_page))
        reached.add(path)
        try:
            text = candidate.text(path)
            if text is None:
                raise ValueError('File is missing in candidate')
            refs = references(text)
        except (ValueError, RuntimeError, OSError) as error:
            issues.append(Issue(path, str(error), 'Restore the file in the candidate.', 'link_read'))
            complete = False
            continue
        for ref in refs:
            try:
                target = resolve(path, ref.href, docs_root=docs_root)
                if target is None:
                    if (ref.kind in {'link', 'html_link'} and locale(rendered_page, docs_root) == 'en'
                            and _russian_url(ref.href)):
                        raise ValueError(f'Unconfirmed English target for Russian URL: {ref.href}; specify a replacement')
                    continue
                data = candidate.read(target.path)
                if data is None:
                    raise ValueError(f'Missing {ref.kind} target: {ref.href} -> {target.path}')
                if not data:
                    raise ValueError(f'Empty {ref.kind} target: {ref.href} -> {target.path}')
                if (ref.kind in {'link', 'html_link'} and target.path.endswith(('.md', '.yaml', '.yml'))
                        and locale(rendered_page, docs_root) == 'en' and locale(target.path, docs_root) == 'ru'):
                    en_path = mirror(target.path, docs_root)
                    confirmed = candidate.read(en_path) is not None
                    raise ValueError(f'English page links to Russian page: {ref.href}; '
                                     f'English target {en_path} is {"present; replace the URL" if confirmed else "missing; specify a replacement"}')
                if target.fragment:
                    if build is None or not build.ok_for(candidate.sha):
                        complete = False
                        raise ValueError(f'Anchor not verified by a successful build of this SHA: {ref.href}')
                    anchor_page = (rendered_page if ref.kind == 'include' or
                                   (target.path == path and not urlsplit(ref.href).path) else target.path)
                    if target.fragment not in _anchor_ids(anchor_page, data, build):
                        raise ValueError(f'Missing anchor: {ref.href} in {target.path}')
                if ref.kind == 'include':
                    if target.path in (*ancestors, path):
                        raise ValueError(f'Include cycle: {ref.href} -> {target.path}')
                    pending.append((target.path, rendered_page, (*ancestors, path)))
            except (ValueError, RuntimeError, OSError) as error:
                issues.append(Issue(path, str(error), 'Specify an existing target/anchor or restore the asset.',
                                    'links', target=ref.location))
    return LinkResult(candidate.sha, tuple(issues), complete)


@dataclass(frozen=True)
class AssetPreparation:
    source_sha: str
    copies: Mapping[str, bytes]
    issues: tuple[Issue, ...]


def prepare_assets(snapshot: Candidate, pairs: Mapping[str, str], *,
                   overrides: Mapping[str, bytes | None] | None = None,
                   docs_root: str = 'ydb/docs') -> AssetPreparation:
    """Prepare exact binary copies for source->translated page pairs.

    Explicit deletion/empty overrides are authoritative. Includes are traversed
    for related assets, but never copied as untranslated article content.
    Caller commits copies with translations, then validates the new Candidate.
    """
    overrides = overrides or {}
    copies: dict[str, bytes] = {}
    issues = []
    pending = list(pairs.items())
    seen = set()
    while pending:
        source, destination = pending.pop()
        if (source, destination) in seen:
            continue
        seen.add((source, destination))
        try:
            text = snapshot.text(source)
            if text is None:
                raise ValueError(f'Missing source {source}')
            refs = references(text)
        except (ValueError, RuntimeError, OSError) as error:
            issues.append(Issue(destination, str(error), 'Restore the source file.', 'assets'))
            continue
        for ref in refs:
            try:
                old = resolve(source, ref.href, docs_root=docs_root)
                new = resolve(destination, ref.href, docs_root=docs_root)
                if old is None or new is None:
                    continue
                if ref.kind == 'include':
                    pending.append((old.path, new.path))
                    continue
                if ref.kind != 'asset' and old.path.endswith(('.md', '.yaml', '.yml')):
                    continue
                if not urlsplit(ref.href).path:
                    continue
                data = snapshot.read(old.path)
                if data is None or not data:
                    raise ValueError(f'Missing or empty asset: {ref.href} -> {old.path}')
                if new.path in overrides:
                    if not overrides[new.path]:
                        raise ValueError(f'Deleted or empty asset override: {new.path}')
                    continue
                existing = snapshot.read(new.path)
                if existing is not None:
                    if not existing:
                        raise ValueError(f'Empty destination asset: {new.path}')
                    continue
                if new.path in copies and copies[new.path] != data:
                    raise ValueError(f'Conflicting asset copies: {new.path}')
                copies[new.path] = data
            except (ValueError, RuntimeError, OSError) as error:
                issues.append(Issue(destination, str(error), 'Restore or provide the related asset.', 'assets'))
    return AssetPreparation(snapshot.sha, MappingProxyType(copies), tuple(issues))


def confirmed_english_url(candidate: Candidate, path: str, href: str, *,
                          docs_root: str = 'ydb/docs', build=None) -> str:
    """Propose a URL only after confirming its EN page and unchanged fragment.

    Does not edit prose, includes, source documents or anchors. A subsequent
    committed candidate must be checked/built again after applying a proposal.
    """
    target = resolve(path, href, docs_root=docs_root)
    if target is None:
        if locale(path, docs_root) == 'en' and _russian_url(href):
            raise ValueError(f'Unconfirmed English target for {href}; specify a replacement')
        return href
    if locale(path, docs_root) != 'en' or locale(target.path, docs_root) != 'ru':
        return href
    english = mirror(target.path, docs_root)
    data = candidate.read(english)
    if not data:
        raise ValueError(f'Missing English target for {href}: {english}; specify a replacement')
    if target.fragment and (build is None or not build.ok_for(candidate.sha)
                            or target.fragment not in _anchor_ids(english, data, build)):
        raise ValueError(f'Unconfirmed English anchor for {href}; specify a replacement')
    url = urlsplit(href)
    new_path = ('/' + english[len(docs_root.rstrip('/')) + 1:] if url.path.startswith('/')
                else posixpath.relpath(english, posixpath.dirname(path)))
    if url.path.endswith('.html'):
        new_path = new_path[:-3] + '.html'
    return urlunsplit(('', '', quote(new_path, safe='/'), url.query, url.fragment))
