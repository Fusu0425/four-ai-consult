import json
from unittest.mock import patch

import pytest

from four_ai_consult.analysis_plan import AnalysisPlan, ReportRecord, material_fingerprint, split_losslessly
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
from four_ai_consult.storage import ConsultationRepository
from four_ai_consult.synthesis import request_completion


def session(text="观点A，需要预算条件；风险不可忽略。", second=True):
    s = ConsultationSession("我在预算有限时应该选哪个？", ("deepseek", "kimi") if second else ("deepseek",))
    s.add_result(AnswerResult("deepseek", "DeepSeek", s.question, PaneState.DONE, text=text))
    if second:
        s.add_result(AnswerResult("kimi", "Kimi", s.question, PaneState.DONE, text="观点B：优先稳定；不是无条件推荐。"))
    return s


def complete_next(plan):
    task = plan.next_task()
    body = "具体观点、前提、反对意见与例外 " + " ".join(f"[{sid}]" for sid in task.source_ids)
    plan.accept(body + "\n" + task.marker)
    return task


@pytest.mark.parametrize(
    "text",
    ["字" * 51001, "第一段\n\n第二段。尾段。" * 9000, "a \n\t🙂中" * 4000],
    ids=["unbroken", "paragraphs", "unicode"],
)
def test_chunks_preserve_every_character_and_respect_budget(text):
    parts = split_losslessly(text, 5000)
    assert "".join(parts) == text
    assert all(0 < len(p) <= 5000 for p in parts)


def test_long_answer_tail_is_included_in_extraction_and_export():
    text = "独特观点。" * 4000 + "最后条件：只有预算达到9999时才成立。"
    plan = AnalysisPlan(session(text), "免费网页版", "deepseek")
    assert "".join(s.text for s in plan.record.sources if s.site_id == "deepseek") == text
    prompts = []
    while plan.record.status != "complete":
        prompts.append(complete_next(plan).prompt)
    assert any("最后条件：只有预算达到9999时才成立。" in p for p in prompts)
    assert "最后条件：只有预算达到9999时才成立。" in plan.record.markdown()
    assert plan.record.direct and len(prompts) == 1
    assert ReportRecord.from_json(plan.record.to_json()).to_json() == plan.record.to_json()


def test_missing_finish_marker_and_source_never_count_as_complete():
    plan = AnalysisPlan(session(), "免费网页版", "deepseek")
    task = plan.next_task()
    with pytest.raises(ValueError, match="结束标记"):
        plan.accept("疑似截断 [S1-1]")
    assert not plan.record.notes
    assert plan.record.partial_output
    with pytest.raises(ValueError, match="来源"):
        plan.accept("没有来源的泛泛总结\n" + task.marker)
    with pytest.raises(ValueError, match="来源"):
        plan.accept("只谈一家 [S1-1]\n" + task.marker)
    complete_next(plan)
    assert plan.record.status == "complete"


def test_large_final_material_generates_retained_volumes_without_cutting_notes():
    plan = AnalysisPlan(session(), "免费网页版", "deepseek", input_limit=3500)
    plan.record.notes = ["重要限定" * 900 + f"[{s.id}]" for s in plan.record.sources]
    prompts = []
    for _ in range(20):
        task = plan.next_task()
        if task is None:
            break
        assert len(task.prompt) <= 3500
        prompts.append(task.prompt)
        complete_next(plan)
    assert len(plan.record.notes) == 2
    assert plan.record.status == "complete"
    assert plan.record.levels
    assert sum(p.count("重要限定") for p in prompts) >= 1796  # boundaries may split a phrase, never characters
    assert plan.record.markdown().count("重要限定") == 1800


def test_long_question_is_never_silently_shortened():
    s = session("第一家的完整长观点。" * 4000)
    s.question = "长问题" * 10000 + "关键约束"
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    with pytest.raises(ValueError, match="完整材料"):
        plan.next_task()
    assert plan.record.question == s.question


def test_resume_reuses_only_same_mode_provider_and_material():
    s = session("第一家的完整长观点。" * 4000)
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    complete_next(plan)
    restored = AnalysisPlan(s, "免费网页版", "deepseek", resume=plan.record)
    assert len(restored.record.notes) == 1
    assert restored.next_task().source_ids == ("S1-2",)
    assert not AnalysisPlan(s, "免费网页版", "kimi", resume=plan.record).record.notes
    assert not AnalysisPlan(s, "API 加强版", "deepseek", resume=plan.record).record.notes
    s.results["kimi"].text += "修订"
    assert not AnalysisPlan(s, "免费网页版", "deepseek", resume=plan.record).record.notes


def test_failed_models_are_explicit_not_invented_as_sources():
    s = session()
    s.results["kimi"].state = PaneState.ERROR
    s.results["kimi"].error = "超时"
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    assert len(plan.record.sources) == 1
    assert "Kimi：超时" in plan.record.markdown()


def test_analysis_persistence_preserves_order_and_deletes_with_history(tmp_path):
    repo = ConsultationRepository(tmp_path / "history.sqlite3")
    s = session()
    s.results = dict(reversed(list(s.results.items())))
    repo.save(s, "原始材料")
    assert material_fingerprint(repo.load_session(s.id)) == material_fingerprint(s)
    plan = AnalysisPlan(s, "免费网页版", "deepseek")
    complete_next(plan)
    repo.save_analysis(plan.record)
    assert repo.analysis_records(s.id)[0].status == "complete"
    assert repo.list()[0].report == "原始材料"
    repo.delete(s.id)
    assert repo.analysis_records(s.id) == []


@pytest.mark.parametrize("reason,text", [("length", "半份输出"), ("content_filter", ""), ("stop", "")])
def test_api_rejects_truncation_filters_and_empty_responses(reason, text):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"choices": [{"finish_reason": reason, "message": {"content": text}}]}).encode()

    with patch("urllib.request.urlopen", return_value=Response()), pytest.raises(ValueError):
        request_completion("fake-key", "完整材料")
