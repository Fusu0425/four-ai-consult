from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .adapters import SiteAdapter
from .analysis_plan import AnalysisPlan
from .config import AppConfig
from .webpane import WebPane


class WebSynthesisDialog(QDialog):
    """A temporary, user-visible browser; independent of the four source panes."""

    checkpoint = Signal(str)
    completed = Signal()

    def __init__(self, plan: AnalysisPlan, adapter: SiteAdapter, profile, config: AppConfig, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.stopped = False
        self.waiting = False
        self.running = False
        self._navigation_id = 0
        self.setWindowTitle(f"免费网页综合 · {adapter.name}")
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        self.status = QLabel("请完成登录；每步只会在空白新会话中发送，不改写四家原始回答。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.pane = WebPane(
            adapter,
            profile,
            replace(config, response_timeout_seconds=480, stable_poll_count=5),
            0,
            0,
            lambda _: None,
            self,
        )
        self.pane.full_button.hide()
        self.pane.retry_button.hide()
        self.pane.answer_ready.connect(self._answer)
        self.pane.require_empty_input = True
        self.pane.view.loadStarted.connect(self._navigation_started)
        self.pane.view.loadFinished.connect(self._loaded)
        layout.addWidget(self.pane, 1)
        row = QHBoxLayout()
        self.continue_button = QPushButton("已登录并新建空白会话，继续")
        self.continue_button.clicked.connect(self._check_empty)
        stop = QPushButton("停止并保留进度")
        stop.clicked.connect(self.reject)
        row.addWidget(self.continue_button)
        row.addWidget(stop)
        layout.addLayout(row)
        QTimer.singleShot(0, self._next)

    def _next(self):
        if self.stopped:
            return
        try:
            task = self.plan.next_task()
        except ValueError as error:
            self._fail(str(error))
            return
        if task is None:
            self.stopped = True
            self.checkpoint.emit(self.plan.record.to_json())
            self.completed.emit()
            self.accept()
            return
        self.plan.record.status = "running"
        self.running = False
        self.waiting = True
        self.continue_button.setEnabled(True)
        self.status.setText(task.title + "。正在打开独立空白会话；若有验证码，请手动完成。")
        self._navigation_id += 1
        self.pane.go_home()

    def _loaded(self, ok):
        if not ok and self.waiting and not self.stopped:
            self.status.setText("网页加载失败；请检查网络，页面恢复后再点继续。未发送材料。")
        if ok and self.waiting and not self.stopped:
            navigation_id = self._navigation_id
            QTimer.singleShot(1600, lambda: self._check_empty() if navigation_id == self._navigation_id else None)

    def _navigation_started(self):
        self._navigation_id += 1

    def _check_empty(self):
        if self.stopped or not self.waiting or self.running:
            return
        navigation_id = self._navigation_id

        def ready(raw):
            if self.stopped or not self.waiting or self.running or navigation_id != self._navigation_id:
                return
            data = raw if isinstance(raw, dict) else {}
            if not data.get("ok"):
                self.status.setText("暂时无法读取网页状态；不会按空白会话发送。请等待页面加载完成后点继续。")
                return
            if data.get("count", 0) or data.get("generating") or str(data.get("inputText", "")).strip():
                self.status.setText("为避免污染已有聊天，请在此页手动新建空白会话，然后点继续。不会清空已有输入。")
                return

            def focus(result):
                if self.stopped or not self.waiting or self.running or navigation_id != self._navigation_id:
                    return
                if not isinstance(result, dict) or not result.get("ok"):
                    self.status.setText("未找到可用输入框：请完成登录/验证码，或打开空白新会话后点继续。")
                    return
                self.running = True
                self.waiting = False
                self.continue_button.setEnabled(False)
                task = self.plan.pending
                self.pane.completion_marker = task.marker
                self.status.setText(task.title + " · 正在发送完整材料和等待分析，请勿操作本页输入框。")
                self.pane.dispatch(task.prompt, uuid4().hex)

            self.pane._run_javascript(self.pane.adapter.focus_input_script(), focus)

        self.pane._run_javascript(self.pane.adapter.snapshot_script(), ready)

    def _answer(self, result):
        if self.stopped or not self.running:
            return
        self.running = False
        if not result.succeeded:
            if result.text:
                self.plan.record.partial_output = result.text
            self._fail(result.error or "网页未返回完整回答")
            return
        try:
            self.plan.accept(result.text)
        except ValueError as error:
            if self.plan.repair(str(error)):
                self.checkpoint.emit(self.plan.record.to_json())
                self.status.setText("输出格式未通过检查，保留中间结果并自动重试本步骤一次。")
                QTimer.singleShot(600, self._next)
                return
            self._fail(str(error))
            return
        self.checkpoint.emit(self.plan.record.to_json())
        if self.plan.record.status == "complete":
            self._next()
        else:
            QTimer.singleShot(600, self._next)

    def _fail(self, message):
        self.stopped = True
        self.plan.record.status = "error"
        self.plan.record.error = message
        self.checkpoint.emit(self.plan.record.to_json())
        self.completed.emit()
        self.continue_button.setEnabled(False)
        self.status.setText(message + " 关闭此窗口可回报告页重试或更换综合模型。")

    def reject(self):
        if not self.stopped:
            self.stopped = True
            self.pane.cancel(emit_result=False)
            self.plan.record.status = "cancelled"
            self.plan.record.error = "已停止后续网页分析。已发出的问题可能仍在网站生成，已完成详析保留。"
            self.checkpoint.emit(self.plan.record.to_json())
            self.completed.emit()
        super().reject()
