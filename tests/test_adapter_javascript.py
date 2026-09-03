from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_QT_WEBENGINE_TESTS") != "1",
    reason="Set RUN_QT_WEBENGINE_TESTS=1 to run Qt WebEngine integration tests.",
)


HTML_BY_SITE = {
    "deepseek": """
        <textarea placeholder="给 DeepSeek 发送消息"></textarea>
        <button class="ds-button--primary" onclick="this.dataset.clicked='1'">发送</button>
        <div class="ds-markdown">DeepSeek fixture answer</div>
    """,
    "kimi": """
        <div class="chat-input-editor" contenteditable="true"></div>
        <button class="send-button" onclick="this.dataset.clicked='1'">发送</button>
        <div class="segment-assistant"><div class="markdown">Kimi fixture answer</div></div>
    """,
    "doubao": """
        <div class="tiptap ProseMirror" contenteditable="true"></div>
        <button onclick="this.dataset.clicked='1'"><svg></svg></button>
        <div class="my-0 w-full mx-auto max-w-(--content-max-width)">
          <div class="bg-g-send-msg-bubble-bg">fixture question</div>
        </div>
        <div class="my-0 w-full mx-auto max-w-(--content-max-width)">
          <div>Doubao fixture answer</div>
          <div class="suggest-message-list-wrapper-fixture">unrelated suggestion</div>
        </div>
    """,
    "qwen": """
        <div contenteditable="true" data-placeholder="输入问题"></div>
        <button type="submit" onclick="this.dataset.clicked='1'">发送</button>
        <div class="message-list-fixture">
          <div class="message-select-wrapper-question-fixture">fixture question</div>
          <div class="message-select-wrapper-answer-fixture">
            <div class="markdown-pc-special-class">
              <div id="qk-markdown-react" class="qk-markdown">Qwen fixture answer</div>
            </div>
          </div>
        </div>
    """,
    "yuanbao": """
        <div id="search-bar"><div class="ql-editor" contenteditable="true" data-placeholder="输入内容"></div></div>
        <button id="yuanbao-send-btn" aria-label="发送" onclick="this.dataset.clicked='1'">发送</button>
        <div data-role="assistant"><div class="hyc-content-text">Yuanbao fixture answer</div></div>
    """,
    "zhipu": """
        <div class="ProseMirror" contenteditable="true" data-placeholder="输入问题"></div>
        <button type="submit" aria-label="发送" onclick="this.dataset.clicked='1'">发送</button>
        <div data-message-role="assistant"><div class="markdown-body">Zhipu fixture answer</div></div>
    """,
}


def _run_js(page, script):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    result = []

    def done(value):
        result.append(value)
        loop.quit()

    page.runJavaScript(script, 0, done)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert result, "JavaScript callback timed out"
    return result[0]


def _set_html(page, html):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    page.loadFinished.connect(loop.quit)
    page.setHtml(f"<html><body>{html}</body></html>")
    QTimer.singleShot(5000, loop.quit)
    loop.exec()


def test_all_site_adapters_can_send_and_extract_fixture_answers(tmp_path) -> None:
    from PySide6.QtTest import QTest
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SITE_ADAPTERS

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("adapter-tests", app)
    profile.setPersistentStoragePath(str(tmp_path / "profile"))
    page = QWebEnginePage(profile)

    for adapter in SITE_ADAPTERS:
        _set_html(page, HTML_BY_SITE[adapter.id])
        sent = json.loads(_run_js(page, adapter.send_script("fixture question")))
        assert sent["ok"], (adapter.id, sent)
        QTest.qWait(180)
        editor_text = _run_js(
            page,
            "(() => { const e=document.querySelector('textarea,[contenteditable=true]'); "
            "return (e.value ?? e.innerText ?? e.textContent ?? '').trim(); })()",
        )
        assert editor_text == "fixture question", (adapter.id, editor_text)
        assert _run_js(page, "document.querySelector('button').dataset.clicked || ''") == "1"
        snapshot = json.loads(_run_js(page, adapter.snapshot_script()))
        assert snapshot["ok"]
        assert "fixture answer" in snapshot["text"]
        if adapter.id == "doubao":
            assert "unrelated suggestion" not in snapshot["text"]
        if adapter.id == "qwen":
            assert snapshot["text"] == "Qwen fixture answer"

    delete(page)
    delete(profile)
    app.processEvents()


