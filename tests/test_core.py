from four_ai_consult.adapters import ADAPTER_BY_ID, BACKUP_SITE_IDS, PRIMARY_SITE_IDS, SITE_ADAPTERS
from four_ai_consult.consensus import answer_similarity, build_basic_report
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState


def make_session() -> ConsultationSession:
    return ConsultationSession("怎样判断一个信息是否可靠？", PRIMARY_SITE_IDS)


def test_model_pool_keeps_four_primary_seats_and_two_distinct_backups() -> None:
    assert len(PRIMARY_SITE_IDS) == 4
    assert len(BACKUP_SITE_IDS) == 2
    assert set(PRIMARY_SITE_IDS).isdisjoint(BACKUP_SITE_IDS)
    assert {adapter.id for adapter in SITE_ADAPTERS} == set(PRIMARY_SITE_IDS + BACKUP_SITE_IDS)


def test_session_is_complete_only_after_every_site_returns() -> None:
    session = make_session()
    adapters = [ADAPTER_BY_ID[site_id] for site_id in PRIMARY_SITE_IDS]
    for adapter in adapters[:-1]:
        session.add_result(AnswerResult(adapter.id, adapter.name, session.question, PaneState.ERROR, error="失败"))
    assert not session.complete
    last = adapters[-1]
    session.add_result(AnswerResult(last.id, last.name, session.question, PaneState.DONE, text="回答"))
    assert session.complete


def test_similarity_is_higher_for_related_answers() -> None:
    reference = "需要核对原始来源、作者身份和发布日期，并进行交叉验证。"
    related = "判断可靠性时应核对信息来源与发布日期，还要交叉验证。"
    unrelated = "今天适合去公园散步，记得带水。"
    assert answer_similarity(reference, related) > answer_similarity(reference, unrelated)


def test_basic_report_includes_successes_and_failures() -> None:
    session = make_session()
    adapters = [ADAPTER_BY_ID[site_id] for site_id in PRIMARY_SITE_IDS]
    for adapter in adapters[:3]:
        session.add_result(
            AnswerResult(
                adapter.id,
                adapter.name,
                session.question,
                PaneState.DONE,
                text="建议查看一手资料，并对关键结论做交叉验证。",
                elapsed_seconds=2.0,
            )
        )
    failed = adapters[-1]
    session.add_result(AnswerResult(failed.id, failed.name, session.question, PaneState.ERROR, error="超时"))
    report = build_basic_report(session)
    assert "3/4" in report
    assert "尚未综合" in report
    assert "未完成模型" in report
    assert failed.name in report
    assert "关键共识" not in report
    assert "建议查看一手资料，并对关键结论做交叉验证。" in report
