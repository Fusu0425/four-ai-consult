"""Regression: reasoning pauses are not final answers; late originals remain recoverable."""
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from four_ai_consult.analysis_plan import AnalysisPlan, ReportRecord
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
from four_ai_consult.storage import ConsultationRepository

qt = pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt opt-in")


def test_partial_originals_are_visible_but_not_used_as_complete_sources(tmp_path):
    session = ConsultationSession("比较方案", ("zhipu", "deepseek", "kimi"))
    partial = "正在思考中\n保留已采集的每一行\n" + "尚未结束。" * 1000
    session.add_result(AnswerResult("zhipu", "智谱", session.question, PaneState.ERROR,
                                   text=partial, error="等待回答超时"))
    session.add_result(AnswerResult("deepseek", "DeepSeek", session.question, PaneState.ERROR, error="未登录"))
    session.add_result(AnswerResult("kimi", "Kimi", session.question, PaneState.DONE, text="完整回答"))
    repo = ConsultationRepository(tmp_path / "history.sqlite3")
    repo.save(session, "")
    loaded = repo.load_session(session.id)
    record = AnalysisPlan(loaded, "免费网页版", "kimi").record
    assert [s.site_id for s in record.sources] == ["kimi"]
    docs = dict(ReportRecord.from_json(record.to_json()).documents())
    assert docs["原文 · 智谱（未确认完整）"].endswith(partial)
    assert "未登录" in docs["原文 · DeepSeek（未确认完整）"]
    assert partial in record.markdown()
    assert partial not in AnalysisPlan(loaded, "免费网页版", "kimi").next_task().prompt


@pytest.fixture
def capture_pane(tmp_path, monkeypatch):
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult import webpane
    from four_ai_consult.adapters import ADAPTER_BY_ID
    from four_ai_consult.config import AppConfig

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)
    adapter = replace(ADAPTER_BY_ID["zhipu"], home_url="about:blank")
    pane = webpane.WebPane(adapter, profile, AppConfig(response_timeout_seconds=240, stable_poll_count=2),
                           0, 0, lambda _: None)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(webpane, "time", SimpleNamespace(monotonic=lambda: clock.now))
    results = []
    pane.answer_ready.connect(results.append)
    pane._question = "比较方案"
    pane._batch_id = "test"
    pane._started_at = clock.now
    pane._last_change_at = clock.now
    pane._automation_active = True
    pane._set_state(PaneState.GENERATING)
    yield pane, clock, results
    pane.cancel(emit_result=False)
    pane.close()
    delete(pane)
    delete(profile)
    app.processEvents()


def poll_dom(pane, clock, seconds, monkeypatch):
    from test_adapter_javascript import _run_js

    clock.now = 100 + seconds
    snapshot = json.loads(_run_js(pane.page, pane.adapter.snapshot_script()))
    monkeypatch.setattr(pane, "_run_javascript", lambda script, callback: callback(snapshot))
    pane._poll_answer()
    return snapshot


@qt
@pytest.mark.parametrize("provider", ["zhipu", "kimi"])
def test_long_reasoning_pause_streaming_and_final_tail_reach_history_and_report(
    capture_pane, tmp_path, monkeypatch, provider,
):
    from test_adapter_javascript import _run_js, _set_html

    from four_ai_consult.adapters import ADAPTER_BY_ID

    pane, clock, results = capture_pane
    pane.adapter = replace(ADAPTER_BY_ID[provider], home_url="about:blank")
    _set_html(pane.page, '''
      <textarea></textarea>
      <div data-role="assistant" class="segment-assistant"><div class="markdown-body markdown">旧回答</div>
        <button title="重新生成">旧回答结束</button></div>
      <div data-role="assistant" class="segment-assistant" id="current"><div class="markdown-body markdown">用户需要比较方案，我将搜索相关资料。</div></div>
    ''')
    # Even unlabelled, static planning text is not proof of completion.
    for seconds in [1, 5, 15, 30, 60]:
        snapshot = poll_dom(pane, clock, seconds, monkeypatch)
        assert not snapshot["completed"]
        assert pane.state == PaneState.GENERATING
        assert not results
    _run_js(pane.page, '''document.querySelector('#current').innerHTML =
      '<div class="reasoning">思考过程：先核对条件。</div><div class="markdown-body markdown">第一部分</div>';
      const stop=document.createElement('button');stop.id='stop';stop.title='停止生成';document.body.append(stop);''')
    for seconds in [65, 70, 85]:
        snapshot = poll_dom(pane, clock, seconds, monkeypatch)
        assert snapshot["generating"]
        assert not results
    full = "第一部分\n\n" + "具体对比，保留条件与数字。\n" * 1000 + "最后的关键限制：预算 500 元。"
    _run_js(pane.page, "document.querySelector('#current .markdown-body').textContent=" + json.dumps(full))
    _run_js(pane.page, '''document.querySelector('#stop').remove();
      const end=document.createElement('button');end.title='重新生成';document.querySelector('#current').append(end);''')
    for seconds in [86, 88, 90]:
        poll_dom(pane, clock, seconds, monkeypatch)
    assert len(results) == 1
    assert results[0].state == PaneState.DONE
    assert results[0].text.endswith(full)
    assert "思考过程：先核对条件。" in results[0].text
    session = ConsultationSession(pane._question, (provider,))
    session.add_result(results[0])
    repo = ConsultationRepository(tmp_path / "complete.sqlite3")
    repo.save(session, "")
    loaded = repo.load_session(session.id)
    assert loaded.results[provider].text == results[0].text
    assert dict(AnalysisPlan(loaded, "免费网页版", provider).record.documents())[
        f"原文 · {pane.adapter.name}"
    ] == results[0].text


