from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import psutil
from PySide6.QtCore import Qt, QSize, QThread, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.batch_pipeline import create_effect, delete_effect, list_effects, set_builtin_effect_opacity
from core.ffmpeg_tools import hidden_subprocess_kwargs
from core.project_store import PRIORITIES, PROJECT_STATUSES, TASK_TYPES, Project, ProjectStore
from core.video_analyzer import CompatibilityReport, format_duration, format_size
from ui.editor_common import app_logo_pixmap, _icon_chip, _tint, project_output_root, publish_final_copy
from workers.analysis_worker import AnalysisWorker
from workers.batch_pipeline_worker import BatchPipelineWorker
from workers.concat_worker import ConcatWorker
from workers.normalize_worker import NormalizeWorker
from workers.thumbnail_worker import ThumbnailWorker

_STATUS_META = {
    "":          ("Tất cả",    "⊞",  "#3b82f6"),
    "Bản nháp":  ("Bản nháp", "✏",  "#a855f7"),
    "Chưa chạy": ("Chưa chạy","◌",  "#64748b"),
    "Đang chờ":  ("Đang chờ", "⏳", "#f59e0b"),
    "Đang chạy": ("Đang chạy","▶",  "#2dd4bf"),
    "Tạm dừng":  ("Tạm dừng", "⏸", "#f97316"),
    "Hoàn thành":("Hoàn thành","✔", "#16a34a"),
    "Lỗi":       ("Lỗi",      "✖",  "#ef4444"),
    "Đã hủy":    ("Đã hủy",   "⊘",  "#64748b"),
}

_TASK_ICONS = {
    "Xử lý video":            ("🎬", "#2dd4bf"),
    "Nối video":              ("⛓",  "#3b82f6"),
    "Chuẩn hóa video":        ("⚖",  "#06b6d4"),
    "Chia nhỏ video":         ("✂",  "#a855f7"),
    "Phóng to/thu nhỏ":       ("⤡",  "#f59e0b"),
    "Thêm hiệu ứng":          ("🌟", "#ec4899"),
    "Tách âm thanh":          ("🎵", "#22c55e"),
    "Tách giọng và nhạc nền": ("🎤", "#22c55e"),
    "Chuyển đổi định dạng":   ("⇄",  "#64748b"),
}


_STATUS_STEP = {
    "Bản nháp": 1, "Chưa chạy": 2, "Đang chờ": 3, "Đang chạy": 4,
    "Tạm dừng": 4, "Hoàn thành": 5, "Lỗi": 4, "Đã hủy": 1,
}

_PROCESS_STEPS = (
    ("Thêm video", "Đưa video vào dự án"),
    ("Phân tích", "Phân tích và kiểm tra"),
    ("Chỉnh thiết lập", "Cấu hình xử lý"),
    ("Xử lý", "Đang thực hiện"),
    ("Xuất kết quả", "Hoàn thành"),
)


@lru_cache(maxsize=32)
def _task_icon(task_type: str) -> QIcon:
    glyph, color = _TASK_ICONS.get(task_type, ("▤", "#64748b"))
    size = 44
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    # Nền gradient nhẹ
    from PySide6.QtGui import QLinearGradient
    grad = QLinearGradient(0, 0, size, size)
    c = QColor(color)
    c.setAlpha(70); grad.setColorAt(0, c)
    c2 = QColor(color); c2.setAlpha(35); grad.setColorAt(1, c2)
    from PySide6.QtGui import QBrush
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 12, 12)
    # Viền mảnh
    border_color = QColor(color); border_color.setAlpha(90)
    from PySide6.QtGui import QPen
    pen = QPen(border_color, 1)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 12, 12)
    # Glyph
    painter.setPen(QColor(color))
    font = painter.font()
    font.setPointSize(15)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return QIcon(pixmap)


def _status_pill(status: str) -> QLabel:
    meta, _, color = _STATUS_META.get(status, ("", "●", "#94a3b8"))
    # Icon đại diện cho từng trạng thái
    _pill_icon = {
        "Bản nháp": "✏", "Chưa chạy": "◌", "Đang chờ": "⏳",
        "Đang chạy": "▶", "Tạm dừng": "⏸", "Hoàn thành": "✔",
        "Lỗi": "✖", "Đã hủy": "⊘",
    }
    icon = _pill_icon.get(status, "●")
    label = QLabel(f"{icon}  {status}")
    label.setFixedHeight(26)
    label.setStyleSheet(
        f"background:{_tint(color, 30)}; color:{color}; border-radius:13px; "
        f"padding:0 12px; font-weight:700; font-size:9pt;"
    )
    return label


def _status_cell(status: str) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(6, 4, 6, 4)
    layout.addWidget(_status_pill(status))
    layout.addStretch(1)
    return wrapper


def _progress_cell(value: int) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(6, 10, 6, 10)
    layout.setSpacing(4)
    percent_label = QLabel(f"{value}%")
    percent_label.setObjectName("progressPercent")
    bar = QProgressBar()
    bar.setObjectName("miniProgress")
    bar.setRange(0, 100)
    bar.setValue(value)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    layout.addWidget(percent_label)
    layout.addWidget(bar)
    return wrapper


class GaugeWidget(QWidget):
    def __init__(self, color: str = "#2dd4bf") -> None:
        super().__init__()
        self._value = 0.0
        self._color = QColor(color)
        self.setFixedSize(64, 64)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, value))
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(5, 5, -5, -5)
        pw = 6
        track_pen = painter.pen()
        track_pen.setWidth(pw); track_pen.setColor(QColor("#202838")); track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen); painter.drawArc(rect, 0, 360 * 16)
        val_pen = painter.pen()
        val_pen.setWidth(pw); val_pen.setColor(self._color); val_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(val_pen)
        painter.drawArc(rect, 90 * 16, int(-self._value / 100 * 360 * 16))
        painter.setPen(QColor("#f3f5f8"))
        font = painter.font(); font.setPointSize(10); font.setBold(True); painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, f"{int(round(self._value))}%")
        painter.end()


class ResourceCard(QFrame):
    def __init__(self, title: str, color: str = "#2dd4bf") -> None:
        super().__init__()
        self.setObjectName("resourceCard")
        self.gauge = GaugeWidget(color)
        title_label = QLabel(title); title_label.setObjectName("resourceTitle")
        self.sub_label = QLabel("--"); self.sub_label.setProperty("role", "mutedText")
        text_box = QVBoxLayout(); text_box.setSpacing(4)
        text_box.addStretch(1); text_box.addWidget(title_label); text_box.addWidget(self.sub_label); text_box.addStretch(1)
        layout = QHBoxLayout(self); layout.setContentsMargins(16, 14, 16, 14); layout.setSpacing(14)
        layout.addLayout(text_box, 1); layout.addWidget(self.gauge)

    def set_value(self, value: float, subtitle: str) -> None:
        self.gauge.set_value(value); self.sub_label.setText(subtitle)


