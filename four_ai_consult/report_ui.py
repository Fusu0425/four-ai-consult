from __future__ import annotations

import copy
import re
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabBar,
    QTextBrowser,
    QVBoxLayout,
)

from .adapters import ADAPTER_BY_ID, SITE_ADAPTERS
from .analysis_plan import AnalysisPlan, ReportRecord, material_fingerprint
from .config import AppConfig, SecretStore
from .synthesis import DEEPSEEK_MODEL, SynthesisClient
from .web_synthesis import WebSynthesisDialog


def reading_markdown(text):
    """Collapse layout noise outside fenced code; never change stored material."""
    lines, fence, empty = [], None, False
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            mark = match.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            lines.append(line)
            empty = False
        elif fence:
            lines.append(line)
        elif line.strip():
            lines.append(line)
            empty = False
        elif not empty:
            lines.append("")
            empty = True
    return "\n".join(lines)


class SafeReportBrowser(QTextBrowser):
    def loadResource(self, resource_type, name):  # noqa: N802
        # Model-generated Markdown must not fetch images or local files.
        return QByteArray()


class ReportDialog(QDialog):
    def __init__(
        self,
        basic_report: str,
        report_dir: Path,
        parent=None,
        *,
        session=None,
        profile=None,
        config=None,
        repository=None,
        secret_store=None,
    ):
        super().__init__(parent)
        self.report_dir = report_dir
        self.basic_report = basic_report
        self.session = copy.deepcopy(session)
        self.profile = profile
        self.config = config or AppConfig()
        self.repository = repository
        self.secret_store = secret_store or SecretStore()
        self.record = None
        self.web_runner = None
        self._busy = False
        self._pending_material = None
        self._pages = []
        self._source_pages = {}
        self._documents = []
        self.api = SynthesisClient(self)
        self.api.progress.connect(self._progress)
        self.api.checkpoint.connect(self._checkpoint)
        self.api.finished.connect(self._finished)
        self.setWindowTitle("会诊报告 · 完整观点与比较")
        self.setObjectName("reportDialog")
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        self.resize(
            min(1080, int(area.width() * 0.86)) if area else 1000, min(780, int(area.height() * 0.84)) if area else 700
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        title = QLabel("会诊报告")
        title.setObjectName("reportTitle")
        root.addWidget(title)
        hint = QLabel("先读完整回答，再看有依据的比较。阅读排版不删字，采集文本与完整导出始终保留。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.material_notice = QLabel()
        self.material_notice.setWordWrap(True)
        self.material_notice.hide()
        root.addWidget(self.material_notice)
        if self.session:
            question = QLabel("本次问题：" + self.session.question[:160] + ("…" if len(self.session.question) > 160 else ""))
            question.setToolTip(self.session.question)
            question.setWordWrap(True)
            root.addWidget(question)
        settings = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["免费网页版", "API 加强版"])
        self.provider = QComboBox()
        self.generate = QPushButton("生成比较报告")
        self.generate.setObjectName("primaryButton")
        self.stop = QPushButton("停止")
        self.stop.hide()
        self.key_button = QPushButton("设置 API Key")
        self.key_button.clicked.connect(self.set_api_key)
        settings.addWidget(self.mode)
        settings.addWidget(self.provider, 1)
        settings.addWidget(self.key_button)
        settings.addWidget(self.generate)
        settings.addWidget(self.stop)
        root.addLayout(settings)
        self.status = QLabel("尚未综合：先查看采集原文，或选择综合模型生成报告。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.sections = QTabBar()
        for label in ("会诊结论", "完整回答", "分析附页"):
            self.sections.addTab(label)
        self.sections.setExpanding(False)
        root.addWidget(self.sections)
        reading = QHBoxLayout()
        self.article_title = QLabel()
        self.article_title.setWordWrap(True)
        self.verbatim = QCheckBox("查看采集文本")
        self.verbatim.setToolTip("保留原始空白和 Markdown 标记；复制与导出一直使用未改写的文本。")
        reading.addWidget(self.article_title, 1)
        reading.addWidget(self.verbatim)
        root.addLayout(reading)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.navigation = QListWidget()
        self.navigation.setMinimumWidth(160)
        self.viewer = self._make_text("")
        self.viewer.setOpenLinks(False)
        self.viewer.anchorClicked.connect(self._source_clicked)
        splitter.addWidget(self.navigation)
        splitter.addWidget(self.viewer)
        splitter.setSizes([190, 830])
        root.addWidget(splitter, 1)
        footer = QHBoxLayout()
        previous = QPushButton("上一页")
        following = QPushButton("下一页")
        self.page_label = QLabel()
        export = QPushButton("导出完整报告")
        copy_button = QPushButton("复制当前内容")
        previous.clicked.connect(lambda: self._move_page(-1))
        following.clicked.connect(lambda: self._move_page(1))
        export.clicked.connect(self.save_current)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.current_text()))
        footer.addWidget(previous)
        footer.addWidget(self.page_label)
        footer.addWidget(following)
        footer.addStretch()
        footer.addWidget(copy_button)
        footer.addWidget(export)
        root.addLayout(footer)
        self.navigation.currentRowChanged.connect(self._show_page)
        self.sections.currentChanged.connect(self._filter_pages)
        self.verbatim.toggled.connect(lambda: self._show_page(self.navigation.currentRow()))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.provider.currentIndexChanged.connect(self._load_record)
        self.generate.clicked.connect(self.start_analysis)
        self.stop.clicked.connect(self.stop_analysis)
        self._mode_changed()
        self._restore_last_channel()
        self.generate.setEnabled(bool(self.session and self.session.successful_results))
        if self.record and self.record.status != "complete":
            self.sections.setCurrentIndex(1)

    def _restore_last_channel(self):
        if not self.session or not self.repository:
            return
        try:
            records = [r for r in self.repository.analysis_records(self.session.id)
                       if r.fingerprint == material_fingerprint(self.session)
                       and self.mode.findText(r.mode) >= 0]
        except Exception:
            return
        if not records:
            return
        latest = max(records, key=lambda r: r.updated_at)
        self.mode.setCurrentIndex(self.mode.findText(latest.mode))
        index = self.provider.findData(latest.provider)
        if index >= 0:
            self.provider.setCurrentIndex(index)

    def update_session(self, session, basic_report):
        if not self.session or session.id != self.session.id:
            return
        if material_fingerprint(session) == material_fingerprint(self.session):
            return
        if self._busy:
            self._pending_material = (copy.deepcopy(session), basic_report)
            self.material_notice.setText("原文已更新；当前综合仍使用旧材料。任务结束后将载入新原文，请重新生成比较。")
            self.material_notice.show()
            return
        self.session = copy.deepcopy(session)
        self.basic_report = basic_report
        self._pending_material = None
        self._load_record()
        self.generate.setEnabled(bool(self.session.successful_results))
        self.material_notice.setText("已更新采集原文。旧比较报告不再作为本轮结论，请基于新原文重新生成。")
        self.material_notice.show()

    @staticmethod
    def _make_text(content):
        editor = SafeReportBrowser()
        editor.setObjectName("reportViewer")
        editor.setReadOnly(True)
        editor.setStyleSheet("QTextBrowser { font-family: 'Microsoft YaHei UI'; font-size: 15px; }")
        editor.document().setDefaultFont(QFont("Microsoft YaHei UI", 11))
        editor.document().setDefaultStyleSheet(
            "h1{font-size:24px;color:#302A24;} h2{font-size:18px;color:#6C3F2E;margin-top:20px;}"
            "h3{font-size:15px;color:#4B4037;} p,li{line-height:1.35;} a{color:#9C563C;}"
            "blockquote{color:#76503D;} td,th{padding:8px;}"
        )
        editor.setMarkdown(content)
        ReportDialog._space_text(editor)
        return editor

    @staticmethod
    def _space_text(editor):
        block = editor.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = QTextBlockFormat()
            code = block.blockFormat().nonBreakableLines()
            fmt.setLineHeight(112 if code else 128, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setTopMargin(8 if block.blockFormat().headingLevel() else 0)
            fmt.setBottomMargin(4 if block.text().strip() else 0)
            cursor.mergeBlockFormat(fmt)
            block = block.next()

    def _mode_changed(self, *_):
        self.key_button.setVisible(self.mode.currentIndex() == 1)
        self.provider.blockSignals(True)
        self.provider.clear()
        if self.mode.currentIndex() == 0:
            for adapter in SITE_ADAPTERS:
                self.provider.addItem(adapter.name, adapter.id)
        else:
            for model in (DEEPSEEK_MODEL, "deepseek-v4-pro"):
                self.provider.addItem("DeepSeek · " + model, model)
        self.provider.blockSignals(False)
        self._load_record()

    def _load_record(self, *_):
        self.record = None
        load_error = ""
        if self.session:
            mode, provider = self.mode.currentText(), self.provider.currentData()
            try:
                records = self.repository.analysis_records(self.session.id) if self.repository else []
            except Exception:
                records = []
                load_error = "历史综合暂时读取失败；本轮完整回答仍可阅读和生成，请及时导出。"
            self.record = next(
                (
                    r
                    for r in records
                    if r.mode == mode and r.provider == provider and r.fingerprint == material_fingerprint(self.session)
                ),
                None,
            )
            if self.record is None:
                self.record = AnalysisPlan(self.session, mode, provider).record
            snapshot = self.record.snapshot_path(self.report_dir)
            if snapshot.exists():
                try:
                    recovered = ReportRecord.from_json(snapshot.read_text(encoding="utf-8"))
                    if (recovered.session_id, recovered.fingerprint, recovered.mode, recovered.provider) == (
                        self.session.id, self.record.fingerprint, mode, provider
                    ):
                        if not recovered.updated_at:
                            recovered.updated_at = snapshot.stat().st_mtime
                        if recovered.updated_at > self.record.updated_at:
                            self.record = recovered
                except (OSError, ValueError, TypeError, KeyError):
                    load_error = "独立报告备份读取失败；本轮原文仍可阅读，请及时导出。"
            # Old checkpoints predate unconfirmed-original pages. Recover these
            # from saved answers without changing their status or using them in synthesis.
            self.record.unconfirmed = AnalysisPlan(self.session, mode, provider).record.unconfirmed
        self._render()
        if load_error:
            self.status.setText(load_error)

    def _render(self):
        selected = self.navigation.currentRow()
        selected_title = self._pages[selected][0] if 0 <= selected < len(self._pages) else None
        self._documents = self.record.documents() if self.record else [("材料与状态", self.basic_report)]
        self._pages = []
        self._source_pages = {}
        for title, text in self._documents:
            raw = title.startswith("原文 ·")
            if raw:
                for source in self.record.sources:
                    if title == f"原文 · {source.site_name}":
                        self._source_pages[source.id] = len(self._pages)
            # A full-width article per model. Do not sever code fences/tables at
            # arbitrary character boundaries; long analysis remains in appendices.
            parts = [text]
            for index, part in enumerate(parts, 1):
                label = title + (f" · 页 {index}/{len(parts)}" if len(parts) > 1 else "")
                self._pages.append((label, part, raw))
        self.navigation.blockSignals(True)
        self.navigation.clear()
        self.navigation.addItems([p[0] for p in self._pages])
        self.navigation.blockSignals(False)
        selected = next((i for i, page in enumerate(self._pages) if page[0] == selected_title), 0)
        self.navigation.setCurrentRow(selected)
        self._filter_pages()
        if self.record:
            labels = {
                "complete": "综合已生成；建议核对关键依据",
                "error": "未完成 · 可保留进度重试",
                "cancelled": "已停止 · 已完成详析保留",
                "running": "正在分析",
                "pending": "尚未综合",
            }
            self.status.setText(
                labels.get(self.record.status, self.record.status)
                + (" · 直接读取完整原文" if self.record.direct else
                   f" · 已详析 {len(self.record.notes)}/{len(self.record.sources)} 段")
                + (f" · {self.record.error}" if self.record.error else "")
            )
            self.generate.setText("重新生成" if self.record.status == "complete" else
                                  "继续生成" if self.record.notes else "生成比较报告")

    def _category(self, page):
        title, _, raw = page
        return 1 if raw else 0 if title in {"结论与对比", "材料与状态"} else 2

    def _filter_pages(self, *_):
        visible = []
        for index, page in enumerate(self._pages):
            show = self._category(page) == self.sections.currentIndex()
            self.navigation.item(index).setHidden(not show)
            if show:
                visible.append(index)
        if self.navigation.currentRow() not in visible:
            self.navigation.setCurrentRow(visible[0] if visible else -1)
        if not visible:
            self.viewer.setPlainText("这里将保留长材料的逐家详析、详细对比附篇以及未完成输出。")
            self.article_title.setText("暂无分析附页")
            self.verbatim.hide()
            self.page_label.setText("0 / 0")
        self.navigation.setVisible(len(visible) > 1)

    def _move_page(self, offset):
        visible = [i for i, page in enumerate(self._pages) if self._category(page) == self.sections.currentIndex()]
        current = self.navigation.currentRow()
        if current in visible:
            self.navigation.setCurrentRow(visible[max(0, min(len(visible) - 1, visible.index(current) + offset))])

    def _show_page(self, index):
        if not 0 <= index < len(self._pages):
            return
        title, text, raw = self._pages[index]
        self.article_title.setText(f"{title}  ·  {len(text):,} 字符" if raw else title)
        self.verbatim.setVisible(raw)
        # A clicked anchor's character style must not leak into the raw page.
        self.viewer.setCurrentCharFormat(QTextCharFormat())
        if raw and self.verbatim.isChecked():
            self.viewer.setPlainText(text)
        elif raw:
            self.viewer.setMarkdown(reading_markdown(text))
        else:

            def link(match):
                sid = match.group(1)
                return f"[{sid}](source:{sid})" if sid in self._source_pages else match.group()

            self.viewer.setMarkdown(reading_markdown(re.sub(r"\[(S\d+-\d+)\](?!\()", link, text)))
        self._space_text(self.viewer)
        visible = [i for i, page in enumerate(self._pages) if self._category(page) == self._category(self._pages[index])]
        self.page_label.setText(f"{visible.index(index) + 1} / {len(visible)}")

    def _source_clicked(self, url):
        if url.scheme() == "source":
            page = self._source_pages.get(url.path())
            if page is not None:
                self.sections.setCurrentIndex(1)
                self.navigation.setCurrentRow(page)
                # Same-model citations can share an article; repaint even if the
                # selection did not change, then locate the cited fragment.
                self._show_page(page)
                source = next((s for s in self.record.sources if s.id == url.path()), None)
                if source:
                    needle = next((line.strip().lstrip("#*- ") for line in source.text.splitlines() if line.strip()), "")
                    cursor = self.viewer.document().find(needle[:80]) if needle else QTextCursor()
                    if not cursor.isNull():
                        self.viewer.setTextCursor(cursor)
                        self.viewer.ensureCursorVisible()

    def _set_busy(self, busy):
        self._busy = busy
        self.mode.setEnabled(not busy)
        self.provider.setEnabled(not busy)
        self.key_button.setEnabled(not busy)
        self.generate.setEnabled(not busy)
        self.stop.setVisible(busy)

    def set_api_key(self):
        key, ok = QInputDialog.getText(self, "API 加强版", "输入新的 DeepSeek API Key：", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if not self.secret_store.remember_for_session(key):
            QMessageBox.warning(self, "Key 格式不正确", "请检查 Key 格式；不要包含空格或换行。")
            return
        if (
            QMessageBox.question(self, "保存 Key？", "是否保存到 Windows 凭据库？选择否则仅本次运行使用。")
            == QMessageBox.StandardButton.Yes
        ):
            if not self.secret_store.save_to_keyring(key):
                QMessageBox.warning(self, "未持久保存", "凭据库不可用，Key 仅在本次运行内保留。")

    def start_analysis(self):
        if self._busy or not self.session:
            return
        free = self.mode.currentIndex() == 0
        if free and QApplication.instance().property("web_report_busy"):
            QMessageBox.information(
                self, "已有网页综合任务", "请先完成或停止另一份网页报告，以免多个网页争用输入焦点。"
            )
            return
        if free and self.profile is None:
            QMessageBox.information(self, "无法打开网页", "请从主程序打开报告以共享已登录的浏览器会话。")
            return
        provider = self.provider.currentData()
        plan = AnalysisPlan(
            self.session, self.mode.currentText(), provider, input_limit=24000 if free else 100000, resume=self.record
        )
        try:
            first = plan.next_task()
        except ValueError as error:
            QMessageBox.warning(self, "材料暂无法处理", str(error))
            return
        requests = 1 if first and first.is_final else len(plan.record.sources) - len(plan.record.notes) + 1
        notice = (
            f"将把本轮完整问题、各家回答和逐段分析发送给 {self.provider.currentText()}。\n\n"
            f"预计至少 {requests} 次请求；常规材料直接对比，长材料分篇处理。原文不删减。\n"
            "格式失败时同一通道自动重试一次，可能增加请求；不自动更换服务商。\n\n"
            + (
                "使用你已登录的网页账号，不调用付费 API；仍受网站额度和规则限制。"
                "将打开临时综合窗口；登录或验证码需要你手动完成。"
                if free
                else "API 请求会消耗你的账户额度，长材料会有多次请求。不会在失败后自动换服务商。"
            )
            + "\n\n允许本轮发送并开始？"
        )
        if QMessageBox.question(self, "确认综合通道与材料发送", notice) != QMessageBox.StandardButton.Yes:
            return
        key = ""
        if not free:
            key = self.secret_store.load_deepseek_key()
            if not key:
                key, ok = QInputDialog.getText(
                    self, "API 加强版", "DeepSeek API Key（本次会话使用）：", QLineEdit.EchoMode.Password
                )
                if not ok or not self.secret_store.remember_for_session(key):
                    return
        self.record = plan.record
        self._set_busy(True)
        if free:
            QApplication.instance().setProperty("web_report_busy", True)
            if self.web_runner:
                self.web_runner.close()
                self.web_runner.deleteLater()
            self.web_runner = WebSynthesisDialog(plan, ADAPTER_BY_ID[provider], self.profile, self.config, self)
            self.web_runner.checkpoint.connect(self._checkpoint)
            self.web_runner.completed.connect(self._finished)
            self.web_runner.setWindowModality(Qt.WindowModality.WindowModal)
            self.web_runner.show()
        else:
            self.api.ask(key, plan, provider)

    def _progress(self, text):
        self.status.setText(text)

    def _checkpoint(self, payload):
        self.record = ReportRecord.from_json(payload)
        save_error = ""
        snapshot_saved = False
        try:
            self.record.save_snapshot(self.report_dir)
            snapshot_saved = True
        except OSError:
            save_error = "独立报告备份保存失败；请导出报告留存。"
        if self.repository:
            try:
                self.repository.save_analysis(self.record)
            except Exception:
                save_error = ("历史库保存失败；已保存独立报告备份，仍可继续阅读和导出。" if snapshot_saved else
                              "本地保存失败，请立即导出完整报告；当前内容仍在窗口中。")
        self._render()
        if save_error:
            self.status.setText(save_error)

    def _finished(self):
        if self.mode.currentIndex() == 0:
            QApplication.instance().setProperty("web_report_busy", False)
        self._set_busy(False)
        if self._pending_material:
            self.update_session(*self._pending_material)
            self.sections.setCurrentIndex(1)
            return
        if self.record and self.record.status == "complete":
            self.sections.setCurrentIndex(0)
            self.navigation.setCurrentRow(0)

    def stop_analysis(self):
        if self.api.running:
            self.api.cancel()
            self.status.setText("已请求停止：不再发送下一步，正在等待当前请求结束。")
        elif self.web_runner:
            self.web_runner.reject()

    def current_text(self):
        index = self.navigation.currentRow()
        return self._pages[index][1] if 0 <= index < len(self._pages) else ""

    def save_current(self):
        text = self.record.markdown() if self.record else self.basic_report
        path, _ = QFileDialog.getSaveFileName(
            self, "导出全部页面和原文", str(self.report_dir / "完整会诊报告.md"), "Markdown (*.md)"
        )
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "导出失败", str(error))

    def reject(self):
        if self._busy:
            self.stop_analysis()
            self.status.setText("已请求停止。当前步骤收尾后即可关闭；已完成内容会保留。")
            return
        super().reject()
