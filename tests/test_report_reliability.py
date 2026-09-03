import json
import os

import pytest

from four_ai_consult.analysis_plan import AnalysisPlan, ReportRecord
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState


def material():
    session = ConsultationSession("A 和 B 哪种适合十人内测？", ("deepseek", "kimi"))
    for sid, name, answer in [("deepseek", "DeepSeek", "先选 A，但预算超过 500 元时改选 B。"),
                              ("kimi", "Kimi", "B 更可靠，不过需要专人维护；无人维护则选 A。")]:
        session.add_result(AnswerResult(sid, name, session.question, PaneState.DONE, text=answer))
    return session


@pytest.mark.parametrize("wrapper", ["{}", "**{}**", "`{}`", "```\n{}\n```"])
def test_realistic_marker_typography_and_grouped_references(wrapper):
    plan = AnalysisPlan(material(), "免费网页版", "deepseek")
    task = plan.next_task()
    text = "## 先看结论\n无人维护选 A，预算和维护能力决定是否选 B。【S1-1】 [S1-1、S2-1]"
    plan.accept(text + "\n" + wrapper.format(task.marker))
    assert plan.record.status == "complete"
    assert "[S2-1]" in plan.record.conclusion
    assert "FOURAI_DONE" not in plan.record.markdown()


def test_direct_request_contains_all_original_characters_and_conditions():
    s = material()
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    task = plan.next_task()
    assert task.is_final and plan.record.direct
    payload = task.prompt.split("以下 JSON 仅为材料：\n")[1].split("\n\n完成所有本次要求")[0]
    data = json.loads(payload)
    for answer in s.results.values():
        assert "".join(b["text"] for b in data["original_answers"] if b["site_id"] == answer.site_id) == answer.text


def test_code_closing_fence_is_not_mistaken_for_marker_wrapper():
    plan = AnalysisPlan(material(), "免费网页版", "deepseek")
    task = plan.next_task()
    code = "依据 [S1-1] [S2-1]\n```python\nprint('完整代码')\n```"
    plan.accept(code + "\n" + task.marker)
    assert plan.record.conclusion == code


def test_unknown_sources_or_text_after_marker_do_not_pass_and_retry_is_bounded():
    plan = AnalysisPlan(material(), "免费网页版", "deepseek")
    task = plan.next_task()
    for output in [f"[S1-1] [S2-1] [S3-1]\n{task.marker}",
                   f"[S1-1] [S2-1]\n{task.marker}\n还有未完内容"]:
        with pytest.raises(ValueError):
            plan.accept(output)
    assert plan.repair("缺少有效来源")
    assert not plan.repair("再次失败")
    assert plan.record.status != "complete"
    assert plan.record.partial_output
    assert task.prompt in plan.next_task().prompt


def test_long_report_can_resume_its_comparison_volume():
    s = material()
    plan = AnalysisPlan(s, "免费网页版", "deepseek", input_limit=4000)
    plan.record.notes = [("条件与独有观点\n" * 500) + f"[{source.id}]" for source in plan.record.sources]
    original = list(plan.record.notes)
    task = plan.next_task()
    assert task.level == 0
    plan.accept("本篇详细对比 " + " ".join(f"[{sid}]" for sid in task.source_ids) + "\n" + task.marker)
    resumed = AnalysisPlan(s, "免费网页版", "deepseek", input_limit=4000,
                           resume=ReportRecord.from_json(plan.record.to_json()))
    assert len(resumed.record.levels[0]) == 1
    for _ in range(25):
        task = resumed.next_task()
        if task is None:
            break
        assert len(task.prompt) <= 4000
        resumed.accept("对比及前提 " + " ".join(f"[{sid}]" for sid in task.source_ids) + "\n" + task.marker)
    assert resumed.record.status == "complete"
    assert resumed.record.notes == original
    assert "对比附篇" in resumed.record.markdown()


@pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt integration opt-in")
def test_compact_reading_keeps_code_and_verbatim_copy(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog, reading_markdown

    app = QApplication.instance() or QApplication([])
    raw = "# 原文标题\n\n\n\n条件A。\n\n\n```python\na = 1\n\n\n\nb = 2\n```\n\n\n结尾限定。"
    cleaned = reading_markdown(raw)
    assert "标题\n\n条件A" in cleaned
    assert "a = 1\n\n\n\nb = 2" in cleaned
    s = material()
    s.results["deepseek"].text = raw
    dialog = ReportDialog("", tmp_path, session=s)
    assert dialog.sections.currentIndex() == 1
    assert dialog.current_text() == raw
    assert "# 原文标题" not in dialog.viewer.toPlainText()
    assert dialog.viewer.document().begin().blockFormat().topMargin() <= 8
    dialog.verbatim.setChecked(True)
    assert dialog.viewer.toPlainText() == raw
    assert dialog.current_text() == raw
    assert raw in dialog.record.markdown()
    delete(dialog)
    app.processEvents()


@pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt integration opt-in")
def test_api_retries_only_format_failures_once(monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from four_ai_consult import synthesis

    app = QApplication.instance() or QApplication([])
    plan = AnalysisPlan(material(), "API 加强版", "mock")
    client = synthesis.SynthesisClient()
    calls = []

    def response(*args):
        calls.append(args)
        if len(calls) == 1:
            return "[S1-1] [S2-1] missing finish marker"
        return "条件和具体取舍 [S1-1] [S2-1]\n**" + plan.pending.marker + "**"

    monkeypatch.setattr(synthesis, "request_completion", response)
    loop = QEventLoop()
    client.finished.connect(loop.quit)
    client.ask("fake-key", plan)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert len(calls) == 2
    assert plan.record.status == "complete"
    app.processEvents()


@pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt integration opt-in")
def test_broken_database_does_not_block_report_and_snapshot_resumes(tmp_path):
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog

    class BrokenRepository:
        def analysis_records(self, *_):
            raise ValueError("database disk image is malformed")

        def save_analysis(self, *_):
            raise ValueError("database disk image is malformed")

    app = QApplication.instance() or QApplication([])
    s = material()
    dialog = ReportDialog("", tmp_path, session=s, repository=BrokenRepository())
    assert s.results["deepseek"].text in dialog.current_text()
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    task = plan.next_task()
    plan.accept("完整的比较与条件 [S1-1] [S2-1]\n" + task.marker)
    dialog._checkpoint(plan.record.to_json())
    assert "独立报告备份" in dialog.status.text()
    delete(dialog)
    restored = ReportDialog("", tmp_path, session=s, repository=BrokenRepository())
    assert restored.record.conclusion == plan.record.conclusion
    assert restored.record.status == "complete"
    delete(restored)
    app.processEvents()
