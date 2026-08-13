from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.project_store import PRIORITIES, PROJECT_STATUSES, TASK_TYPES, Project, ProjectStore

_STATUS_COLORS = {
    "Bản nháp": "#64748b", "Chưa chạy": "#64748b", "Đang chờ": "#eab308",
    "Đang chạy": "#3b82f6", "Tạm dừng": "#f97316", "Hoàn thành": "#22c55e",
    "Lỗi": "#ef4444", "Đã hủy": "#475569",
}

class NewProjectDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tạo dự án mới")
        self.setMinimumWidth(520)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ví dụ: Nối video YouTube tháng 7")
        self.task_combo = QComboBox(); self.task_combo.addItems(TASK_TYPES)
        self.priority_combo = QComboBox(); self.priority_combo.addItems(PRIORITIES); self.priority_combo.setCurrentText("Bình thường")
        self.input_edit = QLineEdit(); self.input_edit.setPlaceholderText("File hoặc thư mục nguồn")
        self.output_edit = QLineEdit(); self.output_edit.setPlaceholderText("Thư mục đầu ra")
        input_button = QPushButton("Chọn..."); input_button.clicked.connect(self._choose_input)
        output_button = QPushButton("Chọn..."); output_button.clicked.connect(self._choose_output)
        input_row = QHBoxLayout(); input_row.addWidget(self.input_edit, 1); input_row.addWidget(input_button)
        output_row = QHBoxLayout(); output_row.addWidget(self.output_edit, 1); output_row.addWidget(output_button)
        form = QFormLayout(); form.addRow("Tên dự án", self.name_edit); form.addRow("Loại tác vụ", self.task_combo)
        form.addRow("Mức ưu tiên", self.priority_combo); form.addRow("Nguồn", input_row); form.addRow("Đầu ra", output_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(buttons)

    def _choose_input(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn video")
        if paths: self.input_edit.setText("; ".join(paths))

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục đầu ra")
        if path: self.output_edit.setText(path)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Thiếu tên", "Hãy nhập tên dự án."); return
        self.accept()

    def values(self) -> dict:
        inputs = [p.strip() for p in self.input_edit.text().split(";") if p.strip()]
        return {"name": self.name_edit.text().strip(), "task_type": self.task_combo.currentText(),
                "priority": self.priority_combo.currentText(), "input_path": self.input_edit.text().strip(),
                "output_path": self.output_edit.text().strip(), "file_count": len(inputs)}

class StatCard(QPushButton):
    def __init__(self, label: str, status: str = "") -> None:
        super().__init__()
        self.status = status
        self.label = label
        self.setObjectName("statCard")
        self.setCursor(Qt.PointingHandCursor)
        self.set_count(0)

    def set_count(self, count: int) -> None:
        self.setText(f"{self.label}\n{count}")

class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fast Video Studio — Quản lý dự án")
        self.resize(1480, 880)
        self.store = ProjectStore()
        self._projects: list[Project] = []
        self._status_filter = ""
        self._editor_windows: list[QMainWindow] = []
        self._build_ui()
        self._connect()
        self.refresh()
        self.resource_timer = QTimer(self); self.resource_timer.setInterval(5000)
        self.resource_timer.timeout.connect(self._update_resources); self.resource_timer.start()
        self._update_resources()

    def _build_ui(self) -> None:
        root_widget = QWidget(); root_widget.setObjectName("dashboardRoot")
        root = QVBoxLayout(root_widget); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        header = QFrame(); header.setObjectName("dashHeader"); header_layout = QHBoxLayout(header)
        brand = QLabel("FV"); brand.setObjectName("dashLogo")
        titles = QVBoxLayout(); title = QLabel("Fast Video Studio"); title.setObjectName("dashTitle")
        subtitle = QLabel("Trung tâm quản lý dự án video"); subtitle.setObjectName("muted")
        titles.addWidget(title); titles.addWidget(subtitle)
        header_layout.addWidget(brand); header_layout.addLayout(titles); header_layout.addStretch(1)
        self.new_button = QPushButton("＋  Tạo dự án mới"); self.new_button.setObjectName("primaryButton")
        header_layout.addWidget(self.new_button); root.addWidget(header)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        nav = QFrame(); nav.setObjectName("sideNav"); nav_layout = QVBoxLayout(nav)
        nav_title = QLabel("MENU"); nav_title.setObjectName("navCaption"); nav_layout.addWidget(nav_title)
        self.nav_buttons: list[QPushButton] = []
        for text in ("▦  Tổng quan", "◫  Dự án", "≡  Hàng đợi", "▤  File đầu ra", "◷  Lịch sử", "⚙  Cài đặt"):
            button = QPushButton(text); button.setObjectName("navButton"); button.setCheckable(True)
            if "Tổng quan" in text: button.setChecked(True)
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
        nav_layout.addStretch(1); body.addWidget(nav)

        content = QWidget(); content_layout = QVBoxLayout(content); content_layout.setContentsMargins(22, 20, 16, 18); content_layout.setSpacing(14)
        heading = QHBoxLayout(); heading_title = QLabel("TỔNG QUAN DỰ ÁN"); heading_title.setObjectName("pageTitle")
        self.run_all_button = QPushButton("Chạy tất cả"); self.pause_all_button = QPushButton("Tạm dừng tất cả")
        heading.addWidget(heading_title); heading.addStretch(1); heading.addWidget(self.run_all_button); heading.addWidget(self.pause_all_button)
        content_layout.addLayout(heading)
        cards = QHBoxLayout(); self.cards: list[StatCard] = []
        for label, status in (("Tất cả", ""), ("Bản nháp", "Bản nháp"), ("Chưa chạy", "Chưa chạy"), ("Đang chờ", "Đang chờ"), ("Đang chạy", "Đang chạy"), ("Tạm dừng", "Tạm dừng"), ("Hoàn thành", "Hoàn thành"), ("Lỗi", "Lỗi"), ("Đã hủy", "Đã hủy")):
            card = StatCard(label, status); cards.addWidget(card, 1); self.cards.append(card)
        content_layout.addLayout(cards)
        filters = QHBoxLayout(); self.search_edit = QLineEdit(); self.search_edit.setPlaceholderText("Tìm kiếm tên, nguồn hoặc đầu ra...")
        self.status_combo = QComboBox(); self.status_combo.addItem("Tất cả trạng thái", "");
        for status in PROJECT_STATUSES: self.status_combo.addItem(status, status)
        self.task_combo = QComboBox(); self.task_combo.addItem("Tất cả tác vụ", "");
        for task in TASK_TYPES: self.task_combo.addItem(task, task)
        self.priority_combo = QComboBox(); self.priority_combo.addItem("Tất cả ưu tiên", "");
        for priority in PRIORITIES: self.priority_combo.addItem(priority, priority)
        filters.addWidget(self.search_edit, 1); filters.addWidget(self.status_combo); filters.addWidget(self.task_combo); filters.addWidget(self.priority_combo)
        content_layout.addLayout(filters)
        self.table = QTableWidget(0, 7); self.table.setHorizontalHeaderLabels(("Dự án", "Tác vụ", "Ưu tiên", "Trạng thái", "Tiến trình", "Ngày tạo", "Thao tác"))
        self.table.setSelectionBehavior(QTableWidget.SelectRows); self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False); self.table.setAlternatingRowColors(True)
        header_view = self.table.horizontalHeader(); header_view.setSectionResizeMode(0, QHeaderView.Stretch); header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for index in range(2, 7): header_view.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        content_layout.addWidget(self.table, 1)

        details = QFrame(); details.setObjectName("detailPanel"); details.setMinimumWidth(300); details.setMaximumWidth(390)
        details_layout = QVBoxLayout(details); detail_title = QLabel("CHI TIẾT DỰ ÁN"); detail_title.setObjectName("sectionTitle")
        self.detail_name = QLabel("Chọn một dự án"); self.detail_name.setObjectName("detailName"); self.detail_name.setWordWrap(True)
        self.detail_meta = QLabel("Thông tin dự án sẽ hiển thị tại đây."); self.detail_meta.setObjectName("muted"); self.detail_meta.setWordWrap(True)
        self.detail_progress = QProgressBar(); self.detail_progress.setRange(0, 100)
        self.detail_log = QPlainTextEdit(); self.detail_log.setReadOnly(True); self.detail_log.setPlaceholderText("Nhật ký riêng của dự án")
        detail_actions = QHBoxLayout(); self.detail_run = QPushButton("Chạy"); self.detail_pause = QPushButton("Tạm dừng"); self.detail_open = QPushButton("Mở thư mục")
        detail_actions.addWidget(self.detail_run); detail_actions.addWidget(self.detail_pause)
        details_layout.addWidget(detail_title); details_layout.addWidget(self.detail_name); details_layout.addWidget(self.detail_meta)
        details_layout.addWidget(self.detail_progress); details_layout.addLayout(detail_actions); details_layout.addWidget(self.detail_open); details_layout.addWidget(QLabel("NHẬT KÝ")); details_layout.addWidget(self.detail_log, 1)
        overview_page = QWidget()
        overview_layout = QHBoxLayout(overview_page)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(0)
        overview_layout.addWidget(content, 1)
        overview_layout.addWidget(details)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pageStack")
        self.page_stack.addWidget(overview_page)
        self.page_stack.addWidget(self._build_web_page(
            "DỰ ÁN", "Tổ chức và cấu hình toàn bộ dự án xử lý video.",
            (("Tạo dự án", "Chọn tác vụ, video nguồn, thư mục đầu ra và mức ưu tiên."),
             ("Chỉnh sửa cấu hình", "Mở Giao diện số 2 theo đúng loại tác vụ của dự án."),
             ("Quản lý phiên bản", "Nhân bản dự án để thử cấu hình mới mà không ảnh hưởng bản gốc.")),
        ))
        self.page_stack.addWidget(self._build_web_page(
            "HÀNG ĐỢI", "Điều phối các tác vụ FFmpeg theo thứ tự và mức ưu tiên.",
            (("Đang chờ", "Các dự án sẵn sàng được chạy tiếp theo."),
             ("Đang xử lý", "Theo dõi tác vụ đang sử dụng FFmpeg hoặc AI."),
             ("Điều khiển chung", "Chạy, tạm dừng hoặc tiếp tục toàn bộ hàng đợi.")),
        ))
        self.page_stack.addWidget(self._build_web_page(
            "FILE ĐẦU RA", "Theo dõi kết quả đã xuất và dung lượng lưu trữ.",
            (("Video hoàn thành", "Mở nhanh thư mục chứa các video đã xử lý."),
             ("Dung lượng ổ đĩa", "Cảnh báo khi thư mục đầu ra không còn đủ dung lượng."),
             ("Định dạng", "Phân loại kết quả MP4, MKV, MOV và các tệp âm thanh.")),
        ))
        self.page_stack.addWidget(self._build_web_page(
            "LỊCH SỬ XỬ LÝ", "Tra cứu lần chạy, thông báo và lỗi theo từng dự án.",
            (("Nhật ký dự án", "Mỗi dự án có log riêng và thời điểm thay đổi trạng thái."),
             ("Tác vụ thành công", "Xem lại các dự án đã hoàn thành."),
             ("Lỗi cần xử lý", "Tìm nhanh dự án lỗi để chỉnh sửa và chạy lại.")),
        ))
        self.page_stack.addWidget(self._build_web_page(
            "CÀI ĐẶT", "Thiết lập hiệu năng, FFmpeg và hành vi hàng đợi.",
            (("Chạy đồng thời", "Giới hạn số dự án được xử lý cùng lúc."),
             ("Tăng tốc phần cứng", "Cấu hình GPU và codec phù hợp với máy."),
             ("Tự động tiếp tục", "Khôi phục các tác vụ chưa hoàn thành sau khi mở lại tool.")),
        ))
        body.addWidget(self.page_stack, 1)
        root.addLayout(body, 1)
        self.resource_label = QLabel(); self.resource_label.setObjectName("resourceBar"); root.addWidget(self.resource_label)
        self.setCentralWidget(root_widget); self._apply_style()

    def _build_web_page(self, title: str, subtitle: str, sections: tuple[tuple[str, str], ...]) -> QWidget:
        page = QWidget()
        page.setObjectName("webPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        description = QLabel(subtitle)
        description.setObjectName("pageSubtitle")
        layout.addWidget(heading)
        layout.addWidget(description)
        cards = QHBoxLayout()
        cards.setSpacing(14)
        for section_title, section_text in sections:
            card = QFrame()
            card.setObjectName("pageCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 18, 18, 18)
            card_title = QLabel(section_title)
            card_title.setObjectName("cardTitle")
            card_text = QLabel(section_text)
            card_text.setObjectName("muted")
            card_text.setWordWrap(True)
            card_layout.addWidget(card_title)
            card_layout.addWidget(card_text)
            card_layout.addStretch(1)
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        workspace = QFrame()
        workspace.setObjectName("pageWorkspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(22, 22, 22, 22)
        workspace_title = QLabel("Không gian làm việc")
        workspace_title.setObjectName("sectionTitle")
        workspace_hint = QLabel("Dữ liệu của trang này được đồng bộ từ danh sách dự án và hàng đợi SQLite.")
        workspace_hint.setObjectName("muted")
        workspace_layout.addWidget(workspace_title)
        workspace_layout.addWidget(workspace_hint)
        workspace_layout.addStretch(1)
        layout.addWidget(workspace, 1)
        return page

    def _switch_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
    def _connect(self) -> None:
        self.new_button.clicked.connect(self.create_project)
        for index, button in enumerate(self.nav_buttons):
            button.clicked.connect(lambda _checked=False, page=index: self._switch_page(page))
        self.search_edit.textChanged.connect(self.apply_filters); self.status_combo.currentIndexChanged.connect(self.apply_filters)
        self.task_combo.currentIndexChanged.connect(self.apply_filters); self.priority_combo.currentIndexChanged.connect(self.apply_filters)
        self.table.itemSelectionChanged.connect(self.show_selected_details); self.table.cellDoubleClicked.connect(lambda *_: self.open_editor())
        for card in self.cards: card.clicked.connect(lambda _checked=False, c=card: self.filter_card(c.status))
        self.run_all_button.clicked.connect(self.run_all); self.pause_all_button.clicked.connect(self.pause_all)
        self.detail_run.clicked.connect(self.run_selected); self.detail_pause.clicked.connect(self.pause_selected); self.detail_open.clicked.connect(self.open_output)

    def refresh(self) -> None:
        self._projects = self.store.list_projects(); counts = {status: 0 for status in PROJECT_STATUSES}
        for project in self._projects: counts[project.status] = counts.get(project.status, 0) + 1
        for card in self.cards: card.set_count(len(self._projects) if not card.status else counts.get(card.status, 0))
        self.apply_filters()

    def apply_filters(self) -> None:
        query = self.search_edit.text().strip().casefold(); status = self._status_filter or self.status_combo.currentData(); task = self.task_combo.currentData(); priority = self.priority_combo.currentData()
        projects = [p for p in self._projects if (not query or query in f"{p.name} {p.input_path} {p.output_path}".casefold()) and (not status or p.status == status) and (not task or p.task_type == task) and (not priority or p.priority == priority)]
        self.table.setRowCount(0)
        for project in projects: self._add_row(project)
        if projects: self.table.selectRow(0)
        else: self.clear_details()

    def _add_row(self, project: Project) -> None:
        row = self.table.rowCount(); self.table.insertRow(row)
        name_item = QTableWidgetItem(f"▣  {project.name}\n{project.file_count} file • {project.output_path or 'Chưa chọn đầu ra'}"); name_item.setData(Qt.UserRole, project.id)
        self.table.setItem(row, 0, name_item); self.table.setItem(row, 1, QTableWidgetItem(project.task_type)); self.table.setItem(row, 2, QTableWidgetItem(project.priority))
        status_item = QTableWidgetItem(f"●  {project.status}"); status_item.setForeground(QColor(_STATUS_COLORS.get(project.status, "#94a3b8"))); self.table.setItem(row, 3, status_item)
        progress = QProgressBar(); progress.setRange(0, 100); progress.setValue(project.progress); progress.setFormat("%p%")
        self.table.setCellWidget(row, 4, progress); self.table.setItem(row, 5, QTableWidgetItem(project.created_at.replace("T", " ")))
        menu_button = QPushButton("•••"); menu_button.setObjectName("menuButton"); menu_button.clicked.connect(lambda _=False, p=project, b=menu_button: self.project_menu(p, b))
        self.table.setCellWidget(row, 6, menu_button); self.table.setRowHeight(row, 58)

    def selected_project(self) -> Project | None:
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0): return None
        return self.store.get_project(int(self.table.item(row, 0).data(Qt.UserRole)))

    def show_selected_details(self) -> None:
        p = self.selected_project()
        if p is None: self.clear_details(); return
        self.detail_name.setText(p.name); self.detail_progress.setValue(p.progress)
        self.detail_meta.setText(f"Tác vụ: {p.task_type}\nTrạng thái: {p.status}\nƯu tiên: {p.priority}\nSố video: {p.file_count}\n\nNguồn: {p.input_path or 'Chưa chọn'}\nĐầu ra: {p.output_path or 'Chưa chọn'}\n\nNgày tạo: {p.created_at.replace('T', ' ')}\nLần chạy gần nhất: {p.last_run_at.replace('T', ' ') or 'Chưa chạy'}" + (f"\n\nLỗi: {p.error_message}" if p.error_message else ""))
        lines = [f"{r['created_at'][11:]}  [{r['level']}] {r['message']}" for r in self.store.project_logs(p.id)]
        self.detail_log.setPlainText("\n".join(lines))

    def clear_details(self) -> None:
        self.detail_name.setText("Không có dự án"); self.detail_meta.setText("Hãy tạo dự án mới hoặc thay đổi bộ lọc."); self.detail_progress.setValue(0); self.detail_log.clear()

    def filter_card(self, status: str) -> None:
        self._status_filter = status; self.status_combo.setCurrentIndex(0); self.apply_filters()

    def create_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.Accepted: return
        project_id = self.store.create_project(**dialog.values()); self.refresh(); self.select_project(project_id); self.open_editor()

    def select_project(self, project_id: int) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.UserRole) == project_id: self.table.selectRow(row); break

    def project_menu(self, project: Project, button: QPushButton) -> None:
        menu = QMenu(self); run = menu.addAction("Chạy / Tiếp tục"); edit = menu.addAction("Chỉnh sửa"); duplicate = menu.addAction("Nhân bản"); open_folder = menu.addAction("Mở thư mục đầu ra"); menu.addSeparator(); delete = menu.addAction("Xóa dự án")
        action = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if action == run: self.store.update_status(project.id, "Đang chờ"); self.refresh()
        elif action == edit: self.select_project(project.id); self.open_editor()
        elif action == duplicate: self.store.duplicate_project(project.id); self.refresh()
        elif action == open_folder: self._open_folder(project.output_path)
        elif action == delete and QMessageBox.question(self, "Xóa dự án", f"Xóa dự án “{project.name}”?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes: self.store.delete_project(project.id); self.refresh()

    def open_editor(self) -> None:
        from ui.main_window import MainWindow
        p = self.selected_project(); window = MainWindow()
        if p:
            window.configure_project(
                task_type=p.task_type,
                project_name=p.name,
                input_path=p.input_path,
                output_path=p.output_path,
            )
        window.resize(1400, 900); window.show(); self._editor_windows.append(window)
        window.destroyed.connect(lambda: self._editor_windows.remove(window) if window in self._editor_windows else None)

    def run_selected(self) -> None:
        p = self.selected_project()
        if p: self.store.update_status(p.id, "Đang chờ"); self.refresh(); self.select_project(p.id)

    def pause_selected(self) -> None:
        p = self.selected_project()
        if p: self.store.update_status(p.id, "Tạm dừng"); self.refresh(); self.select_project(p.id)

    def run_all(self) -> None:
        for p in self._projects:
            if p.status in {"Bản nháp", "Chưa chạy", "Tạm dừng", "Lỗi"}: self.store.update_status(p.id, "Đang chờ")
        self.refresh()

    def pause_all(self) -> None:
        for p in self._projects:
            if p.status in {"Đang chạy", "Đang chờ"}: self.store.update_status(p.id, "Tạm dừng")
        self.refresh()

    def open_output(self) -> None:
        p = self.selected_project()
        if p: self._open_folder(p.output_path)

    def _open_folder(self, path: str) -> None:
        if not path: QMessageBox.information(self, "Chưa có đầu ra", "Dự án chưa có thư mục đầu ra."); return
        folder = Path(path); folder = folder if folder.is_dir() else folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _update_resources(self) -> None:
        output = self.selected_project().output_path if self.selected_project() else ""
        target = Path(output).anchor if output else Path.cwd().anchor
        try: free = shutil.disk_usage(target).free / (1024 ** 3); disk = f"Ổ đĩa trống: {free:.1f} GB"
        except OSError: disk = "Ổ đĩa: --"
        running = sum(p.status == "Đang chạy" for p in self._projects); queued = sum(p.status == "Đang chờ" for p in self._projects)
        self.resource_label.setText(f"CPU: hệ thống  |  RAM: hệ thống  |  GPU: tự động  |  {disk}  |  Hàng đợi: {queued}  |  Đang chạy: {running}")

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget#dashboardRoot { background:#09111f; color:#e7edf7; font-family:'Segoe UI'; font-size:10pt; }
            QFrame#dashHeader { background:#101b2b; border-bottom:1px solid #263a54; }
            QLabel#dashLogo { background:#2f6fed; color:white; border-radius:10px; min-width:46px; min-height:46px; qproperty-alignment:AlignCenter; font-size:17px; font-weight:900; }
            QLabel#dashTitle { font-size:18pt; font-weight:800; color:white; } QLabel#muted { color:#8291a8; }
            QLabel#pageTitle { font-size:16pt; font-weight:800; color:white; } QLabel#sectionTitle { color:#8db8ff; font-weight:800; }
            QFrame#sideNav { background:#0c1625; border-right:1px solid #263a54; min-width:190px; max-width:220px; }
            QLabel#navCaption { color:#64748b; font-weight:800; padding:12px 8px 5px; }
            QPushButton#navButton { text-align:left; background:transparent; border:0; border-radius:7px; padding:10px 12px; color:#9dacbf; }
            QPushButton#navButton:hover, QPushButton#navButton:checked { background:#17345e; color:white; }
            QPushButton#primaryButton { background:#2f6fed; border:1px solid #4d88f5; color:white; font-weight:800; padding:9px 16px; border-radius:8px; }
            QPushButton#statCard { text-align:left; background:#111d2f; border:1px solid #293d58; border-radius:10px; padding:10px 13px; color:#dce7f5; font-weight:700; min-height:54px; }
            QPushButton#statCard:hover { border-color:#4d88f5; background:#162945; }
            QLineEdit,QComboBox,QPlainTextEdit { background:#0b1626; border:1px solid #2b3e58; color:#e7edf7; border-radius:7px; padding:7px; }
            QPushButton { background:#18263a; border:1px solid #30435e; color:#e7edf7; border-radius:7px; padding:7px 11px; }
            QPushButton:hover { background:#213754; border-color:#4d88f5; }
            QTableWidget { background:#0c1625; alternate-background-color:#101d2f; border:1px solid #263a54; border-radius:9px; gridline-color:#1e3048; color:#dce5f2; selection-background-color:#17345e; }
            QHeaderView::section { background:#142238; color:#8fa2ba; border:0; border-bottom:1px solid #30435e; padding:9px; font-weight:800; }
            QProgressBar { background:#091321; border:1px solid #2b3d57; border-radius:6px; text-align:center; min-width:90px; }
            QProgressBar::chunk { background:#2f6fed; border-radius:5px; }
            QFrame#detailPanel { background:#101b2b; border-left:1px solid #263a54; }
            QLabel#detailName { font-size:14pt; font-weight:800; color:white; padding:8px 0; }
            QLabel#resourceBar { background:#0c1625; border-top:1px solid #263a54; color:#8fa2ba; padding:8px 18px; }
            QPushButton#menuButton { min-width:42px; padding:4px; font-weight:900; }
            QStackedWidget#pageStack, QWidget#webPage { background:#09111f; }
            QLabel#pageSubtitle { color:#8291a8; font-size:11pt; margin-bottom:6px; }
            QFrame#pageCard { background:#111d2f; border:1px solid #293d58; border-radius:12px; min-height:125px; }
            QFrame#pageCard:hover { border-color:#4d88f5; }
            QLabel#cardTitle { color:#f0f6ff; font-size:12pt; font-weight:800; }
            QFrame#pageWorkspace { background:#0c1625; border:1px solid #263a54; border-radius:12px; }
        """)