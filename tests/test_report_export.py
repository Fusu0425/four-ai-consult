from datetime import datetime

import pytest

from four_ai_consult.analysis_plan import AnalysisPlan
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
from four_ai_consult.report_export import (
    build_report_html,
    normalize_export_path,
    source_targets_for_record,
    write_report_export,
)


def report_record():
    session = ConsultationSession("A 和 B 应该怎么选？", ("deepseek", "kimi"))
    session.add_result(
        AnswerResult("deepseek", "DeepSeek", session.question, PaneState.DONE, text="# DeepSeek 原文\n预算 500 元时选 A。")
    )
    session.add_result(
        AnswerResult("kimi", "Kimi", session.question, PaneState.DONE, text="Kimi 认为有人维护时选 B。")
    )
    plan = AnalysisPlan(session, "免费网页版", "deepseek")
    plan.record.status = "complete"
    plan.record.conclusion = """## 先看结论

预算 500 元时选 **A** [S1-1]；有人维护时再考虑 B [S2-1]。

| 方案 | 条件 |
| --- | --- |
| A | 预算有限 |
| B | 有人维护 |

> 重要事实仍需核验。

```python
print("完整代码")
```
"""
    return session, plan.record


def test_mobile_html_is_self_contained_lossless_and_safe():
    session, record = report_record()
    markdown = record.markdown() + '\n\n<script>alert("不应执行")</script>\n[危险](javascript:alert(1)) [官网](https://example.com)'
    page = build_report_html(
        markdown,
        question=session.question,
        source_targets=source_targets_for_record(record),
        exported_at=datetime(2026, 9, 7, 21, 30),
    )

    assert '<meta name="viewport"' in page
    assert "FOUR AI CONSULT · 可分享报告" in page
    assert "完整对比报告" in page
    assert "预算 500 元时选" in page
    assert "Kimi 认为有人维护时选 B。" in page
    assert '<table>' in page and "预算有限" in page
    assert '<pre><code>print(&quot;完整代码&quot;)</code></pre>' in page
    assert '<a class="citation-link" href="#section-' in page
    assert "javascript:" not in page
    assert "<script>" not in page
    assert "&lt;script&gt;alert" in page
    assert 'href="https://example.com"' in page
    assert "2026-09-07 21:30" in page
    assert "cdn" not in page.lower()


@pytest.mark.parametrize(
    ("path", "selected", "suffix", "kind"),
    [
        ("报告", "网页报告（推荐手机分享） (*.html)", ".html", "html"),
        ("报告.html", "Markdown（便于编辑） (*.md)", ".md", "md"),
        ("报告.md", "网页报告（推荐手机分享） (*.html)", ".html", "html"),
        ("报告.markdown", "", ".markdown", "md"),
    ],
)
def test_export_filter_controls_format_and_extension(path, selected, suffix, kind):
    target, actual = normalize_export_path(path, selected)
    assert target.suffix == suffix
    assert actual == kind


def test_write_report_export_supports_both_formats(tmp_path):
    session, record = report_record()
    markdown = record.markdown()
    html_path = write_report_export(
        str(tmp_path / "分享报告"),
        "网页报告（推荐手机分享） (*.html)",
        markdown,
        question=session.question,
        source_targets=source_targets_for_record(record),
    )
    md_path = write_report_export(
        str(tmp_path / "编辑报告.html"),
        "Markdown（便于编辑） (*.md)",
        markdown,
        question=session.question,
    )
    assert html_path.suffix == ".html"
    assert '<meta name="viewport"' in html_path.read_text(encoding="utf-8")
    assert md_path.suffix == ".md"
    assert md_path.read_text(encoding="utf-8") == markdown
    assert markdown.startswith("# 完整对比报告")


def test_six_model_material_reaches_a_complete_dual_format_report(tmp_path):
    site_ids = ("deepseek", "kimi", "doubao", "qwen", "yuanbao", "zhipu")
    names = ("DeepSeek", "Kimi", "豆包", "通义千问", "腾讯元宝", "智谱清言")
    session = ConsultationSession("六家观点如何比较？", site_ids)
    for index, (site_id, name) in enumerate(zip(site_ids, names, strict=True), 1):
        session.add_result(AnswerResult(
            site_id, name, session.question, PaneState.DONE,
            text=f"{name} 原始完整回答：核心观点 {index}，依据 {index}，限制条件 {index}。",
        ))
    plan = AnalysisPlan(session, "免费网页版", "kimi")
    task = plan.next_task()
    citations = " ".join(f"[S{index}-1]" for index in range(1, 7))
    conclusion = f"""# 先看结论

六家回答可以按适用条件组合判断，不能用多数票代替事实核验。{citations}

# 各家怎么回答

六家分别给出了核心观点、依据和限制；这里逐一保留并链接原始材料。{citations}

# 逐项对比

按目标、依据、成本、风险、适用条件和下一步六个维度对齐。综合判断必须区分原始观点、推断和待核验事实。{citations}

# 共识、分歧与独有观点

共同部分不等于已经证实，分歧可能来自前提不同；每家的独有观点都继续保留。{citations}

# 建议与下一步

先核验影响决策的关键数字，再根据实际约束选择方案；高风险事项交给专业人士复核。{citations}

# 本次来源编号覆盖清单

本次六个来源均已处理：{citations}

补充说明：完整报告同时保留结论和六家原文，便于用户返回来源核对。"""
    conclusion += "\n保留完整上下文和适用边界。" * 20
    plan.accept(conclusion + "\n" + task.marker)
    assert plan.record.status == "complete"
    markdown = plan.record.markdown()
    html_path = write_report_export(
        str(tmp_path / "完整对比报告.html"), "网页报告（推荐手机分享） (*.html)", markdown,
        question=session.question, source_targets=source_targets_for_record(plan.record),
    )
    md_path = write_report_export(
        str(tmp_path / "完整对比报告.md"), "Markdown（便于编辑） (*.md)", markdown,
        question=session.question,
    )
    html_text = html_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    for name in names:
        assert f"原文 · {name}" in md_text
        assert f"{name} 原始完整回答" in html_text
    assert "先看结论" in html_text and "逐项对比" in md_text


@pytest.mark.skipif(__import__("os").getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt integration opt-in")
def test_report_dialog_defaults_to_html_and_can_export_markdown(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    from shiboken6 import delete

    from four_ai_consult.report_ui import ReportDialog

    app = QApplication.instance() or QApplication([])
    session, record = report_record()
    dialog = ReportDialog("", tmp_path, session=session)
    dialog.record = record
    dialog._render()
    html_target = tmp_path / "手机分享.html"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args: (str(html_target), "网页报告（推荐手机分享） (*.html)"),
    )
    dialog.save_current()
    assert session.question in html_target.read_text(encoding="utf-8")
    assert "DeepSeek 原文" in html_target.read_text(encoding="utf-8")

    md_target = tmp_path / "继续编辑.html"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args: (str(md_target), "Markdown（便于编辑） (*.md)"),
    )
    dialog.save_current()
    assert md_target.with_suffix(".md").read_text(encoding="utf-8") == record.markdown()
    delete(dialog)
    app.processEvents()