def test_webpane_waits_for_a_stable_new_answer(tmp_path) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter
    from four_ai_consult.config import AppConfig
    from four_ai_consult.models import PaneState
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("webpane-tests", app)
    profile.setPersistentStoragePath(str(tmp_path / "webpane-profile"))
    adapter = SiteAdapter(
        id="fixture",
        name="Fixture",
        home_url="about:blank",
        input_selectors=("textarea",),
        send_selectors=("button.send",),
        assistant_selectors=(".answer",),
        stop_selectors=(".stop",),
    )
    pane = WebPane(
        adapter,
        profile,
        AppConfig(poll_interval_ms=40, response_timeout_seconds=3, stable_poll_count=2),
        col=0,
        row=0,
        on_fullscreen=lambda _: None,
    )
    html = """
        <textarea></textarea>
        <button class="send" onclick="
          const stop = document.createElement('div');
          stop.className = 'stop';
          document.body.appendChild(stop);
          window.setTimeout(() => {
            const answer = document.createElement('div');
            answer.className = 'answer';
            answer.innerText = 'state machine fixture answer';
            document.body.appendChild(answer);
            stop.remove();
          }, 120);
        ">Send</button>
    """
    _set_html(pane.page, html)
    loop = QEventLoop()
    results = []
    pane.answer_ready.connect(lambda result: (results.append(result), loop.quit()))
    pane.dispatch("fixture question", "fixture-batch")
    QTimer.singleShot(5000, loop.quit)
    loop.exec()

    assert results
    assert results[0].state == PaneState.DONE
    assert results[0].text == "state machine fixture answer"
    delete(pane)
    delete(profile)
    app.processEvents()


def test_webpane_uses_native_enter_when_script_click_is_ignored(tmp_path) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter
    from four_ai_consult.config import AppConfig
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("native-submit-tests", app)
    profile.setPersistentStoragePath(str(tmp_path / "native-submit-profile"))
    adapter = SiteAdapter(
        id="native-fixture",
        name="Native fixture",
        home_url="about:blank",
        input_selectors=(".editor",),
        send_selectors=(".send-button-container",),
        assistant_selectors=(".answer",),
        stop_selectors=(".stop",),
    )
    pane = WebPane(
        adapter,
        profile,
        AppConfig(poll_interval_ms=50, response_timeout_seconds=5, stable_poll_count=1),
        col=0,
        row=0,
        on_fullscreen=lambda _: None,
    )
    pane.resize(800, 600)
    pane.show()
    html = """
        <div class="editor" contenteditable="true"></div>
        <div class="send-button-container"></div>
        <script>
          document.querySelector('.editor').addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || !event.isTrusted) return;
            event.preventDefault();
            const answer = document.createElement('div');
            answer.className = 'answer';
            answer.innerText = 'native fallback answer';
            document.body.appendChild(answer);
          });
        </script>
    """
    _set_html(pane.page, html)
    loop = QEventLoop()
    results = []
    pane.answer_ready.connect(lambda result: (results.append(result), loop.quit()))
    pane.dispatch("native fallback question", "native-fallback-batch")
    QTimer.singleShot(7000, loop.quit)
    loop.exec()

    assert results
    assert results[0].text == "native fallback answer"
    pane.close()
    delete(pane)
    delete(profile)
    app.processEvents()


@pytest.mark.parametrize("question", ["原生输入测试", ("中文 abc 123\n" * 800) + "尾部条件保留"], ids=["short", "long"])
def test_webpane_can_type_with_trusted_events_for_controlled_editors(tmp_path, question) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter
    from four_ai_consult.config import AppConfig
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("trusted-input-tests", app)
    profile.setPersistentStoragePath(str(tmp_path / "trusted-input-profile"))
    adapter = SiteAdapter(
        id="controlled-fixture",
        name="Controlled fixture",
        home_url="about:blank",
        input_selectors=(".editor",),
        send_selectors=(".send",),
        assistant_selectors=(".answer",),
        stop_selectors=(".stop",),
        native_input=True,
    )
    pane = WebPane(
        adapter,
        profile,
        AppConfig(poll_interval_ms=50, response_timeout_seconds=20, stable_poll_count=1),
        col=0,
        row=0,
        on_fullscreen=lambda _: None,
    )
    pane.resize(800, 600)
    pane.show()
    html = """
        <div class="editor" contenteditable="true"></div>
        <button class="send" disabled></button>
        <script>
          let receivedTrustedInput = false;
          const editor = document.querySelector('.editor');
          editor.addEventListener('input', (event) => {
            if (event.isTrusted) receivedTrustedInput = true;
          });
          editor.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || event.shiftKey || !event.isTrusted || !receivedTrustedInput) return;
            event.preventDefault();
            const answer = document.createElement('div');
            answer.className = 'answer';
            answer.innerText = editor.innerText === window.expectedQuestion ? 'trusted input answer' : 'wrong input';
            document.body.appendChild(answer);
          });
        </script>
    """
    _set_html(pane.page, html)
    _run_js(pane.page, "window.expectedQuestion=" + json.dumps(question))
    loop = QEventLoop()
    results = []
    pane.answer_ready.connect(lambda result: (results.append(result), loop.quit()))
    pane.dispatch(question, "trusted-input-batch")
    QTimer.singleShot(22000, loop.quit)
    loop.exec()

    assert results
    assert results[0].text == "trusted input answer"
    pane.close()
    delete(pane)
    delete(profile)
    app.processEvents()


