"""Collect desktop GUI dependencies without the unused Qt Virtual Keyboard.

Physical keyboard and Windows IME input remain available. This application
does not implement a Qt QML virtual keyboard or an embedded kiosk interface.
"""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, target) for source, target in binaries
            if 'qtvirtualkeyboard' not in source.lower()]
