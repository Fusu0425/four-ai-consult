from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .adapters import ADAPTER_BY_ID, BACKUP_SITE_IDS, PRIMARY_SITE_IDS, SITE_ADAPTERS
from .config import AppConfig, SecretStore, local_settings
from .consensus import build_basic_report
from .models import AnswerResult, ConsultationSession, PaneState
from .pilot import sanitized_diagnostic, write_json
from .pilot_ui import PilotCenter
from .report_ui import ReportDialog, SafeReportBrowser
from .storage import ConsultationRepository, HistoryItem
from .webpane import WebPane

logger = logging.getLogger("four_ai_consult")

HANDLE_WIDTH = 7
GRIP_SIZE = 22


def make_icon() -> QIcon:
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    icon_path = resource_root / "resources" / "four-ai-consult.png"
    if icon_path.exists():
        return QIcon(str(icon_path))
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    for index, color in enumerate(("#9C563C", "#B97855", "#6F8068", "#C39A56")):
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        x = (index % 2) * 32 + 2
        y = (index // 2) * 32 + 2
        painter.drawRoundedRect(x, y, 28, 28, 5, 5)
    painter.end()
    return QIcon(pixmap)


class CenterGrip(QWidget):
    def __init__(self, apply_sync, parent: QWidget) -> None:
        super().__init__(parent)
        self.apply_sync = apply_sync
        self._dragging = False
        self._start_global = None
        self._start_left = 0
        self._start_top = 0
        self.setFixedSize(GRIP_SIZE, GRIP_SIZE)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("拖动中心点：同步调整四格；拖分割线：单独调整")

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#9C563C"))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor("white"), 2))
        center = self.rect().center()
        painter.drawLine(center.x() - 5, center.y(), center.x() + 5, center.y())
        painter.drawLine(center.x(), center.y() - 5, center.x(), center.y() + 5)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parentWidget()
            self._dragging = True
            self._start_global = event.globalPosition().toPoint()
            self._start_left, self._start_top = parent.current_split_position()
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._dragging or self._start_global is None:
            return
        delta = event.globalPosition().toPoint() - self._start_global
        self.apply_sync(self._start_left + delta.x(), self._start_top + delta.y())
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._dragging = False
        self._start_global = None
        event.accept()


class SplitContainer(QWidget):
    """Owns the nested splitters and center grip so coordinate systems stay aligned."""

    splitter_moved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.outer = QSplitter(Qt.Orientation.Horizontal)
        self.left = QSplitter(Qt.Orientation.Vertical)
        self.right = QSplitter(Qt.Orientation.Vertical)
        self.outer.addWidget(self.left)
        self.outer.addWidget(self.right)
        for splitter in (self.outer, self.left, self.right):
            splitter.setHandleWidth(HANDLE_WIDTH)
            splitter.setOpaqueResize(False)
            splitter.setStyleSheet(
                "QSplitter::handle{background:#D7CCBD;}"
                "QSplitter::handle:hover{background:#B97855;}"
                "QSplitter::handle:pressed{background:#9C563C;}"
            )
            splitter.splitterMoved.connect(self._moved)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.outer)
        self.grip = CenterGrip(self.apply_sync, self)
        self._maximized = False

    def add_pane(self, pane: WebPane) -> None:
        (self.left if pane.col == 0 else self.right).addWidget(pane)
        pane.setMinimumSize(0, 0)
        splitter = self.left if pane.col == 0 else self.right
        splitter.setCollapsible(splitter.count() - 1, True)
        self.outer.setCollapsible(pane.col, True)

    def current_split_position(self) -> tuple[int, int]:
        outer_sizes = self.outer.sizes()
        left_sizes = self.left.sizes()
        return (outer_sizes[0] if outer_sizes else 0, left_sizes[0] if left_sizes else 0)

    def apply_sync(self, left_width: int, top_height: int) -> None:
        width = max(1, self.outer.width() - HANDLE_WIDTH)
        height = max(1, self.left.height() - HANDLE_WIDTH)
        left_width = max(0, min(width, left_width))
        top_height = max(0, min(height, top_height))
        self.outer.setSizes([left_width, width - left_width])
        self.left.setSizes([top_height, height - top_height])
        self.right.setSizes([top_height, height - top_height])
        self.reposition_grip()

    def equalize(self) -> None:
        self.apply_sync(
            max(1, self.outer.width() - HANDLE_WIDTH) // 2,
            max(1, self.left.height() - HANDLE_WIDTH) // 2,
        )

    def reposition_grip(self) -> None:
        if self._maximized:
            self.grip.hide()
            return
        left_width, top_height = self.current_split_position()
        x = left_width + HANDLE_WIDTH // 2 - GRIP_SIZE // 2
        y = top_height + HANDLE_WIDTH // 2 - GRIP_SIZE // 2
        self.grip.move(x, y)
        self.grip.show()
        self.grip.raise_()

    def set_maximized(self, active: bool) -> None:
        self._maximized = active
        self.reposition_grip()

    def _moved(self, *args) -> None:
        self.reposition_grip()
        self.splitter_moved.emit()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.reposition_grip()




