"""Collect build-environment notices; optionally snapshot official Qt attributions."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen


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


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.skip += 1
        if tag in {'p', 'div', 'br', 'pre', 'h1', 'h2', 'h3', 'tr', 'li'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def qt_notices(destination: Path):
    from PySide6.QtCore import qVersion

    version = qVersion()
    base = 'https://doc.qt.io/qt-6/'

    def fetch(url):
        with urlopen(url, timeout=30) as response:
            return response.read()

    index = fetch(base + 'licenses-used-in-qt.html').decode('utf-8')
    if f'Qt {version}' not in index:
        raise RuntimeError('Qt documentation version does not match the build; use a reviewed matching snapshot')
    # Superset: preserve all attributions in this official release, including
    # notices for modules that may not be present in our smaller desktop bundle.
    names = sorted(set(re.findall(r'href="([^"/#]*(?:-attribution-|-3rdparty-)[^"/#]+\.html)"', index)))
    if len(names) < 50:
        raise RuntimeError('Incomplete Qt attribution index')
    folder = destination / ('qt-' + version)
    folder.mkdir(parents=True, exist_ok=True)

    def save(name):
        url = base + name
        data = fetch(url)
        page = PageText()
        page.feed(data.decode('utf-8'))
        text = ''.join(page.parts)
        if len(text) < 500:
            raise RuntimeError('Incomplete attribution: ' + name)
        target = folder / (name.removesuffix('.html') + '.txt')
        target.write_text('Official source: ' + url + '\n\n' + text, encoding='utf-8')
        return {'url': url, 'sha256': hashlib.sha256(data).hexdigest(), 'file': target.name}

    with ThreadPoolExecutor(max_workers=4) as pool:
        inventory = list(pool.map(save, names))
    for name in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'GPL-2.0-only.txt', 'LGPL-2.0-or-later.txt'):
        url = f'https://raw.githubusercontent.com/qt/qtwebengine/v{version}/LICENSES/{name}'
        data = fetch(url)
        (folder / name).write_bytes(data)
        inventory.append({'url': url, 'sha256': hashlib.sha256(data).hexdigest(), 'file': name})
    (folder / 'sources.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    print(f'Collected {len(inventory)} official Qt notice sources for {version}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--qt-docs', action='store_true', help='Download matching official Qt notices (network required)')
    args = parser.parse_args()
    collect(args.destination)
    if args.qt_docs:
        qt_notices(args.destination)
    print('Collected dependency inventory; manual license/source review is still required.')


if __name__ == '__main__':
    main()
