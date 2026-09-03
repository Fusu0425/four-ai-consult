import os

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt integration opt-in")


def test_report_pages_and_source_navigation_preserve_long_material(tmp_path):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextCharFormat
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.analysis_plan import AnalysisPlan
    from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
    from four_ai_consult.report_ui import ReportDialog

    app = QApplication.instance() or QApplication([])
    s = ConsultationSession("测试对比", ("deepseek",))
    s.add_result(AnswerResult("deepseek", "DeepSeek", s.question, PaneState.DONE, text="长原文。" * 6000 + "结尾条件"))
    dialog = ReportDialog("材料", tmp_path, session=s)
    assert len(dialog._pages) == 2  # one continuous full answer, not six source fragments
    assert dialog.sections.currentIndex() == 1
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    plan.record.notes = ["详细观点 [S1-1] " * 1000]
    dialog._checkpoint(plan.record.to_json())
    assert any("结尾条件" in text for _, text, _ in dialog._pages)
    detail_pages = [text for title, text, _ in dialog._pages if title.startswith("详析")]
    assert "".join(detail_pages) == plan.record.notes[0]
    anchor_style = QTextCharFormat()
    anchor_style.setAnchor(True)
    anchor_style.setAnchorHref("source:S1-1")
    dialog.viewer.setCurrentCharFormat(anchor_style)
    dialog._source_clicked(QUrl("source:S1-1"))
    assert dialog.navigation.currentRow() == dialog._source_pages["S1-1"]
    assert "长原文" in dialog.viewer.toPlainText()
    assert not dialog.viewer.document().begin().begin().fragment().charFormat().isAnchor()
    s.results["deepseek"].text = "用户后续修改"
    assert "用户后续修改" not in dialog.session.results["deepseek"].text
    delete(dialog)
    app.processEvents()


def test_api_pipeline_checkpoints_and_stops_without_sending_next_request(monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from four_ai_consult import synthesis
    from four_ai_consult.analysis_plan import AnalysisPlan
    from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState

    app = QApplication.instance() or QApplication([])
    s = ConsultationSession("测试取消", ("deepseek",))
    s.add_result(AnswerResult("deepseek", "DeepSeek", s.question, PaneState.DONE, text="完整观点"))
    plan = AnalysisPlan(s, "API 加强版", "mock")
    client = synthesis.SynthesisClient()
    calls = []

    def response(key, prompt, model):
        calls.append(prompt)
        client.cancel()
        return "观点 [S1-1]\n" + plan.pending.marker

    monkeypatch.setattr(synthesis, "request_completion", response)
    loop = QEventLoop()
    client.finished.connect(loop.quit)
    client.ask("fake-key", plan)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert len(calls) == 1
    assert plan.record.status == "cancelled"
    assert not plan.record.notes
    app.processEvents()


@pytest.mark.parametrize("format_retry", [False, True], ids=["direct", "format-repair"])
def test_free_browser_pipeline_end_to_end_with_local_site(tmp_path, format_retry):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter
    from four_ai_consult.analysis_plan import AnalysisPlan
    from four_ai_consult.config import AppConfig
    from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
    from four_ai_consult.web_synthesis import WebSynthesisDialog

    html = b"""<html><body><textarea></textarea><button onclick="
      const prompt=document.querySelector('textarea').value;
      const marker=prompt.match(/FOURAI_DONE_[a-f0-9]+/g).at(-1);
      const ids=[...new Set(prompt.match(/S[0-9]+-[0-9]+/g))];
      const answer=document.createElement('div'); answer.className='answer';
      let citations=ids.map(x=>'['+x+']').join(' ');
      if (INJECT_BAD && localStorage.getItem('badReturned') !== '1') {
        citations='[S1-1]'; localStorage.setItem('badReturned','1');
      }
      answer.innerText='Detailed positions and conditions '+citations+ '\\n**'+marker+'**';
      document.body.appendChild(answer); document.querySelector('textarea').value='';
    ">Send</button></body></html>""".replace(b"INJECT_BAD", b"true" if format_retry else b"false")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("report-flow-test", app)
    profile.setPersistentStoragePath(str(tmp_path / "profile"))
    s = ConsultationSession("对比问题", ("deepseek", "kimi"))
    s.add_result(AnswerResult("deepseek", "DeepSeek", s.question, PaneState.DONE, text="原始完整观点与限制"))
    s.add_result(AnswerResult("kimi", "Kimi", s.question, PaneState.DONE, text="另一立场及相反条件"))
    plan = AnalysisPlan(s, "免费网页版", "fixture")
    adapter = SiteAdapter(
        "fixture", "Fixture", f"http://127.0.0.1:{server.server_port}", ("textarea",), ("button",), (".answer",), ()
    )
    runner = WebSynthesisDialog(plan, adapter, profile, AppConfig(poll_interval_ms=40))
    loop = QEventLoop()
    records = []
    runner.checkpoint.connect(records.append)
    runner.completed.connect(loop.quit)
    runner.show()
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    try:
        assert plan.record.status == "complete", plan.record.error
        assert plan.record.direct
        assert not plan.record.notes
        assert "[S1-1]" in plan.record.conclusion
        assert "原始完整观点与限制" in plan.record.markdown()
        assert len(records) >= 1
        if format_retry:
            assert any('partial_output' in payload and 'Detailed positions' in payload for payload in records)
            assert len(records) >= 3
    finally:
        runner.reject()
        delete(runner)
        delete(profile)
        server.shutdown()
        server.server_close()
        app.processEvents()


def test_api_pipeline_finishes_both_stages_with_mock_transport(monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from four_ai_consult import synthesis
    from four_ai_consult.analysis_plan import AnalysisPlan
    from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState

    app = QApplication.instance() or QApplication([])
    s = ConsultationSession("测试 API 两阶段", ("deepseek",))
    s.add_result(AnswerResult("deepseek", "DeepSeek", s.question, PaneState.DONE, text="原文末尾不可丢失"))
    plan = AnalysisPlan(s, "API 加强版", "mock")
    client = synthesis.SynthesisClient()
    prompts = []

    def response(key, prompt, model):
        prompts.append(prompt)
        return "条件完整保留 [S1-1]\n" + plan.pending.marker

    monkeypatch.setattr(synthesis, "request_completion", response)
    loop = QEventLoop()
    client.finished.connect(loop.quit)
    client.ask("fake-key", plan)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert len(prompts) == 1
    assert "原文末尾不可丢失" in prompts[0]
    assert "条件完整保留" in plan.record.conclusion
    assert plan.record.status == "complete"
    assert not client.running
    app.processEvents()
