from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QInputMethodEvent, QKeyEvent
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .adapters import SiteAdapter, normalize_input_text
from .config import AppConfig
from .models import AnswerResult, PaneState

logger = logging.getLogger("four_ai_consult")

STATE_COLORS = {
    PaneState.LOADING: "#8A7A69",
    PaneState.READY: "#4F718C",
    PaneState.SENDING: "#A66C25",
    PaneState.GENERATING: "#805D86",
    PaneState.DONE: "#4E7A62",
    PaneState.ERROR: "#B34D4D",
    PaneState.CANCELLED: "#8A7A69",
}


class EmbeddedWebView(QWebEngineView):
    zoom_requested = Signal(float)

    def createWindow(self, window_type):  # noqa: N802 - Qt API name
        # OAuth/login flows often request a new window. Keeping the navigation in
        # this pane preserves the user's session and avoids an unmanaged popup.
        return self

    def wheelEvent(self, event):  # noqa: N802 - Qt API name
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_requested.emit(1.15 if delta > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)


class WebPane(QWidget):
    state_changed = Signal(str, object)
    answer_ready = Signal(object)
    collection_started = Signal(str)
    diagnostic_ready = Signal(str, str)

    def __init__(
        self,
        adapter: SiteAdapter,
        profile: QWebEngineProfile,
        config: AppConfig,
        col: int,
        row: int,
        on_fullscreen: Callable[[WebPane], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.adapter = adapter
        self.config = config
        self.col = col
        self.row = row
        self.on_fullscreen = on_fullscreen

        self.state = PaneState.LOADING
        self.zoom = 1.0
        self.manual_zoom = False
        self._batch_id = ""
        self._question = ""
        self._started_at = 0.0
        self._baseline_signature = ""
        self._baseline_count = 0
        self._last_signature = ""
        self._last_text = ""
        self._stable_polls = 0
        self.completion_marker = ""
        self.require_empty_input = False
        self._last_change_at = 0.0
        self._automation_active = False
        self._last_snapshot: dict[str, object] = {}
        self._manual_capture = False
        self._capture_url = ""

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(config.poll_interval_ms)
        self.poll_timer.timeout.connect(self._poll_answer)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar_widget = QWidget()
        bar_widget.setObjectName("paneBar")
        bar = QHBoxLayout(bar_widget)
        bar.setContentsMargins(7, 4, 7, 4)
        bar.setSpacing(5)

        self.status_dot = QLabel("●")
        self.title_label = QLabel(adapter.name)
        self.title_label.setObjectName("paneTitle")
        self.status_label = QLabel(PaneState.LOADING.label)
        self.status_label.setStyleSheet("color:#8A7A69;")
        self.refresh_button = QPushButton("刷新")
        self.retry_button = QPushButton("重试")
        self.capture_button = QPushButton("补采")
        self.capture_button.setFixedWidth(44)
        self.capture_button.setToolTip("网页回答已完成但报告缺失？补采当前原文，不重新发送问题。")
        self.capture_button.clicked.connect(self.recapture)
        self.capture_button.setEnabled(False)
        self.full_button = QPushButton("全屏")
        self.more_button = QPushButton("•••")
        for button in (self.refresh_button, self.retry_button, self.full_button):
            button.setFixedWidth(44)
        self.more_button.setFixedWidth(34)
        self.more_button.setToolTip("更多面板操作")
        self.retry_button.hide()

        pane_menu = QMenu(self.more_button)
        home_action = pane_menu.addAction("返回模型主页")
        external_action = pane_menu.addAction("在外部浏览器打开")
        pane_menu.addSeparator()
        minus_action = pane_menu.addAction("缩小网页")
        plus_action = pane_menu.addAction("放大网页")
        reset_action = pane_menu.addAction("重置网页缩放")
        self.zoom_status_action = pane_menu.addAction("当前缩放：100%")
        self.zoom_status_action.setEnabled(False)
        self.more_button.setMenu(pane_menu)

        bar.addWidget(self.status_dot)
        bar.addWidget(self.title_label)
        bar.addWidget(self.status_label)
        bar.addStretch(1)
        bar.addWidget(self.refresh_button)
        bar.addWidget(self.retry_button)
        bar.addWidget(self.capture_button)
        bar.addWidget(self.full_button)
        bar.addWidget(self.more_button)

        self.view = EmbeddedWebView()
        self.page = QWebEnginePage(profile, self.view)
        self.view.setPage(self.page)

        self.refresh_button.clicked.connect(self.view.reload)
        self.retry_button.clicked.connect(self.retry)
        self.full_button.clicked.connect(lambda: self.on_fullscreen(self))
        home_action.triggered.connect(self.go_home)
        external_action.triggered.connect(self.open_external)
        minus_action.triggered.connect(lambda: self._change_zoom(1 / 1.15))
        plus_action.triggered.connect(lambda: self._change_zoom(1.15))
        reset_action.triggered.connect(self.reset_zoom)
        self.view.zoom_requested.connect(self._change_zoom)
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadFinished.connect(self._on_load_finished)

        root.addWidget(bar_widget)
        root.addWidget(self.view, 1)
        self._set_state(PaneState.LOADING)
        self.view.setUrl(QUrl(adapter.home_url))

    @property
    def busy(self) -> bool:
        return self.state in {PaneState.SENDING, PaneState.GENERATING}

    def set_adapter(self, adapter: SiteAdapter) -> None:
        """Reuse this visual seat for another provider without loading six browsers."""
        if adapter.id == self.adapter.id:
            return
        self.cancel(emit_result=False)
        self.adapter = adapter
        self._batch_id = ""
        self._question = ""
        self._started_at = 0.0
        self._baseline_signature = ""
        self._baseline_count = 0
        self._last_signature = ""
        self._last_text = ""
        self._stable_polls = 0
        self._automation_active = False
        self._last_snapshot = {}
        self.title_label.setText(adapter.name)
        self.retry_button.hide()
        self._set_state(PaneState.LOADING)
        self.view.setUrl(QUrl(adapter.home_url))

    def _set_state(self, state: PaneState, detail: str = "") -> None:
        self.state = state
        color = STATE_COLORS[state]
        self.status_dot.setStyleSheet(f"color:{color};")
        self.status_label.setText(detail or state.label)
        self.status_label.setStyleSheet(f"color:{color};")
        self.status_label.setToolTip(detail if state == PaneState.ERROR else "")
        self.retry_button.setVisible(state == PaneState.ERROR and bool(self._question))
        self.capture_button.setEnabled(bool(self._question) and state not in {PaneState.SENDING, PaneState.LOADING})
        self.state_changed.emit(self.adapter.id, state)

    def _run_javascript(self, script: str, callback: Callable[[object], None]) -> None:
        def decode(raw: object) -> None:
            if isinstance(raw, str):
                try:
                    callback(json.loads(raw))
                    return
                except json.JSONDecodeError:
                    pass
            callback(raw)

        self.page.runJavaScript(script, 0, decode)

    def dispatch(self, question: str, batch_id: str) -> None:
        self.cancel(emit_result=False)
        self._automation_active = True
        # The same consultation ID may be retried. Every dispatch still needs
        # its own token so an old watchdog/callback cannot affect the new run.
        self._batch_id = uuid4().hex
        self._question = question
        self._started_at = time.monotonic()
        self._baseline_signature = ""
        self._baseline_count = 0
        self._last_signature = ""
        self._last_text = ""
        self._stable_polls = 0
        self._manual_capture = False
        self._capture_url = ""
        self._last_change_at = self._started_at
        self._set_state(PaneState.SENDING)
        active_batch = self._batch_id

        def watchdog() -> None:
            if self._batch_id == active_batch and self.busy:
                self._finish_error(self._timeout_message())

        QTimer.singleShot(self.config.response_timeout_seconds * 1000, watchdog)

        def after_snapshot(raw: object) -> None:
            if active_batch != self._batch_id:
                return
            data = raw if isinstance(raw, dict) else {}
            if not data.get("ok"):
                self._finish_error("无法读取网页状态，已阻止发送；请等待加载完成后重试。")
                return
            if self.require_empty_input and (data.get("count") or data.get("generating") or
                                             str(data.get("inputText", "")).strip()):
                self._finish_error("会话状态已变化，已阻止发送；请新建空白聊天，不会覆盖已有草稿。")
                return
            self._baseline_signature = str(data.get("signature", ""))
            self._baseline_count = int(data.get("count", 0) or 0)
            logger.info(
                "%s baseline: url=%s count=%s generating=%s",
                self.adapter.id,
                self.page.url().toString(),
                self._baseline_count,
                bool(data.get("generating")),
            )
            if self.adapter.native_input:
                self._send_with_native_input(question, active_batch, after_send)
            else:
                self._run_javascript(self.adapter.send_script(question), after_send)

        def after_send(raw: object) -> None:
            if active_batch != self._batch_id:
                return
            data = raw if isinstance(raw, dict) else {}
            logger.info("%s send result: %s", self.adapter.id, data)
            if not data.get("ok"):
                self._finish_error(str(data.get("detail") or data.get("code") or "发送失败"))
                return
            self._set_state(PaneState.GENERATING)
            self.poll_timer.start()
            QTimer.singleShot(650, self._poll_answer)
            QTimer.singleShot(1800, lambda: self._retry_native_submit(active_batch))

        self._run_javascript(self.adapter.snapshot_script(), after_snapshot)

    def _send_with_native_input(
        self,
        question: str,
        active_batch: str,
        callback: Callable[[object], None],
    ) -> None:
        """Type through Qt so controlled web editors receive trusted DOM events."""

        def after_focus(result: object) -> None:
            if active_batch != self._batch_id:
                return
            if not isinstance(result, dict) or not result.get("ok"):
                callback({"ok": False, "code": "NO_INPUT", "detail": "未找到当前站点的聊天输入框"})
                return

            target = self.view.focusProxy() or self.view
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)

            def key(key_code: Qt.Key, text: str = "", modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
                QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, key_code, modifiers, text))
                QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyRelease, key_code, modifiers, text))

            key(Qt.Key.Key_A, "a", Qt.KeyboardModifier.ControlModifier)
            key(Qt.Key.Key_Backspace)

            # Bound each batch while yielding to the renderer. A timer per
            # character makes long report prompts spend minutes merely typing.
            position = 0
            used_ime = False

            def type_next() -> None:
                nonlocal position
                if active_batch != self._batch_id:
                    return
                if position >= len(question):
                    QTimer.singleShot(250, submit)
                    return
                for character in question[position:position + 12]:
                    if character == "\n":
                        key(Qt.Key.Key_Return, "\r", Qt.KeyboardModifier.ShiftModifier)
                    else:
                        key(Qt.Key.Key_unknown, character)
                position += 12
                QTimer.singleShot(1, type_next)

            def submit() -> None:
                if active_batch != self._batch_id:
                    return

                def verify(raw: object) -> None:
                    nonlocal used_ime
                    if active_batch != self._batch_id:
                        return
                    actual = str(raw.get("inputText", "")) if isinstance(raw, dict) else ""
                    if normalize_input_text(actual) != normalize_input_text(question):
                        if used_ime:
                            # Some controlled editors reject IME commits. Clear
                            # only this unsent draft and use the key-event path.
                            used_ime = False
                            key(Qt.Key.Key_A, "a", Qt.KeyboardModifier.ControlModifier)
                            key(Qt.Key.Key_Backspace)
                            type_next()
                            return
                        callback({"ok": False, "code": "INPUT_MISMATCH",
                                  "detail": "网页输入内容与问题不一致，已阻止发送；可能是输入框兼容问题或网站长度限制。"})
                        return
                    key(Qt.Key.Key_Return, "\r")
                    logger.info("%s used verified native input and Enter", self.adapter.id)
                    callback({"ok": True, "code": "NATIVE_SUBMIT", "detail": "trusted Qt key events"})

                self._run_javascript(self.adapter.snapshot_script(), verify)

            if len(question) > 256:
                used_ime = True
                event = QInputMethodEvent()
                event.setCommitString(question)
                QApplication.sendEvent(target, event)
                QTimer.singleShot(500, submit)
            else:
                type_next()

        self._run_javascript(self.adapter.focus_input_script(), after_focus)

    def _retry_native_submit(self, active_batch: str) -> None:
        if active_batch != self._batch_id or self.state != PaneState.GENERATING:
            return

        def after_snapshot(raw: object) -> None:
            if active_batch != self._batch_id or self.state != PaneState.GENERATING:
                return
            data = raw if isinstance(raw, dict) else {}
            if (not data.get("ok") or data.get("generating")
                or int(data.get("count", 0) or 0) > self._baseline_count
                or normalize_input_text(self._question) != normalize_input_text(str(data.get("inputText", "")))):
                return

            def after_focus(result: object) -> None:
                if active_batch != self._batch_id or self.state != PaneState.GENERATING:
                    return
                if not isinstance(result, dict) or not result.get("ok"):
                    return
                self._run_javascript(self.adapter.snapshot_script(), submit_if_unchanged)

            def submit_if_unchanged(latest):
                if active_batch != self._batch_id or self.state != PaneState.GENERATING:
                    return
                if (not isinstance(latest, dict) or not latest.get("ok") or latest.get("generating")
                    or int(latest.get("count", 0) or 0) > self._baseline_count
                    or normalize_input_text(str(latest.get("inputText", ""))) != normalize_input_text(self._question)):
                    return
                target = self.view.focusProxy() or self.view
                self.view.setFocus(Qt.FocusReason.OtherFocusReason)
                QApplication.sendEvent(
                    target,
                    QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r"),
                )
                QApplication.sendEvent(
                    target,
                    QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r"),
                )
                logger.info("%s used native Enter submit fallback", self.adapter.id)

            self._run_javascript(self.adapter.focus_input_script(), after_focus)

        self._run_javascript(self.adapter.snapshot_script(), after_snapshot)

    def _poll_answer(self) -> None:
        if self.state != PaneState.GENERATING:
            return
        active_batch = self._batch_id

        def after_poll(raw: object) -> None:
            if active_batch != self._batch_id or self.state != PaneState.GENERATING:
                return
            elapsed = time.monotonic() - self._started_at
            if elapsed >= self.config.response_timeout_seconds:
                self._finish_error(self._timeout_message())
                return

            data = raw if isinstance(raw, dict) else {}
            self._last_snapshot = data
            if not data.get("ok"):
                return
            text = str(data.get("text", "")).strip()
            signature = str(data.get("signature", ""))
            count = int(data.get("count", 0) or 0)
            generating = bool(data.get("generating"))
            changed = bool(text) and (signature != self._baseline_signature or count > self._baseline_count)

            current_url = str(data.get("url", ""))
            if self._manual_capture and QUrl(current_url) != QUrl(self._capture_url):
                self._finish_error("补采期间网页已切换，已停止，避免把其他会话写入本轮报告。")
                return
            input_text = str(data.get("inputText", "")).strip()
            login_redirect = any(marker in current_url.lower() for marker in ("sign_in", "signin", "login", "from_logout"))
            message_not_sent = elapsed >= 12 and self._question in input_text and not changed and not generating
            if login_redirect or message_not_sent:
                self._finish_error("消息未发出：请确认已登录，或点击该面板的重试按钮")
                return

            if changed and signature == self._last_signature:
                self._stable_polls += 1
            elif changed:
                self._stable_polls = 0
                self._last_change_at = time.monotonic()
            self._last_signature = signature
            if changed:
                self._last_text = text

            seconds = int(elapsed)
            evidence = bool(data.get("completed")) or self._manual_capture
            if self.completion_marker:
                evidence = self.completion_marker in text
            confirmed = (evidence or not self.adapter.require_completion_evidence) and not data.get("reasoningOnly")
            detail = "补采中" if self._manual_capture else "生成中"
            if changed and not generating and not confirmed:
                detail = "等待完整正文"
            self._set_state(PaneState.GENERATING, f"{detail} {seconds}s")
            # A hidden/changed stop button is not proof that a long synthesis
            # ended. Allow a longer quiet period when its finish marker is absent.
            report_ready = (not self.completion_marker or self.completion_marker in text
                            or time.monotonic() - self._last_change_at >= 30)
            if (changed and not generating and confirmed and report_ready
                    and self._stable_polls >= self.config.stable_poll_count):
                self.poll_timer.stop()
                self._automation_active = False
                self._set_state(PaneState.DONE)
                self.answer_ready.emit(
                    AnswerResult(
                        site_id=self.adapter.id,
                        site_name=self.adapter.name,
                        question=self._question,
                        state=PaneState.DONE,
                        text=self._last_text,
                        elapsed_seconds=elapsed,
                    )
                )

        self._run_javascript(self.adapter.snapshot_script(), after_poll)

    def _timeout_message(self) -> str:
        if self._last_text:
            return ("等待回答超时：已保留采集内容，但尚未确认完整结束。"
                    "网页随后生成完成时，请点击本面板的“补采”，无需重新提问。")
        return f"等待回答超时（{self.config.response_timeout_seconds} 秒）；网页完成后可点击“补采”。"

    def recapture(self) -> None:
        """Read the existing answer only; never send or overwrite an editor."""
        if not self._question or self.state in {PaneState.SENDING, PaneState.LOADING}:
            return
        token = self._batch_id
        original_url = self.page.url().toString()
        response = QMessageBox.question(
            self, "补采本轮原文（不重新提问）",
            f"本轮问题：{self._question[:240]}\n\n"
            "请先核对：当前网页最后一条回答是否对应本轮问题，而且已经完整生成？\n"
            "确认后只读取原文并更新本轮报告，不会发送新消息。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if (response != QMessageBox.StandardButton.Yes or token != self._batch_id
                or original_url != self.page.url().toString()):
            return
        self._batch_id = uuid4().hex
        self._manual_capture = True
        self._capture_url = original_url
        self._started_at = time.monotonic()
        self._last_change_at = self._started_at
        self._last_signature = ""
        self._stable_polls = 0
        self._automation_active = True
        self.collection_started.emit(self.adapter.id)
        self._set_state(PaneState.GENERATING, "补采原文中")
        self.poll_timer.start()
        active_batch = self._batch_id

        def watchdog() -> None:
            if self._batch_id == active_batch and self.busy:
                self._finish_error(self._timeout_message())

        QTimer.singleShot(self.config.response_timeout_seconds * 1000, watchdog)
        self._poll_answer()

    def _finish_error(self, message: str) -> None:
        self._batch_id = uuid4().hex
        self.poll_timer.stop()
        self._automation_active = False
        elapsed = max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0
        logger.warning(
            "%s failed after %.1fs: %s; url=%s; last_snapshot=%s",
            self.adapter.id,
            elapsed,
            message,
            self.page.url().toString(),
            {key: value for key, value in self._last_snapshot.items() if key not in {"text", "inputText", "signature"}},
        )
        # Full DOM diagnostics are only produced on an explicit diagnostic action.
        detail = ("原文待确认" if self._last_text else "回答超时") if "等待回答超时" in message else "发送失败"
        if "网页输入内容" in message:
            detail = "输入校验失败"
        self._set_state(PaneState.ERROR, detail)
        self.status_label.setToolTip(message)
        self.answer_ready.emit(
            AnswerResult(
                site_id=self.adapter.id,
                site_name=self.adapter.name,
                question=self._question,
                state=PaneState.ERROR,
                text=self._last_text,
                error=message,
                elapsed_seconds=elapsed,
            )
        )

    def retry(self) -> None:
        if self._question:
            self.dispatch(self._question, self._batch_id)

    def cancel(self, emit_result: bool = True) -> None:
        was_busy = self.busy
        # Invalidate native typing timers and in-flight JS callbacks immediately.
        self._batch_id = uuid4().hex
        self.page.runJavaScript("window.__fourAiSendRun = (window.__fourAiSendRun || 0) + 1;")
        self.poll_timer.stop()
        self._automation_active = False
        if was_busy:
            self._set_state(PaneState.CANCELLED)
            if emit_result:
                self.answer_ready.emit(
                    AnswerResult(
                        site_id=self.adapter.id,
                        site_name=self.adapter.name,
                        question=self._question,
                        state=PaneState.CANCELLED,
                        text=self._last_text,
                        error="用户取消",
                        elapsed_seconds=max(0.0, time.monotonic() - self._started_at),
                    )
                )

    def diagnose(self) -> None:
        def done(raw: object) -> None:
            def with_capture(snapshot: object) -> None:
                payload = dict(raw) if isinstance(raw, dict) else {"dom": raw}
                if isinstance(snapshot, dict):
                    payload["captureState"] = {key: value for key, value in snapshot.items()
                                               if key not in {"text", "inputText", "signature"}}
                    payload["captureState"]["textLength"] = len(str(snapshot.get("text", "")))
                    payload["captureState"]["requiresCompletionEvidence"] = self.adapter.require_completion_evidence
                self.diagnostic_ready.emit(self.adapter.id, json.dumps(payload, ensure_ascii=False, indent=2))

            self._run_javascript(self.adapter.snapshot_script(), with_capture)

        self._run_javascript(self.adapter.diagnostic_script(), done)

    def go_home(self) -> None:
        self.view.setUrl(QUrl(self.adapter.home_url))

    def open_external(self) -> None:
        QDesktopServices.openUrl(self.view.url())

    def _on_load_finished(self, ok: bool) -> None:
        self._apply_zoom()
        if not self._automation_active:
            self._set_state(PaneState.READY if ok else PaneState.ERROR, "就绪" if ok else "加载失败")

    def _on_load_started(self) -> None:
        if self._manual_capture and self._automation_active:
            self._finish_error("补采期间网页发生导航，已停止；请核对当前问题后重新补采。")
        if not self._automation_active:
            self._set_state(PaneState.LOADING)

    def _apply_zoom(self) -> None:
        self.view.setZoomFactor(self.zoom)
        self.zoom_status_action.setText(f"当前缩放：{round(self.zoom * 100):.0f}%")

    def _change_zoom(self, multiplier: float) -> None:
        self.manual_zoom = True
        self.zoom = max(self.config.manual_zoom_min, min(self.config.manual_zoom_max, self.zoom * multiplier))
        self._apply_zoom()

    def reset_zoom(self) -> None:
        self.manual_zoom = False
        self._auto_fit()

    def _auto_fit(self) -> None:
        width = self.view.width()
        if width > 0:
            self.zoom = max(
                self.config.auto_zoom_min,
                min(self.config.auto_zoom_max, width / self.config.design_width),
            )
        self._apply_zoom()

    def resizeEvent(self, event):  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        if not self.manual_zoom:
            self._auto_fit()

    def set_fullscreen_state(self, active: bool) -> None:
        self.full_button.setText("还原" if active else "全屏")
