"""Collect build-environment notices and embedded Chromium credits for release QA."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import shutil
import sys
from pathlib import Path


def collect(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = distribution.metadata['Name'], distribution.version
        copied = []
        for file in distribution.files or []:
            if file.name.lower().startswith(('license', 'copying', 'notice')) and '.dist-info' in str(file):
                source = Path(distribution.locate_file(file))
                if not source.is_file():
                    continue
                target = destination / 'python-packages' / f'{name}-{version}' / file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(target.relative_to(destination).as_posix())
        inventory.append({'name': name, 'version': version, 'notices': copied,
                          'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),
                          'project_urls': distribution.metadata.get_all('Project-URL') or []})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Missing official Python LICENSE.txt')
    shutil.copy2(python_license, destination / 'PYTHON-LICENSE.txt')
    (destination / 'build-environment.json').write_text(json.dumps({
        'scope': 'Entire build environment; not every distribution is shipped at runtime.',
        'python': sys.version.split()[0], 'distributions': inventory,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def chromium_credits(destination: Path):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)  # off-the-record, no user profile
    page = QWebEnginePage(profile, app)
    success = []

    def save(html):
        if len(html) > 10000 and 'license' in html.lower():
            (destination / 'CHROMIUM-CREDITS.html').write_text(html, encoding='utf-8')
            success.append(True)
        app.quit()

    page.loadFinished.connect(lambda ok: page.toHtml(save) if ok else app.quit())
    page.load(QUrl('chrome://credits'))
    QTimer.singleShot(25000, app.quit)
    app.exec()
    page.deleteLater()
    app.processEvents()
    if not success:
        raise RuntimeError('Embedded Chromium credits could not be collected')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    collect(args.destination)
    chromium_credits(args.destination)
    print('Collected dependency inventory and embedded credits; manual license/source review is still required.')


if __name__ == '__main__':
    main()
