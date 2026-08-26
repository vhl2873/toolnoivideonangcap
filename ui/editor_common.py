from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from core.project_store import Project
from core.video_analyzer import format_duration
from utils.resources import resource_path

"""Widget và style dùng chung cho các màn hình Editor chuyên dụng theo từng loại tác vụ
(ConcatWindow, BatchTaskWindow, SplitZoomWindow) — tránh lặp lại header/workflow/preview/QSS."""

_INVALID_FILENAME_CHARS = '\\/:*?"<>|'


def safe_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _INVALID_FILENAME_CHARS else ch for ch in (name or "").strip())
    return cleaned.strip(" .") or "video"


def project_output_root(project: Project) -> Path:
    """Thư mục gốc RIÊNG cho từng dự án, nằm bên trong output_path đã chọn.

    Pipeline (core/batch_pipeline.py) đặt tên thư mục con theo TÊN FILE NGUỒN, không theo dự án —
    nên nếu 2 dự án khác nhau cùng trỏ tới 1 video nguồn + cùng thư mục đầu ra, chúng sẽ vô tình dùng
    chung 1 thư mục và cơ chế resume sẽ khiến dự án chạy sau bỏ qua luôn, không tạo ra kết quả riêng.
    Thêm 1 cấp thư mục theo "{id}_{tên dự án}" để đảm bảo mỗi dự án luôn có output độc lập.
    """
    base = Path(project.output_path or "")
    return base / f"{project.id}_{safe_filename(project.name)}"


def expected_final_path(project: Project, source_path: str) -> Path:
    """Đường dẫn final.mp4 mà pipeline (core/batch_pipeline.py) tạo ra cho 1 video nguồn của dự án."""
    return project_output_root(project) / safe_filename(Path(source_path).stem) / "final.mp4"


def published_copy_path(project: Project) -> Path:
    """File thành phẩm PHẲNG, đặt tên theo dự án, nằm ngay trong thư mục đầu ra chung — dễ tìm hơn
    final.mp4 nằm sâu trong thư mục con riêng. Xóa dự án chỉ xóa dữ liệu trong DB (project_store.delete_project
    không đụng tới đĩa) nên file này vẫn còn nguyên sau khi xóa."""
    return Path(project.output_path or "") / f"{safe_filename(project.name)}.mp4"


