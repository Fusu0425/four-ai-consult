"""Keep native QtQml dependencies without unrelated QML applications/plugins.

Four AI Consult uses Qt Widgets, not a QML engine. The default hook collects
every installed QML plugin, including unused Charts/Graphs/VirtualKeyboard.
"""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, target) for source, target in binaries
            if 'qmltooling' not in source.lower()]
