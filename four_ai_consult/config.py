from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "FourAIConsult"
ORGANIZATION_NAME = "four-ai"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    """Return a writable per-user data directory, independent of install location."""
    override = os.getenv("FOUR_AI_DATA_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError("FOUR_AI_DATA_DIR 必须为绝对路径，避免数据随启动位置变化")
        return path
    base = os.getenv("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".four-ai-consult"


def local_settings(root: Path):
    """Keep settings alongside data; isolated QA must not change real-user settings."""
    from PySide6.QtCore import QSettings

    target = root / "settings.ini"
    first = not target.exists()
    settings = QSettings(str(target), QSettings.Format.IniFormat)
    if first and not os.getenv("FOUR_AI_DATA_DIR") and root == app_data_dir():
        legacy = QSettings("four-ai", "four-ai-consult")
        for key in ("enabled_models", "geometry_v2", "outer_sizes", "left_sizes", "right_sizes", "onboarding_completed"):
            if legacy.contains(key):
                settings.setValue(key, legacy.value(key))
    settings.setValue("settings_format", 1)
    settings.sync()
    return settings


def ensure_runtime_dirs() -> dict[str, Path]:
    root = app_data_dir()
    paths = {
        "root": root,
        "profile": root / "browser-profile",
        "logs": root / "logs",
        "reports": root / "reports",
        "screenshots": root / "screenshots",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    paths["database"] = root / "consultations.sqlite3"
    return paths


@dataclass(frozen=True)
class AppConfig:
    poll_interval_ms: int = 1200
    response_timeout_seconds: int = 240
    stable_poll_count: int = 3
    design_width: int = 1000
    auto_zoom_min: float = 0.50
    auto_zoom_max: float = 1.20
    manual_zoom_min: float = 0.40
    manual_zoom_max: float = 2.00


def _looks_like_deepseek_key(value: str) -> bool:
    value = (value or "").strip()
    return value.startswith("sk-") and len(value) >= 20 and value.isascii() and not any(c.isspace() for c in value)


class SecretStore:
    """Load secrets from env/keyring, with read-only support for the legacy .env."""

    service_name = "four-ai-consult"
    account_name = "deepseek-api-key"

    def __init__(self) -> None:
        self._session_key = ""
        self._ignore_legacy = False

    def load_deepseek_key(self) -> str:
        for value in (self._session_key, os.getenv("DEEPSEEK_API_KEY", "")):
            if _looks_like_deepseek_key(value):
                return value.strip()

        try:
            import keyring

            value = keyring.get_password(self.service_name, self.account_name) or ""
            if _looks_like_deepseek_key(value):
                return value.strip()
        except Exception:
            pass

        # Compatibility only: never write new keys to this plaintext file.
        if not self._ignore_legacy:
            legacy_env = PROJECT_ROOT / ".env"
            try:
                for line in legacy_env.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("DEEPSEEK_API_KEY="):
                        value = line.split("=", 1)[1].strip()
                        if _looks_like_deepseek_key(value):
                            return value
            except OSError:
                pass
        return ""

    def remember_for_session(self, value: str) -> bool:
        if not _looks_like_deepseek_key(value):
            return False
        self._session_key = value.strip()
        return True

    def save_to_keyring(self, value: str) -> bool:
        if not self.remember_for_session(value):
            return False
        try:
            import keyring

            keyring.set_password(self.service_name, self.account_name, value.strip())
            return True
        except Exception:
            return False

    def clear(self) -> None:
        self._session_key = ""
        self._ignore_legacy = True
        try:
            import keyring

            keyring.delete_password(self.service_name, self.account_name)
        except Exception:
            pass
