import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt opt-in")


def test_six_readiness_checks_do_not_return_private_text(tmp_path):
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from test_adapter_javascript import HTML_BY_SITE, _run_js, _set_html

    from four_ai_consult.adapters import SITE_ADAPTERS

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("pilot-readiness", app)
    profile.setPersistentStoragePath(str(tmp_path / "profile"))
    page = QWebEnginePage(profile)
    try:
        for adapter in SITE_ADAPTERS:
            _set_html(page, HTML_BY_SITE[adapter.id])
            assert json.loads(_run_js(page, adapter.readiness_script())) == {"ok": True, "inputAvailable": True}
            _run_js(page, "document.querySelectorAll('textarea,input,[contenteditable]').forEach(e=>e.style.display='none')")
            assert json.loads(_run_js(page, adapter.readiness_script())) == {"ok": True, "inputAvailable": False}
        _set_html(page, '<textarea disabled>sk-private</textarea>')
        assert json.loads(_run_js(page, SITE_ADAPTERS[0].readiness_script())) == {"ok": True, "inputAvailable": False}
    finally:
        delete(page)
        delete(profile)
        app.processEvents()


def test_main_window_four_way_partial_failure_history_report_and_help(tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from test_adapter_javascript import _run_js

    from four_ai_consult import ui
    from four_ai_consult.config import AppConfig, SecretStore, ensure_runtime_dirs, local_settings
    from four_ai_consult.models import PaneState
    from four_ai_consult.pilot_ui import PilotCenter
    from four_ai_consult.storage import ConsultationRepository
    from tools.pilot_preview import mock_adapters

    monkeypatch.setenv("FOUR_AI_DATA_DIR", str(tmp_path))
    dirs = ensure_runtime_dirs()
    local_settings(tmp_path).setValue("onboarding_completed", True)
    monkeypatch.setattr(ui, "ADAPTER_BY_ID", mock_adapters(ui.ADAPTER_BY_ID))
    monkeypatch.setattr(ui.MainWindow, "_setup_tray", lambda _: None)
    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("pilot-main-flow", app)
    profile.setPersistentStoragePath(str(dirs["profile"]))
    window = ui.MainWindow(profile, dirs, AppConfig(poll_interval_ms=60, stable_poll_count=2,
                                                   response_timeout_seconds=4), SecretStore())
    window.auto_report.setChecked(False)

    def wait_until(condition, timeout=12000):
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if condition() else None)
        timer.start(30)
        QTimer.singleShot(timeout, loop.quit)
        loop.exec()
        assert condition()

    try:
        window.show()
        wait_until(lambda: all(p.state == PaneState.READY for p in window.panes))
        # Simulate one inaccessible provider. Other providers should still work.
        _run_js(window.panes_by_id["deepseek"].page, "document.querySelector('textarea').remove()")
        window.question_input.setText("公开验收：比较两种方案，保留末尾条件")
        window.broadcast()
        wait_until(lambda: window.session is not None and window.session.complete)
        assert len(window.session.successful_results) == 3
        assert window.session.results["deepseek"].state == PaneState.ERROR
        reloaded = ConsultationRepository(dirs["database"]).load_session(window.session.id)
        assert len(reloaded.results) == 4
        assert all(r.text.endswith("500 元。") for r in reloaded.successful_results)
        window.show_report()
        assert len(window.report_dialog.record.sources) == 3
        assert window.report_dialog.record.missing
        assert window.report_dialog.record.status == "pending"
        # A late recovered answer must update the open report and durable history.
        from PySide6.QtWidgets import QMessageBox

        recovered_id = reloaded.successful_results[0].site_id
        pane = window.panes_by_id[recovered_id]
        dialog = window.report_dialog
        pane._last_text = "仅采集到思考草稿"
        pane._finish_error("等待回答超时")
        assert "仅采集到思考草稿" in dialog.record.markdown()
        _run_js(pane.page, "document.querySelector('.answer').textContent='补采完整原文，末尾限制 800 元。'")
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        pane.recapture()
        assert not window.session.complete
        wait_until(lambda: window.session.complete)
        assert pane.state == PaneState.DONE
        assert window.report_dialog is dialog
        assert "补采完整原文，末尾限制 800 元。" in dialog.record.markdown()
        assert "仅采集到思考草稿" not in dialog.record.markdown()
        assert window.repository.load_session(window.session.id).results[recovered_id].text.endswith("800 元。")
        window.show_help()
        center = window.help_dialog
        assert isinstance(center, PilotCenter) and center.tabs.count() == 3
        assert center.data_path.text() == str(tmp_path.resolve())
        center.resize(620, 380)
        app.processEvents()
        assert center.height() <= 380
        assert center.tabs.widget(0).widgetResizable()
        center.check_readiness()
        wait_until(lambda: center.check_button.isEnabled())
        assert "未找到" in center.status_rows["deepseek"].text()
        center.check_health()
        assert "检查通过" in center.health_label.text()
    finally:
        window._quitting = True
        window.close()
        delete(window)
        delete(profile)
        app.processEvents()
