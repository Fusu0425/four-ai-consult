"""Fail closed if a portable release contains runtime data or development files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from four_ai_consult import __version__

FORBIDDEN_DIRS = {"browser-profile", "logs", "reports", "screenshots", ".git", ".venv", ".venv-packaging", ".smoke-data"}
FORBIDDEN_NAMES = {".env", "cookies", "login data", "settings.ini", "application.lock", "startup-error.log", "debug_dom.txt"}
REQUIRED_FILES = ("FourAIConsult.exe", "开始使用.html", "内测说明.txt", "release-info.json",
                  "LICENSE", "THIRD_PARTY_NOTICES.md", "PRIVACY.md", "SECURITY.md",
                  "licenses/build-environment.json", "licenses/qt-6.11.2/sources.json",
                  "licenses/qt-6.11.2/LGPL-3.0-only.txt", "licenses/qt-6.11.2/GPL-3.0-only.txt")
TOP_LEVEL = {"_internal", "licenses", *(name.split('/')[0] for name in REQUIRED_FILES)}


def validate_entries(names):
    normalized = []
    for name in names:
        name = name.replace("\\", "/")
        path = PurePosixPath(name)
        parts = path.parts
        if path.is_absolute() or ".." in parts or ":" in name or not parts or parts[0] not in TOP_LEVEL:
            raise ValueError(f"Unexpected release path: {name}")
        lower = [p.lower() for p in parts]
        if ('_internal/pyside6/qml/' in name.lower() or 'qmltooling' in lower
                or re.match(r'qt6(?:charts|graphs|datavisualization|virtualkeyboard|quick3d)', lower[-1])):
            raise ValueError(f"Unused QML-only component must not enter the Widgets release: {name}")
        if any(p in FORBIDDEN_DIRS for p in lower) or lower[-1] in FORBIDDEN_NAMES:
            raise ValueError(f"Private/development data in release: {name}")
        if re.search(r"\.(sqlite3?|db)(-wal|-shm)?$", lower[-1]) or lower[-1].endswith(".log"):
            raise ValueError(f"Runtime data in release: {name}")
        normalized.append(name)
    for required in REQUIRED_FILES:
        if required not in normalized:
            raise ValueError(f"Missing required release file: {required}")
    if not any(n.endswith("/QtWebEngineProcess.exe") for n in normalized):
        raise ValueError("Missing Qt WebEngine runtime")
    return len(normalized)


def audit_directory(directory: Path):
    files = list(directory.rglob("*"))
    if any(p.is_symlink() for p in files):
        raise ValueError("Symlinks must not enter the release")
    count = validate_entries([p.relative_to(directory).as_posix() for p in files if p.is_file()])
    metadata = json.loads((directory / "release-info.json").read_text(encoding="utf-8-sig"))
    if metadata["version"] != __version__:
        raise ValueError("Release metadata version mismatch")
    return count


def audit_archive(archive: Path):
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("ZIP CRC failed")
        paths = [n for n in bundle.namelist() if not n.endswith("/")]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate archive entry")
        if any(not n.startswith("FourAIConsult/") for n in paths):
            raise ValueError("Unexpected archive root")
        count = validate_entries([n.removeprefix("FourAIConsult/") for n in paths])
        metadata = json.loads(bundle.read("FourAIConsult/release-info.json"))
        if metadata["version"] != __version__:
            raise ValueError("Archive version mismatch")
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest().upper()
    archive.with_suffix(".zip.sha256").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    return {"version": __version__, "files": count, "bytes": archive.stat().st_size, "sha256": digest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        (args.path / "release-info.json").write_text(json.dumps({
            "version": __version__, "channel": "invitation-pilot", "target": "Windows x64",
            "default_mode": "free-web", "requires_user_site_login": True,
            "automatic_feedback_upload": False, "live_site_acceptance": "pending",
            "clean_second_pc_acceptance": "pending",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit_archive(args.path) if args.path.is_file() else {"files": audit_directory(args.path)}))


if __name__ == "__main__":
    main()
