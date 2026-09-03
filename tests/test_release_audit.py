import pytest

from tools.release_audit import validate_entries

BASE = ["FourAIConsult.exe", "开始使用.html", "内测说明.txt", "release-info.json", "_internal/PySide6/QtWebEngineProcess.exe"]


def test_clean_release_layout_passes():
    assert validate_entries(BASE) == 5


@pytest.mark.parametrize("path", [
    ".env", "_internal/.env", "_internal/browser-profile/Cookies", "_internal/consultations.sqlite3-wal",
    "_internal/logs/app.log", "_internal/settings.ini", "../outside", "C:/escape", "/outside",
    "tools/pilot_preview.py", "_internal/login data",
])
def test_private_or_development_files_block_release(path):
    with pytest.raises(ValueError):
        validate_entries([*BASE, path])


def test_incomplete_release_is_blocked():
    with pytest.raises(ValueError):
        validate_entries(BASE[:-1])
