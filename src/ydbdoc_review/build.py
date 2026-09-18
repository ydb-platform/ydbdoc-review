"""Trusted YFM executable on a fresh tree materialized from one exact commit."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from ydbdoc_review.links import Candidate, HtmlReferences, LinkResult, safe_path
from ydbdoc_review.quality import Issue


@dataclass(frozen=True)
class BuildResult:
    candidate_sha: str
    status: Literal['success', 'failure', 'pending']
    log: str = ''
    returncode: int | None = None
    anchors: Mapping[str, frozenset[str]] = field(default_factory=dict)
    rendered_links: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'anchors', MappingProxyType(
            {path: frozenset(ids) for path, ids in self.anchors.items()}))
        object.__setattr__(self, 'rendered_links', MappingProxyType(dict(self.rendered_links)))

    def ok_for(self, candidate_sha: str) -> bool:
        return (self.status == 'success' and self.returncode == 0
                and self.candidate_sha == candidate_sha)

    def issues_for(self, candidate_sha: str) -> tuple[Issue, ...]:
        if self.ok_for(candidate_sha):
            return ()
        # Preserve the first available failure and following context, rather than the
        # tail of a potentially huge cascade. Full output remains in self.log.
        failure = re.search(r'^.*\b(?:error|failed|failure|fatal|exception|timed out|missing)\b',
                            self.log, re.I | re.M)
        # Leading context must not consume the excerpt before the error line.
        start = failure.start() if failure else 0
        excerpt = self.log[start:start + 2000]
        return (Issue('ydb/docs', f'Build {self.status}, SHA {self.candidate_sha}; '
                      f'required candidate SHA {candidate_sha}. {excerpt} '
                      'No baseline comparison was performed; this does not establish that '
                      'the translation introduced the failure.',
                      'Resolve the reported build failure, then complete a successful documentation '
                      'build of the exact candidate SHA. Full output is in the build log.', 'build'),)


def automatic_ok(candidate_sha: str, links: LinkResult, build: BuildResult) -> bool:
    """Automatic gates only; caller additionally requires completed critic checks."""
    return links.candidate_sha == candidate_sha and links.ok and build.ok_for(candidate_sha)


def build_candidate(candidate: Candidate, *, docs_root: str = 'ydb/docs',
                    executable: str = 'yfm', timeout: float = 600) -> BuildResult:
    """Use installed CLI, never execute candidate build.sh or npm install.

    Output and HOME are outside the candidate input. The child receives no
    credentials from the runner. Repo worktree, staged changes and branch names
    are never inputs. Logs/results survive temp tree cleanup in the return value.
    """
    root = safe_path(docs_root).rstrip('/')
    program = shutil.which(executable)
    if program is None:
        return BuildResult(candidate.sha, 'failure', f'YFM executable is missing: {executable}')
    with tempfile.TemporaryDirectory(prefix='ydbdoc-build-') as temp:
        work = Path(temp)
        source = work / 'input'
        output = work / 'output'
        home = work / 'home'
        source.mkdir()
        home.mkdir()
        try:
            count = 0
            for path in candidate.entries:
                if not path.startswith(root + '/'):
                    continue
                data = candidate.read(path)
                relative = path[len(root) + 1:]
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                count += 1
            if not count:
                raise ValueError(f'No documentation files at {root} in {candidate.sha}')
            env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(home),
                   'TMPDIR': str(work), 'LANG': 'C.UTF-8', 'CI': 'true', 'NO_COLOR': '1'}
            result = subprocess.run(
                [str(Path(program).absolute()), '-s', '-i', '.', '-o', str(output),
                 '--allowHTML', '--apply-presets'], cwd=source, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                text=True, errors='replace',
            )
            if result.returncode:
                return BuildResult(candidate.sha, 'failure', result.stdout, result.returncode)
            anchors = {}
            links = {}
            # Diplodoc HTML stores rendered content in an escaped JSON script.
            for path in candidate.entries:
                if not path.startswith(root + '/') or not path.endswith(('.md', '.yaml', '.yml')):
                    continue
                relative = path[len(root) + 1:]
                emitted = output / str(Path(relative).with_suffix('.html'))
                if not emitted.is_file():
                    continue
                content = emitted.read_text(encoding='utf-8')
                state = re.search(r'<script[^>]*id="diplodoc-state"[^>]*>(.*?)</script>', content, re.S)
                if state:
                    # Match the CLI loader: only these three entities, not all
                    # HTML entities inside JSON string values.
                    raw = state.group(1).replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                    state_data = json.loads(raw)['data']
                    content = state_data.get('html', '')
                parser = HtmlReferences()
                parser.feed(content)
                anchors[path] = frozenset(parser.ids)
                links[path] = tuple(ref.href for ref in parser.refs)
            if not anchors:
                return BuildResult(candidate.sha, 'failure', result.stdout + '\nNo pages emitted.', 0)
            return BuildResult(candidate.sha, 'success', result.stdout, 0, anchors, links)
        except subprocess.TimeoutExpired as error:
            log = error.stdout or b''
            if isinstance(log, bytes):
                log = log.decode('utf-8', errors='replace')
            return BuildResult(candidate.sha, 'failure', f'YFM timed out after {timeout}s\n{log}')
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            return BuildResult(candidate.sha, 'failure', f'YFM build failed: {error}')
