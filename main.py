from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QApplication, QMessageBox

from four_ai_consult import __version__
from four_ai_consult.config import APP_NAME, ORGANIZATION_NAME, AppConfig, SecretStore, ensure_runtime_dirs
from four_ai_consult.logging_setup import configure_logging
from four_ai_consult.ui import APP_STYLE, MainWindow, make_icon


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setWindowIcon(make_icon())
    app.setStyleSheet(APP_STYLE)

    runtime_dirs = ensure_runtime_dirs()
    logger = configure_logging(runtime_dirs["logs"])
    instance_lock = QLockFile(str(runtime_dirs["root"] / "application.lock"))
    instance_lock.setStaleLockTime(10_000)
    if not instance_lock.tryLock(100):
        logger.info("Launch ignored because another application instance is running")
        QMessageBox.information(None, "四模型会诊", "程序已经在运行，请查看主窗口或系统托盘。")
        return 0

    def handle_exception(exc_type, exc_value, traceback) -> None:
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, traceback))
        sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = handle_exception

    # The Qt default profile is off-the-record on this runtime, so merely
    # assigning it a storage path does not persist login cookies. A named
    # profile is required for sessions to survive normal application restarts.
    profile = QWebEngineProfile(APP_NAME, app)
    profile.setPersistentStoragePath(str(runtime_dirs["profile"]))
    profile.setCachePath(str(runtime_dirs["profile"] / "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)

    logger.info("Application starting: version=%s executable=%s data=%s database=%s", __version__,
                sys.executable, runtime_dirs["root"].resolve(), runtime_dirs["database"].resolve())
    window = MainWindow(profile, runtime_dirs, AppConfig(), SecretStore())
    logger.info("Main window initialized")
    window.show()
    logger.info("Main window shown")
    exit_code = app.exec()
    instance_lock.unlock()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
