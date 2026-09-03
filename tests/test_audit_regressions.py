import os

import pytest

from four_ai_consult.analysis_plan import AnalysisPlan, ReportRecord
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState


def sample():
    session = ConsultationSession("保留全部约束", ("deepseek",))
    session.add_result(AnswerResult("deepseek", "DeepSeek", session.question, PaneState.DONE, text="带条件的完整原文"))
    return session


qt = pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt opt-in")


@qt
def test_newer_snapshot_wins_over_stale_readable_database(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog

    app = QApplication.instance() or QApplication([])
    session = sample()
    plan = AnalysisPlan(session, "免费网页版", "deepseek")
    stale = ReportRecord.from_json(plan.record.to_json())
    task = plan.next_task()
    plan.accept("新完成的报告 [S1-1]\n" + task.marker)
    plan.record.save_snapshot(tmp_path)

    class Repository:
        def analysis_records(self, *_):
            return [stale]

    dialog = ReportDialog("", tmp_path, session=session, repository=Repository())
    try:
        assert dialog.record.status == "complete"
    finally:
        delete(dialog)
        app.processEvents()


def test_input_verification_preserves_significant_whitespace():
    from four_ai_consult.adapters import normalize_input_text

    assert normalize_input_text("return a b") != normalize_input_text("return ab")
    assert normalize_input_text("if ok:\n    go()") != normalize_input_text("if ok:\ngo()")
    assert normalize_input_text("a\r\nb\u00a0c") == "a\nb c"


@qt
def test_repeated_consultation_id_does_not_reuse_watchdog(monkeypatch):
    from types import SimpleNamespace

    from four_ai_consult import webpane

    callbacks = []
    monkeypatch.setattr(webpane, "QTimer", SimpleNamespace(singleShot=lambda _, callback: callbacks.append(callback)))

    class Pane:
        busy = True
        config = SimpleNamespace(response_timeout_seconds=1)
        adapter = SimpleNamespace(snapshot_script=lambda: "snapshot")
        errors = []

        def cancel(self, **_):
            pass

        def _set_state(self, *_):
            pass

        def _run_javascript(self, *_):
            pass

        def _finish_error(self, text):
            self.errors.append(text)

    pane = Pane()
    webpane.WebPane.dispatch(pane, "question", "same-session")
    previous = pane._batch_id
    webpane.WebPane.dispatch(pane, "question", "same-session")
    assert pane._batch_id != previous
    callbacks[0]()
    assert not pane.errors


@qt
@pytest.mark.parametrize("draft", ["question 用户追加内容", "question\n不同的条件"])
def test_submit_fallback_never_sends_modified_user_draft(draft):
    from four_ai_consult.webpane import WebPane

    class Adapter:
        def snapshot_script(self):
            return "snapshot"

        def focus_input_script(self):
            pytest.fail("Modified draft must not be focused for submission")

    class Pane:
        _batch_id = "test"
        _question = "question"
        _baseline_count = 0
        state = PaneState.GENERATING
        adapter = Adapter()

        def _run_javascript(self, script, callback):
            assert script == "snapshot"
            callback({"ok": True, "inputText": draft, "count": 0, "generating": False})

    WebPane._retry_native_submit(Pane(), "test")


def test_one_invalid_analysis_record_does_not_hide_other_provider(tmp_path):
    from four_ai_consult.storage import ConsultationRepository

    repo = ConsultationRepository(tmp_path / "history.sqlite3")
    session = sample()
    repo.save(session, "原文")
    record = AnalysisPlan(session, "免费网页版", "deepseek").record
    repo.save_analysis(record)
    with repo._connect() as connection:
        connection.execute("INSERT INTO analysis_reports VALUES (?, ?, ?, ?, ?)",
                           (session.id, "免费网页版", "kimi", "{broken-json", "2026-08-30"))
    assert [r.provider for r in repo.analysis_records(session.id)] == ["deepseek"]


@qt
def test_history_reopens_the_provider_that_actually_generated_report(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog
    from four_ai_consult.storage import ConsultationRepository

    app = QApplication.instance() or QApplication([])
    session = sample()
    repo = ConsultationRepository(tmp_path / "history.sqlite3")
    repo.save(session, "原文")
    plan = AnalysisPlan(session, "免费网页版", "kimi")
    task = plan.next_task()
    plan.accept("Kimi已生成的比较 [S1-1]\n" + task.marker)
    repo.save_analysis(plan.record)
    dialog = ReportDialog("", tmp_path, session=session, repository=repo)
    try:
        assert dialog.provider.currentData() == "kimi"
        assert dialog.record.status == "complete"
        assert "Kimi已生成的比较" in dialog.viewer.toPlainText()
    finally:
        delete(dialog)
        app.processEvents()


@qt
def test_legacy_report_without_session_is_visible(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog

    app = QApplication.instance() or QApplication([])
    dialog = ReportDialog("原先保存的完整报告", tmp_path)
    try:
        assert "原先保存的完整报告" in dialog.viewer.toPlainText()
    finally:
        delete(dialog)
        app.processEvents()


@qt
def test_unreadable_web_snapshot_is_not_a_blank_chat():
    from four_ai_consult.web_synthesis import WebSynthesisDialog

    class Label:
        def setText(self, text):
            self.text = text

    class Adapter:
        def snapshot_script(self):
            return "snapshot"

        def focus_input_script(self):
            return "focus"

    class Pane:
        adapter = Adapter()
        calls = []

        def _run_javascript(self, script, callback):
            self.calls.append(script)
            if script == "snapshot":
                callback(None)

    class Runner:
        stopped = False
        waiting = True
        running = False
        _navigation_id = 1
        status = Label()
        pane = Pane()

    runner = Runner()
    WebSynthesisDialog._check_empty(runner)
    assert runner.pane.calls == ["snapshot"]


@qt
def test_deleted_history_session_falls_back_to_saved_report(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.storage import HistoryItem
    from four_ai_consult.ui import HistoryDialog

    class Repository:
        def list(self, *_):
            return [HistoryItem("id", "问题", "2026-08-30", "2026-08-30", 1, 1, "旧材料仍可看")]

        def load_session(self, *_):
            return None

        def analysis_records(self, *_):
            return [AnalysisPlan(sample(), "免费网页版", "deepseek").record]

    app = QApplication.instance() or QApplication([])
    dialog = HistoryDialog(Repository(), tmp_path)
    try:
        assert "旧材料仍可看" in dialog._selected_report()
    finally:
        delete(dialog)
        app.processEvents()
