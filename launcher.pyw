"""Double-click launcher with a visible error report for startup failures."""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


def show_startup_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "Four AI Consult - startup error",
            0x10,
        )
    except Exception:
        pass


def run() -> int:
    try:
        os.chdir(PROJECT_DIR)
        from main import main

        return main()
    except BaseException:
        details = traceback.format_exc()
        # Startup errors must remain visible even inside a read-only install.
        log_path = None
        roots = [Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "FourAIConsult" / "logs",
                 Path(tempfile.gettempdir()) / "FourAIConsult-startup"]
        isolated = os.getenv("FOUR_AI_DATA_DIR")
        if isolated and Path(isolated).is_absolute():
            roots.insert(0, Path(isolated) / "logs")
        for root in roots:
            try:
                root.mkdir(parents=True, exist_ok=True)
                candidate = root / "startup-error.log"
                candidate.write_text(details, encoding="utf-8")
                log_path = candidate
                break
            except OSError:
                continue
        show_startup_error(
            "四模型会诊未能启动。请先完整解压，确认旧版本已经从托盘彻底退出。\n"
            "不要删除历史数据或关闭安全软件。\n\n"
            + (f"本地错误记录：\n{log_path}\n记录可能含个人路径，请勿直接公开发布。" if log_path else
               "无法保存错误记录，请检查磁盘空间和目录权限。")
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
