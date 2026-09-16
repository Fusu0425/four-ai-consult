"""Short, user-facing progress language shared by report transports."""

from __future__ import annotations


def report_stage_label(task_title: str) -> str:
    """Translate internal task names into stable, reassuring progress stages."""
    title = task_title or ""
    if "自动修复" in title:
        return "正在复查报告结构与来源"
    if title.startswith("逐家详析"):
        return "正在提取各家核心观点"
    if title.startswith("长报告"):
        return "正在整理长材料与对比附篇"
    if title.startswith("综合比较"):
        return "正在核对共识、分歧与来源"
    if title.startswith("直接对比"):
        return "正在对比各家观点与依据"
    return "正在生成完整对比报告"
