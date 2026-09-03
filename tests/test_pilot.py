import json
import os
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest

from four_ai_consult.adapters import ADAPTER_BY_ID
from four_ai_consult.analysis_plan import AnalysisPlan
from four_ai_consult.models import AnswerResult, ConsultationSession, PaneState
from four_ai_consult.pilot import create_backup, sanitized_diagnostic, support_payload, write_json
from four_ai_consult.storage import ConsultationRepository

qt = pytest.mark.skipif(os.getenv("RUN_QT_WEBENGINE_TESTS") != "1", reason="Qt opt-in")


def sample():
    s = ConsultationSession("私人问题", ("deepseek", "kimi", "doubao", "qwen"))
    s.add_result(AnswerResult("deepseek", "DeepSeek", s.question, PaneState.DONE, text="完整原文\n末尾条件 500 元"))
    return s


def test_partial_session_survives_reopen(tmp_path):
    path = tmp_path / "history.sqlite3"
    s = sample()
    ConsultationRepository(path).save(s, "尚未全部结束")
    restored = ConsultationRepository(path).load_session(s.id)
    assert restored.site_ids == s.site_ids
    assert restored.results["deepseek"].text == s.results["deepseek"].text
    assert not restored.complete


def test_backup_includes_live_wal_but_no_login_or_keys(tmp_path):
    repo = ConsultationRepository(tmp_path / "history.sqlite3")
    s = sample()
    report = AnalysisPlan(s, "免费网页版", "deepseek").record
    # Keep a WAL reader alive while new data is committed.
    reader = sqlite3.connect(repo.database_path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM consultations").fetchall()
        repo.save(s, "材料")
        repo.save_analysis(report)
        report.save_snapshot(tmp_path / "reports")
        (tmp_path / "reports" / "report-invalid.json").write_text("broken", encoding="utf-8")
        (tmp_path / "browser-profile").mkdir()
        (tmp_path / "browser-profile" / "Cookies").write_text("secret-cookie", encoding="utf-8")
        (tmp_path / ".env").write_text("SECRET=private-key", encoding="utf-8")
        destination = tmp_path / "my-private-backup.zip"
        create_backup(repo, tmp_path / "reports", destination)
    finally:
        reader.close()
    with zipfile.ZipFile(destination) as bundle:
        assert bundle.testzip() is None
        names = bundle.namelist()
        assert len(names) == 3
        assert "browser-profile/Cookies" not in names and ".env" not in names
        assert json.loads(bundle.read("manifest.json"))["skipped_invalid_checkpoints"] == 1
        recovered = tmp_path / "restore" / "consultations.sqlite3"
        recovered.parent.mkdir()
        recovered.write_bytes(bundle.read("consultations.sqlite3"))
    restored = ConsultationRepository(recovered)
    assert restored.health() == "ok"
    assert restored.load_session(s.id).results["deepseek"].text.endswith("500 元")
    assert len(restored.analysis_records(s.id)) == 1


def test_failed_backup_preserves_previous_backup(tmp_path):
    destination = tmp_path / "previous.zip"
    destination.write_bytes(b"keep-this-backup")

    class Repository:
        database_path = tmp_path / "history.sqlite3"

        def backup_to(self, _):
            raise sqlite3.DatabaseError("malformed")

    with pytest.raises(sqlite3.DatabaseError):
        create_backup(Repository(), tmp_path / "reports", destination)
    assert destination.read_bytes() == b"keep-this-backup"


def test_support_strict_allowlist_never_copies_raw_data(tmp_path):
    repo = ConsultationRepository(tmp_path / "history.sqlite3")
    s = sample()
    repo.save(s, "SECRET-REPORT")
    pane = SimpleNamespace(adapter=ADAPTER_BY_ID["deepseek"], state=PaneState.ERROR,
                           _last_snapshot={"text": "private", "url": "https://private"},
                           error="sk-secret", question="私人问题")
    text = json.dumps(support_payload([pane], repo), ensure_ascii=False)
    for secret in ("SECRET-REPORT", "sk-secret", "https://private", "私人问题", str(tmp_path)):
        assert secret not in text
    assert json.loads(text)["database"] == "ok"


@pytest.mark.parametrize("raw", [None, "broken", "null", [], 12])
def test_invalid_diagnostic_is_safe(raw):
    assert sanitized_diagnostic(raw) == {"available": False}


def test_diagnostic_drops_urls_text_and_selector_values():
    raw = {"url": "https://secret", "title": "private question", "editorCandidates": [{"text": "key"}],
           "input": [{"selector": "secret", "count": 2}, {"count": "private"}, {"count": True}],
           "send": [{"count": 9999999}]}
    assert sanitized_diagnostic(json.dumps(raw)) == {
        "available": True, "input_matches": [2], "send_matches": [100000],
        "assistant_matches": [], "stop_matches": [],
    }


def test_atomic_json_keeps_previous_on_replace_failure(tmp_path, monkeypatch):
    import four_ai_consult.pilot as pilot

    target = tmp_path / "support.json"
    target.write_text("old", encoding="utf-8")

    def fail(*_):
        raise PermissionError("locked")

    monkeypatch.setattr(pilot.os, "replace", fail)
    with pytest.raises(PermissionError):
        write_json(target, {"new": 1})
    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("*.tmp")) == []


@qt
def test_partial_answer_is_saved_before_other_models_finish(tmp_path):
    from four_ai_consult.ui import MainWindow

    s = sample()
    answer = s.results.pop("deepseek")

    class Window:
        session = s
        repository = ConsultationRepository(tmp_path / "history.sqlite3")
        storage_warning = SimpleNamespace(setVisible=lambda _: None)
        report_button = SimpleNamespace(setEnabled=lambda _: None)
        report_dialog = None
        _save_session = MainWindow._save_session

    MainWindow._on_answer_ready(Window(), answer)
    stored = ConsultationRepository(tmp_path / "history.sqlite3").load_session(s.id)
    assert stored.results["deepseek"].text == answer.text


@qt
def test_save_failure_remains_visible():
    from four_ai_consult.ui import MainWindow

    shown = []

    def fail(*_):
        raise OSError("disk full")

    window = SimpleNamespace(session=sample(), repository=SimpleNamespace(save=fail),
                             storage_warning=SimpleNamespace(setVisible=shown.append))
    MainWindow._save_session(window)
    assert window._save_failed and shown == [True]


@qt
def test_unsaved_answers_are_not_overwritten_by_new_question(monkeypatch):
    from four_ai_consult.ui import MainWindow, QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    window = SimpleNamespace(_save_failed=True, _save_session=lambda: None)
    # No question_input is needed: it must stop before reading a new question.
    MainWindow.broadcast(window)
    assert warnings


@qt
def test_settings_are_isolated_and_persist_in_actual_data_directory(tmp_path, monkeypatch):
    from four_ai_consult.config import app_data_dir, local_settings

    monkeypatch.setenv("FOUR_AI_DATA_DIR", str(tmp_path))
    assert app_data_dir() == tmp_path
    settings = local_settings(tmp_path)
    settings.setValue("enabled_models", "deepseek,kimi,doubao,zhipu")
    settings.sync()
    assert local_settings(tmp_path).value("enabled_models") == "deepseek,kimi,doubao,zhipu"
    assert settings.fileName().replace("\\", "/").endswith("/settings.ini")
    monkeypatch.setenv("FOUR_AI_DATA_DIR", "relative")
    with pytest.raises(ValueError):
        app_data_dir()