def test_webpane_can_reuse_a_seat_for_a_backup_provider(tmp_path) -> None:
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter
    from four_ai_consult.config import AppConfig
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("seat-switch-tests", app)
    profile.setPersistentStoragePath(str(tmp_path / "seat-switch-profile"))
    primary = SiteAdapter("primary", "Primary", "about:blank", ("textarea",), ("button",), (".answer",), ())
    backup = SiteAdapter("backup", "Backup", "about:blank", ("textarea",), ("button",), (".answer",), ())
    pane = WebPane(primary, profile, AppConfig(), 0, 0, lambda _: None)

    pane.set_adapter(backup)

    assert pane.adapter.id == "backup"
    assert pane.title_label.text() == "Backup"
    assert not pane.busy
    delete(pane)
    delete(profile)
    app.processEvents()


def test_nested_answer_capture_keeps_all_sections_and_citations(tmp_path):
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("nested-test", app)
    page = QWebEnginePage(profile)
    adapter = SiteAdapter("nested", "Nested", "about:blank", ("textarea",), ("button",),
                          (".answer", ".markdown"), ())
    _set_html(page, '<div class="answer"><div class="markdown"><h2>第一部分观点</h2>'
                   '<ol><li>执行步骤</li><li>确认条件</li></ol><table><tr><th>列名</th></tr>'
                   '<tr><td>重要数字99</td></tr></table></div>'
                   '<div class="markdown"><p>最后的限制<span class="citation">来源编号</span></p></div></div>')
    snapshot = json.loads(_run_js(page, adapter.snapshot_script()))
    assert "第一部分观点" in snapshot["text"]
    assert "最后的限制" in snapshot["text"]
    assert "来源编号" in snapshot["text"]
    assert "## 第一部分观点" in snapshot["text"]
    assert "2. 确认条件" in snapshot["text"]
    assert "| 重要数字99 |" in snapshot["text"]
    assert snapshot["count"] == 1
    delete(page)
    delete(profile)
    app.processEvents()


@pytest.mark.parametrize("mutation,question", [
    ("this.value.slice(0,4)", "完整材料与最后的必要条件"),
    ("this.value.replaceAll(' ', '')", "return a b"),
], ids=["truncated", "whitespace-lost"])
def test_site_input_truncation_blocks_submission(tmp_path, mutation, question):
    from PySide6.QtTest import QTest
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SiteAdapter

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile("input-limit-test", app)
    page = QWebEnginePage(profile)
    adapter = SiteAdapter("limit", "Limit", "about:blank", ("textarea",), ("button",), (".answer",), ())
    _set_html(page, f'<textarea oninput="this.value={mutation}"></textarea>'
                   '<button onclick="this.dataset.clicked=1">Send</button>')
    result = json.loads(_run_js(page, adapter.send_script(question)))
    assert not result["ok"]
    assert result["code"] == "INPUT_TRUNCATED"
    QTest.qWait(300)
    assert _run_js(page, "document.querySelector('button').dataset.clicked || ''") == ""
    delete(page)
    delete(profile)
    app.processEvents()


def test_kimi_does_not_capture_user_messages_as_answers(tmp_path):
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SITE_ADAPTERS

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)
    page = QWebEnginePage(profile)
    adapter = next(a for a in SITE_ADAPTERS if a.id == "kimi")
    try:
        _set_html(page, '<div class="message-list"><div class="chat-content-item-user">'
                       '新问题不是答案</div></div>')
        snapshot = json.loads(_run_js(page, adapter.snapshot_script()))
        assert snapshot["count"] == 0
        assert snapshot["text"] == ""
        _run_js(page, "document.querySelector('.message-list').insertAdjacentHTML('afterbegin', "
                "'<div class=\"segment-assistant\"><div class=\"markdown\">旧回答</div></div>')")
        snapshot = json.loads(_run_js(page, adapter.snapshot_script()))
        assert snapshot["count"] == 1
        assert snapshot["text"] == "旧回答"
    finally:
        delete(page)
        delete(profile)
        app.processEvents()


