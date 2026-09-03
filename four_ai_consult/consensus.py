from __future__ import annotations

import re
from collections import Counter

from .models import AnswerResult, ConsultationSession

_LATIN_STOPWORDS = {
    "and",
    "are",
    "but",
    "for",
    "from",
    "have",
    "not",
    "that",
    "the",
    "this",
    "with",
    "一个",
    "以及",
    "但是",
    "可以",
    "如果",
    "应该",
    "我们",
    "这个",
    "这些",
    "需要",
    "进行",
}


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", lowered))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    bigrams = {run[index : index + 2] for run in chinese_runs for index in range(max(0, len(run) - 1))}
    return {token for token in words | bigrams if token not in _LATIN_STOPWORDS}


def answer_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _common_terms(results: list[AnswerResult], limit: int = 10) -> list[str]:
    if len(results) < 2:
        return []
    counts: Counter[str] = Counter()
    for result in results:
        counts.update(_tokens(result.text))
    threshold = max(2, (len(results) + 1) // 2)
    return [token for token, count in counts.most_common() if count >= threshold][:limit]


def _snippet(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "……"


def build_basic_report(session: ConsultationSession) -> str:
    """Collection receipt only; never disguise word overlap as semantic consensus."""
    lines = ["# 材料已收集，尚未综合", "", f"问题：{session.question}", "",
             f"已收到 {len(session.successful_results)}/{len(session.site_ids)} 家有效回答。",
             "请在报告页选择免费网页综合或 API 加强版，生成针对问题的分析与比较。",
             "以下是程序采集到的完整回答，不是 AI 总结；网页未加载的内容可能不在其中。", ""]
    for site_id in session.site_ids:
        result = session.results.get(site_id)
        if result is None:
            lines.extend([f"## 未完成模型 · {site_id}", "", "尚未返回。", ""])
        elif result.succeeded:
            lines.extend([f"## 原文 · {result.site_name}", "", result.text, ""])
        else:
            lines.extend([f"## 未完成模型 · {result.site_name}", "", result.error or result.state.label, "",
                          result.text, ""])
    return "\n".join(lines)