@qt
def test_thinking_only_hidden_old_stop_and_same_length_middle_edits(capture_pane, monkeypatch):
    from test_adapter_javascript import _run_js, _set_html

    pane, clock, results = capture_pane
    _set_html(pane.page, '''<textarea></textarea>
      <div hidden><button aria-label="停止生成">停止生成</button></div>
      <div data-role="assistant"><div class="thinking">正在思考中\n用户的问题需要搜索。</div>
        <button>重新生成</button></div>''')
    for seconds in [1, 5, 15, 45]:
        snapshot = poll_dom(pane, clock, seconds, monkeypatch)
        assert snapshot["reasoningOnly"]
        assert not snapshot["generating"]
        assert not results
    _run_js(pane.page, "document.querySelector('.thinking').className='body'")
    _run_js(pane.page, "document.querySelector('.body').textContent='A'+'x'.repeat(300)")
    first = poll_dom(pane, clock, 46, monkeypatch)
    _run_js(pane.page, "document.querySelector('.body').textContent='B'+'x'.repeat(300)")
    second = poll_dom(pane, clock, 47, monkeypatch)
    assert first["signature"] != second["signature"]
    assert pane._stable_polls == 0
    assert not results


@qt
def test_zhipu_thought_finished_marker_completes_without_labelled_action_button(
    capture_pane, monkeypatch,
):
    """ChatGLM now renders icon-only actions but exposes 思考结束 beside the final body."""
    from test_adapter_javascript import _run_js, _set_html

    pane, clock, results = capture_pane
    _set_html(pane.page, '''<textarea></textarea>
      <div data-message-role="assistant" class="row-answer-0" id="old">
        <div>思考结束</div><div class="markdown-body">旧回答</div></div>
      <div data-message-role="assistant" class="row-answer-1" id="current">
        <div class="reasoning">正在思考中，先查询资料。</div>
        <div class="markdown-body">当前回答第一段。</div>
        <button id="stop" aria-label="停止生成"></button></div>''')
    for seconds in [1, 4, 8]:
        snapshot = poll_dom(pane, clock, seconds, monkeypatch)
        assert not snapshot["completed"]
        assert snapshot["generating"]
        assert not results

    _run_js(pane.page, '''document.querySelector('#stop').remove();
      document.querySelector('#current .reasoning').textContent='思考结束';
      document.querySelector('#current .markdown-body').textContent=
        '当前完整正文，包含最后的限制条件。';
      const icon=document.createElement('button');
      icon.innerHTML='<svg></svg>';document.querySelector('#current').append(icon);''')
    for seconds in [10, 12, 14, 16]:
        snapshot = poll_dom(pane, clock, seconds, monkeypatch)
    assert snapshot["completed"]
    assert not snapshot["generating"]
    assert len(results) == 1
    assert results[0].state == PaneState.DONE
    assert "当前完整正文，包含最后的限制条件。" in results[0].text


