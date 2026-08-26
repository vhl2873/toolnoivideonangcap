from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from core.batch_pipeline import list_effects
from core.ffmpeg_tools import check_required_tools
from core.project_store import Project, ProjectStore
from ui.editor_common import (
    ComparisonPreviewPane, WorkflowBar, app_logo_pixmap, base_editor_stylesheet, expected_final_path,
    project_output_root, publish_final_copy,
)
from workers.batch_pipeline_worker import BatchPipelineWorker

_WORKFLOW_STEPS = ("Thêm video", "Tách giọng/nhạc nền", "Cắt + Zoom so le", "Ghép final.mp4", "Hoàn thành")
_STEP_INDEX = {
    "Chuẩn bị video": 2,
    "Tách voice / chuẩn bị audio": 2,
    "Cắt đoạn video không audio": 3,
    "Zoom xen kẽ từng đoạn": 3,
    "Ghép final.mp4": 4,
    "Kiểm tra final.mp4": 4,
    "Hoàn thành video": 5,
}
_LIVE_STATUS_LABELS = {
    "Đang chạy": "Đang xử lý",
    "Hoàn thành": "Hoàn thành",
    "Lỗi": "Lỗi / đã dừng",
    "Tạm dừng": "Tạm dừng",
    "Đang chờ": "Đang chờ trong hàng đợi",
}