class HistoryDialog(QDialog):
    def __init__(self, repository: ConsultationRepository, report_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.report_dir = report_dir
        self.items_by_id: dict[str, HistoryItem] = {}
        self.setWindowTitle("会诊历史")
        self.resize(1080, 720)

        root = QVBoxLayout(self)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("按问题关键词搜索本机记录……")
        search_row.addWidget(self.search_input, 1)
        root.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(300)
        self.preview = SafeReportBrowser()
        self.preview.setOpenLinks(False)
        self.preview.setObjectName("historyPreview")
        self.preview.setReadOnly(True)
        splitter.addWidget(self.list_widget)
        splitter.addWidget(self.preview)
        splitter.setSizes([340, 740])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        copy_button = QPushButton("复制")
        export_button = QPushButton("导出 Markdown")
        delete_button = QPushButton("删除")
        close_button = QPushButton("关闭")
        open_button = QPushButton("打开多页报告 / 继续综合")
        open_button.clicked.connect(self.open_selected)
        buttons.addWidget(open_button)
        buttons.addWidget(copy_button)
        buttons.addWidget(export_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self.search_input.textChanged.connect(self.refresh)
        self.list_widget.currentItemChanged.connect(self._show_selected)
        copy_button.clicked.connect(self.copy_selected)
        export_button.clicked.connect(self.export_selected)
        delete_button.clicked.connect(self.delete_selected)
        close_button.clicked.connect(self.close)
        self.refresh()

    def refresh(self, *_args) -> None:
        selected_id = self._selected_id()
        self.list_widget.clear()
        try:
            self.items_by_id = {item.id: item for item in self.repository.list(self.search_input.text())}
        except Exception:
            self.items_by_id = {}
            self.preview.setPlainText("历史数据库暂时无法读取。未删除或重建数据；请保留文件并检查独立报告备份。")
            return
        for item in self.items_by_id.values():
            started = item.started_at.replace("T", " ")[:16]
            question = item.question.replace("\n", " ")
            label = f"{started}  [{item.successful_count}/{item.total_count}]\n{question}"
            widget_item = QListWidgetItem(label)
            widget_item.setData(Qt.ItemDataRole.UserRole, item.id)
            self.list_widget.addItem(widget_item)
            if item.id == selected_id:
                self.list_widget.setCurrentItem(widget_item)
        if self.list_widget.currentItem() is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        if not self.list_widget.count():
            self.preview.setMarkdown("## 暂无记录\n\n没有匹配的本地会诊记录。")

    def _selected_id(self) -> str:
        item = self.list_widget.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _show_selected(self, *_args) -> None:
        self.preview.setMarkdown(self._selected_report())

    def _selected_report(self) -> str:
        item = self.items_by_id.get(self._selected_id())
        if not item:
            return ""
        from .analysis_plan import material_fingerprint

        try:
            session = self.repository.load_session(item.id)
            if session is None:
                return item.report
            records = self.repository.analysis_records(item.id)
        except Exception:
            return item.report
        record = next((r for r in records if r.fingerprint == material_fingerprint(session)), None)
        return record.markdown() if record else item.report

    def open_selected(self) -> None:
        item = self.items_by_id.get(self._selected_id())
        if not item:
            return
        parent = self.parentWidget()
        try:
            session = self.repository.load_session(item.id)
        except Exception:
            session = None
        self.report_window = ReportDialog(item.report, self.report_dir, self,
            session=session, profile=getattr(parent, "profile", None),
            config=getattr(parent, "config", None), repository=self.repository,
            secret_store=getattr(parent, "secret_store", None))
        self.report_window.show()

    def copy_selected(self) -> None:
        QApplication.clipboard().setText(self._selected_report())

    def export_selected(self) -> None:
        item = self.items_by_id.get(self._selected_id())
        if not item:
            return
        default = self.report_dir / f"会诊报告-{item.started_at[:10]}-{item.id[:8]}.md"
        path, _ = QFileDialog.getSaveFileName(self, "导出会诊记录", str(default), "Markdown (*.md)")
        if path:
            Path(path).write_text(self._selected_report(), encoding="utf-8")

    def delete_selected(self) -> None:
        item = self.items_by_id.get(self._selected_id())
        if not item:
            return
        choice = QMessageBox.question(self, "删除会诊记录？", "该操作只删除本机记录，且无法撤销。")
        if choice == QMessageBox.StandardButton.Yes:
            self.repository.delete(item.id)
            self.refresh()


class MainWindow(QMainWindow):
    def __init__(
        self,
        profile: QWebEngineProfile,
        runtime_dirs: dict[str, Path],
        config: AppConfig,
        secret_store: SecretStore,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.runtime_dirs = runtime_dirs
        self.config = config
        self.secret_store = secret_store
        self.settings = local_settings(runtime_dirs["root"])
        self.repository = ConsultationRepository(runtime_dirs["database"])
        self.session: ConsultationSession | None = None
        self.report_dialog: ReportDialog | None = None
        self.history_dialog: HistoryDialog | None = None
        self._reported_session_id = ""
        self._quitting = False
        self._maximized_pane: WebPane | None = None
        self._saved_split_sizes: tuple[list[int], list[int], list[int]] | None = None
        self._programmatic_split = False
        self._diagnostics: dict[str, str] = {}
        self._shown_once = False
        self._welcome_scheduled = False
        self._cancelling = False
        self._model_signal_guard = False
        self._save_failed = False
        self.help_dialog = None
        self.active_model_ids = self._load_active_model_ids()

        self.setWindowTitle(f"四模型会诊 · {__version__} 内测版")
        self.setWindowIcon(make_icon())

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())

        self.progress_label = QLabel("请先分别登录四个 AI，然后在上方输入问题。")
        self.progress_label.setObjectName("progressLabel")
        self.progress_label.setContentsMargins(18, 8, 18, 9)
        root.addWidget(self.progress_label)
        self.storage_warning = QLabel("本轮保存失败：请先查看报告并导出原文，不要直接退出。可到「使用与帮助」查看数据位置。")
        self.storage_warning.setWordWrap(True)
        self.storage_warning.setContentsMargins(18, 5, 18, 5)
        self.storage_warning.setStyleSheet("color:#963b2a;background:#fff0de;")
        self.storage_warning.hide()
        root.addWidget(self.storage_warning)

        self.split_container = SplitContainer()
        self.split_container.splitter_moved.connect(self._on_splitter_moved)
        self.panes: list[WebPane] = []
        self.panes_by_id: dict[str, WebPane] = {}
        for index, site_id in enumerate(self.active_model_ids):
            adapter = ADAPTER_BY_ID[site_id]
            logger.info("Creating web pane: %s", adapter.id)
            pane = WebPane(
                adapter=adapter,
                profile=profile,
                config=config,
                col=index % 2,
                row=index // 2,
                on_fullscreen=self.toggle_maximize,
            )
            pane.state_changed.connect(self._on_pane_state)
            pane.answer_ready.connect(self._on_answer_ready)
            pane.collection_started.connect(self._on_collection_started)
            pane.diagnostic_ready.connect(self._on_diagnostic_ready)
            self.split_container.add_pane(pane)
            self.panes.append(pane)
            self.panes_by_id[adapter.id] = pane
            logger.info("Web pane ready: %s", adapter.id)
        root.addWidget(self.split_container, 1)
        self.setCentralWidget(central)

        self._apply_initial_geometry()
        self._setup_tray()

    def _load_active_model_ids(self) -> list[str]:
        saved = [item for item in str(self.settings.value("enabled_models", "")).split(",") if item]
        unique = list(dict.fromkeys(item for item in saved if item in ADAPTER_BY_ID))
        return unique if len(unique) == 4 else list(PRIMARY_SITE_IDS)

    def _apply_initial_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            self.resize(1260, 765)
            return
        available = screen.availableGeometry()
        saved = self.settings.value("geometry_v2")
        if saved and self.restoreGeometry(saved):
            frame = self.frameGeometry()
            restored_screen = QApplication.screenAt(frame.center())
            if (
                restored_screen
                and frame.width() <= restored_screen.availableGeometry().width() * 0.94
                and frame.height() <= restored_screen.availableGeometry().height() * 0.94
            ):
                return
        target_height = min(765, int(available.height() * 0.84))
        target_height = min(max(420, target_height), max(240, available.height() - 36))
        target_width = min(1260, int(available.width() * 0.84), round(target_height * 1680 / 1020))
        target_width = min(max(760, target_width), max(320, available.width() - 36))
        self.resize(target_width, target_height)
        self.move(
            available.x() + (available.width() - target_width) // 2,
            available.y() + (available.height() - target_height) // 2,
        )

    def _build_toolbar(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("headerPanel")
        toolbar = QVBoxLayout(panel)
        toolbar.setContentsMargins(18, 9, 18, 9)
        toolbar.setSpacing(7)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        brand_icon = QLabel()
        brand_icon.setPixmap(make_icon().pixmap(24, 24))
        title = QLabel("四模型会诊")
        title.setObjectName("appTitle")
        local_badge = QLabel("免费网页 · 内测版")
        local_badge.setObjectName("localBadge")

        self.question_input = QLineEdit()
        self.question_input.setObjectName("questionInput")
        self.question_input.setPlaceholderText("写下这次真正需要判断的问题……")
        self.question_input.returnPressed.connect(self.broadcast)
        self.send_button = QPushButton("开始会诊")
        self.send_button.setObjectName("primaryButton")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("quietButton")
        self.cancel_button.hide()
        self.report_button = QPushButton("查看报告")
        self.report_button.setEnabled(False)
        history_button = QPushButton("历史记录")
        help_button = QPushButton("使用与帮助")
        help_button.setObjectName("quietButton")
        help_button.clicked.connect(self.show_help)
        self.models_button = QPushButton()
        model_menu = QMenu(self.models_button)
        model_menu.addSection("主力模型（默认）")
        active_models = set(self.active_model_ids)
        self.model_actions: dict[str, QAction] = {}
        for adapter in SITE_ADAPTERS:
            if adapter.id in BACKUP_SITE_IDS and not any(
                action.text() == "候补模型" for action in model_menu.actions()
            ):
                model_menu.addSeparator()
                model_menu.addSection("候补模型")
            action = model_menu.addAction(adapter.name)
            action.setCheckable(True)
            action.setChecked(adapter.id in active_models)
            action.toggled.connect(
                lambda checked, site_id=adapter.id: self._on_model_toggled(site_id, checked)
            )
            self.model_actions[adapter.id] = action
        self.models_button.setMenu(model_menu)
        self.models_button.setText(f"会诊阵容 4/{len(SITE_ADAPTERS)}")
        more_button = QPushButton("更多")
        more_button.setObjectName("quietButton")
        more_menu = QMenu(more_button)
        self.auto_report = more_menu.addAction("完成后自动打开报告")
        self.auto_report.setCheckable(True)
        self.auto_report.setChecked(True)
        more_menu.addSeparator()
        equal_action = more_menu.addAction("重置四宫格布局")
        diagnose_action = more_menu.addAction("运行适配诊断")
        reload_action = more_menu.addAction("刷新全部网页")
        more_button.setMenu(more_menu)
        for button in (history_button, self.report_button):
            button.setObjectName("quietButton")

        self.send_button.clicked.connect(self.broadcast)
        self.cancel_button.clicked.connect(self.cancel_all)
        self.report_button.clicked.connect(self.show_report)
        history_button.clicked.connect(self.show_history)
        equal_action.triggered.connect(self.reset_split)
        diagnose_action.triggered.connect(self.run_diagnostics)
        reload_action.triggered.connect(self.reload_all)

        brand_row.addWidget(brand_icon)
        brand_row.addWidget(title)
        brand_row.addWidget(local_badge)
        brand_row.addStretch(1)
        brand_row.addWidget(self.report_button)
        brand_row.addWidget(history_button)
        brand_row.addWidget(help_button)

        compose_row = QHBoxLayout()
        compose_row.setSpacing(7)
        compose_row.addWidget(self.question_input, 1)
        compose_row.addWidget(self.models_button)
        compose_row.addWidget(self.send_button)
        compose_row.addWidget(self.cancel_button)
        compose_row.addWidget(more_button)

        toolbar.addLayout(brand_row)
        toolbar.addLayout(compose_row)
        return panel

    def broadcast(self) -> None:
        if self._save_failed:
            self._save_session()
            if self._save_failed:
                QMessageBox.warning(self, "上一轮尚未保存", "请先导出上一轮报告并检查数据目录。保存恢复后再开始新问题，避免覆盖唯一的内存结果。")
                return
        question = self.question_input.text().strip()
        if not question:
            self.progress_label.setText("请先输入问题。")
            self.question_input.setFocus()
            return
        if any(pane.busy for pane in self.panes):
            choice = QMessageBox.question(self, "开始新会诊？", "当前仍有模型正在回答。要取消它们并开始新问题吗？")
            if choice != QMessageBox.StandardButton.Yes:
                return
            self.cancel_all()

        selected_panes = list(self.panes)
        self.session = ConsultationSession(question=question, site_ids=tuple(p.adapter.id for p in selected_panes))
        self._reported_session_id = ""
        self.report_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.models_button.setEnabled(False)
        self.cancel_button.show()
        self.progress_label.setText(f"正在把问题发送给 {len(selected_panes)} 个 AI……")
        self._save_session()
        for pane in selected_panes:
            pane.dispatch(question, self.session.id)

    def cancel_all(self) -> None:
        self._cancelling = True
        try:
            for pane in self.panes:
                pane.cancel()
        finally:
            self._cancelling = False
        self.send_button.setEnabled(True)
        self.models_button.setEnabled(True)
        self.cancel_button.hide()
        self.progress_label.setText("本轮会诊已取消。")

    def _on_pane_state(self, site_id: str, state: PaneState) -> None:
        if self.session and state == PaneState.SENDING:
            self.session.results.pop(site_id, None)
            self._reported_session_id = ""
        counts = {item: 0 for item in PaneState}
        active_site_ids = set(self.session.site_ids) if self.session else {pane.adapter.id for pane in self.panes}
        for pane in self.panes:
            if pane.adapter.id not in active_site_ids:
                continue
            counts[pane.state] += 1
        self.progress_label.setText(
            f"已完成 {counts[PaneState.DONE]} · 生成中 {counts[PaneState.GENERATING]} · "
            f"发送中 {counts[PaneState.SENDING]} · 失败 {counts[PaneState.ERROR]}"
        )

    def _on_collection_started(self, site_id: str) -> None:
        if not self.session or site_id not in self.session.site_ids:
            return
        self.session.results.pop(site_id, None)
        self.send_button.setEnabled(False)
        self.models_button.setEnabled(False)
        self.cancel_button.show()

    def _on_answer_ready(self, result: AnswerResult) -> None:
        if not self.session or result.question != self.session.question:
            return
        self.session.add_result(result)
        # Persist each terminal answer, including partial/error output. A crash
        # while another provider is still running must not lose this answer.
        self._save_session()
        if self.report_dialog:
            self.report_dialog.update_session(self.session, build_basic_report(self.session))
        self.report_button.setEnabled(True)
        if not self.session.complete:
            return
        self.send_button.setEnabled(True)
        self.models_button.setEnabled(True)
        self.cancel_button.hide()
        self.report_button.setEnabled(True)
        successes = len(self.session.successful_results)
        self.progress_label.setText(
            f"本轮完成：{successes}/{len(self.session.site_ids)} 家获得回答，可以查看会诊报告。"
        )
        if not self._cancelling and self.auto_report.isChecked() and self._reported_session_id != self.session.id:
            self._reported_session_id = self.session.id
            self.show_report()

    def _save_session(self) -> None:
        if not self.session:
            return
        try:
            self.repository.save(self.session, build_basic_report(self.session))
            self._save_failed = False
        except Exception:
            self._save_failed = True
            logger.exception("Failed to save consultation history")
        self.storage_warning.setVisible(self._save_failed)

    def show_help(self) -> None:
        if self.help_dialog:
            self.help_dialog.close()
            self.help_dialog.deleteLater()
        self.help_dialog = PilotCenter(self)
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.settings.setValue("onboarding_completed", True)

    def show_report(self) -> None:
        if not self.session or not self.session.results:
            QMessageBox.information(self, "暂无报告", "请先完成至少一轮群发。")
            return
        if self.report_dialog and self.report_dialog.session and self.report_dialog.session.id == self.session.id:
            self.report_dialog.update_session(self.session, build_basic_report(self.session))
            self.report_dialog.show()
            self.report_dialog.raise_()
            return
        self.report_dialog = ReportDialog(
            build_basic_report(self.session),
            self.runtime_dirs["reports"],
            self,
            session=self.session,
            profile=self.profile,
            config=self.config,
            repository=self.repository,
            secret_store=self.secret_store,
        )
        self.report_dialog.show()
        self.report_dialog.raise_()

    def show_history(self) -> None:
        self.history_dialog = HistoryDialog(self.repository, self.runtime_dirs["reports"], self)
        self.history_dialog.show()
        self.history_dialog.raise_()

    def _on_model_toggled(self, site_id: str, checked: bool) -> None:
        if self._model_signal_guard:
            return
        if not checked:
            self._model_signal_guard = True
            self.model_actions[site_id].setChecked(True)
            self._model_signal_guard = False
            QMessageBox.information(
                self,
                "保持四个会诊席位",
                "请直接勾选想加入的候补模型，再选择要被替换的当前模型。",
            )
            return
        if site_id in self.active_model_ids:
            return

        names = [ADAPTER_BY_ID[item].name for item in self.active_model_ids]
        replacement_name, accepted = QInputDialog.getItem(
            self,
            "替换会诊模型",
            f"让 {ADAPTER_BY_ID[site_id].name} 替换哪一家？",
            names,
            0,
            False,
        )
        if not accepted:
            self._model_signal_guard = True
            self.model_actions[site_id].setChecked(False)
            self._model_signal_guard = False
            return

        replacement_index = names.index(replacement_name)
        replaced_id = self.active_model_ids[replacement_index]
        self.active_model_ids[replacement_index] = site_id
        self._model_signal_guard = True
        self.model_actions[replaced_id].setChecked(False)
        self.model_actions[site_id].setChecked(True)
        self._model_signal_guard = False
        self._apply_active_models(replaced_id, site_id)

    def _apply_active_models(self, replaced_id: str, replacement_id: str) -> None:
        self.settings.setValue("enabled_models", ",".join(self.active_model_ids))
        if self._maximized_pane:
            self._restore_split()
        for pane, site_id in zip(self.panes, self.active_model_ids, strict=True):
            if pane.adapter.id != site_id:
                pane.set_adapter(ADAPTER_BY_ID[site_id])
        self.panes_by_id = {pane.adapter.id: pane for pane in self.panes}
        self.progress_label.setText(
            f"已用 {ADAPTER_BY_ID[replacement_id].name} 替换 {ADAPTER_BY_ID[replaced_id].name}。"
            "首次使用请先在对应面板完成登录。"
        )


    def run_diagnostics(self) -> None:
        self._diagnostics = {}
        self.progress_label.setText("正在检查四个站点的页面适配状态……")
        for pane in self.panes:
            pane.diagnose()

    def _on_diagnostic_ready(self, site_id: str, content: str) -> None:
        self._diagnostics[site_id] = content
        if len(self._diagnostics) != len(self.panes):
            return
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "active_models": list(self.active_model_ids),
            "app_version": __version__,
            "sites": {site_id: sanitized_diagnostic(content) for site_id, content in self._diagnostics.items()},
        }
        path = self.runtime_dirs["logs"] / f"diagnostics-{datetime.now():%Y%m%d-%H%M%S}.json"
        try:
            write_json(path, payload)
        except OSError:
            QMessageBox.warning(self, "诊断保存失败", "请到「使用与帮助 → 反馈问题」选择其他位置导出诊断摘要。")
            return
        self.progress_label.setText(f"诊断完成：{path}")
        QMessageBox.information(self, "诊断完成", f"仅保存适配器匹配计数，不含原文、网址或账号。未上传。\n{path}")

    def reload_all(self) -> None:
        for pane in self.panes:
            pane.view.reload()

    def reset_split(self) -> None:
        if self._maximized_pane:
            self._restore_split()
        self._programmatic_split = True
        self.split_container.equalize()
        self._programmatic_split = False

    def toggle_maximize(self, pane: WebPane) -> None:
        if self._maximized_pane is pane:
            self._restore_split()
            return
        if self._maximized_pane:
            self._restore_split()
        self._saved_split_sizes = (
            list(self.split_container.outer.sizes()),
            list(self.split_container.left.sizes()),
            list(self.split_container.right.sizes()),
        )
        self._maximized_pane = pane
        self._programmatic_split = True
        width = max(1, self.split_container.outer.width() - HANDLE_WIDTH)
        height = max(1, self.split_container.left.height() - HANDLE_WIDTH)
        self.split_container.outer.setSizes([width, 0] if pane.col == 0 else [0, width])
        inner = self.split_container.left if pane.col == 0 else self.split_container.right
        inner.setSizes([height, 0] if pane.row == 0 else [0, height])
        self._programmatic_split = False
        self.split_container.set_maximized(True)
        self._update_fullscreen_buttons()

    def _restore_split(self) -> None:
        if not self._saved_split_sizes:
            return
        self._programmatic_split = True
        self.split_container.outer.setSizes(self._saved_split_sizes[0])
        self.split_container.left.setSizes(self._saved_split_sizes[1])
        self.split_container.right.setSizes(self._saved_split_sizes[2])
        self._programmatic_split = False
        self._saved_split_sizes = None
        self._maximized_pane = None
        self.split_container.set_maximized(False)
        self._update_fullscreen_buttons()

    def _on_splitter_moved(self) -> None:
        if self._programmatic_split:
            return
        if self._maximized_pane:
            self._maximized_pane = None
            self._saved_split_sizes = None
            self.split_container.set_maximized(False)
            self._update_fullscreen_buttons()

    def _update_fullscreen_buttons(self) -> None:
        for pane in self.panes:
            pane.set_fullscreen_state(pane is self._maximized_pane)

    def _save_state(self) -> None:
        self.settings.setValue("geometry_v2", self.saveGeometry())
        if not self._maximized_pane:
            self.settings.setValue("outer_sizes", self.split_container.outer.sizes())
            self.settings.setValue("left_sizes", self.split_container.left.sizes())
            self.settings.setValue("right_sizes", self.split_container.right.sizes())

    @staticmethod
    def _sizes(value) -> list[int] | None:
        if not value:
            return None
        try:
            sizes = [int(item) for item in value]
            return sizes if len(sizes) == 2 else None
        except (TypeError, ValueError):
            return None

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._shown_once:
            self.split_container.reposition_grip()
            return
        self._shown_once = True
        if not self.settings.value("onboarding_completed", False, type=bool) and not self._welcome_scheduled:
            self._welcome_scheduled = True
            QTimer.singleShot(500, self._show_welcome)
        outer = self._sizes(self.settings.value("outer_sizes"))
        left = self._sizes(self.settings.value("left_sizes"))
        right = self._sizes(self.settings.value("right_sizes"))
        if outer and left and right:
            self.split_container.outer.setSizes(outer)
            self.split_container.left.setSizes(left)
            self.split_container.right.setSizes(right)
        else:
            self.split_container.equalize()
        self.split_container.reposition_grip()

    def _show_welcome(self) -> None:
        self.show_help()

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("四模型会诊")
        menu = QMenu()
        show_action = menu.addAction("显示主窗口")
        quit_action = menu.addAction("彻底退出")
        show_action.triggered.connect(self._show_from_tray)
        quit_action.triggered.connect(self._real_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _real_quit(self) -> None:
        if any(dialog._busy for dialog in self.findChildren(ReportDialog)):
            QMessageBox.information(self, "报告仍在生成", "请先在报告窗口停止任务，等待当前步骤结束后再退出。已完成的内容会保留。")
            return
        if any(pane.busy for pane in self.panes):
            if QMessageBox.question(self, "结束本轮并退出？", "仍有模型在回答。退出会停止等待，已采集的回答将保存到历史。") != QMessageBox.StandardButton.Yes:
                return
            self.cancel_all()
        self._save_session()
        if self._save_failed:
            QMessageBox.warning(self, "请先导出", "本轮保存失败。请先导出当前报告，检查数据目录后再退出。")
            return
        self._quitting = True
        self._save_state()
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):  # noqa: N802
        self._save_state()
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "四模型会诊",
            "已最小化到托盘，四个站点保持运行。",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )


APP_STYLE = """
QMainWindow, QDialog, QWidget {
    background:#F7F2E9;
    color:#302A24;
    font-family:"Microsoft YaHei UI", "Segoe UI";
    font-size:12px;
}
QFrame#headerPanel {
    background:#FBF8F2;
    border-bottom:1px solid #DED3C3;
}
QLabel#appTitle { font-size:18px; font-weight:600; color:#302A24; }
QLabel#appSubtitle { color:#8A7665; padding-left:3px; }
QLabel#localBadge {
    color:#76503D; background:#EFE3D7; border:1px solid #D9C1AF;
    border-radius:3px; padding:3px 7px;
}
QLabel#progressLabel { color:#746659; background:#F1EADF; border-bottom:1px solid #DED3C3; }
QLabel#paneTitle { font-weight:600; color:#3A312A; }
QWidget#paneBar { background:#FBF8F2; border-bottom:1px solid #DDD2C2; }
QLabel#eyebrowLabel { color:#9C563C; font-size:10px; font-weight:600; letter-spacing:1px; }
QLabel#reportTitle { color:#302A24; font-size:23px; font-weight:600; }
QLabel#reportSubtitle { color:#7E6F61; padding-bottom:5px; }
QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget {
    background:#FFFDF8; color:#302A24; border:1px solid #D8CCBC;
    border-radius:3px; padding:7px 9px; selection-background-color:#C99B82;
}
QLineEdit#questionInput { min-height:24px; padding:9px 12px; font-size:14px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus, QListWidget:focus {
    border-color:#B97855;
}
QTextBrowser#reportViewer, QTextBrowser#historyPreview {
    padding:16px 19px; background:#FFFDF8;
}
QListWidget::item { border-bottom:1px solid #E9E0D4; padding:9px 7px; }
QListWidget::item:selected { background:#EFE3D7; color:#5E3E2F; }
QPushButton {
    background:#FFFDF8; color:#4B4037; border:1px solid #D5C8B8;
    border-radius:3px; padding:6px 10px;
}
QPushButton:hover { background:#F1E7DB; border-color:#BE9D87; }
QPushButton:pressed { background:#E7D8C8; }
QPushButton:disabled { color:#A89B8E; background:#F1ECE4; border-color:#E2D9CD; }
QPushButton#primaryButton {
    background:#9C563C; border-color:#9C563C; color:#FFF9F1; font-weight:600;
    padding:7px 14px;
}
QPushButton#primaryButton:hover { background:#AD6549; border-color:#AD6549; }
QPushButton#primaryButton:pressed { background:#874832; }
QPushButton#quietButton { background:transparent; border-color:transparent; color:#746457; }
QPushButton#quietButton:hover { background:#EFE5D9; border-color:#E1D3C4; }
QCheckBox { color:#675A4F; spacing:6px; }
QCheckBox::indicator { width:14px; height:14px; }
QCheckBox::indicator:checked { background:#9C563C; border:1px solid #874832; }
QMenu { background:#FFFDF8; color:#302A24; border:1px solid #D8CCBC; padding:4px; }
QMenu::item { padding:6px 22px 6px 10px; }
QMenu::item:selected { background:#EFE3D7; }
QTabWidget::pane { border:1px solid #D8CCBC; background:#FFFDF8; }
QTabBar::tab { background:#EDE5DA; color:#756659; padding:8px 16px; border:1px solid #D8CCBC; }
QTabBar::tab:selected { background:#FFFDF8; color:#6C3F2E; border-bottom-color:#FFFDF8; }
QSplitter::handle { background:#D7CCBD; }
QScrollBar:vertical { background:#F0E9DF; width:11px; margin:0; }
QScrollBar::handle:vertical { background:#C9BAA8; min-height:30px; border-radius:4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QToolTip { background:#3A312A; color:#FFF9F1; border:1px solid #3A312A; padding:5px; }
"""