def publish_final_copy(project: Project) -> Path | None:
    """Sau khi xử lý xong, sao chép final.mp4 ra file phẳng '{tên dự án}.mp4' ở thư mục đầu ra chung.
    Trả về đường dẫn bản sao nếu thành công, None nếu chưa có final.mp4 hoặc chưa có thư mục đầu ra."""
    if not project.output_path:
        return None
    paths = [p.strip() for p in (project.input_path or "").split(";") if p.strip()]
    if not paths:
        return None
    source = expected_final_path(project, paths[0])
    if not source.is_file():
        return None
    target = published_copy_path(project)
    if target.resolve() == source.resolve():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def app_logo_pixmap(size: int = 40, radius: int = 10) -> QPixmap:
    """Logo app (assets/app_icon.png), bo góc để dùng trong badge nhỏ ở header — thay cho chữ 'FV' cũ."""
    source = QPixmap(resource_path("assets", "app_icon.png"))
    if source.isNull():
        return source
    scaled = source.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    cropped = scaled.copy(x, y, size, size)
    rounded = QPixmap(size, size)
    rounded.fill(Qt.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return rounded


def _tint(color: str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _icon_chip(glyph: str, color: str, size: int = 36, font_size: int = 15) -> QLabel:
    label = QLabel(glyph)
    label.setAlignment(Qt.AlignCenter)
    label.setFixedSize(size, size)
    # Bo tròn hoàn toàn (circle) nếu size đủ nhỏ, dùng 45% border-radius
    radius = size // 2
    label.setStyleSheet(
        f"background:{_tint(color, 40)}; color:{color}; border-radius:{radius}px; "
        f"font-size:{font_size}px; font-weight:800; border:none;"
    )
    return label


class WorkflowBar(QFrame):
    """Thanh 4-5 bước quy trình xử lý, hiển thị bước đang thực hiện."""

    def __init__(self, steps: tuple[str, ...]) -> None:
        super().__init__()
        self.setObjectName("workflowBar")
        layout = QHBoxLayout(self); layout.setContentsMargins(20, 10, 20, 10); layout.setSpacing(10)
        self._pills: list[QLabel] = []
        for index, name in enumerate(steps, start=1):
            pill = QLabel(f"{index:02d}   {name}"); pill.setObjectName("workflowPill")
            layout.addWidget(pill, 1)
            self._pills.append(pill)
        self.set_active_step(1)

    def set_active_step(self, active: int) -> None:
        active = max(1, min(len(self._pills), active))
        for index, pill in enumerate(self._pills, start=1):
            state = "active" if index == active else "done" if index < active else "pending"
            pill.setProperty("state", state)
            pill.style().unpolish(pill); pill.style().polish(pill)


class VideoPreviewPane(QFrame):
    """Khung xem trước video: player Qt Multimedia + control tối giản."""

    choose_requested = Signal()

    def __init__(self, title: str, *, choosable: bool = False) -> None:
        super().__init__()
        self.setObjectName("previewPane")
        layout = QVBoxLayout(self); layout.setContentsMargins(14, 14, 14, 14); layout.setSpacing(10)

        header = QHBoxLayout()
        title_label = QLabel(title); title_label.setObjectName("previewTitle")
        header.addWidget(title_label)
        if choosable:
            self.choose_button = QPushButton("Chọn video…")
            self.choose_button.setProperty("variant", "quiet")
            self.choose_button.clicked.connect(self.choose_requested.emit)
            header.addWidget(self.choose_button)
        self.badge_label = QLabel(""); self.badge_label.setObjectName("previewBadge")
        header.addStretch(1); header.addWidget(self.badge_label)
        layout.addLayout(header)

        self.video_widget = QVideoWidget(); self.video_widget.setObjectName("previewVideo")
        self.video_widget.setMinimumHeight(240)
        layout.addWidget(self.video_widget, 1)

        self.note_label = QLabel(""); self.note_label.setObjectName("previewNote")
        self.note_label.setWordWrap(True); self.note_label.setVisible(False)
        layout.addWidget(self.note_label)

        controls = QHBoxLayout(); controls.setSpacing(8)
        self.time_label = QLabel("00:00:00 / 00:00:00"); self.time_label.setObjectName("previewTime")
        self.seek_slider = QSlider(Qt.Horizontal); self.seek_slider.setRange(0, 1000)
        controls.addWidget(self.time_label); controls.addWidget(self.seek_slider, 1)
        layout.addLayout(controls)

        buttons = QHBoxLayout(); buttons.setSpacing(6)
        self.prev_button = QPushButton("⏮"); self.play_button = QPushButton("▶"); self.next_button = QPushButton("⏭")
        self.fullscreen_button = QPushButton("⛶")
        for button in (self.prev_button, self.play_button, self.next_button, self.fullscreen_button):
            button.setProperty("variant", "quiet"); button.setFixedWidth(34)
        self.volume_icon = QLabel("🔊")
        self.volume_slider = QSlider(Qt.Horizontal); self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80); self.volume_slider.setFixedWidth(80)
        buttons.addWidget(self.prev_button); buttons.addWidget(self.play_button); buttons.addWidget(self.next_button)
        buttons.addStretch(1)
        buttons.addWidget(self.volume_icon); buttons.addWidget(self.volume_slider); buttons.addWidget(self.fullscreen_button)
        layout.addLayout(buttons)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        try:
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(self.video_widget)
            self.audio_output.setVolume(0.8)
        except Exception:
            pass
        self._current_path: str | None = None

        self.play_button.clicked.connect(self._toggle_play)
        self.prev_button.clicked.connect(lambda: self._seek_relative(-5))
        self.next_button.clicked.connect(lambda: self._seek_relative(5))
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        self.seek_slider.sliderMoved.connect(self._seek_to_slider)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)

    def set_badge(self, text: str) -> None:
        self.badge_label.setText(text)

    def set_note(self, text: str) -> None:
        self.note_label.setText(text)
        self.note_label.setVisible(bool(text))

    def load(self, path: str, *, start_seconds: float = 0.0, autoplay: bool = False) -> None:
        resolved = str(Path(path).resolve())
        if self._current_path != resolved:
            try:
                self.player.setSource(QUrl.fromLocalFile(resolved))
            except Exception:
                return
            self._current_path = resolved
        if start_seconds > 0:
            self.player.setPosition(int(start_seconds * 1000))
        if autoplay:
            self.player.play()

    def clear(self) -> None:
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass
        self._current_path = None
        self.time_label.setText("00:00:00 / 00:00:00")

    def _toggle_play(self) -> None:
        if self._current_path is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _seek_relative(self, delta_seconds: int) -> None:
        if self.player.duration() <= 0:
            return
        target = max(0, min(self.player.duration(), self.player.position() + delta_seconds * 1000))
        self.player.setPosition(target)

    def _toggle_fullscreen(self) -> None:
        self.video_widget.setFullScreen(not self.video_widget.isFullScreen())

    def _seek_to_slider(self, value: int) -> None:
        duration = self.player.duration()
        if duration > 0:
            self.player.setPosition(int(duration * value / 1000))

    def _on_position_changed(self, position_ms: int) -> None:
        duration_ms = self.player.duration()
        if duration_ms > 0:
            blocked = self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(int(position_ms * 1000 / duration_ms))
            self.seek_slider.blockSignals(blocked)
        self.time_label.setText(f"{format_duration(position_ms / 1000)} / {format_duration(duration_ms / 1000)}")

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.time_label.setText(f"00:00:00 / {format_duration(duration_ms / 1000)}")

    def _on_state_changed(self, state) -> None:
        self.play_button.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")


