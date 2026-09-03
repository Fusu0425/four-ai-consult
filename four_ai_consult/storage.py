from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import ConsultationSession


@dataclass(frozen=True)
class HistoryItem:
    id: str
    question: str
    started_at: str
    completed_at: str
    successful_count: int
    total_count: int
    report: str


@dataclass(frozen=True)
class StoredAnswer:
    site_id: str
    site_name: str
    state: str
    text: str
    error: str
    elapsed_seconds: float


class ConsultationRepository:
    """Small local-first SQLite store with explicit, forward-only migrations."""

    SCHEMA_VERSION = 1

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        finally:
            # sqlite3's transaction context alone does NOT close the handle.
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self.SCHEMA_VERSION:
                raise RuntimeError(f"本地数据版本 {version} 高于当前程序支持的版本 {self.SCHEMA_VERSION}")
            if version < 1:
                connection.executescript(
                    """
                    CREATE TABLE consultations (
                        id TEXT PRIMARY KEY,
                        question TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        successful_count INTEGER NOT NULL,
                        total_count INTEGER NOT NULL,
                        report TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE answers (
                        consultation_id TEXT NOT NULL REFERENCES consultations(id) ON DELETE CASCADE,
                        site_id TEXT NOT NULL,
                        site_name TEXT NOT NULL,
                        state TEXT NOT NULL,
                        text TEXT NOT NULL DEFAULT '',
                        error TEXT NOT NULL DEFAULT '',
                        elapsed_seconds REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (consultation_id, site_id)
                    );
                    CREATE INDEX consultations_started_at_idx ON consultations(started_at DESC);
                    PRAGMA user_version = 1;
                    """
                )

            # Additive table: older desktop versions can still open the database.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS analysis_reports (
                    consultation_id TEXT NOT NULL REFERENCES consultations(id) ON DELETE CASCADE,
                    mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (consultation_id, mode, provider)
                )
            """)
            connection.execute("CREATE TABLE IF NOT EXISTS session_metadata (consultation_id TEXT PRIMARY KEY "
                               "REFERENCES consultations(id) ON DELETE CASCADE, site_ids TEXT NOT NULL)")

    def save_analysis(self, record) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO analysis_reports VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(consultation_id, mode, provider) DO UPDATE SET
                    payload=excluded.payload, updated_at=excluded.updated_at
                """, (record.session_id, record.mode, record.provider, record.to_json(), datetime.now().isoformat()))

    def health(self) -> str:
        with self._connect() as connection:
            return "ok" if connection.execute("PRAGMA quick_check").fetchone()[0] == "ok" else "error"

    def backup_to(self, destination: Path) -> None:
        if destination.resolve() == self.database_path.resolve():
            raise ValueError("不能覆盖正在使用的数据库")
        # SQLite backup includes committed WAL pages and does not stop the app.
        with self._connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("数据库备份未通过完整性检查")
            finally:
                target.close()

    def analysis_records(self, consultation_id: str):
        from .analysis_plan import ReportRecord

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload, updated_at FROM analysis_reports WHERE consultation_id = ? ORDER BY updated_at DESC",
                (consultation_id,),
            ).fetchall()
        records = []
        for row in rows:
            try:
                record = ReportRecord.from_json(row["payload"])
                if not record.updated_at:
                    record.updated_at = datetime.fromisoformat(row["updated_at"]).timestamp()
                records.append(record)
            except (ValueError, TypeError, KeyError):
                # One damaged report must not hide other providers' valid work.
                continue
        return records

    def save(self, session: ConsultationSession, report: str) -> None:
        completed_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO consultations (
                    id, question, started_at, completed_at, successful_count, total_count, report
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    question=excluded.question,
                    completed_at=excluded.completed_at,
                    successful_count=excluded.successful_count,
                    total_count=excluded.total_count,
                    report=excluded.report
                """,
                (
                    session.id,
                    session.question,
                    session.started_at.isoformat(timespec="seconds"),
                    completed_at,
                    len(session.successful_results),
                    len(session.site_ids),
                    report,
                ),
            )
            connection.execute("INSERT INTO session_metadata VALUES (?, ?) ON CONFLICT(consultation_id) "
                               "DO UPDATE SET site_ids=excluded.site_ids", (session.id, json.dumps(session.site_ids)))
            connection.execute("DELETE FROM answers WHERE consultation_id = ?", (session.id,))
            connection.executemany(
                """
                INSERT INTO answers (
                    consultation_id, site_id, site_name, state, text, error, elapsed_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session.id,
                        result.site_id,
                        result.site_name,
                        result.state.value,
                        result.text,
                        result.error,
                        result.elapsed_seconds,
                    )
                    for result in session.results.values()
                ],
            )

    def list(self, query: str = "", limit: int = 200) -> list[HistoryItem]:
        query = query.strip()
        where = (
            """WHERE question LIKE ? ESCAPE '\\' OR EXISTS (
                SELECT 1 FROM answers
                WHERE answers.consultation_id = consultations.id
                  AND answers.text LIKE ? ESCAPE '\\'
            )"""
            if query
            else ""
        )
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        parameters: tuple[object, ...] = (pattern, pattern, limit) if query else (limit,)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, question, started_at, completed_at, successful_count, total_count, report
                FROM consultations
                {where}
                ORDER BY started_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [HistoryItem(**dict(row)) for row in rows]

    def answers(self, consultation_id: str) -> list[StoredAnswer]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT site_id, site_name, state, text, error, elapsed_seconds
                FROM answers WHERE consultation_id = ? ORDER BY rowid
                """,
                (consultation_id,),
            ).fetchall()
        return [StoredAnswer(**dict(row)) for row in rows]

    def load_session(self, consultation_id: str) -> ConsultationSession | None:
        from .models import AnswerResult, PaneState

        with self._connect() as connection:
            row = connection.execute("SELECT question, started_at FROM consultations WHERE id = ?",
                                     (consultation_id,)).fetchone()
            meta = connection.execute("SELECT site_ids FROM session_metadata WHERE consultation_id = ?",
                                      (consultation_id,)).fetchone()
        if row is None:
            return None
        answers = self.answers(consultation_id)
        site_ids = tuple(json.loads(meta["site_ids"])) if meta else tuple(a.site_id for a in answers)
        session = ConsultationSession(row["question"], site_ids, id=consultation_id,
                                      started_at=datetime.fromisoformat(row["started_at"]))
        for answer in answers:
            session.add_result(AnswerResult(answer.site_id, answer.site_name, session.question, PaneState(answer.state),
                                           answer.text, answer.error, answer.elapsed_seconds))
        return session

    def delete(self, consultation_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM consultations WHERE id = ?", (consultation_id,))
        from .analysis_plan import ReportRecord

        # Remove only this session's app-managed JSON checkpoints. User-exported
        # Markdown and unrelated reports remain untouched.
        directory = self.database_path.parent / "reports"
        for path in directory.glob("report-*.json"):
            try:
                record = ReportRecord.from_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if record.session_id == consultation_id and path == record.snapshot_path(directory):
                path.unlink()
