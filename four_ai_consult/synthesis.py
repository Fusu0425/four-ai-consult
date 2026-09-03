from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Signal

from .analysis_plan import RULES, AnalysisPlan

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


class PartialCompletionError(ValueError):
    def __init__(self, message, partial):
        super().__init__(message)
        self.partial = partial


def request_completion(api_key: str, prompt: str, model: str = DEEPSEEK_MODEL) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": RULES}, {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
        "max_tokens": 16000,
        "stream": False,
    }
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        messages = {401: "API Key 无效或已过期", 402: "API 余额不足", 429: "API 请求受到频率限制"}
        raise ValueError(messages.get(error.code, f"API 请求失败（HTTP {error.code}）")) from None
    choice = data["choices"][0]
    text = choice.get("message", {}).get("content")
    if choice.get("finish_reason") != "stop":
        raise PartialCompletionError(
            f"API 未完整结束（{choice.get('finish_reason', '未知')}），本段未计入完整报告。",
            text if isinstance(text, str) else "",
        )
    if not isinstance(text, str) or not text.strip():
        raise ValueError("API 返回空回答，本段未计入报告。")
    return text


class SynthesisClient(QObject):
    progress = Signal(str)
    checkpoint = Signal(str)
    finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.running = False

    def cancel(self) -> None:
        self._cancel.set()

    def ask(self, api_key: str, plan: AnalysisPlan, model: str = DEEPSEEK_MODEL) -> None:
        if self.running:
            raise RuntimeError("已有综合任务正在运行")
        self.running = True
        self._cancel = threading.Event()
        cancelled = self._cancel

        def work() -> None:
            try:
                plan.record.status = "running"
                while not cancelled.is_set():
                    task = plan.next_task()
                    if task is None:
                        break
                    self.progress.emit(task.title)
                    text = request_completion(api_key, task.prompt, model)
                    if cancelled.is_set():
                        break
                    try:
                        plan.accept(text)
                    except ValueError as error:
                        self.checkpoint.emit(plan.record.to_json())
                        if plan.repair(str(error)):
                            continue
                        raise
                    self.checkpoint.emit(plan.record.to_json())
                if cancelled.is_set() and plan.record.status != "complete":
                    plan.record.status = "cancelled"
                    plan.record.error = "已停止后续分析；已发出的 API 请求可能仍产生费用。已完成详析保留，可继续。"
            except Exception as error:
                plan.record.status = "error"
                plan.record.error = str(error)
                if isinstance(error, PartialCompletionError):
                    plan.record.partial_output = error.partial
            finally:
                self.running = False
                self.checkpoint.emit(plan.record.to_json())
                self.finished.emit()

        threading.Thread(target=work, name="report-api", daemon=True).start()