class ComparisonPreviewPane(QFrame):
    """Khung so sánh: video gốc (trên) và video đã xử lý final.mp4 (dưới), để đối chiếu trực tiếp."""

    choose_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("comparisonPreviewPane")
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(10)
        self.original_pane = VideoPreviewPane("VIDEO GỐC", choosable=True)
        self.original_pane.choose_requested.connect(self.choose_requested.emit)
        self.result_pane = VideoPreviewPane("VIDEO ĐÃ XỬ LÝ (final.mp4)")
        self.original_pane.video_widget.setMinimumHeight(240)
        self.result_pane.video_widget.setMinimumHeight(240)
        # Nút "Chọn video…" chỉ có ở khung gốc khiến 2 khung có kích thước tối thiểu khác nhau —
        # bỏ qua sizeHint ngang để layout luôn chia đúng 50/50 theo stretch factor, dễ đối chiếu.
        self.original_pane.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.result_pane.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        layout.addWidget(self.original_pane, 1)
        layout.addWidget(self.result_pane, 1)

    def show_original(self, path: str) -> None:
        self.original_pane.set_note("")
        self.original_pane.load(path)
        self.original_pane.set_badge(Path(path).name)

    def show_result(self, path: str | None) -> None:
        if path and Path(path).is_file():
            self.result_pane.set_note("")
            self.result_pane.load(path)
            self.result_pane.set_badge(Path(path).name)
        else:
            self.result_pane.clear()
            self.result_pane.set_badge("")
            self.result_pane.set_note("Chưa có final.mp4 — sẽ hiển thị ngay khi xử lý xong video này.")

    def clear(self) -> None:
        self.original_pane.clear()
        self.original_pane.set_badge("")
        self.result_pane.clear()
        self.result_pane.set_badge("")
        self.result_pane.set_note("")


