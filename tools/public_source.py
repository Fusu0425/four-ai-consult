"""Export a reviewed source allowlist; never copy private development history.

The scanner reports locations/rule names only, not matched secret values. It is
a safety net, not proof that a repository contains no confidential information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT_FILES = (
    '.env.example', '.gitignore', 'README.md', 'LICENSE', 'PRIVACY.md', 'SECURITY.md',
    'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'main.py', 'launcher.pyw',
    'pyproject.toml', 'requirements.txt', 'requirements-dev.txt', 'requirements-build.txt',
    'start.bat', 'start-debug.bat',
)
DIRECTORIES = ('four_ai_consult', 'tests', 'packaging', 'resources', '.github', 'licenses')
TOOLS = ('__init__.py', 'release_audit.py', 'public_source.py', 'collect_licenses.py',
         'pilot_preview.py', 'public_preview.py', 'build_icon_assets.py')
DOCS = ('PUBLIC_TESTING.md', 'RELEASE_CHECKLIST.md', 'OPEN_SOURCE_BUILD.md',
        'screenshots/main.png', 'screenshots/report.png')
PATTERNS = {
    'provider-key': re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'aws-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'personal-path': re.compile(r'(?:[A-Za-z]:[\\/]+Users[\\/]+)(?!example\b|user\b|<)[^\s"\']+', re.I),
}
FORBIDDEN = {'.env', 'cookies', 'login data', 'settings.ini', 'application.lock',
             'browser-profile', 'logs', 'reports', '.git', '__pycache__', 'marketing'}


def scan_bytes(data: bytes) -> list[tuple[int, str]]:
    if b'\x00' in data:
        return []
    text = data.decode('utf-8', errors='replace')
    return [(text.count('\n', 0, match.start()) + 1, rule)
            for rule, pattern in PATTERNS.items() for match in pattern.finditer(text)]


def selected_files(root: Path) -> list[Path]:
    paths = [root / name for name in ROOT_FILES]
    paths += [root / 'tools' / name for name in TOOLS]
    paths += [root / 'docs' / name for name in DOCS]
    for name in DIRECTORIES:
        folder = root / name
        if folder.is_dir():
            paths += [p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return sorted(set(p for p in paths if p.is_file()))


def inspect_files(root: Path, files: list[Path]) -> list[dict]:
    findings = []
    for path in files:
        relative = path.relative_to(root)
        parts = {p.lower() for p in relative.parts}
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            findings.append({'path': relative.as_posix(), 'rule': 'external-link'})
            continue
        if parts & FORBIDDEN or path.suffix.lower() in {'.sqlite3', '.db', '.log', '.zip', '.mp4'}:
            findings.append({'path': relative.as_posix(), 'rule': 'private-or-generated-file'})
        if path.stat().st_size > 5 * 1024 * 1024:
            findings.append({'path': relative.as_posix(), 'rule': 'oversized-source-file'})
        findings += [{'path': relative.as_posix(), 'line': line, 'rule': rule}
                     for line, rule in scan_bytes(path.read_bytes())]
    return findings


def scan_git_history(root: Path) -> dict:
    """Inspect unique historical blobs without printing their contents."""
    objects = subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=root).splitlines()
    findings, blobs = [], 0
    for line in objects:
        oid, _, path = line.partition(b' ')
        kind = subprocess.check_output(['git', 'cat-file', '-t', oid.decode()], cwd=root).strip()
        if kind != b'blob':
            continue
        blobs += 1
        data = subprocess.check_output(['git', 'cat-file', '-p', oid.decode()], cwd=root)
        findings += [{'object': oid.decode()[:12], 'path': path.decode('utf-8', errors='replace'),
                      'line': number, 'rule': rule} for number, rule in scan_bytes(data)]
    return {'blobs_checked': blobs, 'findings': findings}


def export(root: Path, destination: Path) -> dict:
    root, destination = root.resolve(), destination.resolve()
    if destination == root or root.is_relative_to(destination) or destination.is_relative_to(root):
        raise ValueError('Export must be a separate sibling directory, not an ancestor/child of the development tree')
    if destination.exists():
        raise ValueError('Destination already exists; refusing to merge or overwrite')
    files = selected_files(root)
    findings = inspect_files(root, files)
    if findings:
        raise ValueError(json.dumps(findings, ensure_ascii=False))
    required = ('README.md', 'LICENSE', 'PRIVACY.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md')
    if any(not (root / name).is_file() for name in required):
        raise ValueError('Publication documents/license approval are incomplete')
    manifest = []
    destination.mkdir(parents=True)
    for source in files:
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    return {'files': len(manifest), 'manifest': manifest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--export', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.history:
        result = scan_git_history(root)
    elif args.export:
        result = export(root, args.export)
    else:
        files = selected_files(root)
        result = {'files_checked': len(files), 'findings': inspect_files(root, files)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get('findings') else 0


if __name__ == '__main__':
    raise SystemExit(main())