class PipelineWindow(QWidget):
    """Màn hình duy nhất cho mọi dự án: pipeline tự động Tách giọng/nhạc nền -> Cắt đoạn -> Zoom so le -> final.mp4,
    giống hệt luồng batch của bản web (core/batch_pipeline.py). Được nhúng vào DashboardWindow."""

    back_requested = Signal()

    def __init__(self, *, project: Project, store: ProjectStore,
                 active_worker: BatchPipelineWorker | None = None) -> None:
        super().__init__()
        self.store = store
        self.project = store.get_project(project.id) or project
        self._paths: list[str] = self._resolve_source_paths()
        self._worker_thread: QThread | None = None
        self._worker: BatchPipelineWorker | None = None
        self._owns_worker: bool = False

        self._build_ui()
        self._connect()
        self._refresh_preview()
        self._apply_ui_values(self.project.settings.get("split_zoom", {}))
        self._apply_live_status()
        if active_worker is not None:
            self._attach_worker(active_worker)

    def _apply_live_status(self) -> None:
        """Đồng bộ hiển thị (progress/step) với trạng thái thật của dự án trong DB —
        tránh việc mở lại 1 dự án đang chạy (qua Hàng đợi ở Tổng quan) mà vẫn thấy 0%/Chờ xử lý."""
        status = self.project.status
        progress = int(self.project.progress or 0)
        if status in {"Đang chạy", "Hoàn thành"}:
            self.progress_bar.setValue(progress)
        self.status_value_label.setText(f"Trạng thái:  {_LIVE_STATUS_LABELS.get(status, 'Chờ xử lý')}")
        if status == "Hoàn thành":
            self.workflow_bar.set_active_step(5)
        elif status == "Đang chạy":
            self.workflow_bar.set_active_step(2 if progress < 60 else 3 if progress < 90 else 4)
        else:
            self.workflow_bar.set_active_step(1 if not self._paths else 2)

    def _attach_worker(self, worker: BatchPipelineWorker) -> None:
        """Gắn vào worker ĐANG chạy thật (do Hàng đợi ở Tổng quan sở hữu) để nhận cập nhật
        tiến trình/log/log trực tiếp, thay vì hiển thị trạng thái giả lập 0% khi mở dự án."""
        self._worker = worker
        self._owns_worker = False
        worker.log.connect(self._append_log)
        worker.progress.connect(self._on_worker_progress)
        worker.status.connect(self._on_worker_status)
        worker.finished.connect(self._on_worker_finished)
        self.start_button.setText("⏹  DỪNG XỬ LÝ")
        self.start_button.setObjectName("stopButton")
        self.start_button.style().unpolish(self.start_button); self.start_button.style().polish(self.start_button)

    def _resolve_source_paths(self) -> list[str]:
        return [p.strip() for p in (self.project.input_path or "").split(";")
                if p.strip() and Path(p.strip()).is_file()]

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame(); header.setObjectName("editorHeader")
        header_layout = QHBoxLayout(header); header_layout.setContentsMargins(20, 14, 20, 14)
        self.back_button = QPushButton("← Quay lại"); self.back_button.setObjectName("backButton")
        self.back_button.setProperty("variant", "quiet")
        header_layout.addWidget(self.back_button)
        brand = QLabel(); brand.setObjectName("dashLogo"); brand.setPixmap(app_logo_pixmap())
        titles = QVBoxLayout()
        title = QLabel("Fast Video Studio"); title.setObjectName("dashTitle")
        subtitle = QLabel(f"{self.project.name}  —  Tách giọng + Cắt + Zoom so le + Nối final.mp4")
        subtitle.setProperty("role", "mutedText")
        titles.addWidget(title); titles.addWidget(subtitle)
        header_layout.addWidget(brand); header_layout.addLayout(titles); header_layout.addStretch(1)
        engine_badge = QLabel("●  FFmpeg Engine"); engine_badge.setObjectName("engineBadge")
        self.refresh_button = QPushButton("Làm mới"); self.refresh_button.setProperty("variant", "quiet")
        header_layout.addWidget(engine_badge); header_layout.addWidget(self.refresh_button)
        root_layout.addWidget(header)

        self.workflow_bar = WorkflowBar(_WORKFLOW_STEPS)
        root_layout.addWidget(self.workflow_bar)

        info_bar = QFrame(); info_bar.setObjectName("infoBar")
        info_layout = QHBoxLayout(info_bar); info_layout.setContentsMargins(20, 10, 20, 10); info_layout.setSpacing(18)
        self.project_name_label = QLabel(f"Dự án: {self.project.name}  ✎")
        self.project_name_label.setObjectName("infoItem"); self.project_name_label.setCursor(Qt.PointingHandCursor)
        self.output_label = QLabel(f"Thư mục: {self._short_output_path()}  📁")
        self.output_label.setObjectName("infoItem"); self.output_label.setCursor(Qt.PointingHandCursor)
        self.video_progress_label = QLabel(""); self.video_progress_label.setProperty("role", "mutedText")
        info_layout.addWidget(self.project_name_label); info_layout.addWidget(self.output_label)
        info_layout.addWidget(self.video_progress_label); info_layout.addStretch(1)
        self.settings_button = QPushButton("⚙  Cài đặt dự án"); self.settings_button.setProperty("variant", "quiet")
        info_layout.addWidget(self.settings_button)
        root_layout.addWidget(info_bar)

        body = QWidget(); body.setObjectName("editorBody")
        body_layout = QHBoxLayout(body); body_layout.setContentsMargins(20, 16, 20, 16); body_layout.setSpacing(14)
        self.preview_pane = ComparisonPreviewPane()
        self.settings_panel = self._build_settings_panel()
        body_layout.addWidget(self.preview_pane, 1)
        body_layout.addWidget(self.settings_panel)
        root_layout.addWidget(body, 1)

        bottom = QFrame(); bottom.setObjectName("progressBarFrame")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(20, 12, 20, 12); bottom_layout.setSpacing(14)
        progress_box = QVBoxLayout()
        progress_caption = QLabel("THANH TIẾN TRÌNH"); progress_caption.setObjectName("progressCaption")
        status_row = QHBoxLayout()
        self.status_value_label = QLabel("Trạng thái:  Chờ xử lý"); self.status_value_label.setProperty("role", "mutedText")
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        status_row.addWidget(self.status_value_label); status_row.addWidget(self.progress_bar, 1)
        progress_box.addWidget(progress_caption); progress_box.addLayout(status_row)
        bottom_layout.addLayout(progress_box, 1)
        self.log_button = QPushButton("Xem nhật ký"); self.log_button.setProperty("variant", "quiet"); self.log_button.setCheckable(True)
        self.open_output_button = QPushButton("Mở thư mục output"); self.open_output_button.setProperty("variant", "quiet")
        self.start_button = QPushButton("BẮT ĐẦU XỬ LÝ"); self.start_button.setObjectName("startButton")
        bottom_layout.addWidget(self.log_button); bottom_layout.addWidget(self.open_output_button); bottom_layout.addWidget(self.start_button)
        root_layout.addWidget(bottom)

        self.log_panel = QPlainTextEdit(); self.log_panel.setObjectName("logPanel")
        self.log_panel.setReadOnly(True); self.log_panel.setMaximumHeight(160); self.log_panel.setVisible(False)
        root_layout.addWidget(self.log_panel)

        self.setStyleSheet(base_editor_stylesheet() + """
            QPushButton#backButton {
                color: #9aa4b2; font-weight: 700; border: 1px solid #334155;
                border-radius: 7px; padding: 6px 14px; margin-right: 8px;
            }
            QPushButton#backButton:hover { color: #f3f5f8; border-color: #2dd4bf; }
        """)

    def _build_settings_panel(self) -> QFrame:
        panel = QFrame(); panel.setObjectName("settingsPanel"); panel.setFixedWidth(320)
        layout = QVBoxLayout(panel); layout.setContentsMargins(18, 16, 18, 16); layout.setSpacing(10)
        title = QLabel("THIẾT LẬP XỬ LÝ"); title.setObjectName("panelTitle")
        layout.addWidget(title)
        hint = QLabel("Mỗi video sẽ tự động: tách giọng/bỏ nhạc nền → cắt thành đoạn → zoom so le → nối lại thành final.mp4.")
        hint.setProperty("role", "mutedText"); hint.setWordWrap(True)
        layout.addWidget(hint)

        self.ai_voice_check = QCheckBox("Tách giọng, bỏ nhạc nền bằng AI (Demucs)")
        self.ai_voice_check.setObjectName("settingsCheck")
        self.ai_voice_check.setChecked(True)
        layout.addWidget(self.ai_voice_check)

        layout.addWidget(self._caption("Thời lượng mỗi đoạn"))
        self.duration_combo = QComboBox()
        for minute in range(1, 11): self.duration_combo.addItem(f"{minute} phút", minute * 60)
        self.duration_combo.setCurrentIndex(2)
        layout.addWidget(self.duration_combo)
        layout.addWidget(self._caption("Khoảng cho phép: 1 – 10 phút"))

        layout.addWidget(self._caption("Zoom đoạn lẻ"))
        self.odd_combo = QComboBox(); layout.addWidget(self.odd_combo)
        layout.addWidget(self._caption("Zoom đoạn chẵn"))
        self.even_combo = QComboBox(); layout.addWidget(self.even_combo)
        for percent in (90, 95, 100, 105, 110, 115, 120, 130):
            self.odd_combo.addItem(f"{percent}%", percent)
            self.even_combo.addItem(f"{percent}%", percent)
        self.odd_combo.setCurrentIndex(2); self.even_combo.setCurrentIndex(4)

        layout.addWidget(self._caption("Bộ mã hóa"))
        self.encoder_combo = QComboBox()
        self.encoder_combo.addItem("Tự động (ưu tiên GPU NVIDIA)", "auto")
        self.encoder_combo.addItem("GPU NVIDIA (NVENC)", "nvidia")
        self.encoder_combo.addItem("CPU", "cpu")
        layout.addWidget(self.encoder_combo)

        layout.addWidget(self._caption("Tốc độ video"))
        self.speed_combo = QComboBox()
        for percent in range(90, 111):
            self.speed_combo.addItem(f"{percent}%", percent)
        self.speed_combo.setCurrentIndex(10)
        layout.addWidget(self.speed_combo)

        layout.addWidget(self._caption("Hiệu ứng lớp phủ (tạo/quản lý ở tab Hiệu ứng)"))
        self.effect_combo = QComboBox()
        layout.addWidget(self.effect_combo)
        self._refresh_effects_combo()

        self.upscale_4k_check = QCheckBox("Render 4K (3840x2160)")
        self.upscale_4k_check.setObjectName("settingsCheck")
        layout.addWidget(self.upscale_4k_check)

        layout.addStretch(1)
        self.apply_button = QPushButton("Lưu thiết lập"); self.apply_button.setObjectName("linkButton")
        layout.addWidget(self.apply_button)
        return panel

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text); label.setProperty("role", "mutedText")
        return label

    def _refresh_effects_combo(self, *, select: str | None = None) -> None:
        target = select if select is not None else self.effect_combo.currentData()
        self.effect_combo.blockSignals(True)
        self.effect_combo.clear()
        self.effect_combo.addItem("Không dùng", "")
        for preset in list_effects():
            self.effect_combo.addItem(preset.name, preset.name)
        index = self.effect_combo.findData(target) if target else 0
        self.effect_combo.setCurrentIndex(index if index >= 0 else 0)
        self.effect_combo.blockSignals(False)

    # ------------------------------------------------------------- wiring

    def _connect(self) -> None:
        self.back_button.clicked.connect(self._on_back)
        self.refresh_button.clicked.connect(self._refresh_preview)
        self.settings_button.clicked.connect(self._open_project_settings)
        self.project_name_label.mousePressEvent = lambda _e: self._open_project_settings()
        self.output_label.mousePressEvent = lambda _e: self._open_output_folder()
        self.preview_pane.choose_requested.connect(self._choose_video)
        self.log_button.toggled.connect(self.log_panel.setVisible)
        self.open_output_button.clicked.connect(self._open_output_folder)
        self.apply_button.clicked.connect(self._persist_settings)
        self.start_button.clicked.connect(self._on_start_button)

    def _on_back(self) -> None:
        if self._worker is not None:
            if self._owns_worker:
                self._worker.stop()
            else:
                # Worker thuộc Hàng đợi ở Tổng quan (không phải cửa sổ này khởi chạy) —
                # chỉ gỡ kết nối, KHÔNG được dừng job đang chạy thật ở nền.
                for signal, slot in (
                    (self._worker.log, self._append_log),
                    (self._worker.progress, self._on_worker_progress),
                    (self._worker.status, self._on_worker_status),
                    (self._worker.finished, self._on_worker_finished),
                ):
                    try:
                        signal.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
        try:
            self.preview_pane.clear()
        except Exception:
            pass
        self.back_requested.emit()

    def _choose_video(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Chọn video nguồn")
        if not path:
            return
        self._paths = [path]
        self.store.update_fields(self.project.id, input_path=path)
        self.workflow_bar.set_active_step(2)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if not self._paths:
            self.preview_pane.clear()
            return
        path = self._paths[0]
        self.preview_pane.show_original(path)
        final_path = expected_final_path(self.project, path)
        self.preview_pane.show_result(str(final_path) if final_path.is_file() else None)

    def _short_output_path(self) -> str:
        return self.project.output_path or "Chưa chọn"

    def _open_project_settings(self) -> None:
        dialog = QDialog(self); dialog.setWindowTitle("Cài đặt dự án"); dialog.setMinimumWidth(440)
        name_edit = QLineEdit(self.project.name)
        output_edit = QLineEdit(self.project.output_path)
        output_button = QPushButton("Chọn...")
        output_button.clicked.connect(
            lambda: output_edit.setText(QFileDialog.getExistingDirectory(dialog, "Chọn thư mục đầu ra") or output_edit.text())
        )
        output_row = QHBoxLayout(); output_row.addWidget(output_edit, 1); output_row.addWidget(output_button)
        form = QFormLayout(); form.addRow("Tên dự án", name_edit); form.addRow("Thư mục đầu ra", output_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog); layout.addLayout(form); layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        new_name = name_edit.text().strip() or self.project.name
        self.store.update_fields(self.project.id, name=new_name, output_path=output_edit.text().strip())
        self.project = self.store.get_project(self.project.id) or self.project
        self.project_name_label.setText(f"Dự án: {self.project.name}  ✎")
        self.output_label.setText(f"Thư mục: {self._short_output_path()}  📁")

    def _open_output_folder(self) -> None:
        if not self.project.output_path:
            QMessageBox.information(self, "Chưa có đầu ra", "Dự án chưa có thư mục đầu ra."); return
        project_folder = project_output_root(self.project)
        folder = project_folder if project_folder.is_dir() else Path(self.project.output_path)
        folder = folder if folder.is_dir() else folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _append_log(self, message: str) -> None:
        self.log_panel.appendPlainText(message)

    # ------------------------------------------------------ lưu/khôi phục thiết lập

    def _current_ui_values(self) -> dict:
        return dict(
            ai_voice=self.ai_voice_check.isChecked(),
            segment_seconds=self.duration_combo.currentData(),
            odd_percent=self.odd_combo.currentData(),
            even_percent=self.even_combo.currentData(),
            encoder_mode=self.encoder_combo.currentData(),
            speed_percent=self.speed_combo.currentData(),
            effect_name=self.effect_combo.currentData() or "",
            upscale_4k=self.upscale_4k_check.isChecked(),
        )

    def _apply_ui_values(self, values: dict) -> None:
        if not values:
            return
        self.ai_voice_check.setChecked(bool(values.get("ai_voice", True)))
        self._refresh_effects_combo(select=values.get("effect_name") or "")
        self.upscale_4k_check.setChecked(bool(values.get("upscale_4k", False)))
        speed_index = self.speed_combo.findData(values.get("speed_percent", 100))
        if speed_index >= 0: self.speed_combo.setCurrentIndex(speed_index)
        index = self.duration_combo.findData(values.get("segment_seconds"))
        if index >= 0: self.duration_combo.setCurrentIndex(index)
        self._set_combo_value(self.odd_combo, values.get("odd_percent"))
        self._set_combo_value(self.even_combo, values.get("even_percent"))
        encoder_index = self.encoder_combo.findData(values.get("encoder_mode"))
        if encoder_index >= 0: self.encoder_combo.setCurrentIndex(encoder_index)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0: combo.setCurrentIndex(index)

    def _persist_settings(self) -> None:
        self.store.update_settings(self.project.id, split_zoom=self._current_ui_values())
        self.status_value_label.setText("Trạng thái:  Đã lưu thiết lập")

    # ---------------------------------------------------------- processing

    def _on_start_button(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self.status_value_label.setText("Trạng thái:  Đang dừng...")
            return
        self._start_processing()

    def _start_processing(self) -> None:
        paths = self._paths
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy chọn video nguồn cho dự án."); return
        output_dir = self.project.output_path
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output trong Cài đặt dự án."); return
        latest = self.store.get_project(self.project.id)
        if latest and latest.status == "Đang chạy":
            QMessageBox.information(
                self, "Đang xử lý",
                "Dự án này đang được xử lý (có thể qua Hàng đợi ở Tổng quan). "
                "Hãy chờ xử lý xong hoặc bấm Tạm dừng ở Tổng quan trước khi chạy lại tại đây."
            )
            return
        ok, _tools = check_required_tools()
        if not ok:
            QMessageBox.warning(self, "Thiếu FFmpeg", "Không tìm thấy FFmpeg/FFprobe."); return

        self._persist_settings()
        self.workflow_bar.set_active_step(2)
        self.start_button.setText("⏹  DỪNG XỬ LÝ")
        self.start_button.setObjectName("stopButton")
        self.start_button.style().unpolish(self.start_button); self.start_button.style().polish(self.start_button)
        self.progress_bar.setValue(0)
        self.status_value_label.setText("Trạng thái:  Đang xử lý")
        self.video_progress_label.setText(f"0 / {len(paths)} video")
        self.store.update_status(self.project.id, "Đang chạy", progress=0)

        ai_voice = self.ai_voice_check.isChecked()
        project_root = str(project_output_root(self.project))
        self._worker_thread = QThread(self)
        self._owns_worker = True
        self._worker = BatchPipelineWorker(
            paths, project_root,
            enable_ai_voice=ai_voice,
            remove_background=ai_voice,
            segment_seconds=float(self.duration_combo.currentData() or 180),
            odd_zoom_percent=self.odd_combo.currentData(),
            even_zoom_percent=self.even_combo.currentData(),
            encoder_mode=self.encoder_combo.currentData(),
            speed_percent=self.speed_combo.currentData(),
            effect_name=self.effect_combo.currentData() or "",
            upscale_4k=self.upscale_4k_check.isChecked(),
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log.connect(self._append_log)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.status.connect(self._on_worker_status)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)
        self._worker_thread.start()

    def _on_worker_progress(self, percent: int) -> None:
        self.progress_bar.setValue(percent)
        if self._owns_worker:
            self.store.update_status(self.project.id, "Đang chạy", progress=percent)

    def _on_worker_status(self, changes: dict) -> None:
        step = changes.get("current_step")
        if step and step in _STEP_INDEX:
            self.workflow_bar.set_active_step(_STEP_INDEX[step])
        if step:
            self.status_value_label.setText(f"Trạng thái:  {step}")
        processed = changes.get("processed_videos")
        total = changes.get("total_videos")
        if processed is not None and total:
            self.video_progress_label.setText(f"{processed} / {total} video")
        current_video = changes.get("current_video")
        if current_video:
            self.video_progress_label.setText(
                f"{changes.get('processed_videos', 0)} / {changes.get('total_videos', len(self._paths))} video — {current_video}"
            )

    def _cleanup_worker(self) -> None:
        self._worker_thread = None
        self._worker = None
        self.start_button.setText("BẮT ĐẦU XỬ LÝ")
        self.start_button.setObjectName("startButton")
        self.start_button.style().unpolish(self.start_button); self.start_button.style().polish(self.start_button)

    def _on_worker_finished(self, ok: bool, message: str) -> None:
        self._append_log(message)
        if ok:
            self.progress_bar.setValue(100)
            self.status_value_label.setText("Trạng thái:  Hoàn thành")
            self.workflow_bar.set_active_step(5)
            if self._owns_worker:
                self.store.update_status(self.project.id, "Hoàn thành", progress=100)
        else:
            self.status_value_label.setText("Trạng thái:  Lỗi / đã dừng")
            if self._owns_worker:
                self.store.update_status(self.project.id, "Lỗi")
                self.store.add_log(self.project.id, "ERROR", message)
        if not self._owns_worker:
            # Worker do Hàng đợi sở hữu: cửa sổ này chỉ "lắng nghe", tự dọn tham chiếu khi xong.
            self._worker = None
            self.start_button.setText("BẮT ĐẦU XỬ LÝ")
            self.start_button.setObjectName("startButton")
            self.start_button.style().unpolish(self.start_button); self.start_button.style().polish(self.start_button)
        self.project = self.store.get_project(self.project.id) or self.project
        if ok and self._owns_worker:
            self._publish_final_copy()
        self._refresh_preview()

    def _publish_final_copy(self) -> None:
        try:
            copy_path = publish_final_copy(self.project)
        except OSError as exc:
            self.store.add_log(self.project.id, "ERROR", f"Không sao chép được file thành phẩm: {exc}")
            return
        if copy_path is not None:
            self.store.add_log(self.project.id, "INFO", f"Đã lưu bản thành phẩm: {copy_path}")