def base_editor_stylesheet() -> str:
    """QSS dùng chung cho mọi màn hình Editor chuyên dụng (header/workflow/info bar/panel/progress bar)."""
    return """
        QWidget#editorRoot, QWidget#editorBody { background: #0b0d12; }
        QScrollArea#editorScroll, QScrollArea#editorScroll > QWidget { background: transparent; border: none; }
        QFrame#editorHeader { background: #10131b; border-bottom: 1px solid #202838; }
        QLabel#dashLogo {
            background: #2dd4bf; color: #041011; border-radius: 10px;
            min-width: 40px; min-height: 40px; qproperty-alignment: AlignCenter;
            font-size: 15px; font-weight: 800;
        }
        QLabel#dashTitle { font-size: 14pt; font-weight: 700; color: #ffffff; }
        QLabel#engineBadge {
            background: rgba(45, 212, 191, 0.16); color: #2dd4bf; border: 1px solid #2dd4bf;
            border-radius: 12px; padding: 5px 14px; font-weight: 700; margin-right: 8px;
        }

        QFrame#workflowBar { background: #0d1017; border-bottom: 1px solid #202838; }
        QLabel#workflowPill {
            background: #11151f; border: 1px solid #202838; border-radius: 8px;
            padding: 8px 14px; color: #9aa4b2; font-weight: 700;
        }
        QLabel#workflowPill[state="active"] { border-color: #2dd4bf; color: #2dd4bf; background: rgba(45, 212, 191, 0.1); }
        QLabel#workflowPill[state="done"] { color: #5eead4; }

        QFrame#infoBar { background: #0d1017; border-bottom: 1px solid #202838; }
        QLabel#infoItem { color: #e7ecf3; font-weight: 700; }

        QFrame#previewPane, QFrame#settingsPanel, QFrame#pageWorkspace {
            background: #11151f; border: 1px solid #202838; border-radius: 10px;
        }
        QLabel#previewTitle { color: #2dd4bf; font-weight: 800; font-size: 9.5pt; }
        QLabel#previewBadge {
            background: rgba(45, 212, 191, 0.14); color: #2dd4bf; border-radius: 8px;
            padding: 2px 8px; font-weight: 700; font-size: 8pt;
        }
        QLabel#previewNote { color: #f59e0b; font-size: 8.5pt; font-style: italic; }
        QLabel#previewTime { color: #9aa4b2; font-size: 8.5pt; min-width: 90px; }
        QVideoWidget#previewVideo { background: #050709; border-radius: 8px; }

        QLabel#panelTitle { color: #f3f5f8; font-weight: 800; font-size: 9.5pt; }
        QLabel#settingsSection { color: #2dd4bf; font-weight: 800; font-size: 8.5pt; margin-top: 6px; }
        QPushButton#linkButton { background: transparent; border: none; color: #2dd4bf; font-weight: 700; padding: 0; min-height: 0; }
        QPushButton#linkButton:hover { color: #5eead4; }
        QPushButton#applyButton { background: #3b82f6; border: 1px solid #3b82f6; color: #ffffff; font-weight: 800; border-radius: 8px; padding: 10px 14px; }
        QPushButton#applyButton:hover { background: #60a5fa; border-color: #60a5fa; }

        QTableWidget {
            background: #11151f; alternate-background-color: #141a25; border: 1px solid #202838;
            border-radius: 8px; gridline-color: #202838; color: #e7ecf3;
        }
        QHeaderView::section { background: #141a25; color: #9aa4b2; border: 0; border-bottom: 1px solid #202838; padding: 6px; font-weight: 700; }
        QPushButton#previewRowButton { min-width: 26px; max-width: 26px; padding: 2px; }

        QLabel#infoValue { color: #f3f5f8; font-weight: 700; }

        QFrame#progressBarFrame { background: #0d1017; border-top: 1px solid #202838; }
        QLabel#progressCaption { color: #9aa4b2; font-weight: 700; font-size: 8pt; }
        QPushButton#startButton { background: #2dd4bf; border: 1px solid #2dd4bf; color: #041011; font-weight: 800; border-radius: 8px; padding: 10px 20px; }
        QPushButton#startButton:hover { background: #5eead4; border-color: #5eead4; }
        QPushButton#stopButton { background: #2a1720; border: 1px solid #7f1d1d; color: #fecaca; font-weight: 800; border-radius: 8px; padding: 10px 20px; }
        QPushButton#stopButton:hover { background: #3a1b26; border-color: #ef4444; }
        QPlainTextEdit#logPanel { background: #0b0d12; border-top: 1px solid #202838; color: #cbd5e1; font-family: 'Cascadia Mono','Consolas'; font-size: 9pt; }

        QCheckBox#settingsCheck { color: #e7ecf3; padding: 4px 0; }
    """
