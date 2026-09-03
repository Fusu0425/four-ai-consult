from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


class PaneState(str, Enum):
    LOADING = "loading"
    READY = "ready"
    SENDING = "sending"
    GENERATING = "generating"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            PaneState.LOADING: "加载中",
            PaneState.READY: "就绪",
            PaneState.SENDING: "发送中",
            PaneState.GENERATING: "生成中",
            PaneState.DONE: "已完成",
            PaneState.ERROR: "失败",
            PaneState.CANCELLED: "已取消",
        }[self]

    @property
    def terminal(self) -> bool:
        return self in {PaneState.DONE, PaneState.ERROR, PaneState.CANCELLED}


@dataclass
class AnswerResult:
    site_id: str
    site_name: str
    question: str
    state: PaneState
    text: str = ""
    error: str = ""
    elapsed_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.state == PaneState.DONE and bool(self.text.strip())


@dataclass
class ConsultationSession:
    question: str
    site_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=datetime.now)
    results: dict[str, AnswerResult] = field(default_factory=dict)

    def add_result(self, result: AnswerResult) -> None:
        self.results[result.site_id] = result

    @property
    def complete(self) -> bool:
        return all(site_id in self.results for site_id in self.site_ids)

    @property
    def successful_results(self) -> list[AnswerResult]:
        return [result for result in self.results.values() if result.succeeded]
