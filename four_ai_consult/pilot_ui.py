from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .pilot import SAMPLE_QUESTION, create_backup, support_payload, write_json
from .report_ui import ReportDialog


def paragraph(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def scroll_page(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(widget)
    return area


class PilotCenter(QDialog):
    """Small, local-only welcome/data/support center for invitation testing."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._check_id = 0
        self.setWindowTitle(f"使用与帮助 · {__version__} 十人内测版")
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(680, int(area.width() * .88)), min(610, int(area.height() * .86)))
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        title = QLabel("从四份回答，到一个有依据的判断")
        title.setObjectName("reportTitle")
        root.addWidget(title)
        root.addWidget(paragraph("邀请内测 · 默认免费网页版 · 不需要配置 API Key"))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        guide = QWidget()
        layout = QVBoxLayout(guide)
        layout.addWidget(paragraph(
            "① 在四个面板分别登录你自己的 AI 账号。放大单个面板更方便登录。\n"
            "② 写下需要比较的问题，点击「开始会诊」。失败模型可单独重试。\n"
            "③ 在报告里先看「完整回答」，再点击「生成比较报告」，确认材料发送后生成结论。"
        ))
        layout.addWidget(paragraph("网页加载完成不代表已经登录；下面只检查输入框，不发送问题，也不验证剩余额度。"))
        self.check_button = QPushButton("检查四个面板")
        self.check_button.clicked.connect(self.check_readiness)
        layout.addWidget(self.check_button)
        self.status_rows = {}
        self.site_names = {pane.adapter.id: pane.adapter.name for pane in window.panes}
        for pane in window.panes:
            label = paragraph(pane.adapter.name + " · 尚未检查")
            self.status_rows[pane.adapter.id] = label
            layout.addWidget(label)
        sample = QPushButton("填入一个适合比较的示例问题")
        sample.clicked.connect(self.fill_sample)
        layout.addWidget(sample)
        layout.addWidget(paragraph(
            "问题会发送给当前阵容的网站。生成比较报告前，会再次确认将问题和各家回答交给哪一家综合。"
            "免费模式仍受网站登录、额度、验证码和适配变化影响；API 加强版可选且可能收费。"
        ))
        layout.addStretch()
        self.tabs.addTab(scroll_page(guide), "开始使用")

        data = QWidget()
        layout = QVBoxLayout(data)
        layout.addWidget(paragraph("这次运行实际使用的数据目录（不是安装目录）："))
        self.data_path = QLineEdit(str(window.runtime_dirs["root"].resolve()))
        self.data_path.setReadOnly(True)
        layout.addWidget(self.data_path)
        open_button = QPushButton("打开数据文件夹")
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.data_path.text())))
        layout.addWidget(open_button)
        layout.addWidget(paragraph(
            "历史：consultations.sqlite3\n报告检查点：reports\n登录资料：browser-profile\n设置：settings.ini\n"
            "收到一家回答就保存一次。重启后可到历史查看已收到的回答；未结束的网页请求不会自动续传。"
        ))
        self.health_label = paragraph("尚未检查数据库；完整性通过不代表旧记录已恢复。")
        layout.addWidget(self.health_label)
        health = QPushButton("检查历史库完整性")
        health.clicked.connect(self.check_health)
        backup = QPushButton("备份历史与报告…")
        backup.clicked.connect(self.backup)
        layout.addWidget(health)
        layout.addWidget(backup)
        layout.addWidget(paragraph(
            "备份含私人问题和回答，不要作为反馈发给别人。备份不含登录资料、密钥、日志和手动导出的文件。"
            "换版本前先彻底退出旧版；不要同时运行两个版本，不要删除数据目录。"
        ))
        layout.addStretch()
        self.tabs.addTab(scroll_page(data), "数据与备份")

        feedback = QWidget()
        layout = QVBoxLayout(feedback)
        layout.addWidget(paragraph(
            "遇到问题时，把下面四项描述发给邀请你试用的人即可。无须提供账号或密钥。\n"
            "① 做了什么  ② 期望什么  ③ 实际发生什么  ④ 能否重复出现"
        ))
        self.feedback_text = QTextEdit()
        self.feedback_text.setPlainText(
            f"版本：{__version__}\n操作步骤：\n期望结果：\n实际结果：\n是否每次出现：\n"
            "报告是否帮助判断（有用／部分有用／没用）：\n最需要改善的一点："
        )
        layout.addWidget(self.feedback_text, 1)
        copy = QPushButton("复制反馈描述")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.feedback_text.toPlainText()))
        export = QPushButton("导出无原文诊断摘要…")
        export.clicked.connect(self.export_support)
        layout.addWidget(copy)
        layout.addWidget(export)
        layout.addWidget(paragraph(
            "诊断摘要仅含版本、系统类型、模型状态和数据库检查结果；不含问题、回答、账号、密钥、网址、"
            "个人路径或原始日志。不自动上传。发送截图前，请先遮住聊天和个人信息。"
        ))
        self.tabs.addTab(scroll_page(feedback), "反馈问题")
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("返回会诊")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    def fill_sample(self):
        if self.window.question_input.text().strip():
            if QMessageBox.question(self, "替换当前问题？", "这只会填入示例，不会发送。要替换输入框中的内容吗？") != QMessageBox.StandardButton.Yes:
                return
        self.window.question_input.setText(SAMPLE_QUESTION)
        self.accept()
        self.window.question_input.setFocus()

    def check_readiness(self):
        if set(self.window.panes_by_id) != set(self.status_rows):
            QMessageBox.information(self, "阵容已变化", "请关闭并重新打开使用与帮助，以检查新阵容。")
            return
        self._check_id += 1
        check_id = self._check_id
        self.check_button.setEnabled(False)
        pending = set(self.status_rows)

        def done(sid, raw):
            if check_id != self._check_id or sid not in pending:
                return
            pending.remove(sid)
            data = raw if isinstance(raw, dict) else {}
            text = "无法确认 · 请检查网络、登录或验证码，再重新检查"
            if data.get("ok") and data.get("inputAvailable"):
                text = "输入框可用 · 请确认已登录，实际可用性以发送结果为准"
            elif data.get("ok"):
                text = "未找到可用输入框 · 请登录、等待加载或刷新该面板"
            self.status_rows[sid].setText(self.site_names[sid] + " · " + text)
            if not pending:
                self.check_button.setEnabled(True)

        for pane in self.window.panes:
            sid = pane.adapter.id
            if sid not in pending:
                continue
            self.status_rows[sid].setText(pane.adapter.name + " · 检查中…")
            if pane.busy:
                pending.remove(sid)
                self.status_rows[sid].setText(pane.adapter.name + " · 正在回答，请等待完成")
            else:
                pane._run_javascript(pane.adapter.readiness_script(), lambda raw, sid=sid: done(sid, raw))
        if not pending:
            self.check_button.setEnabled(True)
        QTimer.singleShot(8000, self, lambda: [done(sid, None) for sid in list(pending)])

    def check_health(self):
        try:
            ok = self.window.repository.health() == "ok"
            self.health_label.setText("检查通过：当前历史库结构正常。这不代表旧记录已恢复。" if ok else
                                      "检查未通过：请保留数据目录，不要删除或覆盖，请联系内测负责人。")
        except Exception:
            self.health_label.setText("无法读取当前历史库；请保留原文件，联系内测负责人。")

    def backup(self):
        if any(p.busy for p in self.window.panes) or any(d._busy for d in self.window.findChildren(ReportDialog)):
            QMessageBox.information(self, "请稍候", "请等待回答和报告任务结束后备份，避免遗漏新生成的内容。")
            return
        if QMessageBox.question(self, "私人备份", "备份包含问题、回答和报告，只供你自己保管。继续选择保存位置？") != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, "备份历史与报告", f"FourAI-private-backup-{datetime.now():%Y%m%d-%H%M%S}.zip", "ZIP (*.zip)")
        if not path:
            return
        try:
            create_backup(self.window.repository, self.window.runtime_dirs["reports"], Path(path))
        except Exception:
            QMessageBox.warning(self, "备份失败", "未生成有效备份。请检查保存位置权限、空间和数据库状态，原数据未被替换。")
            return
        QMessageBox.information(self, "备份完成", "已生成并校验备份。请自己保管；恢复前先保留当前数据，联系内测负责人协助验证。")

    def export_support(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出无原文诊断摘要", f"FourAI-support-{datetime.now():%Y%m%d-%H%M%S}.json", "JSON (*.json)")
        if not path:
            return
        try:
            if Path(path).suffix.lower() != ".json":
                raise ValueError("请选择 JSON 文件")
            write_json(Path(path), support_payload(self.window.panes, self.window.repository))
        except (OSError, ValueError):
            QMessageBox.warning(self, "导出失败", "请选择可写的 JSON 文件位置。")
            return
        QMessageBox.information(self, "已导出，未上传", "可先用记事本查看，再自行发给内测负责人。它不包含聊天原文或密钥。")
