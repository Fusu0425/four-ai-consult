"""Local-only pilot support: strict telemetry allowlist and consistent backups."""
from __future__ import annotations

import json
import os
import platform
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .adapters import ADAPTER_BY_ID
from .models import PaneState

SAMPLE_QUESTION = (
    "我想给十位朋友试用一款 Windows 工具，没有专职运维，优先考虑上手简单和数据隐私。"
    "请比较本地桌面版与网页在线版：各自优势、成本构成、风险、适用条件和验证步骤。"
    "最后给出有条件的建议；不知道的信息明确说明，不要编造价格。"
)


def support_payload(panes, repository) -> dict:
    """Never include URLs, paths, exception messages, identifiers or free text."""
    sites = []
    for pane in panes:
        sid = pane.adapter.id
        if sid not in ADAPTER_BY_ID:
            continue
        state = pane.state.value if isinstance(pane.state, PaneState) else "unknown"
        sites.append({"model": sid, "state": state})
    try:
        health = repository.health()
    except Exception:
        health = "unavailable"
    return {
        "format": 1, "app_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "os": platform.system(), "architecture": platform.machine(),
        "database": health if health in {"ok", "error"} else "unavailable",
        "models": sites,
        "privacy": "No questions, answers, keys, cookies, URLs, file paths or raw logs. Not uploaded.",
    }


def sanitized_diagnostic(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if not isinstance(raw, dict):
        return {"available": False}
    result = {"available": True}
    for key in ("input", "send", "assistant", "stop"):
        rows = raw.get(key, [])
        if isinstance(rows, list):
            result[key + "_matches"] = [
                min(100000, max(-1, row["count"])) for row in rows[:30]
                if isinstance(row, dict) and type(row.get("count")) is int
            ]
    return result


def write_json(path: Path, payload: dict) -> None:
    """Atomic replacement only after a complete write."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".four-ai-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def create_backup(repository, report_dir: Path, destination: Path) -> None:
    """Explicit user backup, not a shareable support bundle. Never copy live WAL."""
    from .analysis_plan import ReportRecord

    if destination.resolve() == repository.database_path.resolve():
        raise ValueError("备份不能覆盖当前数据库")
    if destination.suffix.lower() != ".zip":
        raise ValueError("请选择 .zip 备份文件")
    # Build completely before replacing an existing, user-selected backup.
    with tempfile.TemporaryDirectory(prefix="four-ai-backup-", dir=destination.parent) as scratch:
        scratch = Path(scratch)
        database = scratch / "consultations.sqlite3"
        repository.backup_to(database)
        archive = scratch / "backup.zip"
        invalid_count = 0
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(database, "consultations.sqlite3")
            for path in report_dir.glob("report-*.json"):
                try:
                    payload = path.read_text(encoding="utf-8")
                    record = ReportRecord.from_json(payload)
                    if path != record.snapshot_path(report_dir):
                        raise ValueError("Unexpected checkpoint name")
                except (OSError, ValueError, KeyError, TypeError):
                    invalid_count += 1
                    continue
                bundle.writestr("reports/" + path.name, payload)
            bundle.writestr("manifest.json", json.dumps({
                "format": 1, "app_version": __version__,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "contains_private_conversations": True, "skipped_invalid_checkpoints": invalid_count,
                "restore": "Exit the app. Preserve the current data folder first. Restore into a separate folder for verification. Do not overwrite live data.",
                "excluded": ["browser-profile", "API keys", "logs", "settings", "screenshots", "user exports"],
            }, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(archive) as check:
            if check.testzip() is not None:
                raise ValueError("备份校验失败，未替换目标文件")
        os.replace(archive, destination)
