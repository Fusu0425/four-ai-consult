import pytest

from tools.release_audit import REQUIRED_FILES, validate_entries

BASE = [*REQUIRED_FILES, "_internal/PySide6/QtWebEngineProcess.exe"]


def test_clean_release_layout_passes():
    assert validate_entries(BASE) == len(BASE)


@pytest.mark.parametrize("path", [
    ".env", "_internal/.env", "_internal/browser-profile/Cookies", "_internal/consultations.sqlite3-wal",
    "_internal/logs/app.log", "_internal/settings.ini", "../outside", "C:/escape", "/outside",
    "tools/pilot_preview.py", "_internal/login data",
    "_internal/PySide6/qml/QtCharts/qmldir", "_internal/PySide6/Qt6Charts.dll",
    "_internal/PySide6/plugins/qmltooling/qmldbg_tcp.dll", "_internal/PySide6/Qt6Quick3DUtils.dll",
])
def test_private_or_development_files_block_release(path):
    with pytest.raises(ValueError):
        validate_entries([*BASE, path])


def test_incomplete_release_is_blocked():
    with pytest.raises(ValueError):
        validate_entries(BASE[:-1])