@qt
def test_timeout_then_explicit_recapture_does_not_resend(capture_pane, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from test_adapter_javascript import _run_js, _set_html

    pane, clock, results = capture_pane
    _set_html(pane.page, '''<textarea>保留用户草稿</textarea><div data-role="assistant">思考草稿</div>
      <button onclick="window.submissions++">发送</button><script>window.submissions=0</script>''')
    poll_dom(pane, clock, 1, monkeypatch)
    poll_dom(pane, clock, 241, monkeypatch)
    assert results[-1].state == PaneState.ERROR
    assert results[-1].text == "思考草稿"
    assert pane.capture_button.isEnabled()
    assert "补采" in results[-1].error
    _run_js(pane.page, "document.querySelector('[data-role=assistant]').textContent='迟到的完整正文，包括最后的限制条件。'")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    starts = []
    pane.collection_started.connect(starts.append)
    # Supply the new DOM to the first synchronous recapture poll too.
    poll_dom(pane, clock, 242, monkeypatch)
    pane.recapture()
    for seconds in [244, 246]:
        poll_dom(pane, clock, seconds, monkeypatch)
    assert starts == ["zhipu"]
    assert len(results) == 2
    assert results[-1].state == PaneState.DONE
    assert results[-1].text == "迟到的完整正文，包括最后的限制条件。"
    assert _run_js(pane.page, "window.submissions") == 0
    assert _run_js(pane.page, "document.querySelector('textarea').value") == "保留用户草稿"


@qt
def test_open_report_refreshes_and_defers_material_change_during_synthesis(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog

    app = QApplication.instance() or QApplication([])
    session = ConsultationSession("比较方案", ("zhipu",))
    session.add_result(AnswerResult("zhipu", "智谱", session.question, PaneState.ERROR, text="思考草稿"))
    dialog = ReportDialog("", tmp_path, session=session)
    try:
        assert "思考草稿" in dialog.record.markdown()
        session.add_result(AnswerResult("zhipu", "智谱", session.question, PaneState.DONE, text="完整正文"))
        dialog.update_session(session, "新报告")
        assert dict(dialog.record.documents())["原文 · 智谱"] == "完整正文"
        assert not dialog.record.unconfirmed
        assert dialog.generate.isEnabled()
        dialog._set_busy(True)
        session.results["zhipu"].text = "补充了末尾的限制条件"
        dialog.update_session(session, "更新报告")
        assert dialog.session.results["zhipu"].text == "完整正文"
        assert dialog._pending_material
        dialog._finished()
        assert dict(dialog.record.documents())["原文 · 智谱"] == "补充了末尾的限制条件"
        assert dialog.record.status == "pending"
        assert dialog._pending_material is None
    finally:
        delete(dialog)
        app.processEvents()


@qt
def test_real_timer_does_not_finish_during_sixteen_second_thinking_pause(tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from test_adapter_javascript import _set_html

    from four_ai_consult.adapters import ADAPTER_BY_ID
    from four_ai_consult.config import AppConfig
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)
    adapter = replace(ADAPTER_BY_ID["zhipu"], home_url="about:blank", native_input=False,
                      send_selectors=(".send",))
    pane = WebPane(adapter, profile, AppConfig(poll_interval_ms=100, stable_poll_count=3,
                                              response_timeout_seconds=25), 0, 0, lambda _: None)
    results, midway = [], []
    loop = QEventLoop()
    try:
        _set_html(pane.page, '''<textarea></textarea><button class="send" onclick="
          document.querySelector('textarea').value='';
          const answer=document.createElement('div');answer.dataset.role='assistant';
          answer.textContent='我将先搜索资料，并比较不同的方案。';document.body.append(answer);
          setTimeout(() => {
            answer.textContent='正式回答第一段。';
            setTimeout(() => {
              answer.textContent+='最后一段：保留全部限制条件。';
              const end=document.createElement('button');end.title='重新生成';answer.append(end);
            }, 500);
          }, 16000);
        ">发送</button>''')
        pane.answer_ready.connect(lambda result: (results.append(result), loop.quit()))
        pane.dispatch("比较方案", "real-timer")
        QTimer.singleShot(12000, lambda: midway.append((pane.state, len(results))))
        QTimer.singleShot(28000, loop.quit)
        loop.exec()
        assert midway == [(PaneState.GENERATING, 0)]
        assert len(results) == 1 and results[0].state == PaneState.DONE
        assert results[0].text == "正式回答第一段。最后一段：保留全部限制条件。"
        assert results[0].elapsed_seconds >= 16
    finally:
        pane.cancel(emit_result=False)
        pane.close()
        delete(pane)
        delete(profile)
        app.processEvents()
