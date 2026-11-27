"""Read a fixed AlphaDefect git revision or GitHub commit ZIP, never a dirty worktree."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from zipfile import ZipFile


class UpstreamSource:
    def __init__(self, checkout: Path, archive: Path | None = None):
        self.checkout = checkout
        self.archive = ZipFile(archive) if archive else None
        if self.archive:
            roots = {n.split('/')[0] for n in self.archive.namelist()}
            if len(roots) != 1:
                self.archive.close()
                raise ValueError('Expected a GitHub commit archive with one root')
            self.root = roots.pop()
            match = re.fullmatch(r'AlphaDefect-Public-([0-9a-f]{40})', self.root)
            if not match:
                self.archive.close()
                raise ValueError('Archive must name an immutable 40-character commit, not a branch')
            self.commit = match[1]
        else:
            self.commit = self.git('rev-parse', 'HEAD').decode().strip()

    def git(self, *args: str) -> bytes:
        return subprocess.run(['git', '-C', str(self.checkout), *args], check=True, capture_output=True).stdout

    def read(self, path: str) -> bytes:
        if self.archive:
            return self.archive.read(f'{self.root}/{path}')
        return self.git('show', f'{self.commit}:{path}')

    def files(self, prefix: str) -> list[str]:
        if self.archive:
            root = self.root + '/'
            return [n[len(root):] for n in self.archive.namelist()
                    if n.startswith(root + prefix) and not n.endswith('/')]
        return self.git('ls-tree', '-r', '--name-only', self.commit, '--', prefix).decode().splitlines()
