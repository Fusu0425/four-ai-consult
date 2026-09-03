from four_ai_consult.adapters import SITE_ADAPTERS
from four_ai_consult.consensus import build_basic_report
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
from four_ai_consult.storage import ConsultationRepository


def test_repository_saves_searches_and_deletes_consultations(tmp_path) -> None:
    repository = ConsultationRepository(tmp_path / "consultations.sqlite3")
    session = ConsultationSession("怎样验证一条产品假设？", ("deepseek", "qwen"))
    session.add_result(
        AnswerResult("deepseek", "DeepSeek", session.question, PaneState.DONE, text="先定义可证伪指标。", elapsed_seconds=1.2)
    )
    session.add_result(
        AnswerResult("qwen", "通义千问", session.question, PaneState.ERROR, error="网络超时", elapsed_seconds=3.4)
    )
    report = build_basic_report(session)

    repository.save(session, report)

    items = repository.list("产品假设")
    assert len(items) == 1
    assert items[0].successful_count == 1
    assert items[0].total_count == 2
    assert items[0].report == report
    answers = repository.answers(session.id)
    assert [answer.site_id for answer in answers] == ["deepseek", "qwen"]
    assert answers[0].text == "先定义可证伪指标。"
    assert answers[1].error == "网络超时"
    assert repository.list("可证伪")[0].id == session.id
    assert repository.list("不存在") == []

    repository.delete(session.id)
    assert repository.list() == []


def test_repository_save_is_idempotent(tmp_path) -> None:
    repository = ConsultationRepository(tmp_path / "consultations.sqlite3")
    adapter = SITE_ADAPTERS[0]
    session = ConsultationSession("重复保存", (adapter.id,))
    session.add_result(AnswerResult(adapter.id, adapter.name, session.question, PaneState.DONE, text="第一版"))
    repository.save(session, "报告一")
    session.results[adapter.id].text = "第二版"
    repository.save(session, "报告二")

    assert len(repository.list()) == 1
    assert repository.list()[0].report == "报告二"
    assert repository.answers(session.id)[0].text == "第二版"


def test_repository_closes_connections_and_deletes_only_matching_snapshots(tmp_path):
    import sqlite3

    from four_ai_consult.analysis_plan import AnalysisPlan

    path = tmp_path / "consultations.sqlite3"
    repository = ConsultationRepository(path)
    first = ConsultationSession("第一轮", ("deepseek",))
    second = ConsultationSession("第二轮", ("deepseek",))
    for session in (first, second):
        session.add_result(AnswerResult("deepseek", "DeepSeek", session.question, PaneState.DONE, text="完整观点"))
        repository.save(session, "原文")
        record = AnalysisPlan(session, "免费网页版", "deepseek").record
        record.save_snapshot(tmp_path / "reports")
    # Reading without WAL proves that every closed transaction was checkpointed.
    with sqlite3.connect(f"file:{path}?immutable=1", uri=True) as connection:
        assert connection.execute("select count(*) from consultations").fetchone()[0] == 2
    repository.delete(first.id)
    remaining = list((tmp_path / "reports").glob("report-*.json"))
    assert len(remaining) == 1
    assert second.id in remaining[0].read_text(encoding="utf-8")