class ProcessingStepper(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("pageWorkspace")
        outer = QVBoxLayout(self); outer.setContentsMargins(22, 18, 22, 18); outer.setSpacing(14)
        title = QLabel("QUY TRÌNH XỬ LÝ"); title.setObjectName("pageTitle")
        outer.addWidget(title)
        row = QHBoxLayout(); row.setSpacing(0)
        self._circles: list[QLabel] = []; self._lines: list[QFrame] = []
        for index, (step_title, step_subtitle) in enumerate(_PROCESS_STEPS):
            if index > 0:
                line = QFrame(); line.setObjectName("stepLine"); line.setFixedHeight(2)
                row.addWidget(line, 1); self._lines.append(line)
            col = QVBoxLayout(); col.setSpacing(4); col.setAlignment(Qt.AlignHCenter)
            circle = QLabel(str(index + 1)); circle.setObjectName("stepCircle")
            circle.setAlignment(Qt.AlignCenter); circle.setFixedSize(34, 34)
            lbl = QLabel(step_title); lbl.setObjectName("stepTitle"); lbl.setAlignment(Qt.AlignHCenter)
            sub = QLabel(step_subtitle); sub.setProperty("role", "mutedText"); sub.setAlignment(Qt.AlignHCenter)
            col.addWidget(circle, 0, Qt.AlignHCenter); col.addWidget(lbl); col.addWidget(sub)
            wrap = QWidget(); wrap.setLayout(col); row.addWidget(wrap, 0); self._circles.append(circle)
        outer.addLayout(row)
        self.set_active_step(1)

    def set_active_step(self, active: int) -> None:
        active = max(1, min(len(self._circles), active))
        for i, circle in enumerate(self._circles, start=1):
            state = "done" if i < active else "active" if i == active else "pending"
            circle.setProperty("state", state); circle.style().unpolish(circle); circle.style().polish(circle)
        for i, line in enumerate(self._lines, start=1):
            line.setProperty("state", "done" if i < active else "pending")
            line.style().unpolish(line); line.style().polish(line)


class NewProjectDialog(QDialog):
    """Tạo dự án mới — chỉ cần tên. Mọi dự án đều dùng chung 1 pipeline (tách giọng/nhạc nền -> cắt đoạn ->
    zoom so le -> nối final.mp4), không còn chọn loại tác vụ như trước — giống hệt cách bản web làm."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tạo dự án mới")
        self.setMinimumWidth(460)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ví dụ: Video_01, Clip tháng 7...")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_name = QLabel("Tên dự án")
        lbl_name.setStyleSheet("color:#9aa4b2; font-weight:600; font-size:9pt;")

        layout.addWidget(lbl_name)
        layout.addWidget(self.name_edit)

        hint = QLabel(
            "Mỗi dự án tự động: tách giọng/bỏ nhạc nền → cắt đoạn → zoom so le → nối thành final.mp4. "
            "Video nguồn và thư mục đầu ra sẽ chọn bên trong màn hình dự án."
        )
        hint.setStyleSheet("color:#64748b; font-size:9pt;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Thiếu tên", "Hãy nhập tên dự án."); return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "task_type": TASK_TYPES[0],
            "priority": "Bình thường",
        }


class StatCard(QPushButton):
    def __init__(self, label: str, status: str = "", glyph: str = "▦", color: str = "#3b82f6") -> None:
        super().__init__()
        self.status = status; self.label = label
        self.setObjectName("statCard"); self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self); layout.setContentsMargins(12, 10, 12, 10); layout.setSpacing(10)
        layout.addWidget(_icon_chip(glyph, color, size=36, font_size=15))
        text_box = QVBoxLayout(); text_box.setSpacing(0)
        self.count_label = QLabel("0"); self.count_label.setObjectName("statCount")
        self.name_label = QLabel(label); self.name_label.setObjectName("statName")
        text_box.addWidget(self.count_label); text_box.addWidget(self.name_label)
        layout.addLayout(text_box); layout.addStretch(1)

    def set_count(self, count: int) -> None:
        self.count_label.setText(str(count))


def _table_icon_button(glyph: str, tooltip: str) -> QPushButton:
    """Nút icon nhỏ gọn để đặt trong ô bảng — style QPushButton nền có padding/min-height lớn (30px,
    7px 14px) làm icon bị bóp méo/che mất và tràn khỏi hàng, nên ghi đè trực tiếp trên widget."""
    button = QPushButton(glyph)
    button.setToolTip(tooltip)
    button.setFixedSize(28, 24)
    button.setStyleSheet(
        "QPushButton { min-height: 0; min-width: 0; padding: 0px; font-size: 12px; "
        "background: transparent; border: 1px solid #334155; border-radius: 5px; color: #cbd5e1; }"
        "QPushButton:hover { background: #1a2230; border-color: #475569; }"
    )
    return button


def _make_project_table() -> QTableWidget:
    """Tạo QTableWidget chuẩn dùng chung cho các tab — 5 cột (bỏ Tác vụ, Ưu tiên)."""
    table = QTableWidget(0, 5)
    table.setHorizontalHeaderLabels(("Dự án", "Trạng thái", "Tiến trình", "Ngày tạo", "Thao tác"))
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setIconSize(QSize(40, 40))
    hv = table.horizontalHeader()
    hv.setSectionResizeMode(0, QHeaderView.Stretch)
    for idx in (1, 3, 4): hv.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
    hv.setSectionResizeMode(2, QHeaderView.Fixed); table.setColumnWidth(2, 140)
    return table


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fast Video Studio — Quản lý dự án")
        self.resize(1600, 940)
        self.store = ProjectStore()
        self._projects: list[Project] = []
        self._filtered: list[Project] = []
        self._status_filter = ""
        self._page = 0
        self._page_size = 8
        self._current_editor: QWidget | None = None
        self._selected_project_id: int | None = None
        self._queue_thread: QThread | None = None
        self._queue_worker: BatchPipelineWorker | None = None
        self._queue_active_project_id: int | None = None
        self._concat_paths: list[str] = []
        self._concat_report: CompatibilityReport | None = None
        self._concat_thread: QThread | None = None
        self._concat_worker: ConcatWorker | None = None
        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._concat_thumbnail_queue: list[str] = []
        self._concat_thumbnail_thread: QThread | None = None
        self._concat_thumbnail_worker: ThumbnailWorker | None = None
        self._normalize_thread: QThread | None = None
        self._normalize_worker: NormalizeWorker | None = None
        self._build_ui()
        self._connect()
        self.refresh()
        self.resource_timer = QTimer(self); self.resource_timer.setInterval(5000)
        self.resource_timer.timeout.connect(self._update_resources); self.resource_timer.start()
        self._update_resources()
        self._queue_timer = QTimer(self); self._queue_timer.setInterval(1500)
        self._queue_timer.timeout.connect(self.refresh)

    # ──────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ──────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root_widget = QWidget(); root_widget.setObjectName("dashboardRoot")
        root = QVBoxLayout(root_widget); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        header = QFrame(); header.setObjectName("dashHeader")
        hl = QHBoxLayout(header)
        brand = QLabel(); brand.setObjectName("dashLogo"); brand.setPixmap(app_logo_pixmap())
        titles = QVBoxLayout()
        titles.addWidget(QLabel("Fast Video Studio") if False else self._make_title())
        subtitle = QLabel("Trung tâm quản lý dự án video"); subtitle.setProperty("role", "mutedText")
        titles.addWidget(subtitle)
        hl.addWidget(brand); hl.addLayout(titles); hl.addStretch(1)
        self.new_button = QPushButton("＋  Tạo dự án mới"); self.new_button.setObjectName("btnPrimary")
        hl.addWidget(self.new_button)
        root.addWidget(header)

        # ── Body: sidenav + page stack ───────────────────────────────────
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        nav = QFrame(); nav.setObjectName("sideNav"); nav_layout = QVBoxLayout(nav)
        nav_title = QLabel("MENU"); nav_title.setObjectName("navCaption"); nav_layout.addWidget(nav_title)
        self.nav_buttons: list[QPushButton] = []
        nav_items = (
            ("Tổng quan",  "⊞",  "#3b82f6"),
            ("Dự án",      "🗂", "#a855f7"),
            ("Hàng đợi",   "⏱", "#f59e0b"),
            ("File đầu ra","📤", "#22c55e"),
            ("Lịch sử",    "📋", "#94a3b8"),
            ("Nối video",  "🔗", "#0ea5e9"),
            ("Hiệu ứng",   "✨", "#ec4899"),
            ("Cài đặt",    "⚙",  "#64748b"),
        )
        for index, (text, glyph, color) in enumerate(nav_items):
            btn = QPushButton(); btn.setObjectName("navButton"); btn.setCheckable(True)
            row = QHBoxLayout(btn); row.setContentsMargins(10, 6, 10, 6); row.setSpacing(10)
            row.addWidget(_icon_chip(glyph, color, size=26, font_size=12))
            lbl = QLabel(text); lbl.setObjectName("navButtonLabel"); row.addWidget(lbl, 1)
            if index == 0: btn.setChecked(True)
            nav_layout.addWidget(btn); self.nav_buttons.append(btn)
        nav_layout.addStretch(1)

        # quick stats sidebar
        quick_stats = QFrame(); quick_stats.setObjectName("quickStats")
        qs = QVBoxLayout(quick_stats); qs.setContentsMargins(14, 14, 14, 14); qs.setSpacing(10)
        qs.addWidget(QLabel("THỐNG KÊ NHANH") if False else self._make_nav_caption("THỐNG KÊ NHANH"))
        self.quick_stat_labels: dict[str, QLabel] = {}
        for key, label, glyph, color in (
            ("total",     "Tổng dự án",  "🗂", "#3b82f6"),
            ("running",   "Đang chạy",   "▶",  "#2dd4bf"),
            ("done",      "Hoàn thành",  "✔",  "#22c55e"),
            ("cancelled", "Đã hủy",      "⊘",  "#64748b"),
        ):
            sr = QHBoxLayout(); sr.setSpacing(10)
            sr.addWidget(_icon_chip(glyph, color, size=28, font_size=12))
            st = QLabel(label); st.setProperty("role", "mutedText")
            cl = QLabel("0"); cl.setObjectName("quickStatCount")
            sr.addWidget(st, 1); sr.addWidget(cl); qs.addLayout(sr)
            self.quick_stat_labels[key] = cl
        nav_layout.addWidget(quick_stats)

        self.page_stack = QStackedWidget(); self.page_stack.setObjectName("pageStack")
        self.page_stack.addWidget(self._build_overview_page())   # 0 — Tổng quan
        self.page_stack.addWidget(self._build_projects_page())   # 1 — Dự án
        self.page_stack.addWidget(self._build_queue_page())      # 2 — Hàng đợi
        self.page_stack.addWidget(self._build_output_page())     # 3 — File đầu ra
        self.page_stack.addWidget(self._build_history_page())    # 4 — Lịch sử
        self.page_stack.addWidget(self._build_concat_page())     # 5 — Nối video
        self.page_stack.addWidget(self._build_effects_page())    # 6 — Hiệu ứng
        self.page_stack.addWidget(self._build_settings_page())   # 7 — Cài đặt

        body.addWidget(nav); body.addWidget(self.page_stack, 1)
        root.addLayout(body, 1)
        self.resource_label = QLabel(); self.resource_label.setObjectName("resourceBar")
        root.addWidget(self.resource_label)

        # ── Tầng 2: editor container ─────────────────────────────────────
        self._editor_container = QWidget(); self._editor_container.setObjectName("editorContainer")
        ecl = QVBoxLayout(self._editor_container); ecl.setContentsMargins(0, 0, 0, 0); ecl.setSpacing(0)

        self.main_stack = QStackedWidget()
        self.main_stack.addWidget(root_widget)            # 0 — dashboard
        self.main_stack.addWidget(self._editor_container) # 1 — editor

        self.setCentralWidget(self.main_stack)
        self._apply_style()

    @staticmethod
    def _make_title() -> QLabel:
        lbl = QLabel("Fast Video Studio"); lbl.setObjectName("dashTitle"); return lbl

    @staticmethod
    def _make_nav_caption(text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("navCaption"); return lbl

    # ──────────────────────────────────────────────────────────────────────
    # PAGE BUILDERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_detail_panel(self) -> QScrollArea:
        """Panel 'CHI TIẾT DỰ ÁN' (tên/trạng thái/nút Chạy-Tạm dừng/nhật ký) — dùng ở tab Dự án,
        nơi người dùng duyệt và chọn dự án cụ thể (không đặt ở Tổng quan để tránh lẫn với phần thống kê chung).
        Chỉ hiển thị THÔNG TIN — chỉnh thiết lập xử lý (cắt/zoom/mã hóa...) phải mở hẳn vào dự án (open_editor)."""
        details = QFrame(); details.setObjectName("detailPanel")
        dl = QVBoxLayout(details)
        dt = QLabel("CHI TIẾT DỰ ÁN"); dt.setProperty("role", "sectionTitle")
        self.detail_name = QLabel("Chọn một dự án"); self.detail_name.setObjectName("detailName"); self.detail_name.setWordWrap(True)
        self.detail_meta = QLabel("Thông tin dự án sẽ hiển thị tại đây."); self.detail_meta.setProperty("role", "mutedText"); self.detail_meta.setWordWrap(True)
        self.detail_progress = QProgressBar(); self.detail_progress.setRange(0, 100)
        da = QHBoxLayout()
        self.detail_run = QPushButton("Chạy"); self.detail_run.setObjectName("btnPrimary")
        self.detail_pause = QPushButton("Tạm dừng"); self.detail_pause.setObjectName("btnQuiet")
        self.detail_open = QPushButton("Mở thư mục"); self.detail_open.setObjectName("btnQuiet")
        da.addWidget(self.detail_run); da.addWidget(self.detail_pause)
        dl.addWidget(dt); dl.addWidget(self.detail_name); dl.addWidget(self.detail_meta)
        dl.addWidget(self.detail_progress); dl.addLayout(da); dl.addWidget(self.detail_open)

        self.detail_edit_hint = QPushButton("⚙  Vào dự án để chỉnh thiết lập xử lý")
        self.detail_edit_hint.setObjectName("linkButton")
        self.detail_edit_hint.clicked.connect(self.open_editor)
        dl.addWidget(self.detail_edit_hint)

        lh = QHBoxLayout()
        lt = QLabel("NHẬT KÝ"); lt.setProperty("role", "sectionTitle")
        lva = QPushButton("Xem tất cả"); lva.setObjectName("linkButton")
        lva.clicked.connect(self.show_selected_details)
        lh.addWidget(lt); lh.addStretch(1); lh.addWidget(lva)
        dl.addLayout(lh)
        self.detail_log = QPlainTextEdit(); self.detail_log.setReadOnly(True); self.detail_log.setPlaceholderText("Nhật ký riêng của dự án")
        dl.addWidget(self.detail_log, 1)

        details_scroll = QScrollArea(); details_scroll.setObjectName("detailScroll")
        details_scroll.setWidgetResizable(True); details_scroll.setFrameShape(QFrame.NoFrame)
        details_scroll.setWidget(details)
        details_scroll.setMinimumWidth(320); details_scroll.setMaximumWidth(420)
        return details_scroll

    def _build_overview_page(self) -> QWidget:
        """Tab 0 — Tổng quan: stat cards + danh sách cần chú ý + stepper + tài nguyên (không có chi tiết dự án)."""
        content = QWidget(); content.setObjectName("overviewContent")
        cl = QVBoxLayout(content); cl.setContentsMargins(22, 20, 16, 18); cl.setSpacing(14)

        heading = QHBoxLayout()
        ht = QLabel("TỔNG QUAN DỰ ÁN"); ht.setObjectName("pageTitle")
        self.run_all_button = QPushButton("▶  Chạy tất cả"); self.run_all_button.setObjectName("btnPrimary")
        self.pause_all_button = QPushButton("⏸  Tạm dừng tất cả"); self.pause_all_button.setObjectName("btnQuiet")
        heading.addWidget(ht); heading.addStretch(1)
        heading.addWidget(self.run_all_button); heading.addWidget(self.pause_all_button)
        cl.addLayout(heading)

        cards = QGridLayout(); cards.setSpacing(10); self.cards: list[StatCard] = []
        for col in range(5): cards.setColumnStretch(col, 1)
        for idx, sk in enumerate(("", "Bản nháp", "Chưa chạy", "Đang chờ", "Đang chạy", "Tạm dừng", "Hoàn thành", "Lỗi", "Đã hủy")):
            label, glyph, color = _STATUS_META[sk]
            card = StatCard(label, sk, glyph, color)
            cards.addWidget(card, idx // 5, idx % 5); self.cards.append(card)
        cl.addLayout(cards)

        attn_head = QHBoxLayout()
        attn_title = QLabel("CẦN CHÚ Ý"); attn_title.setObjectName("sectionTitle")
        attn_hint = self._muted("Dự án lỗi, đang chạy hoặc đang chờ xử lý — danh sách đầy đủ ở tab Dự án.")
        attn_head.addWidget(attn_title); attn_head.addWidget(attn_hint); attn_head.addStretch(1)
        cl.addLayout(attn_head)

        self.attention_table = _make_project_table()
        self.attention_table.setMaximumHeight(420)
        self.attention_empty_label = QLabel("🎉  Không có dự án nào cần chú ý ngay bây giờ.")
        self.attention_empty_label.setProperty("role", "mutedText")
        cl.addWidget(self.attention_table)
        cl.addWidget(self.attention_empty_label)

        self.stepper = ProcessingStepper(); cl.addWidget(self.stepper)

        res_title = QLabel("TÀI NGUYÊN HỆ THỐNG"); res_title.setObjectName("pageTitle"); cl.addWidget(res_title)
        res_row = QHBoxLayout(); res_row.setSpacing(12)
        self.cpu_card = ResourceCard("CPU", "#2dd4bf"); self.ram_card = ResourceCard("RAM", "#3b82f6")
        self.gpu_card = ResourceCard("GPU", "#f59e0b"); self.disk_card = ResourceCard("Ổ ĐĨA (C:)", "#22c55e")
        for c in (self.cpu_card, self.ram_card, self.gpu_card, self.disk_card): res_row.addWidget(c, 1)
        cl.addLayout(res_row)

        content_scroll = QScrollArea(); content_scroll.setObjectName("contentScroll")
        content_scroll.setWidgetResizable(True); content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setWidget(content)
        return content_scroll

    def _build_projects_page(self) -> QWidget:
        """Tab 1 — Dự án: danh sách đầy đủ + tìm kiếm/lọc/phân trang — trang duyệt toàn bộ dự án chính."""
        content = QWidget(); content.setObjectName("webPage")
        pl = QVBoxLayout(content); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(14)

        heading = QHBoxLayout()
        ht = QLabel("DỰ ÁN"); ht.setObjectName("pageTitle")
        self.proj_run_all_btn = QPushButton("▶  Chạy tất cả"); self.proj_run_all_btn.setObjectName("btnPrimary")
        self.proj_pause_all_btn = QPushButton("⏸  Tạm dừng tất cả"); self.proj_pause_all_btn.setObjectName("btnQuiet")
        self.proj_new_btn = QPushButton("＋  Tạo dự án mới"); self.proj_new_btn.setObjectName("btnPrimary")
        heading.addWidget(ht); heading.addStretch(1)
        heading.addWidget(self.proj_run_all_btn); heading.addWidget(self.proj_pause_all_btn); heading.addWidget(self.proj_new_btn)
        pl.addLayout(heading)

        output_row = QHBoxLayout()
        out_caption = QLabel("Thư mục đầu ra chung:"); out_caption.setProperty("role", "mutedText")
        self.shared_output_label = QLabel(self.store.get_app_setting("default_output_dir", "") or "Chưa đặt")
        self.shared_output_label.setObjectName("infoItem"); self.shared_output_label.setWordWrap(True)
        self.shared_output_button = QPushButton("📁  Chọn thư mục cho tất cả dự án")
        self.shared_output_button.setObjectName("btnQuiet")
        output_row.addWidget(out_caption); output_row.addWidget(self.shared_output_label, 1)
        output_row.addWidget(self.shared_output_button)
        pl.addLayout(output_row)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("🔍  Tìm kiếm tên hoặc đầu ra...")
        self.status_combo = QComboBox(); self.status_combo.addItem("Tất cả trạng thái", "")
        for s in PROJECT_STATUSES: self.status_combo.addItem(s, s)
        self.filter_reset_button = QPushButton("☰"); self.filter_reset_button.setObjectName("btnQuiet")
        self.filter_reset_button.setToolTip("Đặt lại bộ lọc")
        filters.addWidget(self.search_edit, 1); filters.addWidget(self.status_combo)
        filters.addWidget(self.filter_reset_button)
        pl.addLayout(filters)

        hint = QLabel("Nhấp đúp vào dự án để mở màn hình xử lý chi tiết.")
        hint.setProperty("role", "mutedText"); pl.addWidget(hint)

        self.table = _make_project_table()
        pl.addWidget(self.table, 1)

        pag = QHBoxLayout()
        self.pagination_label = QLabel(""); self.pagination_label.setProperty("role", "mutedText")
        self.prev_page_button = QPushButton("‹"); self.prev_page_button.setObjectName("pageNavButton"); self.prev_page_button.setFixedWidth(34)
        self.page_indicator = QLabel("1"); self.page_indicator.setObjectName("pageIndicator"); self.page_indicator.setAlignment(Qt.AlignCenter); self.page_indicator.setFixedWidth(28)
        self.next_page_button = QPushButton("›"); self.next_page_button.setObjectName("pageNavButton"); self.next_page_button.setFixedWidth(34)
        pag.addWidget(self.pagination_label); pag.addStretch(1)
        pag.addWidget(self.prev_page_button); pag.addWidget(self.page_indicator); pag.addWidget(self.next_page_button)
        pl.addLayout(pag)

        content_scroll = QScrollArea(); content_scroll.setObjectName("contentScroll")
        content_scroll.setWidgetResizable(True); content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setWidget(content)

        page = QWidget()
        page_layout = QHBoxLayout(page); page_layout.setContentsMargins(0, 0, 0, 0); page_layout.setSpacing(0)
        page_layout.addWidget(content_scroll, 1)
        page_layout.addWidget(self._build_detail_panel())
        return page

    def _build_queue_page(self) -> QWidget:
        """Tab 2 — Hàng đợi: dự án đang chờ + đang chạy."""
        page = QWidget(); page.setObjectName("webPage")
        pl = QVBoxLayout(page); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(14)

        heading = QHBoxLayout()
        ht = QLabel("HÀNG ĐỢI XỬ LÝ"); ht.setObjectName("pageTitle")
        self.queue_run_all = QPushButton("▶  Chạy tất cả"); self.queue_run_all.setObjectName("btnPrimary")
        self.queue_pause_all = QPushButton("❚❚  Tạm dừng"); self.queue_pause_all.setObjectName("btnQuiet")
        heading.addWidget(ht); heading.addStretch(1)
        heading.addWidget(self.queue_run_all); heading.addWidget(self.queue_pause_all)
        pl.addLayout(heading)

        self.queue_status_label = QLabel("0 đang chạy  •  0 đang chờ")
        self.queue_status_label.setProperty("role", "mutedText"); pl.addWidget(self.queue_status_label)

        self.queue_table = _make_project_table()
        pl.addWidget(self.queue_table, 1)
        return page

    def _build_output_page(self) -> QWidget:
        """Tab 3 — File đầu ra: dự án đã hoàn thành."""
        page = QWidget(); page.setObjectName("webPage")
        pl = QVBoxLayout(page); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(14)

        heading = QHBoxLayout()
        ht = QLabel("FILE ĐẦU RA"); ht.setObjectName("pageTitle")
        self.output_open_all = QPushButton("📁  Mở thư mục output"); self.output_open_all.setObjectName("btnQuiet")
        heading.addWidget(ht); heading.addStretch(1); heading.addWidget(self.output_open_all)
        pl.addLayout(heading)

        self.output_summary = QLabel("0 dự án hoàn thành")
        self.output_summary.setProperty("role", "mutedText"); pl.addWidget(self.output_summary)

        self.output_table = _make_project_table()
        pl.addWidget(self.output_table, 1)
        return page

    def _build_history_page(self) -> QWidget:
        """Tab 4 — Lịch sử: nhật ký tổng hợp từ tất cả dự án."""
        page = QWidget(); page.setObjectName("webPage")
        pl = QVBoxLayout(page); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(14)

        heading = QHBoxLayout()
        ht = QLabel("LỊCH SỬ XỬ LÝ"); ht.setObjectName("pageTitle")
        self.history_refresh_btn = QPushButton("↻  Làm mới"); self.history_refresh_btn.setObjectName("btnQuiet")
        heading.addWidget(ht); heading.addStretch(1); heading.addWidget(self.history_refresh_btn)
        pl.addLayout(heading)

        hint = QLabel("Nhật ký từ tất cả dự án — mới nhất trước.")
        hint.setProperty("role", "mutedText"); pl.addWidget(hint)

        self.history_log = QPlainTextEdit(); self.history_log.setReadOnly(True)
        self.history_log.setPlaceholderText("Chưa có nhật ký nào...")
        pl.addWidget(self.history_log, 1)
        return page

    def _build_concat_page(self) -> QWidget:
        """Tab Nối video: nối nhanh nhiều video thành 1 file bằng stream-copy (không render lại) — y hệt
        tính năng 'Nối video' cũ. Chỉ nối thành công khi các file cùng codec/độ phân giải/fps/audio; bấm
        Phân tích trước để kiểm tra, có chế độ an toàn (remux MKV tạm) cho trường hợp dễ lỗi timestamp."""
        page = QWidget(); page.setObjectName("webPage")
        pl = QVBoxLayout(page); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(14)
        ht = QLabel("NỐI VIDEO"); ht.setObjectName("pageTitle"); pl.addWidget(ht)
        pl.addWidget(self._muted(
            "Nối nhiều video thành 1 file bằng stream-copy (không render lại, giữ nguyên chất lượng). "
            "Chỉ nối được khi các file cùng codec/độ phân giải/fps/audio — bấm Phân tích trước khi nối."
        ))

        body = QHBoxLayout(); body.setSpacing(14)

        left_card = QFrame(); left_card.setObjectName("pageCard")
        ll = QVBoxLayout(left_card); ll.setContentsMargins(16, 16, 16, 16); ll.setSpacing(8)
        lt = QLabel("DANH SÁCH VIDEO"); lt.setObjectName("cardTitle"); ll.addWidget(lt)
        self.concat_list = QListWidget()
        self.concat_list.setIconSize(QSize(64, 36))
        ll.addWidget(self.concat_list, 1)
        list_btns = QHBoxLayout(); list_btns.setSpacing(6)
        self.concat_add_btn = QPushButton("Thêm file"); self.concat_add_btn.setProperty("variant", "quiet")
        self.concat_remove_btn = QPushButton("Xóa"); self.concat_remove_btn.setProperty("variant", "quiet")
        self.concat_up_btn = QPushButton("↑"); self.concat_up_btn.setProperty("variant", "quiet"); self.concat_up_btn.setFixedWidth(34)
        self.concat_down_btn = QPushButton("↓"); self.concat_down_btn.setProperty("variant", "quiet"); self.concat_down_btn.setFixedWidth(34)
        list_btns.addWidget(self.concat_add_btn); list_btns.addWidget(self.concat_remove_btn)
        list_btns.addStretch(1); list_btns.addWidget(self.concat_up_btn); list_btns.addWidget(self.concat_down_btn)
        ll.addLayout(list_btns)
        body.addWidget(left_card, 1)

        right_card = QFrame(); right_card.setObjectName("pageCard"); right_card.setFixedWidth(340)
        rl = QVBoxLayout(right_card); rl.setContentsMargins(16, 16, 16, 16); rl.setSpacing(8)
        rt = QLabel("THIẾT LẬP ĐẦU RA"); rt.setObjectName("cardTitle"); rl.addWidget(rt)

        rl.addWidget(self._muted("Thư mục lưu"))
        output_row = QHBoxLayout(); output_row.setSpacing(6)
        self.concat_output_edit = QLineEdit()
        self.concat_output_button = QPushButton("Chọn..."); self.concat_output_button.setProperty("variant", "quiet")
        output_row.addWidget(self.concat_output_edit, 1); output_row.addWidget(self.concat_output_button)
        rl.addLayout(output_row)

        rl.addWidget(self._muted("Định dạng xuất"))
        self.concat_format_combo = QComboBox()
        self.concat_format_combo.addItem("MP4", "mp4")
        self.concat_format_combo.addItem("MKV (khuyến nghị cho video rất dài)", "mkv")
        rl.addWidget(self.concat_format_combo)

        self.concat_safe_check = QCheckBox("Chế độ an toàn (remux tạm trước khi nối — chậm hơn, ít lỗi khung hình ở điểm nối)")
        self.concat_safe_check.setObjectName("settingsCheck")
        rl.addWidget(self.concat_safe_check)

        self.concat_analyze_btn = QPushButton("🔍  Phân tích tương thích")
        self.concat_analyze_btn.setProperty("variant", "quiet")
        rl.addWidget(self.concat_analyze_btn)
        self.concat_normalize_btn = QPushButton("🛠  Chuẩn hóa video không tương thích")
        self.concat_normalize_btn.setProperty("variant", "quiet")
        self.concat_normalize_btn.setEnabled(False)
        rl.addWidget(self.concat_normalize_btn)
        self.concat_start_btn = QPushButton("▶  Nối video"); self.concat_start_btn.setObjectName("btnPrimary")
        rl.addWidget(self.concat_start_btn)
        rl.addStretch(1)
        body.addWidget(right_card)

        pl.addLayout(body, 1)

        self.concat_status_label = QLabel("Sẵn sàng — thêm video rồi bấm Phân tích.")
        self.concat_status_label.setProperty("role", "mutedText")
        pl.addWidget(self.concat_status_label)
        self.concat_progress_bar = QProgressBar(); self.concat_progress_bar.setRange(0, 100)
        pl.addWidget(self.concat_progress_bar)
        self.concat_log = QPlainTextEdit(); self.concat_log.setReadOnly(True)
        self.concat_log.setMaximumHeight(140); self.concat_log.setPlaceholderText("Nhật ký xử lý...")
        pl.addWidget(self.concat_log)
        return page

    def _concat_append_log(self, message: str) -> None:
        self.concat_log.appendPlainText(message)

    def _concat_add_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Chọn video để nối")
        if not paths:
            return
        for path in paths:
            if path not in self._concat_paths:
                self._concat_paths.append(path)
        self._concat_report = None
        self._refresh_concat_list()

    def _concat_remove_selected(self) -> None:
        row = self.concat_list.currentRow()
        if row < 0:
            return
        del self._concat_paths[row]
        self._concat_report = None
        self._refresh_concat_list()

    def _concat_move_selected(self, delta: int) -> None:
        row = self.concat_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self._concat_paths)):
            return
        self._concat_paths[row], self._concat_paths[target] = self._concat_paths[target], self._concat_paths[row]
        self._refresh_concat_list()
        self.concat_list.setCurrentRow(target)

    def _refresh_concat_list(self) -> None:
        self.concat_list.clear()
        for path in self._concat_paths:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            self.concat_list.addItem(item)
        self.concat_normalize_btn.setEnabled(False)
        self._concat_queue_thumbnails(self._concat_paths)

    def _concat_queue_thumbnails(self, paths: list[str]) -> None:
        queued = set(self._concat_thumbnail_queue)
        for path in paths:
            if path not in queued:
                self._concat_thumbnail_queue.append(path)
                queued.add(path)
        if self._concat_thumbnail_thread is None:
            self._concat_start_thumbnail_worker()

    def _concat_start_thumbnail_worker(self) -> None:
        if not self._concat_thumbnail_queue:
            return
        paths = self._concat_thumbnail_queue
        self._concat_thumbnail_queue = []
        self._concat_thumbnail_thread = QThread(self)
        self._concat_thumbnail_worker = ThumbnailWorker(paths)
        self._concat_thumbnail_worker.moveToThread(self._concat_thumbnail_thread)
        self._concat_thumbnail_thread.started.connect(self._concat_thumbnail_worker.run)
        self._concat_thumbnail_worker.thumbnail_ready.connect(self._concat_set_thumbnail)
        self._concat_thumbnail_worker.finished.connect(self._concat_thumbnail_thread.quit)
        self._concat_thumbnail_thread.finished.connect(self._concat_thumbnail_done)
        self._concat_thumbnail_thread.start()

    def _concat_thumbnail_done(self) -> None:
        self._concat_thumbnail_thread = None
        self._concat_thumbnail_worker = None
        self._concat_start_thumbnail_worker()

    def _concat_set_thumbnail(self, video_path: str, thumbnail_path: str) -> None:
        pixmap = QPixmap(thumbnail_path)
        if pixmap.isNull():
            return
        icon = QIcon(pixmap)
        resolved = str(Path(video_path).resolve())
        for i in range(self.concat_list.count()):
            item = self.concat_list.item(i)
            item_path = item.data(Qt.UserRole)
            if item_path and str(Path(item_path).resolve()) == resolved:
                item.setIcon(icon)

    def _concat_choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu output")
        if path:
            self.concat_output_edit.setText(path)

    def _concat_output_extension(self) -> str:
        ext = self.concat_format_combo.currentData()
        return ext if ext in {"mp4", "mkv"} else "mkv"

    def _concat_make_output_path(self, output_folder: Path) -> Path:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = self._concat_output_extension()
        candidate = output_folder / f"VIDEO_{stamp}.{extension}"
        suffix = 2
        while candidate.exists():
            candidate = output_folder / f"VIDEO_{stamp}_{suffix}.{extension}"
            suffix += 1
        return candidate

    def _concat_durations(self, paths: list[str]) -> list[float | None]:
        if not self._concat_report:
            return [None for _ in paths]
        by_path = {item.path: item.duration for item in self._concat_report.files}
        return [by_path.get(path) or by_path.get(str(Path(path).resolve())) for path in paths]

    def _concat_set_busy(self, busy: bool) -> None:
        for widget in (self.concat_add_btn, self.concat_remove_btn, self.concat_up_btn, self.concat_down_btn,
                       self.concat_analyze_btn, self.concat_output_button, self.concat_format_combo,
                       self.concat_safe_check):
            widget.setEnabled(not busy)
        if busy:
            self.concat_normalize_btn.setEnabled(False)
        self.concat_start_btn.setText("⏹  Dừng" if busy else "▶  Nối video")

    def _concat_analyze(self) -> None:
        if not self._concat_paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm ít nhất 1 video."); return
        self._concat_set_busy(True)
        self.concat_status_label.setText("Đang phân tích...")
        self._concat_append_log("Bắt đầu phân tích bằng ffprobe.")
        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(list(self._concat_paths))
        self._analysis_worker.moveToThread(self._analysis_thread)
        self._analysis_thread.started.connect(self._analysis_worker.run)
        self._analysis_worker.log.connect(self._concat_append_log)
        self._analysis_worker.finished.connect(self._concat_analysis_finished)
        self._analysis_worker.failed.connect(self._concat_analysis_failed)
        self._analysis_worker.finished.connect(self._analysis_thread.quit)
        self._analysis_worker.failed.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._concat_clear_analysis_thread)
        self._analysis_thread.start()

    def _concat_analysis_finished(self, report: CompatibilityReport) -> None:
        self._concat_report = report
        size_hint = f" — Dung lượng: {format_size(sum(f.size for f in report.files if f.size > 0))}"
        self.concat_status_label.setText(
            f"{report.message} Tổng thời lượng: {format_duration(report.total_duration)}{size_hint}"
        )
        self._concat_append_log(report.message)
        if report.issues:
            self._concat_append_log("Các khác biệt phát hiện:")
            for issue in report.issues:
                self._concat_append_log(f"- {issue}")
            if report.incompatible_paths:
                names = ", ".join(Path(p).name for p in report.incompatible_paths)
                self._concat_append_log(f"File nên xóa khỏi danh sách: {names}")
        self._concat_highlight_incompatible(report.incompatible_paths)
        self.concat_normalize_btn.setEnabled(bool(report.incompatible_paths) and bool(report.compatible_paths))
        self._concat_set_busy(False)

    def _concat_analysis_failed(self, message: str) -> None:
        self._concat_report = None
        self.concat_status_label.setText("Phân tích thất bại.")
        self._concat_append_log(f"Lỗi phân tích: {message}")
        self._concat_highlight_incompatible([])
        QMessageBox.critical(self, "Lỗi phân tích", message)
        self._concat_set_busy(False)

    def _concat_clear_analysis_thread(self) -> None:
        self._analysis_thread = None
        self._analysis_worker = None

    def _concat_highlight_incompatible(self, incompatible_paths: list[str]) -> None:
        """Tô đỏ các file KHÔNG cùng nhóm tương thích lớn nhất trong danh sách."""
        incompatible = {str(Path(p).resolve()) for p in incompatible_paths}
        for i in range(self.concat_list.count()):
            item = self.concat_list.item(i)
            item_path = item.data(Qt.UserRole)
            resolved = str(Path(item_path).resolve()) if item_path else None
            if resolved and resolved in incompatible:
                item.setBackground(QBrush(QColor("#4c1d2f")))
                item.setForeground(QBrush(QColor("#fecdd3")))
            else:
                item.setBackground(QBrush())
                item.setForeground(QBrush())

    def _concat_normalize(self) -> None:
        if not self._concat_report or not self._concat_report.incompatible_paths or not self._concat_report.compatible_paths:
            QMessageBox.information(self, "Không có file cần chuẩn hóa", "Hãy Phân tích trước.")
            return
        ref_path = self._concat_report.compatible_paths[0]
        ref_analysis = next((f for f in self._concat_report.files if f.path == ref_path), None)
        if ref_analysis is None:
            QMessageBox.warning(self, "Lỗi", "Không tìm thấy thông số video chuẩn."); return
        video_stream = next((s for s in ref_analysis.streams if s.codec_type == "video"), None)
        audio_stream = next((s for s in ref_analysis.streams if s.codec_type == "audio"), None)
        if video_stream is None:
            QMessageBox.warning(self, "Lỗi", "Video chuẩn không có stream hình ảnh."); return
        try:
            width = int(video_stream.signature.get("width") or 0)
            height = int(video_stream.signature.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width <= 0 or height <= 0:
            QMessageBox.warning(self, "Lỗi", "Không xác định được độ phân giải chuẩn."); return
        fps = video_stream.signature.get("r_frame_rate") or "30"
        pix_fmt = video_stream.signature.get("pix_fmt") or "yuv420p"
        profile = video_stream.signature.get("profile") or ""
        level = video_stream.signature.get("level") or ""
        try:
            sample_rate = int(audio_stream.signature.get("sample_rate")) if audio_stream else 44100
        except (TypeError, ValueError):
            sample_rate = 44100
        try:
            channels = int(audio_stream.signature.get("channels")) if audio_stream else 2
        except (TypeError, ValueError):
            channels = 2

        output_dir = self.concat_output_edit.text().strip() or str(Path(ref_path).parent)
        target_dir = str(Path(output_dir) / "_chuan_hoa")
        incompatible_paths = list(self._concat_report.incompatible_paths)
        self._concat_append_log(
            f"Chuẩn hóa {len(incompatible_paths)} file về {width}x{height}, {fps}fps, {pix_fmt}, "
            f"profile {profile or '?'}, audio {sample_rate}Hz/{channels}ch (theo {Path(ref_path).name})..."
        )
        self._concat_set_busy(True)
        self.concat_normalize_btn.setEnabled(False)
        self.concat_status_label.setText("Đang chuẩn hóa video không tương thích...")

        self._normalize_thread = QThread(self)
        self._normalize_worker = NormalizeWorker(
            incompatible_paths, width=width, height=height, fps=fps,
            sample_rate=sample_rate, channels=channels, output_dir=target_dir,
            pix_fmt=pix_fmt, profile=profile, level=level,
        )
        self._normalize_worker.moveToThread(self._normalize_thread)
        self._normalize_thread.started.connect(self._normalize_worker.run)
        self._normalize_worker.log.connect(self._concat_append_log)
        self._normalize_worker.file_done.connect(self._concat_normalize_file_done)
        self._normalize_worker.finished.connect(self._concat_normalize_finished)
        self._normalize_worker.finished.connect(self._normalize_thread.quit)
        self._normalize_thread.finished.connect(self._concat_clear_normalize_thread)
        self._normalize_thread.start()

    def _concat_normalize_file_done(self, original_path: str, normalized_path: str) -> None:
        resolved = str(Path(original_path).resolve())
        for i, path in enumerate(self._concat_paths):
            if str(Path(path).resolve()) == resolved:
                self._concat_paths[i] = normalized_path
        self._concat_append_log(f"Đã thay {Path(original_path).name} bằng bản chuẩn hóa trong danh sách.")

    def _concat_normalize_finished(self, ok: bool, message: str) -> None:
        self._concat_append_log(message)
        self.concat_status_label.setText(message if ok else "Chuẩn hóa thất bại.")
        if not ok:
            QMessageBox.warning(self, "Chuẩn hóa thất bại", message)
        self._concat_report = None
        self._refresh_concat_list()
        self._concat_set_busy(False)

    def _concat_clear_normalize_thread(self) -> None:
        self._normalize_thread = None
        self._normalize_worker = None

    def _concat_start(self) -> None:
        if self._concat_worker is not None:
            self._concat_worker.stop()
            self.concat_status_label.setText("Đang dừng...")
            return
        paths = list(self._concat_paths)
        output_dir = self.concat_output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm video trước khi nối."); return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output."); return
        output_folder = Path(output_dir).resolve()
        if output_folder.exists() and not output_folder.is_dir():
            QMessageBox.critical(self, "Thư mục không hợp lệ", "Đường dẫn output phải là thư mục."); return
        if self._concat_report is None or [f.path for f in self._concat_report.files] != [str(Path(p).resolve()) for p in paths]:
            QMessageBox.warning(self, "Chưa phân tích", "Hãy bấm Phân tích sau khi thêm hoặc đổi thứ tự file."); return
        if not self._concat_report.is_compatible:
            QMessageBox.critical(
                self, "Không thể nối",
                "Các file không cùng thông số stream — không thể nối bằng stream copy. Xem log chi tiết."
            ); return

        output_path = self._concat_make_output_path(output_folder)
        if any(Path(p).resolve() == output_path for p in paths):
            QMessageBox.critical(self, "Output không hợp lệ", "Output không được trùng file input."); return

        durations = self._concat_durations(paths)
        expected = sum(d for d in durations if d and d > 0) or None
        if self._concat_output_extension() == "mp4" and expected and expected >= 24 * 3600:
            if expected >= 100 * 3600:
                QMessageBox.critical(
                    self, "Không xuất MP4 trên 100 tiếng",
                    f"Output dài khoảng {format_duration(expected)}. Hãy đổi định dạng sang MKV rồi chạy lại."
                ); return
            if QMessageBox.question(
                self, "MP4 rất dài",
                f"Output dài khoảng {format_duration(expected)}. MP4 trên 24 giờ có thể bị vài trình phát "
                "hiển thị sai thời lượng. Nên dùng MKV. Vẫn muốn tiếp tục xuất MP4?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return

        safe_mode = self.concat_safe_check.isChecked()
        self._concat_set_busy(True)
        self.concat_progress_bar.setValue(0)
        self.concat_status_label.setText("Đang nối an toàn..." if safe_mode else "Đang nối video...")
        self._concat_append_log(f"Bắt đầu nối {len(paths)} file -> {output_path}")

        self._concat_thread = QThread(self)
        self._concat_worker = ConcatWorker(
            paths, str(output_path),
            expected_duration=expected, file_durations=durations, safe_mode=safe_mode,
        )
        self._concat_worker.moveToThread(self._concat_thread)
        self._concat_thread.started.connect(self._concat_worker.run)
        self._concat_worker.log.connect(self._concat_append_log)
        self._concat_worker.progress.connect(self._concat_progress)
        self._concat_worker.finished.connect(self._concat_finished)
        self._concat_worker.finished.connect(self._concat_thread.quit)
        self._concat_thread.finished.connect(self._concat_clear_thread)
        self._concat_thread.start()

    def _concat_progress(self, value: str) -> None:
        expected = getattr(self._concat_worker, "expected_duration", None) or 0
        if value.isdigit():
            seconds = int(value) / 1_000_000
            self.concat_status_label.setText(f"Đang nối... {format_duration(seconds)}")
            if expected > 0:
                self.concat_progress_bar.setValue(max(0, min(100, int(seconds * 100 / expected))))
            return
        self.concat_status_label.setText(f"Đang nối... {value}")

    def _concat_finished(self, ok: bool, message: str) -> None:
        self._concat_append_log(message)
        self.concat_status_label.setText("Hoàn tất." if ok else "Nối video thất bại hoặc đã dừng.")
        self.concat_progress_bar.setValue(100 if ok else 0)
        if ok:
            QMessageBox.information(self, "Hoàn tất", message)
        else:
            QMessageBox.warning(self, "Chưa hoàn tất", message)

    def _concat_clear_thread(self) -> None:
        self._concat_thread = None
        self._concat_worker = None
        self._concat_set_busy(False)

    def _build_effects_page(self) -> QWidget:
        """Tab Hiệu ứng: tạo hiệu ứng lớp phủ (video + độ trong suốt) 1 lần — vào từng dự án chỉ cần
        chọn theo tên, không phải chỉnh lại video/độ trong suốt mỗi lần."""
        page = QWidget(); page.setObjectName("webPage")
        pl = QVBoxLayout(page); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(16)
        ht = QLabel("HIỆU ỨNG"); ht.setObjectName("pageTitle"); pl.addWidget(ht)
        pl.addWidget(self._muted("Tạo hiệu ứng lớp phủ (video overlay + độ trong suốt) 1 lần — mọi dự án sau đó chỉ cần chọn theo tên."))

        form_card = QFrame(); form_card.setObjectName("pageCard")
        fl = QVBoxLayout(form_card); fl.setContentsMargins(18, 18, 18, 18); fl.setSpacing(10)
        ft = QLabel("TẠO HIỆU ỨNG MỚI"); ft.setObjectName("cardTitle"); fl.addWidget(ft)

        fl.addWidget(self._muted("Tên hiệu ứng"))
        self.effect_name_edit = QLineEdit(); self.effect_name_edit.setPlaceholderText("Ví dụ: Hạt phim cổ điển")
        fl.addWidget(self.effect_name_edit)

        fl.addWidget(self._muted("Video overlay"))
        video_row = QHBoxLayout(); video_row.setSpacing(8)
        self.effect_video_label = QLabel("Chưa chọn video"); self.effect_video_label.setProperty("role", "mutedText")
        self.effect_choose_video_button = QPushButton("Chọn video…"); self.effect_choose_video_button.setProperty("variant", "quiet")
        video_row.addWidget(self.effect_video_label, 1); video_row.addWidget(self.effect_choose_video_button)
        fl.addLayout(video_row)

        self.effect_opacity_caption = self._muted("Độ trong suốt: 100%")
        fl.addWidget(self.effect_opacity_caption)
        self.effect_opacity_slider = QSlider(Qt.Horizontal)
        self.effect_opacity_slider.setRange(0, 100); self.effect_opacity_slider.setValue(100)
        fl.addWidget(self.effect_opacity_slider)

        self.effect_create_button = QPushButton("＋  Tạo hiệu ứng"); self.effect_create_button.setObjectName("btnPrimary")
        fl.addWidget(self.effect_create_button)
        pl.addWidget(form_card)

        list_title = QLabel("HIỆU ỨNG ĐÃ CÓ"); list_title.setObjectName("pageTitle"); pl.addWidget(list_title)
        self.effects_table = QTableWidget(0, 4)
        self.effects_table.setHorizontalHeaderLabels(("Tên", "Độ trong suốt", "Nguồn", "Thao tác"))
        self.effects_table.setSelectionMode(QTableWidget.NoSelection)
        self.effects_table.verticalHeader().setVisible(False)
        self.effects_table.setAlternatingRowColors(True)
        hv = self.effects_table.horizontalHeader()
        hv.setSectionResizeMode(0, QHeaderView.Stretch)
        for idx in (1, 2, 3): hv.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        pl.addWidget(self.effects_table, 1)

        self._effect_video_path = ""
        self._refresh_effects_table()
        return page

    def _choose_effect_video(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Chọn video hiệu ứng lớp phủ (overlay)", "", "Video (*.mp4 *.mov *.mkv *.webm)"
        )
        if not path:
            return
        self._effect_video_path = path
        self.effect_video_label.setText(Path(path).name)

    def _on_effect_opacity_changed(self, value: int) -> None:
        self.effect_opacity_caption.setText(f"Độ trong suốt: {value}%")

    def _create_effect(self) -> None:
        name = self.effect_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu tên", "Hãy đặt tên cho hiệu ứng."); return
        if not self._effect_video_path:
            QMessageBox.warning(self, "Thiếu video", "Hãy chọn video overlay cho hiệu ứng."); return
        try:
            create_effect(name, self._effect_video_path, self.effect_opacity_slider.value() / 100)
        except (ValueError, FileNotFoundError, OSError) as exc:
            QMessageBox.warning(self, "Lỗi", f"Không tạo được hiệu ứng: {exc}"); return
        self.effect_name_edit.clear()
        self._effect_video_path = ""
        self.effect_video_label.setText("Chưa chọn video")
        self.effect_opacity_slider.setValue(100)
        self._refresh_effects_table()

    def _refresh_effects_table(self) -> None:
        self.effects_table.setRowCount(0)
        for preset in list_effects():
            row = self.effects_table.rowCount()
            self.effects_table.insertRow(row)
            self.effects_table.setItem(row, 0, QTableWidgetItem(preset.name))
            self.effects_table.setItem(row, 1, QTableWidgetItem(f"{round(preset.opacity * 100)}%"))
            self.effects_table.setItem(row, 2, QTableWidgetItem("Có sẵn" if preset.builtin else "Tự tạo"))
            if preset.builtin:
                edit_button = _table_icon_button("✏", "Sửa độ trong suốt")
                edit_button.clicked.connect(lambda _c=False, n=preset.name, o=preset.opacity: self._edit_builtin_effect_opacity(n, o))
                self.effects_table.setCellWidget(row, 3, edit_button)
            else:
                delete_button = _table_icon_button("🗑", "Xóa hiệu ứng")
                delete_button.clicked.connect(lambda _c=False, n=preset.name: self._delete_effect(n))
                self.effects_table.setCellWidget(row, 3, delete_button)

    def _edit_builtin_effect_opacity(self, name: str, current_opacity: float) -> None:
        value, ok = QInputDialog.getInt(
            self, "Sửa độ trong suốt", f"Độ trong suốt cho \"{name}\" (%):",
            round(current_opacity * 100), 0, 100, 1,
        )
        if not ok:
            return
        set_builtin_effect_opacity(name, value / 100)
        self._refresh_effects_table()

    def _delete_effect(self, name: str) -> None:
        if QMessageBox.question(self, "Xóa hiệu ứng", f"Xóa hiệu ứng \"{name}\"?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        delete_effect(name)
        self._refresh_effects_table()

    def _build_settings_page(self) -> QWidget:
        """Tab 6 — Cài đặt."""
        page = QWidget(); page.setObjectName("webPage")
        pl = QVBoxLayout(page); pl.setContentsMargins(22, 20, 22, 18); pl.setSpacing(16)
        ht = QLabel("CÀI ĐẶT"); ht.setObjectName("pageTitle"); pl.addWidget(ht)

        for title, text in (
            ("Chạy đồng thời", "Giới hạn số dự án được xử lý cùng lúc để tránh quá tải CPU/GPU."),
            ("Tăng tốc phần cứng", "Cấu hình NVIDIA NVENC hoặc CPU libx264 làm encoder mặc định."),
            ("Tự động tiếp tục", "Khôi phục tiến trình xử lý chưa hoàn thành sau khi khởi động lại phần mềm."),
        ):
            card = QFrame(); card.setObjectName("pageCard")
            cl = QVBoxLayout(card); cl.setContentsMargins(18, 18, 18, 18)
            ct = QLabel(title); ct.setObjectName("cardTitle")
            cx = QLabel(text); cx.setProperty("role", "mutedText"); cx.setWordWrap(True)
            cl.addWidget(ct); cl.addWidget(cx); cl.addStretch(1)
            pl.addWidget(card)
        pl.addStretch(1)
        return page

    @staticmethod
    def _muted(text: str) -> QLabel:
        lbl = QLabel(text); lbl.setProperty("role", "mutedText"); return lbl

    # ──────────────────────────────────────────────────────────────────────
    # NAVIGATION & CONNECT
    # ──────────────────────────────────────────────────────────────────────
    def _switch_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons): btn.setChecked(i == index)
        if index == 4: self._refresh_history()          # Lịch sử — tải log mỗi khi vào tab
        if index == 6: self._refresh_effects_table()     # Hiệu ứng — cập nhật danh sách mỗi khi vào tab

    def _connect(self) -> None:
        self.new_button.clicked.connect(self.create_project)
        self.proj_new_btn.clicked.connect(self.create_project)
        self.shared_output_button.clicked.connect(self._choose_shared_output_dir)
        self.effect_choose_video_button.clicked.connect(self._choose_effect_video)
        self.effect_opacity_slider.valueChanged.connect(self._on_effect_opacity_changed)
        self.effect_create_button.clicked.connect(self._create_effect)
        self.concat_add_btn.clicked.connect(self._concat_add_files)
        self.concat_remove_btn.clicked.connect(self._concat_remove_selected)
        self.concat_up_btn.clicked.connect(lambda: self._concat_move_selected(-1))
        self.concat_down_btn.clicked.connect(lambda: self._concat_move_selected(1))
        self.concat_output_button.clicked.connect(self._concat_choose_output)
        self.concat_analyze_btn.clicked.connect(self._concat_analyze)
        self.concat_normalize_btn.clicked.connect(self._concat_normalize)
        self.concat_start_btn.clicked.connect(self._concat_start)
        for i, btn in enumerate(self.nav_buttons):
            btn.clicked.connect(lambda _c=False, p=i: self._switch_page(p))
        self.search_edit.textChanged.connect(self.apply_filters)
        self.status_combo.currentIndexChanged.connect(self.apply_filters)
        self.filter_reset_button.clicked.connect(self._reset_filters)
        self.table.itemSelectionChanged.connect(lambda: self._on_table_row_selected(self.table))
        self.table.cellDoubleClicked.connect(lambda *_: self._open_editor_from_table(self.table))
        self.attention_table.itemSelectionChanged.connect(lambda: self._on_table_row_selected(self.attention_table))
        self.attention_table.cellDoubleClicked.connect(lambda *_: self._open_editor_from_table(self.attention_table))
        self.queue_table.cellDoubleClicked.connect(lambda *_: self._open_editor_from_table(self.queue_table))
        self.output_table.cellDoubleClicked.connect(lambda *_: self._open_editor_from_table(self.output_table))
        for card in self.cards: card.clicked.connect(lambda _c=False, c=card: self.filter_card(c.status))
        self.run_all_button.clicked.connect(self.run_all)
        self.pause_all_button.clicked.connect(self.pause_all)
        self.proj_run_all_btn.clicked.connect(self.run_all)
        self.proj_pause_all_btn.clicked.connect(self.pause_all)
        self.detail_run.clicked.connect(self.run_selected)
        self.detail_pause.clicked.connect(self.pause_selected)
        self.detail_open.clicked.connect(self.open_output)
        self.prev_page_button.clicked.connect(lambda: self._change_page(-1))
        self.next_page_button.clicked.connect(lambda: self._change_page(1))
        self.queue_run_all.clicked.connect(self.run_all)
        self.queue_pause_all.clicked.connect(self.pause_all)
        self.output_open_all.clicked.connect(self._open_first_output_folder)
        self.history_refresh_btn.clicked.connect(self._refresh_history)

    # ──────────────────────────────────────────────────────────────────────
    # REFRESH
    # ──────────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self._projects = self.store.list_projects()
        counts = {s: 0 for s in PROJECT_STATUSES}
        for p in self._projects: counts[p.status] = counts.get(p.status, 0) + 1
        for card in self.cards: card.set_count(len(self._projects) if not card.status else counts.get(card.status, 0))
        self.quick_stat_labels["total"].setText(str(len(self._projects)))
        self.quick_stat_labels["running"].setText(str(counts.get("Đang chạy", 0)))
        self.quick_stat_labels["done"].setText(str(counts.get("Hoàn thành", 0)))
        self.quick_stat_labels["cancelled"].setText(str(counts.get("Đã hủy", 0)))
        self.apply_filters()
        self._refresh_attention_table()
        self._refresh_queue_table()
        self._refresh_output_table()

    def _refresh_attention_table(self) -> None:
        """Tab Tổng quan: chỉ dự án Lỗi/Đang chạy/Đang chờ — cần chú ý ngay, không trùng với tab Dự án."""
        priority = {"Lỗi": 0, "Đang chạy": 1, "Đang chờ": 2}
        attention = sorted(
            (p for p in self._projects if p.status in priority),
            key=lambda p: priority[p.status],
        )[:8]
        self.attention_table.setRowCount(0)
        for p in attention: self._fill_row(self.attention_table, p)
        self.attention_table.setVisible(bool(attention))
        self.attention_empty_label.setVisible(not attention)

    def _refresh_queue_table(self) -> None:
        queue = [p for p in self._projects if p.status in ("Đang chờ", "Đang chạy", "Tạm dừng")]
        running = sum(1 for p in queue if p.status == "Đang chạy")
        waiting = sum(1 for p in queue if p.status == "Đang chờ")
        self.queue_status_label.setText(f"{running} đang chạy  •  {waiting} đang chờ")
        self.queue_table.setRowCount(0)
        for p in queue: self._fill_row(self.queue_table, p)

    def _refresh_output_table(self) -> None:
        done = [p for p in self._projects if p.status == "Hoàn thành"]
        self.output_summary.setText(f"{len(done)} dự án hoàn thành")
        self.output_table.setRowCount(0)
        for p in done: self._fill_row(self.output_table, p)

    def _refresh_history(self) -> None:
        lines: list[str] = []
        for p in self._projects:
            for row in self.store.project_logs(p.id):
                lines.append(f"[{row['created_at'][5:]}] [{row['level']}] {p.name}: {row['message']}")
        lines.sort(reverse=True)
        self.history_log.setPlainText("\n".join(lines[:500]) if lines else "Chưa có nhật ký.")

    # ──────────────────────────────────────────────────────────────────────
    # FILTER / PAGINATION (tab Tổng quan)
    # ──────────────────────────────────────────────────────────────────────
    def apply_filters(self) -> None:
        query = self.search_edit.text().strip().casefold()
        status = self._status_filter or self.status_combo.currentData()
        self._filtered = [p for p in self._projects
                          if (not query or query in f"{p.name} {p.input_path} {p.output_path}".casefold())
                          and (not status or p.status == status)]
        self._page = 0; self._render_page()

    def _render_page(self) -> None:
        total = len(self._filtered)
        page_count = max(1, (total + self._page_size - 1) // self._page_size)
        self._page = max(0, min(self._page, page_count - 1))
        start = self._page * self._page_size
        page_items = self._filtered[start:start + self._page_size]
        self.table.setRowCount(0)
        for p in page_items: self._add_row(p)
        if page_items: self.table.selectRow(0)
        else: self.clear_details()
        self.pagination_label.setText(
            f"Hiển thị {start + 1} – {start + len(page_items)} của {total} dự án" if total else "Không có dự án nào"
        )
        self.page_indicator.setText(str(self._page + 1))
        self.prev_page_button.setEnabled(self._page > 0)
        self.next_page_button.setEnabled(self._page < page_count - 1)

    def _change_page(self, delta: int) -> None:
        self._page += delta; self._render_page()

    def _reset_filters(self) -> None:
        self._status_filter = ""
        self.search_edit.clear(); self.status_combo.setCurrentIndex(0)
        self.apply_filters()

    def filter_card(self, status: str) -> None:
        """Bấm stat card ở Tổng quan → chuyển sang tab Dự án đã lọc sẵn theo trạng thái (tránh trùng bảng)."""
        self._status_filter = status
        self.status_combo.setCurrentIndex(0)
        self._switch_page(1)
        self.apply_filters()

    # ──────────────────────────────────────────────────────────────────────
    # TABLE HELPERS
    # ──────────────────────────────────────────────────────────────────────
    def _fill_row(self, table: QTableWidget, project: Project) -> None:
        """Thêm một hàng vào bất kỳ QTableWidget nào — 5 cột: Dự án, Trạng thái, Tiến trình, Ngày tạo, Thao tác."""
        row = table.rowCount(); table.insertRow(row)
        name_item = QTableWidgetItem(f"  {project.name}\n  {project.output_path or 'Chưa chọn đầu ra'}")
        name_item.setData(Qt.UserRole, project.id); name_item.setIcon(_task_icon(project.task_type))
        table.setItem(row, 0, name_item)
        table.setCellWidget(row, 1, _status_cell(project.status))
        table.setCellWidget(row, 2, _progress_cell(project.progress))
        table.setItem(row, 3, QTableWidgetItem(project.created_at.replace("T", " ")))
        mb = QPushButton("•••"); mb.setObjectName("menuButton")
        mb.clicked.connect(lambda _=False, p=project, b=mb: self.project_menu(p, b))
        table.setCellWidget(row, 4, mb); table.setRowHeight(row, 64)

    def _add_row(self, project: Project) -> None:
        """Thêm hàng vào bảng đầy đủ của tab Dự án."""
        self._fill_row(self.table, project)

    def _selected_from_table(self, table: QTableWidget) -> Project | None:
        row = table.currentRow()
        if row < 0 or not table.item(row, 0): return None
        return self.store.get_project(int(table.item(row, 0).data(Qt.UserRole)))

    def _on_table_row_selected(self, table: QTableWidget) -> None:
        """Bất kỳ bảng dự án nào (Tổng quan/Dự án) đổi lựa chọn đều cập nhật ID đang chọn + panel chi tiết."""
        p = self._selected_from_table(table)
        self._selected_project_id = p.id if p else None
        self.show_selected_details()

    def selected_project(self) -> Project | None:
        return self.store.get_project(self._selected_project_id) if self._selected_project_id else None

    def _open_editor_from_table(self, table: QTableWidget) -> None:
        p = self._selected_from_table(table)
        if p is None: return
        self._selected_project_id = p.id
        self.open_editor()

    # ──────────────────────────────────────────────────────────────────────
    # DETAIL PANEL
    # ──────────────────────────────────────────────────────────────────────
    def show_selected_details(self) -> None:
        p = self.selected_project()
        if p is None: self.clear_details(); return
        self.detail_name.setText(p.name); self.detail_progress.setValue(p.progress)
        self.detail_meta.setText(
            f"Tác vụ: {p.task_type}\nTrạng thái: {p.status}\nƯu tiên: {p.priority}\n"
            f"Số video: {p.file_count}\n\nNguồn: {p.input_path or 'Chưa chọn'}\n"
            f"Đầu ra: {p.output_path or 'Chưa chọn'}\n\n"
            f"Ngày tạo: {p.created_at.replace('T', ' ')}\n"
            f"Lần chạy gần nhất: {p.last_run_at.replace('T', ' ') or 'Chưa chạy'}"
            + (f"\n\nLỗi: {p.error_message}" if p.error_message else "")
        )
        lines = [f"{r['created_at'][11:]}  [{r['level']}] {r['message']}" for r in self.store.project_logs(p.id)]
        self.detail_log.setPlainText("\n".join(lines))
        self.stepper.set_active_step(_STATUS_STEP.get(p.status, 1))

    def clear_details(self) -> None:
        self._selected_project_id = None
        self.detail_name.setText("Không có dự án")
        self.detail_meta.setText("Hãy tạo dự án mới hoặc thay đổi bộ lọc.")
        self.detail_progress.setValue(0); self.detail_log.clear()
        self.stepper.set_active_step(1)

    # ──────────────────────────────────────────────────────────────────────
    # ACTIONS
    # ──────────────────────────────────────────────────────────────────────
    def create_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.Accepted: return
        values = dialog.values()
        default_output = self.store.get_app_setting("default_output_dir", "")
        if default_output:
            values["output_path"] = default_output
        pid = self.store.create_project(**values)
        self.refresh(); self.select_project(pid); self.open_editor()

    def _choose_shared_output_dir(self) -> None:
        """Đặt 1 thư mục đầu ra dùng chung cho MỌI dự án (hiện có + tạo mới sau này).
        Mỗi dự án vẫn có thư mục con riêng bên trong (project_output_root) nên không đụng độ nhau."""
        current = self.store.get_app_setting("default_output_dir", "")
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục đầu ra chung cho tất cả dự án", current)
        if not path:
            return
        self.store.set_app_setting("default_output_dir", path)
        self.shared_output_label.setText(path)
        for p in self.store.list_projects():
            self.store.update_fields(p.id, output_path=path)
        self.refresh()
        self.show_selected_details()
        QMessageBox.information(
            self, "Đã cập nhật",
            f"Đã đặt thư mục đầu ra chung cho {len(self._projects)} dự án hiện có "
            f"và sẽ áp dụng cho dự án tạo mới sau này:\n{path}"
        )

    def select_project(self, project_id: int) -> None:
        """Chọn dự án theo id bất kể đang ở tab nào / có đang lọc hay không — luôn cập nhật panel chi tiết trước,
        rồi cố gắng highlight đúng hàng trong bảng Dự án nếu dự án đó đang hiển thị ở trang hiện tại."""
        self._selected_project_id = project_id
        self.show_selected_details()
        idx = next((i for i, p in enumerate(self._filtered) if p.id == project_id), None)
        if idx is None: return
        tp = idx // self._page_size
        if tp != self._page: self._page = tp; self._render_page()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0) and self.table.item(row, 0).data(Qt.UserRole) == project_id:
                self.table.selectRow(row); break

    def project_menu(self, project: Project, button: QPushButton) -> None:
        menu = QMenu(self)
        run = menu.addAction("Chạy / Tiếp tục")
        edit = menu.addAction("Chỉnh sửa")
        duplicate = menu.addAction("Nhân bản")
        open_folder = menu.addAction("Mở thư mục đầu ra")
        menu.addSeparator()
        delete = menu.addAction("Xóa dự án")
        action = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if action == run:
            self.store.update_status(project.id, "Đang chờ"); self.refresh()
        elif action == edit:
            self.select_project(project.id); self.open_editor()
        elif action == duplicate:
            self.store.duplicate_project(project.id); self.refresh()
        elif action == open_folder:
            self._open_project_folder(project)
        elif action == delete:
            if QMessageBox.question(self, "Xóa dự án", f"Xóa dự án \"{project.name}\"?",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self._delete_project(project)

    def _delete_project(self, project: Project) -> None:
        """Xóa dự án: dọn sạch dữ liệu DB + toàn bộ thư mục xử lý riêng của dự án (segment/voice.wav/final.mp4...),
        NHƯNG giữ lại file thành phẩm phẳng '{tên dự án}.mp4' ở thư mục đầu ra chung (publish_final_copy)."""
        try:
            publish_final_copy(project)
        except OSError:
            pass
        folder = project_output_root(project)
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self.store.delete_project(project.id)
        if self._selected_project_id == project.id:
            self._selected_project_id = None
            self.show_selected_details()
        self.refresh()

    def run_selected(self) -> None:
        p = self.selected_project()
        if p: self.store.update_status(p.id, "Đang chờ"); self.refresh(); self.select_project(p.id)
        self._process_next_in_queue()

    def pause_selected(self) -> None:
        p = self.selected_project()
        if not p: return
        if p.id == self._queue_active_project_id and self._queue_worker is not None:
            self._queue_worker.stop()
        self.store.update_status(p.id, "Tạm dừng"); self.refresh(); self.select_project(p.id)

    def run_all(self) -> None:
        for p in self._projects:
            if p.status in {"Bản nháp", "Chưa chạy", "Tạm dừng", "Lỗi"}:
                self.store.update_status(p.id, "Đang chờ")
        self.refresh()
        self._process_next_in_queue()

    def pause_all(self) -> None:
        if self._queue_worker is not None:
            self._queue_worker.stop()
        for p in self._projects:
            if p.status in {"Đang chạy", "Đang chờ"}:
                self.store.update_status(p.id, "Tạm dừng")
        self.refresh()

    # ──────────────────────────────────────────────────────────────────────
    # HÀNG ĐỢI XỬ LÝ (chạy tuần tự, tự động chuyển dự án kế tiếp)
    # ──────────────────────────────────────────────────────────────────────
    def _process_next_in_queue(self) -> None:
        if self._queue_thread is not None:
            return  # đã có 1 dự án đang xử lý qua hàng đợi — chờ xong mới lấy tiếp
        projects = self.store.list_projects()  # đã sắp theo mức ưu tiên
        next_project = next((p for p in projects if p.status == "Đang chờ"), None)
        if next_project is None:
            if self._queue_timer.isActive(): self._queue_timer.stop()
            return
        self._start_queue_job(next_project)

    def _start_queue_job(self, project: Project) -> None:
        paths = [part.strip() for part in (project.input_path or "").split(";")
                 if part.strip() and Path(part.strip()).is_file()]
        if not paths or not project.output_path:
            self.store.update_status(project.id, "Lỗi")
            self.store.add_log(project.id, "ERROR",
                "Hàng đợi: thiếu video nguồn hoặc chưa chọn thư mục đầu ra — mở dự án để thiết lập rồi chạy lại.")
            self.refresh()
            self._process_next_in_queue()
            return

        settings = project.settings.get("split_zoom", {})
        settings = settings if isinstance(settings, dict) else {}
        ai_voice = bool(settings.get("ai_voice", True))

        self.store.update_status(project.id, "Đang chạy", progress=0)
        self._queue_active_project_id = project.id
        self.refresh()
        if not self._queue_timer.isActive(): self._queue_timer.start()

        self._queue_thread = QThread(self)
        self._queue_worker = BatchPipelineWorker(
            paths, str(project_output_root(project)),
            enable_ai_voice=ai_voice, remove_background=ai_voice,
            segment_seconds=float(settings.get("segment_seconds", 180)),
            odd_zoom_percent=int(settings.get("odd_percent", 100)),
            even_zoom_percent=int(settings.get("even_percent", 110)),
            encoder_mode=str(settings.get("encoder_mode", "auto")),
            speed_percent=int(settings.get("speed_percent", 100)),
            effect_name=str(settings.get("effect_name", "")),
            upscale_4k=bool(settings.get("upscale_4k", False)),
        )
        self._queue_worker.moveToThread(self._queue_thread)
        self._queue_thread.started.connect(self._queue_worker.run)
        project_id = project.id
        self._queue_worker.log.connect(lambda message, pid=project_id: self.store.add_log(pid, "INFO", message))
        self._queue_worker.progress.connect(
            lambda percent, pid=project_id: self.store.update_status(pid, "Đang chạy", progress=percent)
        )
        self._queue_worker.finished.connect(lambda ok, message, pid=project_id: self._on_queue_job_finished(pid, ok, message))
        self._queue_worker.finished.connect(self._queue_thread.quit)
        self._queue_thread.finished.connect(self._cleanup_queue_thread)
        self._queue_thread.start()

    def _on_queue_job_finished(self, project_id: int, ok: bool, message: str) -> None:
        if ok:
            self.store.update_status(project_id, "Hoàn thành", progress=100)
            project = self.store.get_project(project_id)
            if project is not None:
                try:
                    copy_path = publish_final_copy(project)
                except OSError as exc:
                    self.store.add_log(project_id, "ERROR", f"Không sao chép được file thành phẩm: {exc}")
                else:
                    if copy_path is not None:
                        self.store.add_log(project_id, "INFO", f"Đã lưu bản thành phẩm: {copy_path}")
        else:
            self.store.update_status(project_id, "Lỗi")
            self.store.add_log(project_id, "ERROR", message)

    def _cleanup_queue_thread(self) -> None:
        self._queue_thread = None
        self._queue_worker = None
        self._queue_active_project_id = None
        self.refresh()
        self._process_next_in_queue()

    def open_output(self) -> None:
        p = self.selected_project()
        if p: self._open_project_folder(p)

    def _open_first_output_folder(self) -> None:
        done = [p for p in self._projects if p.status == "Hoàn thành" and p.output_path]
        if done: self._open_project_folder(done[0])
        else: QMessageBox.information(self, "Chưa có output", "Chưa có dự án nào hoàn thành.")

    def _open_project_folder(self, project: Project) -> None:
        """Mở đúng thư mục kết quả RIÊNG của dự án (nếu đã tạo), không mở thư mục cha dùng chung."""
        project_root = project_output_root(project)
        self._open_folder(str(project_root) if project_root.is_dir() else project.output_path)

    def _open_folder(self, path: str) -> None:
        if not path: QMessageBox.information(self, "Chưa có đầu ra", "Dự án chưa có thư mục đầu ra."); return
        folder = Path(path); folder = folder if folder.is_dir() else folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    # ──────────────────────────────────────────────────────────────────────
    # EDITOR (2-layer navigation)
    # ──────────────────────────────────────────────────────────────────────
    def active_worker_for(self, project_id: int) -> BatchPipelineWorker | None:
        """Worker THẬT đang chạy dự án này qua Hàng đợi (nếu có) — để màn hình dự án gắn vào
        và hiển thị tiến trình thật thay vì trạng thái mặc định 0%."""
        return self._queue_worker if self._queue_active_project_id == project_id else None

    def open_editor(self) -> None:
        p = self.selected_project()
        if p is None: return
        from ui.pipeline_window import PipelineWindow
        panel: QWidget = PipelineWindow(project=p, store=self.store, active_worker=self.active_worker_for(p.id))
        panel.back_requested.connect(self._go_back)  # type: ignore[attr-defined]
        self._show_editor_panel(panel)

    def _show_editor_panel(self, panel: QWidget) -> None:
        cl = self._editor_container.layout()
        while cl.count():
            item = cl.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._current_editor = panel
        cl.addWidget(panel)
        self.main_stack.setCurrentIndex(1)
        p = getattr(panel, "project", None)
        self.setWindowTitle(f"Fast Video Studio — {p.name}" if p else "Fast Video Studio — Chi tiết dự án")

    def _go_back(self) -> None:
        self.main_stack.setCurrentIndex(0)
        self.setWindowTitle("Fast Video Studio — Quản lý dự án")
        cl = self._editor_container.layout()
        while cl.count():
            item = cl.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._current_editor = None
        self.refresh()

    # ──────────────────────────────────────────────────────────────────────
    # SYSTEM RESOURCES
    # ──────────────────────────────────────────────────────────────────────
    def _gpu_percent(self) -> float | None:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=1.5, check=False, **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired): return None
        if result.returncode != 0 or not result.stdout.strip(): return None
        try: return float(result.stdout.strip().splitlines()[0].strip())
        except ValueError: return None

    def _update_resources(self) -> None:
        selected = self.selected_project()
        output = selected.output_path if selected else ""
        target = Path(output).anchor if output else Path.cwd().anchor
        try:
            usage = shutil.disk_usage(target)
            disk_pct = usage.used / usage.total * 100 if usage.total else 0.0
            disk_text = f"Ổ đĩa trống: {usage.free / (1024**3):.1f} GB"
            disk_sub  = f"{usage.used / (1024**3):.1f} / {usage.total / (1024**3):.0f} GB"
        except OSError:
            disk_pct = 0.0; disk_text = "Ổ đĩa: --"; disk_sub = "--"
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        gpu = self._gpu_percent()
        running = sum(p.status == "Đang chạy" for p in self._projects)
        queued  = sum(p.status == "Đang chờ"  for p in self._projects)
        gpu_txt = f"{gpu:.0f}%" if gpu is not None else "--"
        self.resource_label.setText(
            f"CPU: {cpu:.0f}%  |  RAM: {mem.percent:.0f}%  |  GPU: {gpu_txt}  |  "
            f"{disk_text}  |  Hàng đợi: {queued}  |  Đang chạy: {running}"
        )
        self.cpu_card.set_value(cpu, f"{psutil.cpu_count(logical=True) or 0} nhân")
        self.ram_card.set_value(mem.percent, f"{mem.used / (1024**3):.1f} / {mem.total / (1024**3):.0f} GB")
        self.gpu_card.set_value(0 if gpu is None else gpu, "Không phát hiện GPU" if gpu is None else "NVIDIA GPU")
        self.disk_card.set_value(disk_pct, disk_sub)

    # ──────────────────────────────────────────────────────────────────────
    # STYLESHEET  — gộp đủ cả global lẫn dashboard-specific để tránh ghi đè
    # ──────────────────────────────────────────────────────────────────────
    def _apply_style(self) -> None:
        self.setStyleSheet("""
/* ── Global reset ──────────────────────────────────────────────────── */
* { font-family: "Segoe UI"; font-size: 10pt; color: #f3f5f8;
    selection-background-color: #2dd4bf; selection-color: #071113; }

QMainWindow, QWidget#dashboardRoot, QWidget#overviewContent,
QWidget#editorContainer { background: #0b0d12; }

/* ── Input controls ─────────────────────────────────────────────────── */
QListWidget, QPlainTextEdit, QLineEdit, QComboBox {
    background: #0f131c; border: 1px solid #283246;
    border-radius: 8px; color: #eef2f7; padding: 8px; }
QLineEdit { min-height: 20px; }
QComboBox { min-height: 20px; padding: 7px 30px 7px 10px; }
QComboBox:hover { background: #121a27; border-color: #475569; }
QComboBox:disabled { background: #151922; border-color: #202838; color: #586274; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right;
    width: 26px; border-left: 1px solid #283246;
    border-top-right-radius: 8px; border-bottom-right-radius: 8px; background: #111827; }
QComboBox::drop-down:hover { background: #1a2230; }
QComboBox QAbstractItemView { background: #0f131c; border: 1px solid #283246;
    color: #eef2f7; outline: 0; padding: 4px;
    selection-background-color: #2dd4bf; selection-color: #071113; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #2dd4bf; }
QPlainTextEdit { color: #cbd5e1; font-family: "Cascadia Mono","Consolas","Segoe UI";
    font-size: 9.5pt; }

/* ── Buttons — base ─────────────────────────────────────────────────── */
QPushButton { background: #1a2230; border: 1px solid #334155; border-radius: 7px;
    color: #f8fafc; font-weight: 600; min-height: 30px; padding: 7px 14px; }
QPushButton:hover { background: #253247; border-color: #475569; }
QPushButton:pressed { background: #111827; border-color: #2dd4bf; }
QPushButton:disabled { background: #151922; border-color: #202838; color: #586274; }

/* ── Buttons — variants via objectName (giải pháp dùng objectName thay vì property) */
QPushButton#btnPrimary {
    background: #2dd4bf; border-color: #2dd4bf; color: #041011; font-weight: 750; }
QPushButton#btnPrimary:hover { background: #5eead4; border-color: #5eead4; }
QPushButton#btnPrimary:pressed { background: #14b8a6; }
QPushButton#btnPrimary:disabled { background: #1a3d38; border-color: #1a3d38; color: #4d8a82; }

QPushButton#btnQuiet, QPushButton[variant="quiet"] {
    background: transparent; border-color: #334155; color: #cbd5e1; }
QPushButton#btnQuiet:hover, QPushButton[variant="quiet"]:hover { background: #1a2230; }

QPushButton[variant="primary"] {
    background: #2dd4bf; border-color: #2dd4bf; color: #041011; font-weight: 750; }
QPushButton[variant="primary"]:hover { background: #5eead4; border-color: #5eead4; }
QPushButton[variant="danger"]  { background: #2a1720; border-color: #7f1d1d; color: #fecaca; }
QPushButton[variant="danger"]:hover { background: #3a1b26; border-color: #ef4444; }

/* ── Scrollbars ─────────────────────────────────────────────────────── */
QScrollBar:vertical { background: #0f131c; width: 12px; margin: 2px; border-radius: 6px; }
QScrollBar::handle:vertical { background: #334155; border-radius: 6px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: #475569; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* ── Header ─────────────────────────────────────────────────────────── */
QFrame#dashHeader { background: #10131b; border-bottom: 1px solid #202838; }
QLabel#dashLogo { background: #2dd4bf; color: #041011; border-radius: 10px;
    min-width: 44px; min-height: 44px; qproperty-alignment: AlignCenter;
    font-size: 16px; font-weight: 800; }
QLabel#dashTitle { font-size: 16pt; font-weight: 700; color: #ffffff; }
QLabel#pageTitle { font-size: 14pt; font-weight: 700; color: #ffffff; }
QLabel#pageSubtitle { color: #9aa4b2; margin-bottom: 4px; }
QLabel[role="sectionTitle"] { color: #f7f8fb; font-size: 11pt; font-weight: 650; }
QLabel[role="mutedText"] { color: #9aa4b2; }

/* ── Scroll areas ───────────────────────────────────────────────────── */
QScrollArea#contentScroll, QScrollArea#contentScroll > QWidget,
QScrollArea#detailScroll,  QScrollArea#detailScroll > QWidget { background: transparent; border: none; }

/* ── Side nav ───────────────────────────────────────────────────────── */
QFrame#sideNav { background: #0d1017; border-right: 1px solid #202838; min-width: 200px; max-width: 220px; }
QLabel#navCaption { color: #64748b; font-weight: 700; padding: 12px 10px 6px; }
QPushButton#navButton { text-align: left; background: transparent; border: 0; border-radius: 8px; padding: 0; }
QPushButton#navButton:hover, QPushButton#navButton:checked { background: #16202f; }
QLabel#navButtonLabel { color: #9aa4b2; font-weight: 600; background: transparent; }
QPushButton#navButton:hover QLabel#navButtonLabel,
QPushButton#navButton:checked QLabel#navButtonLabel { color: #f3f5f8; }

/* ── Quick stats ────────────────────────────────────────────────────── */
QFrame#quickStats { background: #0d1017; border: 1px solid #202838; border-radius: 8px; margin: 0 10px 12px 10px; }
QLabel#quickStatCount { color: #f3f5f8; font-weight: 800; }

/* ── Stat cards ─────────────────────────────────────────────────────── */
QPushButton#statCard { text-align: left; background: #11151f; border: 1px solid #202838;
    border-radius: 8px; padding: 0; min-height: 58px; }
QPushButton#statCard:hover { border-color: #2dd4bf; }
QLabel#statCount { color: #f3f5f8; font-size: 13pt; font-weight: 800; }
QLabel#statName  { color: #9aa4b2; font-size: 8.5pt; font-weight: 600; }

/* ── Table ──────────────────────────────────────────────────────────── */
QTableWidget { background: #11151f; alternate-background-color: #141a25;
    border: 1px solid #202838; border-radius: 8px; gridline-color: #202838;
    color: #e7ecf3; selection-background-color: #1e3a4a; }
QHeaderView::section { background: #141a25; color: #9aa4b2; border: 0;
    border-bottom: 1px solid #202838; padding: 8px; font-weight: 700; }
QLabel#progressPercent { color: #e7ecf3; font-weight: 700; font-size: 8.5pt; }
QProgressBar#miniProgress { background: #0f131c; border: none; border-radius: 3px; }
QProgressBar#miniProgress::chunk { background: #2dd4bf; border-radius: 3px; }
QProgressBar { background: #0f131c; border: 1px solid #283246; border-radius: 6px;
    text-align: center; min-width: 90px; }
QProgressBar::chunk { background: #2dd4bf; border-radius: 5px; }

/* ── Pagination ─────────────────────────────────────────────────────── */
QPushButton#pageNavButton { padding: 4px; font-weight: 800; min-width: 20px; }
QLabel#pageIndicator { font-weight: 700; color: #f3f5f8; }

/* ── Resource cards ─────────────────────────────────────────────────── */
QFrame#resourceCard, QFrame#pageWorkspace { background: #11151f; border: 1px solid #202838; border-radius: 8px; }
QLabel#resourceTitle { color: #e7ecf3; font-weight: 700; font-size: 9pt; }

/* ── Processing stepper ─────────────────────────────────────────────── */
QLabel#stepCircle { background: #141a25; border: 2px solid #283246; color: #64748b; border-radius: 17px; font-weight: 800; }
QLabel#stepCircle[state="active"] { background: #2dd4bf; border-color: #2dd4bf; color: #041011; }
QLabel#stepCircle[state="done"]   { background: #123b36; border-color: #2dd4bf; color: #2dd4bf; }
QLabel#stepTitle { color: #e7ecf3; font-weight: 700; font-size: 9.5pt; }
QFrame#stepLine { background: #283246; }
QFrame#stepLine[state="done"] { background: #2dd4bf; }

/* ── Detail panel ───────────────────────────────────────────────────── */
QFrame#detailPanel { background: #10131b; }
QLabel#detailName { font-size: 13pt; font-weight: 700; color: #ffffff; padding: 6px 0; }
QPushButton#linkButton { background: transparent; border: none; color: #2dd4bf; font-weight: 700;
    padding: 0; min-height: 0; text-align: left; }
QPushButton#linkButton:hover { color: #5eead4; }
QPushButton#linkButton:checked { color: #9aa4b2; }

/* ── Resource bar ───────────────────────────────────────────────────── */
QLabel#resourceBar { background: #0d1017; border-top: 1px solid #202838; color: #9aa4b2; padding: 8px 18px; }
QPushButton#menuButton { min-width: 40px; padding: 4px; font-weight: 700; }

/* ── Page stack / web pages ─────────────────────────────────────────── */
QStackedWidget#pageStack, QWidget#webPage { background: #0b0d12; }
QFrame#pageCard { background: #11151f; border: 1px solid #202838; border-radius: 8px; min-height: 120px; }
QFrame#pageCard:hover { border-color: #2dd4bf; }
QLabel#cardTitle { color: #f3f5f8; font-size: 11pt; font-weight: 700; }

/* ── Filter button (☰) ──────────────────────────────────────────────── */
QPushButton#btnQuiet { min-width: 20px; padding: 6px 10px; }
        """)