@pytest.mark.parametrize("question,truncate", [
    ("回复我：buOK", False),
    (("中文 abc 123\n" * 800) + "最后的必要条件", False),
    ("完整问题不可截断", True),
], ids=["short", "long", "truncation-blocked"])
def test_kimi_dispatch_uses_verified_native_input(tmp_path, question, truncate):
    """Exercise the production Kimi adapter against an incompatible execCommand editor."""
    from dataclasses import replace

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SITE_ADAPTERS
    from four_ai_consult.config import AppConfig
    from four_ai_consult.models import PaneState
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)
    adapter = replace(next(a for a in SITE_ADAPTERS if a.id == "kimi"), home_url="about:blank")
    pane = WebPane(adapter, profile,
                   AppConfig(poll_interval_ms=50, response_timeout_seconds=20, stable_poll_count=1),
                   0, 0, lambda _: None)
    pane.resize(800, 600)
    pane.show()
    try:
        _set_html(pane.page, """
          <div class="chat-input-editor" role="textbox" contenteditable="true"></div>
          <button class="send-button" disabled></button>
          <div class="message-list"></div>
          <script>
            const editor = document.querySelector('.chat-input-editor');
            const originalExec = document.execCommand.bind(document);
            // Model a controlled editor that duplicates bulk insertText input.
            document.execCommand = (command, ui, value) => {
              const result = originalExec(command, ui, value);
              if (command === 'insertText') editor.textContent += value;
              return result;
            };
            window.submissions = 0;
            window.trustedInput = false;
            editor.addEventListener('input', event => {
              if (event.isTrusted) window.trustedInput = true;
              if (window.truncate) editor.textContent = editor.textContent.slice(0, 4);
            });
            editor.addEventListener('keydown', event => {
              if (event.key !== 'Enter' || event.shiftKey) return;
              event.preventDefault();
              if (!event.isTrusted || !window.trustedInput) return;
              window.submissions++;
              window.submitted = editor.innerText;
              document.querySelector('.message-list').innerHTML =
                '<div class="segment-assistant"><div class="markdown">完整回答</div><button>重新生成</button></div>';
              editor.textContent = '';
            });
          </script>
        """)
        _run_js(pane.page, "window.truncate=" + json.dumps(truncate))
        results = []
        loop = QEventLoop()
        pane.answer_ready.connect(lambda result: (results.append(result), loop.quit()))
        pane.dispatch(question, "kimi-regression")
        QTimer.singleShot(22000, loop.quit)
        loop.exec()
        assert len(results) == 1
        if truncate:
            assert results[0].state == PaneState.ERROR
            assert _run_js(pane.page, "window.submissions") == 0
        else:
            assert results[0].state == PaneState.DONE, results[0].error
            assert results[0].text == "完整回答"
            assert _run_js(pane.page, "window.submitted") == question
            assert _run_js(pane.page, "window.submissions") == 1
    finally:
        pane.close()
        delete(pane)
        delete(profile)
        app.processEvents()


def test_kimi_and_qwen_native_input_do_not_mix_between_panes(tmp_path):
    from dataclasses import replace

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.adapters import SITE_ADAPTERS
    from four_ai_consult.config import AppConfig
    from four_ai_consult.models import PaneState
    from four_ai_consult.webpane import WebPane

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)
    panes = []
    results = []
    questions = {"kimi": "Kimi 必须完整保留这段独立的问题。" * 8,
                 "qwen": "千问不能混入其他窗口的输入。" * 8}
    loop = QEventLoop()

    def done(result):
        results.append(result)
        if len(results) == 2:
            loop.quit()

    try:
        for site_id in questions:
            adapter = replace(next(a for a in SITE_ADAPTERS if a.id == site_id),
                              home_url="about:blank", assistant_selectors=(".answer",))
            pane = WebPane(adapter, profile,
                           AppConfig(poll_interval_ms=50, response_timeout_seconds=10, stable_poll_count=1),
                           0, 0, lambda _: None)
            panes.append(pane)
            pane.resize(800, 600)
            pane.show()
            _set_html(pane.page, """
              <div class="chat-input-editor" contenteditable="true" data-placeholder="输入问题"></div>
              <script>
                const editor = document.querySelector('[contenteditable]');
                window.submissions = 0;
                editor.addEventListener('keydown', event => {
                  if (event.key !== 'Enter' || event.shiftKey || !event.isTrusted) return;
                  event.preventDefault();
                  window.submissions++;
                  const answer = document.createElement('div');
                  answer.className = 'answer';
                  answer.textContent = editor.innerText;
                  const end = document.createElement('button');
                  end.setAttribute('aria-label', '重新生成');
                  answer.appendChild(end);
                  document.body.appendChild(answer);
                  editor.textContent = '';
                });
              </script>
            """)
            pane.answer_ready.connect(done)
        for pane in panes:
            pane.dispatch(questions[pane.adapter.id], "parallel-native-test")
        QTimer.singleShot(12000, loop.quit)
        loop.exec()
        assert len(results) == 2
        for result in results:
            assert result.state == PaneState.DONE, result.error
            assert result.text == questions[result.site_id]
        for pane in panes:
            assert _run_js(pane.page, "window.submissions") == 1
    finally:
        for pane in panes:
            pane.close()
            delete(pane)
        delete(profile)
        app.processEvents()
