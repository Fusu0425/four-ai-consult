"""Isolated offline pilot UI. All displayed answers are explicitly mock data."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QTimer
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QApplication

from four_ai_consult import ui
from four_ai_consult.config import AppConfig, SecretStore, ensure_runtime_dirs, local_settings


def mock_adapters(adapters):
    output = {}
    for sid, adapter in adapters.items():
        body = f"""<!doctype html><meta charset="utf-8"><style>
        body{{font:18px 'Microsoft YaHei';padding:24px;background:#fffaf2;color:#40372f}}
        textarea{{width:90%;height:70px}}button{{padding:8px 18px}}.answer{{white-space:pre-wrap}}
        </style><h2>{adapter.name} · 本地模拟</h2><p>这是离线测试页，不是真实 AI 回答。</p>
        <textarea placeholder="公开测试问题" onkeydown="if(event.key==='Enter'){{event.preventDefault();document.querySelector('button.send').click()}}"></textarea>
        <button class="send" onclick="const a=document.createElement('div');a.className='answer';
        a.textContent='本地模拟材料：先验证十人内测。限制：只适合 Windows。末尾保留条件 500 元。';
        const end=document.createElement('button');end.setAttribute('aria-label','重新生成');a.append(end);
        document.body.append(a);document.querySelector('textarea').value='';">发送</button>"""
        output[sid] = replace(adapter, home_url="data:text/html;charset=utf-8," + quote(body),
                              input_selectors=("textarea",), send_selectors=("button.send",),
                              assistant_selectors=(".answer",), stop_selectors=(".stop",))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    os.environ["FOUR_AI_DATA_DIR"] = str(Path(args.data_dir).resolve())
    app = QApplication(sys.argv)
    app.setStyleSheet(ui.APP_STYLE)
    dirs = ensure_runtime_dirs()
    local_settings(dirs["root"]).setValue("onboarding_completed", False)
    ui.ADAPTER_BY_ID = mock_adapters(ui.ADAPTER_BY_ID)
    profile = QWebEngineProfile("PilotOfflinePreview", app)
    profile.setPersistentStoragePath(str(dirs["profile"]))
    window = ui.MainWindow(profile, dirs, AppConfig(), SecretStore())
    window.setWindowTitle("四模型会诊 · 离线验收（模拟数据）")
    window.show()
    QTimer.singleShot(args.seconds * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
