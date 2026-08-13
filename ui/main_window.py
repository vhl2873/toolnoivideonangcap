from __future__ import annotations

import importlib.util
import random
from datetime import datetime
from pathlib import Path

try:
    import vlc  # type: ignore
except Exception:
    vlc = None

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFontMetrics,
    QIcon,
    QPainter,
    QPixmap,
    QPolygon,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedLayout,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.ffmpeg_tools import check_required_tools
from core.video_analyzer import (
    CompatibilityReport,
    VideoAnalysis,
    format_duration,
    format_size,
    group_analyses_by_resolution,
    summarize_file,
)
from utils.path_utils import path_key, path_order_intersect_group, same_path
from workers.analysis_worker import AnalysisWorker
from workers.concat_worker import ConcatWorker
from workers.stream_concat_worker import StreamConcatWorker
from workers.thumbnail_worker import ThumbnailWorker
from workers.media_worker import MediaWorker

_SIDEBAR_ALL = -1


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fast Video Concatenator")

        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._concat_thread: QThread | None = None
        self._concat_worker: ConcatWorker | None = None
        self._concat_status_hint: str = "Đang nối video"
        self._thumbnail_thread: QThread | None = None
        self._thumbnail_worker: ThumbnailWorker | None = None
        self._thumbnail_queue: list[str] = []
        self._media_thread: QThread | None = None
        self._media_worker: MediaWorker | None = None
        self._media_status_hint: str = "Đang xử lý media"
        self._placeholder_icon: QIcon | None = None
        self._last_report: CompatibilityReport | None = None
        self._last_signature_paths: list[str] = []
        self._path_order: list[str] = []
        self._analysis_by_path: dict[str, VideoAnalysis] = {}
        self._stream_groups_cache: list[list[str]] = []
        self._project_task_type = "Nối video"
        self._thumbnail_path_by_video: dict[str, str] = {}
        self._file_tile_text_width = 156
        self._file_tile_cell = QSize(188, 148)

        self.file_list = QListWidget()
        self.file_list.setObjectName("fileStrip")
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_list.setAlternatingRowColors(False)
        self.file_list.setDragEnabled(True)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDropIndicatorShown(True)
        self.file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

        self.add_button = QPushButton("Thêm file")
        self.remove_button = QPushButton("Xóa")
        self.remove_incompatible_button = QPushButton("Xóa file lỗi")
        self.up_button = QPushButton("Lên")
        self.down_button = QPushButton("Xuống")
        self.random_button = QPushButton("Random")

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Chọn thư mục lưu output, ví dụ D:/video")
        self.output_button = QPushButton("Chọn...")
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItem("MKV (khuyên dùng cho video dài)", "mkv")
        self.output_format_combo.addItem("MP4", "mp4")

        self.analyze_button = QPushButton("Phân tích")
        self.start_button = QPushButton("Nối siêu nhanh")
        self.safe_concat_button = QPushButton("Nối an toàn sửa lỗi đứng hình")
        self.streams_concat_button = QPushButton("Nối tất cả các luồng")
        self.split_button = QPushButton("Băm nhỏ video")
        self.extract_audio_button = QPushButton("Tách audio / nhạc nền")
        self.stop_button = QPushButton("Dừng")
        self.open_folder_button = QPushButton("Mở thư mục output")
        self.reset_button = QPushButton("Làm mới")
        self.normalize_button = QPushButton("Chuẩn hóa video")
        self.effects_button = QPushButton("Hiệu ứng")
        self.zoom_button = QPushButton("Phóng to/thu nhỏ")
        self.export_button = QPushButton("XUẤT VIDEO")

        self.preview_label = QLabel("▶ Preview video\n\nChọn video để xem trước")
        self.preview_label.setObjectName("previewCanvas")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("videoCanvas")
        self.video_widget.hide()
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.time_label = QLabel("00:00 / 00:00")
        self.preview_status_label = QLabel("Sẵn sàng xem trước")
        self.preview_status_label.setObjectName("hintLabel")
        self.preview_meta_label = QLabel("Chưa có metadata")
        self.preview_meta_label.setObjectName("hintLabel")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setValue(0)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(25, 300)
        self.zoom_slider.setValue(100)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.play_button = QPushButton("▶")
        self.pause_button = QPushButton("⏸")
        self.prev_button = QPushButton("⏮")
        self.next_button = QPushButton("⏭")
        self.open_external_player_button = QPushButton("Mở ngoài")
        self.cut_at_cursor_button = QPushButton("✂ Chia tại con trỏ")
        self.set_range_start_button = QPushButton("Đặt A")
        self.set_range_end_button = QPushButton("Đặt B")
        self.cut_range_button = QPushButton("Cắt A-B")
        self.range_label = QLabel("A: --:--  |  B: --:--")
        self.range_label.setObjectName("hintLabel")
        self._range_start_seconds: float | None = None
        self._range_end_seconds: float | None = None
        self._vlc_instance = None
        self._vlc_player = None
        self._vlc_media_path: str | None = None
        self._qt_media_path: str | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(500)
        self.timeline_video_label = QLabel("V1  ┃██████████┃████████████████┃")
        self.timeline_audio_label = QLabel("A1  ┃~~~~~~~~~~┃~~~~~~~~~~~~~~~~┃")
        self.timeline_video_label.setObjectName("timelineTrack")
        self.timeline_audio_label.setObjectName("timelineTrack")
        self.normalize_resolution_combo = QComboBox()
        self.normalize_resolution_combo.addItems(["1920 x 1080", "1280 x 720", "1080 x 1920", "Giữ nguyên"])
        self.normalize_ratio_combo = QComboBox()
        self.normalize_ratio_combo.addItems(["16:9", "9:16", "1:1", "4:3", "Giữ nguyên"])
        self.normalize_fps_combo = QComboBox()
        self.normalize_fps_combo.addItems(["24", "25", "30", "50", "60", "Giữ nguyên"])
        self.normalize_codec_combo = QComboBox()
        self.normalize_codec_combo.addItems(["H.264", "H.265", "AV1", "Giữ nguyên"])
        self.normalize_format_combo = QComboBox()
        self.normalize_format_combo.addItems(["MP4", "MKV", "MOV"])
        self.normalize_bitrate_combo = QComboBox()
        self.normalize_bitrate_combo.addItems(["Tự động", "4M", "8M", "12M", "20M"])
        self.normalize_keep_ratio_radio = QRadioButton("Giữ nguyên tỷ lệ")
        self.normalize_crop_radio = QRadioButton("Cắt đầy khung hình")
        self.normalize_pad_radio = QRadioButton("Thêm viền đen")
        self.normalize_keep_ratio_radio.setChecked(True)
        self.split_mode_time_radio = QRadioButton("Chia theo thời gian")
        self.split_mode_count_radio = QRadioButton("Chia theo số lượng")
        self.split_mode_markers_radio = QRadioButton("Chia tại các mốc tùy chọn")
        self.split_mode_time_radio.setChecked(True)
        self.split_seconds_spin = QSpinBox()
        self.split_seconds_spin.setRange(1, 24 * 60 * 60)
        self.split_seconds_spin.setValue(60)
        self.split_count_spin = QSpinBox()
        self.split_count_spin.setRange(2, 999)
        self.split_count_spin.setValue(10)
        self.split_marker_edit = QPlainTextEdit()
        self.split_marker_edit.setPlaceholderText("Nhập mỗi dòng một đoạn, ví dụ:\n00:00:00-00:01:30\n00:01:30-00:03:00")
        self.split_marker_edit.setMaximumHeight(90)
        self.split_accurate_check = QCheckBox("Chia chính xác (re-encode, chậm hơn)")
        self.split_keep_audio_check = QCheckBox("Giữ lại âm thanh")
        self.split_keep_audio_check.setChecked(True)
        self.split_auto_number_check = QCheckBox("Đánh số tự động")
        self.split_auto_number_check.setChecked(True)
        self.split_separate_folder_check = QCheckBox("Xuất vào thư mục riêng")
        self.split_separate_folder_check.setChecked(True)
        self.audio_extract_radio = QRadioButton("Trích xuất âm thanh từ video")
        self.audio_ai_radio = QRadioButton("Tách giọng nói và nhạc nền bằng AI")
        self.audio_extract_radio.setChecked(True)
        self.audio_format_combo = QComboBox()
        self.audio_format_combo.addItems(["mp3", "wav", "aac"])
        self.audio_quality_combo = QComboBox()
        self.audio_quality_combo.addItems(["320 kbps", "256 kbps", "192 kbps", "WAV lossless"])
        self.audio_result_label = QLabel("Kết quả mẫu:\n├── vocals.wav\n└── background_music.wav")
        self.audio_result_label.setObjectName("hintLabel")
        self.demucs_available = bool(importlib.util.find_spec("demucs"))
        self.property_tabs = QTabWidget()
        self.effect_checks: dict[str, QCheckBox] = {}

        self.status_label = QLabel("Chưa phân tích.")
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)

        self.stream_sidebar_frame = QFrame()
        self.stream_sidebar_frame.setProperty("role", "panel")
        self.stream_sidebar_frame.setVisible(False)
        self.stream_sidebar_frame.setMinimumWidth(220)
        self.stream_sidebar_frame.setMaximumWidth(340)
        self.stream_sidebar_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        sidebar_layout = QVBoxLayout(self.stream_sidebar_frame)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(8)
        self.sidebar_section_title = QLabel("Luồng tương thích")
        self.sidebar_section_title.setProperty("role", "sectionTitle")
        self.stream_sidebar = QListWidget()
        self.stream_sidebar.setObjectName("streamSidebar")
        sidebar_layout.addWidget(self.sidebar_section_title)
        sidebar_layout.addWidget(self.stream_sidebar, 1)

        self.file_panel_title = QLabel("Danh sách video")
        self.file_panel_title.setProperty("role", "sectionTitle")
        self.file_right_panel = QWidget()
        file_right_layout = QVBoxLayout(self.file_right_panel)
        file_right_layout.setContentsMargins(0, 0, 0, 0)
        file_right_layout.setSpacing(8)
        file_right_layout.addWidget(self.file_panel_title)
        file_right_layout.addWidget(self.file_list, 1)

        self.file_splitter = QSplitter(Qt.Horizontal)
        self.file_splitter.addWidget(self.stream_sidebar_frame)
        self.file_splitter.addWidget(self.file_right_panel)
        self.file_splitter.setStretchFactor(0, 0)
        self.file_splitter.setStretchFactor(1, 1)
        self.file_splitter.setCollapsible(0, True)

        self._configure_widgets()
        self._build_ui()
        self._connect_signals()
        self._update_buttons()
        self._log_tool_status()

    def _configure_widgets(self) -> None:
        tile_w = 188
        thumb_w, thumb_h = 168, 94
        cell_h = thumb_h + 54
        self._file_tile_text_width = tile_w - 32
        self._file_tile_cell = QSize(tile_w, cell_h)
        self.file_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.file_list.setFlow(QListView.Flow.LeftToRight)
        self.file_list.setWrapping(False)
        self.file_list.setResizeMode(QListView.ResizeMode.Fixed)
        self.file_list.setMovement(QListWidget.Movement.Snap)
        self.file_list.setSpacing(12)
        self.file_list.setUniformItemSizes(True)
        self.file_list.setIconSize(QSize(thumb_w, thumb_h))
        self.file_list.setGridSize(QSize(tile_w, cell_h))
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.file_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strip_h = cell_h + 16
        self.file_list.setMinimumHeight(strip_h)
        self.file_list.setMaximumHeight(strip_h)
        self.file_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.file_list.setToolTip(
            "Một hàng ngang: kéo thả ô để đổi thứ tự nối (cả «Tất cả» và từng luồng). "
            "Có thể chọn nhiều file rồi Xóa."
        )
        self.stream_sidebar.setMinimumWidth(200)
        self.stream_sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stream_sidebar.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.stream_sidebar.setUniformItemSizes(False)
        self.log_edit.setMinimumHeight(190)
        self.preview_label.setMinimumHeight(260)
        self.preview_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)

        self.add_button.setProperty("variant", "quiet")
        self.remove_button.setProperty("variant", "quiet")
        self.remove_incompatible_button.setProperty("variant", "danger")
        self.up_button.setProperty("variant", "quiet")
        self.down_button.setProperty("variant", "quiet")
        self.random_button.setProperty("variant", "quiet")
        self.output_button.setProperty("variant", "quiet")
        self.analyze_button.setProperty("variant", "quiet")
        self.start_button.setProperty("variant", "primary")
        self.safe_concat_button.setProperty("variant", "primary")
        self.streams_concat_button.setProperty("variant", "primary")
        self.split_button.setProperty("variant", "quiet")
        self.extract_audio_button.setProperty("variant", "quiet")
        self.stop_button.setProperty("variant", "danger")
        self.open_folder_button.setProperty("variant", "quiet")
        self.reset_button.setProperty("variant", "quiet")
        self.normalize_button.setProperty("variant", "quiet")
        self.effects_button.setProperty("variant", "quiet")
        self.zoom_button.setProperty("variant", "quiet")
        self.play_button.setProperty("variant", "quiet")
        self.pause_button.setProperty("variant", "quiet")
        self.prev_button.setProperty("variant", "quiet")
        self.next_button.setProperty("variant", "quiet")
        self.open_external_player_button.setProperty("variant", "quiet")
        self.cut_at_cursor_button.setProperty("variant", "quiet")
        self.set_range_start_button.setProperty("variant", "quiet")
        self.set_range_end_button.setProperty("variant", "quiet")
        self.cut_range_button.setProperty("variant", "primary")
        self.export_button.setProperty("variant", "primary")

        self.reset_button.setMinimumWidth(96)
        self.reset_button.setToolTip(
            "Xóa hết danh sách video, phân tích và log — bắt đầu phiên mới. "
            "Giữ nguyên thư mục output đã chọn."
        )

        self.start_button.setMinimumWidth(150)
        self.safe_concat_button.setMinimumWidth(230)
        self.streams_concat_button.setMinimumWidth(200)
        self.split_button.setMinimumWidth(150)
        self.extract_audio_button.setMinimumWidth(180)
        self.analyze_button.setMinimumWidth(120)
        self.random_button.setMinimumWidth(92)
        self.random_button.setToolTip(
            "Sắp xếp ngẫu nhiên thứ tự nối của các video đang hiển thị. "
            "Bấm nhiều lần để trộn lại thứ tự mới."
        )
        self.stop_button.setMinimumWidth(90)
        self.open_folder_button.setMinimumWidth(160)
        self.output_format_combo.setMinimumWidth(84)
        self.output_format_combo.setToolTip(
            "MP4 tương thích rộng. MKV thường ổn định hơn cho output rất dài hoặc nhiều stream."
        )
        self.start_button.setToolTip(
            "Nối stream copy trực tiếp nhanh nhất. Dùng khi bộ file đã sạch và từng nối ra output ổn."
        )
        self.safe_concat_button.setToolTip(
            "Remux từng file sang MKV tạm với genpts rồi mới nối stream copy. "
            "Chậm hơn nhưng giảm lỗi đứng hình/lỗi ảnh ở điểm nối."
        )
        self.streams_concat_button.setToolTip(
            "Mỗi luồng tương thích (cùng chữ ký stream) → một file MP4 riêng, "
            "theo thứ tự file trong danh sách. Bỏ qua luồng chỉ có 1 file."
        )
        self.split_button.setToolTip(
            "Băm nhỏ các video đang chọn hoặc đang hiển thị thành nhiều đoạn bằng FFmpeg stream copy."
        )
        self.extract_audio_button.setToolTip(
            "Tách phần audio hiện có trong video ra MP3/WAV/AAC. Nếu muốn tách riêng giọng và nhạc nền bằng AI thì cần module nặng hơn."
        )
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("headerBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_layout.setSpacing(12)
        logo = QLabel("FV")
        logo.setObjectName("logoBadge")
        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(1)
        title = QLabel("Fast Video Studio")
        title.setObjectName("appTitle")
        subtitle = QLabel("Nối, chuẩn hóa và xử lý video trong một quy trình thống nhất")
        subtitle.setObjectName("appSubtitle")
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        header_layout.addWidget(logo)
        header_layout.addWidget(brand)
        header_layout.addStretch(1)
        engine_badge = QLabel("●  FFmpeg Engine")
        engine_badge.setObjectName("engineBadge")
        header_layout.addWidget(engine_badge)
        header_layout.addWidget(self.reset_button)
        root.addWidget(header)

        workflow = QFrame()
        workflow.setObjectName("workflowBar")
        workflow_layout = QHBoxLayout(workflow)
        workflow_layout.setContentsMargins(14, 9, 14, 9)
        workflow_layout.setSpacing(8)
        for number, label in (("01", "Thêm video"), ("02", "Phân tích"), ("03", "Chỉnh thiết lập"), ("04", "Xuất kết quả")):
            step = QLabel(f"{number}   {label}")
            step.setProperty("role", "workflowStep")
            workflow_layout.addWidget(step, 1)
        root.addWidget(workflow)

        main_splitter = QSplitter(Qt.Horizontal)

        left_panel = QFrame()
        left_panel.setProperty("role", "panel")
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        func_title = QLabel("TÁC VỤ & NGUỒN VIDEO")
        func_title.setProperty("role", "sectionTitle")
        left_layout.addWidget(func_title)
        for button in (
            self.add_button,
            self.start_button,
            self.safe_concat_button,
            self.normalize_button,
            self.split_button,
            self.zoom_button,
            self.effects_button,
            self.extract_audio_button,
            self.remove_button,
            self.remove_incompatible_button,
        ):
            left_layout.addWidget(button)
        left_layout.addSpacing(8)
        file_title = QLabel("DANH SÁCH FILE")
        file_title.setProperty("role", "sectionTitle")
        left_layout.addWidget(file_title)
        left_layout.addWidget(self.file_splitter, 1)
        order_row = QHBoxLayout()
        for button in (self.up_button, self.down_button, self.random_button):
            order_row.addWidget(button)
        left_layout.addLayout(order_row)

        center_panel = QFrame()
        center_panel.setProperty("role", "panel")
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(12, 12, 12, 12)
        center_layout.setSpacing(10)
        preview_host = QWidget()
        preview_stack = QStackedLayout(preview_host)
        preview_stack.setContentsMargins(0, 0, 0, 0)
        preview_stack.addWidget(self.preview_label)
        preview_stack.addWidget(self.video_widget)
        self.preview_stack = preview_stack
        center_layout.addWidget(preview_host, 3)
        center_layout.addWidget(self.preview_status_label)
        center_layout.addWidget(self.preview_meta_label)
        seek_row = QHBoxLayout()
        seek_row.addWidget(QLabel("00:00"))
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.time_label)
        seek_row.addWidget(self.cut_at_cursor_button)
        center_layout.addLayout(seek_row)
        range_row = QHBoxLayout()
        range_row.addWidget(self.set_range_start_button)
        range_row.addWidget(self.set_range_end_button)
        range_row.addWidget(self.cut_range_button)
        range_row.addWidget(self.range_label, 1)
        center_layout.addLayout(range_row)
        controls = QHBoxLayout()
        for button in (self.prev_button, self.play_button, self.pause_button, self.next_button, self.open_external_player_button):
            controls.addWidget(button)
        controls.addWidget(QLabel("🔊"))
        controls.addWidget(self.volume_slider, 1)
        center_layout.addLayout(controls)
        timeline_box = QFrame()
        timeline_box.setObjectName("timelineBox")
        timeline_layout = QVBoxLayout(timeline_box)
        timeline_layout.setContentsMargins(12, 10, 12, 10)
        timeline_layout.addWidget(QLabel("TIMELINE"))
        timeline_layout.addWidget(self.timeline_video_label)
        timeline_layout.addWidget(self.timeline_audio_label)
        center_layout.addWidget(timeline_box, 1)
        log_title = QLabel("NHẬT KÝ XỬ LÝ")
        log_title.setProperty("role", "sectionTitle")
        center_layout.addWidget(log_title)
        center_layout.addWidget(self.log_edit, 1)

        right_panel = QFrame()
        right_panel.setProperty("role", "panel")
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(10)
        prop_title = QLabel("THIẾT LẬP XỬ LÝ")
        prop_title.setProperty("role", "sectionTitle")
        right_layout.addWidget(prop_title)
        self.property_tabs.addTab(self._build_video_properties_tab(), "Video")
        self.property_tabs.addTab(self._build_split_properties_tab(), "Chia")
        self.property_tabs.addTab(self._build_normalize_properties_tab(), "Chuẩn hóa")
        self.property_tabs.addTab(self._build_audio_properties_tab(), "Tách nhạc")
        right_layout.addWidget(self.property_tabs, 1)

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setStretchFactor(2, 1)
        root.addWidget(main_splitter, 1)

        output_bar = QFrame()
        output_bar.setObjectName("outputBar")
        output_layout = QGridLayout(output_bar)
        output_layout.setContentsMargins(12, 10, 12, 10)
        output_title = QLabel("XUẤT VIDEO")
        output_title.setProperty("role", "sectionTitle")
        output_layout.addWidget(output_title, 0, 0)
        output_layout.addWidget(self.output_edit, 0, 1)
        output_layout.addWidget(self.output_format_combo, 0, 2)
        output_layout.addWidget(self.output_button, 0, 3)
        output_layout.addWidget(self.open_folder_button, 0, 4)
        output_layout.addWidget(self.export_button, 0, 5)
        output_layout.addWidget(QLabel("Tiến trình:"), 1, 0)
        output_layout.addWidget(self.progress_bar, 1, 1, 1, 4)
        output_layout.addWidget(self.status_label, 1, 5)
        root.addWidget(output_bar)
        self.setCentralWidget(central)
        self._apply_editor_style()

    def configure_project(
        self,
        *,
        task_type: str,
        project_name: str = "",
        input_path: str = "",
        output_path: str = "",
    ) -> None:
        """Cấu hình Giao diện số 2 theo đúng loại tác vụ của dự án."""
        self._project_task_type = task_type
        is_concat = task_type == "Nối video"
        is_normalize = task_type == "Chuẩn hóa video"
        is_split = task_type == "Chia nhỏ video"
        is_visual = task_type in {"Phóng to/thu nhỏ", "Thêm hiệu ứng"}

        self.setWindowTitle(
            f"Fast Video Studio — {project_name} — {task_type}"
            if project_name else f"Fast Video Studio — {task_type}"
        )
        if output_path:
            self.output_edit.setText(output_path)

        paths = [item.strip() for item in input_path.split(";") if item.strip()]
        self._path_order = [str(Path(item).resolve()) for item in paths if Path(item).is_file()]
        if self._path_order:
            self._mark_analysis_dirty()
            self._queue_thumbnails(self._path_order)

        self.start_button.setVisible(is_concat)
        self.safe_concat_button.setVisible(is_concat)
        self.streams_concat_button.setVisible(is_concat)
        self.normalize_button.setVisible(is_normalize)
        self.split_button.setVisible(is_split)
        self.zoom_button.setVisible(is_visual)
        self.effects_button.setVisible(is_visual)
        self.extract_audio_button.setVisible(False)

        tab_index = 2 if is_normalize else 1 if is_split else 0
        for index in range(self.property_tabs.count()):
            self.property_tabs.setTabVisible(index, index == tab_index)
        self.property_tabs.setCurrentIndex(tab_index)

        labels = {
            "Nối video": "NỐI VIDEO",
            "Chuẩn hóa video": "CHUẨN HÓA VIDEO",
            "Chia nhỏ video": "BĂM NHỎ VIDEO",
            "Phóng to/thu nhỏ": "ÁP DỤNG PHÓNG TO / THU NHỎ",
            "Thêm hiệu ứng": "ÁP DỤNG HIỆU ỨNG",
        }
        hints = {
            "Nối video": "Sắp xếp file, phân tích tương thích rồi bấm Nối video.",
            "Chuẩn hóa video": "Chọn độ phân giải, FPS, codec và cách khớp khung hình.",
            "Chia nhỏ video": "Chia theo thời gian, số lượng hoặc nhập từng khoảng thời gian yêu cầu.",
            "Phóng to/thu nhỏ": "Điều chỉnh tỷ lệ zoom, vị trí X/Y; có thể kết hợp hiệu ứng video.",
            "Thêm hiệu ứng": "Chọn một hoặc nhiều hiệu ứng; có thể kết hợp zoom/crop/pad.",
        }
        self.export_button.setText(labels.get(task_type, "XỬ LÝ VIDEO"))
        self.status_label.setText(hints.get(task_type, "Chọn file và cấu hình tác vụ video."))
        self._update_buttons()

    def _start_primary_action(self) -> None:
        actions = {
            "Chuẩn hóa video": self.start_normalize_videos,
            "Chia nhỏ video": self.start_split_videos,
            "Phóng to/thu nhỏ": self.start_transform_video,
            "Thêm hiệu ứng": self.start_apply_effects,
        }
        actions.get(self._project_task_type, self.start_concat)()
    def _connect_signals(self) -> None:
        self.add_button.clicked.connect(self.add_files)
        self.remove_button.clicked.connect(self.remove_selected)
        self.remove_incompatible_button.clicked.connect(self.remove_incompatible_files)
        self.up_button.clicked.connect(self.move_selected_up)
        self.down_button.clicked.connect(self.move_selected_down)
        self.random_button.clicked.connect(self.randomize_visible_order)
        self.output_button.clicked.connect(self.choose_output)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.export_button.clicked.connect(self._start_primary_action)
        self.start_button.clicked.connect(self.start_concat)
        self.safe_concat_button.clicked.connect(self.start_safe_concat)
        self.streams_concat_button.clicked.connect(self.start_all_streams_concat)
        self.split_button.clicked.connect(self.start_split_videos)
        self.extract_audio_button.clicked.connect(self.start_extract_audio)
        self.stop_button.clicked.connect(self.stop_concat)
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.normalize_button.clicked.connect(self.start_normalize_videos)
        self.effects_button.clicked.connect(self.start_apply_effects)
        self.zoom_button.clicked.connect(self.start_transform_video)
        self.play_button.clicked.connect(self._play_preview)
        self.pause_button.clicked.connect(self._pause_preview)
        self.prev_button.clicked.connect(lambda: self._seek_relative_seconds(-5))
        self.next_button.clicked.connect(lambda: self._seek_relative_seconds(5))
        self.open_external_player_button.clicked.connect(self._open_selected_external)
        self.cut_at_cursor_button.clicked.connect(self._split_at_cursor_placeholder)
        self.set_range_start_button.clicked.connect(self._set_range_start)
        self.set_range_end_button.clicked.connect(self._set_range_end)
        self.cut_range_button.clicked.connect(self._cut_range)
        self.seek_slider.valueChanged.connect(self._seek_slider_changed)
        self._preview_timer.timeout.connect(self._sync_preview_position)
        self.media_player.positionChanged.connect(self._qt_position_changed)
        self.media_player.durationChanged.connect(self._qt_duration_changed)
        self.media_player.errorOccurred.connect(self._qt_media_error)
        self.reset_button.clicked.connect(self.reset_session)
        self.output_edit.textChanged.connect(self._update_buttons)
        self.file_list.itemSelectionChanged.connect(self._update_preview_placeholder)
        self.file_list.model().rowsMoved.connect(self._rows_moved)
        self.stream_sidebar.currentRowChanged.connect(self._on_stream_sidebar_row_changed)

    def _build_video_properties_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Kích thước video"))
        size_row = QHBoxLayout()
        width_spin = QSpinBox(); width_spin.setRange(1, 9999); width_spin.setValue(1920)
        height_spin = QSpinBox(); height_spin.setRange(1, 9999); height_spin.setValue(1080)
        size_row.addWidget(width_spin); size_row.addWidget(QLabel("x")); size_row.addWidget(height_spin)
        layout.addLayout(size_row)
        layout.addWidget(QLabel("Phóng to / thu nhỏ"))
        layout.addWidget(self.zoom_slider)
        pos_row = QHBoxLayout()
        self.pos_x_spin = QSpinBox(); self.pos_x_spin.setRange(-5000, 5000)
        self.pos_y_spin = QSpinBox(); self.pos_y_spin.setRange(-5000, 5000)
        pos_row.addWidget(QLabel("X")); pos_row.addWidget(self.pos_x_spin)
        pos_row.addWidget(QLabel("Y")); pos_row.addWidget(self.pos_y_spin)
        layout.addLayout(pos_row)
        effects_group = QGroupBox("Hiệu ứng nhanh")
        effects_layout = QVBoxLayout(effects_group)
        self.effect_checks.clear()
        effect_options = [
            ("fade_in", "Fade in"),
            ("fade_out", "Fade out"),
            ("blur", "Blur"),
            ("brightness", "Tăng sáng"),
            ("contrast", "Tương phản"),
            ("sharpen", "Làm nét"),
            ("grayscale", "Đen trắng"),
            ("flip", "Lật ngang"),
            ("rotate", "Xoay 90°"),
            ("speed", "Tăng tốc 1.25x"),
        ]
        for key, label in effect_options:
            checkbox = QCheckBox(label)
            self.effect_checks[key] = checkbox
            effects_layout.addWidget(checkbox)
        apply_effects_button = QPushButton("ÁP DỤNG HIỆU ỨNG")
        apply_effects_button.setProperty("variant", "primary")
        apply_effects_button.clicked.connect(self.start_apply_effects)
        effects_layout.addWidget(apply_effects_button)
        transform_button = QPushButton("ÁP DỤNG ZOOM/CROP/PAD")
        transform_button.setProperty("variant", "primary")
        transform_button.clicked.connect(self.start_transform_video)
        layout.addWidget(effects_group)
        layout.addWidget(transform_button)
        layout.addStretch(1)
        return page

    def _build_split_properties_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        layout.addWidget(QLabel("BĂM NHỎ VIDEO"))
        layout.addWidget(self.split_mode_time_radio)
        layout.addWidget(self.split_mode_count_radio)
        layout.addWidget(self.split_mode_markers_radio)
        layout.addWidget(QLabel("Thời lượng mỗi đoạn (giây)"))
        layout.addWidget(self.split_seconds_spin)
        layout.addWidget(QLabel("Số lượng đoạn"))
        layout.addWidget(self.split_count_spin)
        layout.addWidget(QLabel("Mốc tùy chọn"))
        layout.addWidget(self.split_marker_edit)
        layout.addWidget(self.split_accurate_check)
        layout.addWidget(self.split_keep_audio_check)
        layout.addWidget(self.split_auto_number_check)
        layout.addWidget(self.split_separate_folder_check)
        run_button = QPushButton("BẮT ĐẦU CHIA VIDEO")
        run_button.setProperty("variant", "primary")
        run_button.clicked.connect(self.start_split_videos)
        layout.addWidget(run_button)
        layout.addStretch(1)
        return page

    def _build_normalize_properties_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        layout.addWidget(QLabel("CHUẨN HÓA VIDEO"))
        for label, widget in (
            ("Độ phân giải", self.normalize_resolution_combo),
            ("Tỷ lệ khung", self.normalize_ratio_combo),
            ("FPS", self.normalize_fps_combo),
            ("Codec", self.normalize_codec_combo),
            ("Định dạng", self.normalize_format_combo),
            ("Bitrate", self.normalize_bitrate_combo),
        ):
            layout.addWidget(QLabel(label))
            layout.addWidget(widget)
        layout.addWidget(self.normalize_keep_ratio_radio)
        layout.addWidget(self.normalize_crop_radio)
        layout.addWidget(self.normalize_pad_radio)
        run_button = QPushButton("CHUẨN HÓA VIDEO")
        run_button.setProperty("variant", "primary")
        run_button.clicked.connect(self.start_normalize_videos)
        layout.addWidget(run_button)
        layout.addStretch(1)
        return page

    def _build_audio_properties_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        layout.addWidget(QLabel("TÁCH NHẠC NỀN"))
        layout.addWidget(self.audio_extract_radio)
        layout.addWidget(self.audio_ai_radio)
        layout.addWidget(QLabel("Định dạng"))
        layout.addWidget(self.audio_format_combo)
        layout.addWidget(QLabel("Chất lượng"))
        layout.addWidget(self.audio_quality_combo)
        layout.addWidget(self.audio_result_label)
        demucs_status = QLabel("Demucs: sẵn sàng" if self.demucs_available else "Demucs: chưa cài — AI sẽ báo hướng dẫn cài")
        demucs_status.setObjectName("hintLabel")
        layout.addWidget(demucs_status)
        run_button = QPushButton("BẮT ĐẦU TÁCH NHẠC")
        run_button.setProperty("variant", "primary")
        run_button.clicked.connect(self.start_extract_audio)
        layout.addWidget(run_button)
        layout.addStretch(1)
        return page

    def _timeline_blocks_for_paths(self) -> tuple[str, str]:
        paths = self._paths_for_current_sidebar() or self._current_paths()
        if not paths:
            return "V1  ┃ trống ┃", "A1  ┃ trống ┃"
        v_blocks: list[str] = []
        a_blocks: list[str] = []
        for path in paths[:8]:
            name = Path(path).stem[:14]
            analysis = self._analysis_by_path.get(path)
            duration = format_duration(analysis.duration) if analysis else "??:??"
            video_stream = next((s for s in analysis.streams if s.codec_type == 'video'), None) if analysis else None
            audio_stream = next((s for s in analysis.streams if s.codec_type == 'audio'), None) if analysis else None
            v_blocks.append(f"┃ {name} {duration} ")
            a_blocks.append(f"┃ {audio_stream.codec_name if audio_stream else 'audio'} ")
        suffix = "┃ …" if len(paths) > 8 else "┃"
        return "V1  " + "".join(v_blocks) + suffix, "A1  " + "".join(a_blocks) + suffix

    def _seek_slider_changed(self, value: int) -> None:
        items = self.file_list.selectedItems()
        if not items:
            self.time_label.setText("00:00 / 00:00")
            return
        path = items[0].data(Qt.UserRole)
        analysis = self._analysis_by_path.get(path or "")
        if not analysis or analysis.duration <= 0:
            self.time_label.setText("00:00 / 00:00")
            return
        current = analysis.duration * value / 1000
        self.time_label.setText(f"{format_duration(current)} / {format_duration(analysis.duration)}")
        try:
            self.media_player.setPosition(int(current * 1000))
        except Exception:
            pass
        if self._vlc_player is not None:
            try:
                self._vlc_player.set_time(int(current * 1000))
            except Exception:
                pass

    def _split_at_cursor_placeholder(self) -> None:
        items = self.file_list.selectedItems()
        if not items:
            QMessageBox.information(self, "Chưa chọn video", "Hãy chọn một video trước khi chia tại con trỏ.")
            return
        path = items[0].data(Qt.UserRole)
        analysis = self._analysis_by_path.get(path or "")
        if not analysis or analysis.duration <= 0:
            QMessageBox.information(self, "Chưa có metadata", "Hãy bấm Phân tích để lấy thời lượng trước.")
            return
        current = analysis.duration * self.seek_slider.value() / 1000
        if current <= 0 or current >= analysis.duration:
            QMessageBox.information(self, "Mốc không hợp lệ", "Con trỏ phải nằm giữa video để chia được.")
            return
        marker_text = (
            f"00:00:00-{self._format_marker_time(current)}\n"
            f"{self._format_marker_time(current)}-{self._format_marker_time(analysis.duration)}"
        )
        self.split_mode_markers_radio.setChecked(True)
        self.split_marker_edit.setPlainText(marker_text)
        self.property_tabs.setCurrentIndex(1)
        self.append_log(f"Chia tại con trỏ: {Path(path).name} tại {self._format_marker_time(current)}")
        self._start_media_job(mode="split_markers", split_marker_text=marker_text)

    def _ensure_vlc_player(self) -> bool:
        if vlc is None:
            self.preview_status_label.setText("Chưa có python-vlc — ưu tiên Qt Multimedia hoặc mở ngoài")
            return False
        if self._vlc_player is not None:
            return True
        try:
            self._vlc_instance = vlc.Instance()
            self._vlc_player = self._vlc_instance.media_player_new()
            return True
        except Exception as exc:
            self.preview_status_label.setText(f"Không khởi tạo được VLC: {exc}")
            self._vlc_instance = None
            self._vlc_player = None
            return False

    def _open_selected_external(self) -> None:
        path, _analysis = self._selected_video_and_analysis()
        if not path:
            QMessageBox.information(self, "Chưa chọn video", "Hãy chọn video để mở bằng trình phát ngoài.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _play_preview(self) -> None:
        path, _analysis = self._selected_video_and_analysis()
        if not path:
            QMessageBox.information(self, "Chưa chọn video", "Hãy chọn video để xem trước.")
            return
        try:
            resolved = str(Path(path).resolve())
            if self._qt_media_path != path:
                self.media_player.setSource(QUrl.fromLocalFile(resolved))
                self._qt_media_path = path
            self.preview_stack.setCurrentWidget(self.video_widget)
            self.video_widget.show()
            self.preview_label.hide()
            self.audio_output.setVolume(self.volume_slider.value() / 100)
            self.media_player.play()
            self._preview_timer.start()
            self.preview_status_label.setText(f"Đang phát trong app: {Path(path).name}")
            return
        except Exception as exc:
            self.preview_status_label.setText(f"Qt Multimedia lỗi: {exc}")
        if not self._ensure_vlc_player():
            self._open_selected_external()
            return
        try:
            if self._vlc_media_path != path:
                media = self._vlc_instance.media_new(str(Path(path).resolve()))
                self._vlc_player.set_media(media)
                self._vlc_media_path = path
            self._vlc_player.play()
            self._preview_timer.start()
            self.preview_status_label.setText(f"Đang phát qua VLC: {Path(path).name}")
        except Exception as exc:
            self.preview_status_label.setText(f"Không phát được trong app: {exc}")
            self._open_selected_external()

    def _pause_preview(self) -> None:
        try:
            self.media_player.pause()
        except Exception:
            pass
        if self._vlc_player is not None:
            try:
                self._vlc_player.pause()
            except Exception:
                pass
        self.preview_status_label.setText("Đã tạm dừng preview")

    def _seek_relative_seconds(self, delta_seconds: int) -> None:
        current = self._current_seek_seconds()
        _path, analysis = self._selected_video_and_analysis()
        if current is None or not analysis or analysis.duration <= 0:
            return
        target = max(0.0, min(analysis.duration, current + delta_seconds))
        self.seek_slider.setValue(int(target * 1000 / analysis.duration))
        try:
            self.media_player.setPosition(int(target * 1000))
        except Exception:
            pass
        if self._vlc_player is not None:
            try:
                self._vlc_player.set_time(int(target * 1000))
            except Exception:
                pass

    def _sync_preview_position(self) -> None:
        if self.media_player.duration() > 0:
            return
        if self._vlc_player is None:
            return
        try:
            length_ms = self._vlc_player.get_length()
            time_ms = self._vlc_player.get_time()
        except Exception:
            return
        if length_ms and length_ms > 0 and time_ms >= 0:
            previous = self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(max(0, min(1000, int(time_ms * 1000 / length_ms))))
            self.seek_slider.blockSignals(previous)
            self.time_label.setText(f"{format_duration(time_ms / 1000)} / {format_duration(length_ms / 1000)}")

    def _qt_position_changed(self, position_ms: int) -> None:
        duration_ms = self.media_player.duration()
        if duration_ms <= 0:
            return
        previous = self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(max(0, min(1000, int(position_ms * 1000 / duration_ms))))
        self.seek_slider.blockSignals(previous)
        self.time_label.setText(f"{format_duration(position_ms / 1000)} / {format_duration(duration_ms / 1000)}")

    def _qt_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self.time_label.setText(f"00:00 / {format_duration(duration_ms / 1000)}")

    def _qt_media_error(self, _error, error_string: str) -> None:
        if error_string:
            self.preview_status_label.setText(f"Lỗi preview Qt: {error_string}")

    def _selected_video_and_analysis(self) -> tuple[str | None, VideoAnalysis | None]:
        items = self.file_list.selectedItems()
        if not items:
            return None, None
        path = items[0].data(Qt.UserRole)
        return path, self._analysis_by_path.get(path or "")

    def _current_seek_seconds(self) -> float | None:
        _path, analysis = self._selected_video_and_analysis()
        if not analysis or analysis.duration <= 0:
            return None
        return analysis.duration * self.seek_slider.value() / 1000

    def _refresh_range_label(self) -> None:
        start = self._format_marker_time(self._range_start_seconds) if self._range_start_seconds is not None else "--:--"
        end = self._format_marker_time(self._range_end_seconds) if self._range_end_seconds is not None else "--:--"
        self.range_label.setText(f"A: {start}  |  B: {end}")

    def _set_range_start(self) -> None:
        current = self._current_seek_seconds()
        if current is None:
            QMessageBox.information(self, "Chưa có metadata", "Hãy chọn video và bấm Phân tích trước.")
            return
        self._range_start_seconds = current
        self._refresh_range_label()
        self.append_log(f"Đặt điểm A tại {self._format_marker_time(current)}")

    def _set_range_end(self) -> None:
        current = self._current_seek_seconds()
        if current is None:
            QMessageBox.information(self, "Chưa có metadata", "Hãy chọn video và bấm Phân tích trước.")
            return
        self._range_end_seconds = current
        self._refresh_range_label()
        self.append_log(f"Đặt điểm B tại {self._format_marker_time(current)}")

    def _cut_range(self) -> None:
        path, analysis = self._selected_video_and_analysis()
        if not path or not analysis or analysis.duration <= 0:
            QMessageBox.information(self, "Chưa có metadata", "Hãy chọn video và bấm Phân tích trước.")
            return
        if self._range_start_seconds is None or self._range_end_seconds is None:
            QMessageBox.information(self, "Thiếu mốc A-B", "Hãy đặt đủ A và B trước khi cắt đoạn.")
            return
        start = min(self._range_start_seconds, self._range_end_seconds)
        end = max(self._range_start_seconds, self._range_end_seconds)
        if end - start <= 0:
            QMessageBox.information(self, "Mốc không hợp lệ", "Khoảng A-B phải lớn hơn 0 giây.")
            return
        marker_text = f"{self._format_marker_time(start)}-{self._format_marker_time(end)}"
        self.split_mode_markers_radio.setChecked(True)
        self.split_marker_edit.setPlainText(marker_text)
        self.property_tabs.setCurrentIndex(1)
        self.append_log(f"Cắt A-B: {Path(path).name} từ {self._format_marker_time(start)} đến {self._format_marker_time(end)}")
        self._start_media_job(mode="split_markers", split_marker_text=marker_text)

    def _format_marker_time(self, seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours = total_ms // 3_600_000
        total_ms %= 3_600_000
        minutes = total_ms // 60_000
        total_ms %= 60_000
        secs = total_ms // 1000
        ms = total_ms % 1000
        if ms:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _update_preview_placeholder(self) -> None:
        items = self.file_list.selectedItems()
        if not items:
            self.media_player.stop()
            self.preview_stack.setCurrentWidget(self.preview_label)
            self.preview_label.show()
            self.video_widget.hide()
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("▶ Preview video\n\nChọn video để xem trước")
            self.preview_status_label.setText("Sẵn sàng xem trước")
            self.preview_meta_label.setText("Chưa có metadata")
            self.seek_slider.setValue(0)
            self._range_start_seconds = None
            self._range_end_seconds = None
            self._refresh_range_label()
            self.timeline_video_label.setText("V1  ┃██████████┃████████████████┃")
            self.timeline_audio_label.setText("A1  ┃~~~~~~~~~~┃~~~~~~~~~~~~~~~~┃")
            return
        path = items[0].data(Qt.UserRole)
        name = Path(path).name if path else "video"
        self.preview_stack.setCurrentWidget(self.preview_label)
        self.preview_label.show()
        self.video_widget.hide()
        thumb_path = self._thumbnail_path_by_video.get(path or "")
        if thumb_path:
            pixmap = QPixmap(thumb_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.preview_label.setPixmap(scaled)
                self.preview_label.setText("")
            else:
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText(f"▶ Preview video\n\n{name}")
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(f"▶ Preview video\n\n{name}")
        self.preview_status_label.setText(f"Đã chọn: {name}")
        self._range_start_seconds = None
        self._range_end_seconds = None
        self._refresh_range_label()
        analysis = self._analysis_by_path.get(path or "")
        if analysis:
            video_stream = next((s for s in analysis.streams if s.codec_type == 'video'), None)
            audio_stream = next((s for s in analysis.streams if s.codec_type == 'audio'), None)
            width = video_stream.signature.get('width', '?') if video_stream else '?'
            height = video_stream.signature.get('height', '?') if video_stream else '?'
            fps = video_stream.signature.get('r_frame_rate', '?') if video_stream else '?'
            self.preview_meta_label.setText(
                f"{format_duration(analysis.duration)} • {format_size(analysis.size)} • {width}x{height} • FPS {fps} • V:{video_stream.codec_name if video_stream else '?'} A:{audio_stream.codec_name if audio_stream else '?'}"
            )
            self.seek_slider.setValue(0)
            self.time_label.setText(f"00:00 / {format_duration(analysis.duration)}")
        else:
            self.preview_meta_label.setText("Chưa có metadata")
        timeline_v, timeline_a = self._timeline_blocks_for_paths()
        self.timeline_video_label.setText(timeline_v)
        self.timeline_audio_label.setText(timeline_a)

    def _show_placeholder_message(self) -> None:
        QMessageBox.information(
            self,
            "Đang chuẩn bị",
            "Mục này đã có vị trí trên giao diện mới. Logic xử lý chi tiết sẽ được gắn ở bước nâng cấp tiếp theo.",
        )

    def _apply_editor_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget#appRoot { background: #0f172a; color: #e5eefb; }
            QFrame[role="panel"], QFrame#outputBar, QFrame#headerBar {
                background: #111827; border: 1px solid #263244; border-radius: 12px;
            }
            QLabel#appTitle { font-size: 20px; font-weight: 800; color: #f8fafc; }
            QLabel#logoBadge { background: #2563eb; color: white; border-radius: 8px; padding: 8px 12px; font-weight: 800; }
            QLabel[role="sectionTitle"] { color: #93c5fd; font-weight: 800; letter-spacing: .5px; }
            QLabel#previewCanvas {
                background: #020617; border: 1px solid #334155; border-radius: 14px;
                color: #94a3b8; font-size: 18px; font-weight: 700;
            }
            QLabel#hintLabel { color: #94a3b8; background: transparent; }
            QFrame#timelineBox { background: #0b1220; border: 1px solid #273449; border-radius: 10px; }
            QLabel#timelineTrack { background: #172033; border-radius: 8px; padding: 10px; color: #67e8f9; font-family: Consolas; }
            QPushButton {
                background: #1f2937; color: #e5eefb; border: 1px solid #334155;
                border-radius: 8px; padding: 8px 10px; font-weight: 700;
            }
            QPushButton:hover { background: #293548; }
            QPushButton[variant="primary"] { background: #2563eb; border-color: #3b82f6; color: white; }
            QPushButton[variant="danger"] { background: #7f1d1d; border-color: #ef4444; color: white; }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QListWidget {
                background: #0b1220; color: #e5eefb; border: 1px solid #334155; border-radius: 8px; padding: 6px;
            }
            QGroupBox { border: 1px solid #334155; border-radius: 10px; margin-top: 10px; padding: 10px; color: #e5eefb; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #93c5fd; }
            QTabWidget::pane { border: 1px solid #334155; border-radius: 8px; background: #0b1220; }
            QTabBar::tab { background: #1f2937; color: #cbd5e1; padding: 7px 9px; border-top-left-radius: 7px; border-top-right-radius: 7px; }
            QTabBar::tab:selected { background: #2563eb; color: white; }
            QProgressBar { background: #0b1220; border: 1px solid #334155; border-radius: 8px; text-align: center; color: white; }
            QProgressBar::chunk { background: #22c55e; border-radius: 8px; }

            QFrame#headerBar { background: #101b2d; border-color: #304563; }
            QFrame#workflowBar {
                background: #0c1625; border: 1px solid #263a54; border-radius: 12px;
            }
            QLabel#appSubtitle { color: #8291a8; font-size: 9.5pt; }
            QLabel#logoBadge {
                background: #2f6fed; color: white; border-radius: 10px;
                min-width: 44px; min-height: 44px; qproperty-alignment: AlignCenter;
                font-size: 16px; font-weight: 900;
            }
            QLabel#engineBadge {
                background: #102b29; border: 1px solid #1d514b; border-radius: 13px;
                color: #5eead4; padding: 6px 12px; font-weight: 700;
            }
            QLabel[role="workflowStep"] {
                background: #142238; border: 1px solid #2a405e; border-radius: 8px;
                color: #a9b7ca; padding: 8px 12px; font-weight: 700;
            }
            QLabel[role="mutedText"] { color: #718198; }
            QFrame#leftPanel, QFrame#rightPanel { background: #101b2b; }
            QFrame#outputBar { background: #101c2d; border-color: #315079; }
            QSplitter::handle { background: #0f172a; }
            QSplitter::handle:horizontal { width: 8px; }
            QSplitter::handle:vertical { height: 8px; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
            QPlainTextEdit:focus, QListWidget:focus { border-color: #4d88f5; }
            """
        )

    def _log_tool_status(self) -> None:
        ok, tools = check_required_tools()
        if ok:
            self.append_log(f"ffmpeg: {tools['ffmpeg']}")
            self.append_log(f"ffprobe: {tools['ffprobe']}")
            return
        self.append_log("Thiếu FFmpeg/FFprobe. Hãy cài FFmpeg và thêm thư mục bin vào PATH.")
        self.append_log(f"ffmpeg: {tools.get('ffmpeg') or 'không tìm thấy'}")
        self.append_log(f"ffprobe: {tools.get('ffprobe') or 'không tìm thấy'}")

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn video",
            "",
            "Video files (*.mp4 *.mov *.mkv *.m4v *.avi *.ts *.mts *.m2ts *.webm);;All files (*.*)",
        )
        if not files:
            return
        for path in files:
            resolved = str(Path(path).resolve())
            self._path_order.append(resolved)
        self._mark_analysis_dirty()

    def _file_tile_caption(self, path: str) -> str:
        """Một dòng dưới thumbnail (elide) — tránh chồng chữ khi IconMode + nhiều dòng."""
        fm = QFontMetrics(self.file_list.font())
        return fm.elidedText(
            Path(path).name,
            Qt.TextElideMode.ElideMiddle,
            max(80, self._file_tile_text_width),
        )

    def _file_tile_tooltip(self, path: str) -> str:
        lines = [path]
        if path in self._analysis_by_path:
            lines.append(summarize_file(self._analysis_by_path[path]))
        return "\n".join(lines)

    def _create_file_list_item(self, path: str) -> QListWidgetItem:
        item = QListWidgetItem(self._file_tile_caption(path))
        item.setIcon(self._get_placeholder_icon())
        item.setSizeHint(self._file_tile_cell)
        item.setTextAlignment(int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop))
        item.setData(Qt.UserRole, path)
        item.setToolTip(self._file_tile_tooltip(path))
        return item

    def _get_placeholder_icon(self) -> QIcon:
        if self._placeholder_icon:
            return self._placeholder_icon

        pixmap = QPixmap(150, 84)
        pixmap.fill(QColor("#172033"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#2dd4bf"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(62, 27), QPoint(62, 57), QPoint(92, 42)]))
        painter.end()
        self._placeholder_icon = QIcon(pixmap)
        return self._placeholder_icon

    def remove_selected(self) -> None:
        to_remove = {item.data(Qt.UserRole) for item in self.file_list.selectedItems()}
        to_remove.discard(None)
        if not to_remove:
            return
        remove_keys = {path_key(p) for p in to_remove if p}
        self._path_order = [p for p in self._path_order if path_key(p) not in remove_keys]
        self._mark_analysis_dirty()

    def move_selected_up(self) -> None:
        rows = sorted({self.file_list.row(item) for item in self.file_list.selectedItems()})
        for row in rows:
            if row <= 0:
                continue
            item = self.file_list.takeItem(row)
            self.file_list.insertItem(row - 1, item)
            item.setSelected(True)
        if rows:
            self._commit_visible_order_to_path_order()

    def move_selected_down(self) -> None:
        rows = sorted(
            {self.file_list.row(item) for item in self.file_list.selectedItems()},
            reverse=True,
        )
        for row in rows:
            if row >= self.file_list.count() - 1:
                continue
            item = self.file_list.takeItem(row)
            self.file_list.insertItem(row + 1, item)
            item.setSelected(True)
        if rows:
            self._commit_visible_order_to_path_order()

    def randomize_visible_order(self) -> None:
        paths = self._paths_for_current_sidebar()
        if len(paths) < 2:
            return

        shuffled = list(paths)
        for _attempt in range(8):
            random.shuffle(shuffled)
            if shuffled != paths:
                break
        if shuffled == paths:
            shuffled.reverse()

        idx = self._sidebar_stream_index()
        if idx == _SIDEBAR_ALL or not self._sidebar_shows_streams():
            self._path_order = shuffled
            scope = "toàn bộ danh sách"
        else:
            self._apply_stream_reorder(idx, shuffled)
            scope = f"luồng {idx + 1}"
        self.append_log(f"Đã random thứ tự nối cho {scope}.")
        self._invalidate_analysis_after_reorder()

    def _rows_moved(self, *_args: object) -> None:
        self._commit_visible_order_to_path_order()

    def _sidebar_shows_streams(self) -> bool:
        return self.stream_sidebar_frame.isVisible() and self.stream_sidebar.count() > 0

    def _sidebar_stream_index(self) -> int:
        """-1 = xem tất cả; >=0 = chỉ số luồng (0-based)."""
        if not self._sidebar_shows_streams():
            return _SIDEBAR_ALL
        item = self.stream_sidebar.currentItem()
        if not item:
            return _SIDEBAR_ALL
        role = item.data(Qt.UserRole)
        if role == _SIDEBAR_ALL:
            return _SIDEBAR_ALL
        if isinstance(role, int) and role >= 0:
            return role
        return _SIDEBAR_ALL

    def _paths_for_current_sidebar(self) -> list[str]:
        idx = self._sidebar_stream_index()
        if idx == _SIDEBAR_ALL:
            return list(self._path_order)
        groups = self._active_stream_groups()
        if idx >= len(groups):
            return list(self._path_order)
        group_keys = {path_key(p) for p in groups[idx]}
        return [p for p in self._path_order if path_key(p) in group_keys]

    def _refresh_file_list_display(self) -> None:
        self.file_list.clear()
        for path in self._paths_for_current_sidebar():
            self.file_list.addItem(self._create_file_list_item(path))
        self._update_file_panel_title()
        self._update_file_list_drag_policy()
        if self._last_report is not None:
            self._highlight_incompatible_files(self._last_report)
        visible = self._paths_for_current_sidebar()
        if visible:
            self._queue_thumbnails(visible)
        self._update_preview_placeholder()

    def _update_file_panel_title(self) -> None:
        n = len(self._path_order)
        idx = self._sidebar_stream_index()
        if idx == _SIDEBAR_ALL or not self._sidebar_shows_streams():
            self.file_panel_title.setText(f"Danh sách video — tất cả ({n} file)")
            return
        self.file_panel_title.setText(f"File trong luồng {idx + 1} — {len(self._paths_for_current_sidebar())} file")

    def _update_file_list_drag_policy(self) -> None:
        """Luôn cho kéo thả nội bộ: đổi thứ tự toàn danh sách hoặc chỉ trong luồng đang xem."""
        self.file_list.setDragEnabled(True)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDropIndicatorShown(True)
        self.file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def _active_stream_groups(self) -> list[list[str]]:
        if self._last_report and self._last_report.stream_compatible_groups:
            return self._last_report.stream_compatible_groups
        return self._stream_groups_cache

    def _apply_stream_reorder(self, stream_idx: int, new_order: list[str]) -> None:
        groups = self._active_stream_groups()
        if stream_idx < 0 or stream_idx >= len(groups):
            self._path_order = new_order
            return
        group_keys = {path_key(p) for p in groups[stream_idx]}
        insert_at: int | None = None
        rest: list[str] = []
        for path in self._path_order:
            if path_key(path) in group_keys:
                if insert_at is None:
                    insert_at = len(rest)
                continue
            rest.append(path)
        if insert_at is None:
            self._path_order = new_order + rest
            return
        self._path_order = rest[:insert_at] + new_order + rest[insert_at:]

    def _commit_visible_order_to_path_order(self) -> None:
        visible = [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]
        idx = self._sidebar_stream_index()
        if idx == _SIDEBAR_ALL or not self._sidebar_shows_streams():
            self._path_order = visible
        else:
            self._apply_stream_reorder(idx, visible)
        self._invalidate_analysis_after_reorder()

    def _invalidate_analysis_after_reorder(self) -> None:
        self._last_report = None
        self._last_signature_paths = []
        self._refresh_file_list_display()
        self.status_label.setText("Thứ tự đã đổi. Hãy phân tích lại.")
        self._update_buttons()

    def _rebuild_stream_sidebar_from_report(self, report: CompatibilityReport) -> None:
        self.stream_sidebar.blockSignals(True)
        self.stream_sidebar.clear()
        groups = report.stream_compatible_groups
        if len(groups) <= 1:
            self.stream_sidebar_frame.setVisible(False)
            self.stream_sidebar.blockSignals(False)
            self.file_splitter.setSizes([1000, 3000])
            self._refresh_file_list_display()
            return

        self.stream_sidebar_frame.setVisible(True)
        duration_by_path = {item.path: item.duration for item in report.files}
        all_item = QListWidgetItem(f"Tất cả\n{len(self._path_order)} file")
        all_item.setData(Qt.UserRole, _SIDEBAR_ALL)
        all_item.setToolTip("Xem và sắp xếp toàn bộ danh sách theo thứ tự nối chung.")
        self.stream_sidebar.addItem(all_item)

        for stream_idx, group_paths in enumerate(groups):
            total_sec = sum(duration_by_path.get(p, 0.0) for p in group_paths)
            hint = ""
            if group_paths:
                first = group_paths[0]
                if first in self._analysis_by_path:
                    hint = summarize_file(self._analysis_by_path[first])
                    if len(hint) > 72:
                        hint = hint[:69] + "…"
            lines = [
                f"Luồng {stream_idx + 1}",
                f"{len(group_paths)} file — ~{format_duration(total_sec)}",
            ]
            if hint:
                lines.append(hint)
            s_item = QListWidgetItem("\n".join(lines))
            s_item.setData(Qt.UserRole, stream_idx)
            s_item.setToolTip("\n".join(Path(p).name for p in group_paths))
            self.stream_sidebar.addItem(s_item)

        self.stream_sidebar.setCurrentRow(0)
        self.stream_sidebar.blockSignals(False)
        self.file_splitter.setSizes([268, 2000])
        self._refresh_file_list_display()

    def _on_stream_sidebar_row_changed(self, row: int) -> None:
        if row < 0:
            return
        self._refresh_file_list_display()
        self._update_buttons()

    def choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục lưu output",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if path:
            self.output_edit.setText(path)

    def start_analysis(self) -> None:
        paths = self._current_paths()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm ít nhất 1 file video.")
            return

        self._set_busy(True, mode="analysis")
        self.status_label.setText("Đang phân tích...")
        self.append_log("Bắt đầu phân tích bằng ffprobe.")

        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(paths)
        self._analysis_worker.moveToThread(self._analysis_thread)

        self._analysis_thread.started.connect(self._analysis_worker.run)
        self._analysis_worker.log.connect(self.append_log)
        self._analysis_worker.finished.connect(self._analysis_finished)
        self._analysis_worker.failed.connect(self._analysis_failed)
        self._analysis_worker.finished.connect(self._analysis_thread.quit)
        self._analysis_worker.failed.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._analysis_worker.deleteLater)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)
        self._analysis_thread.finished.connect(self._clear_analysis_thread)
        self._analysis_thread.start()

    def _analysis_finished(self, report: CompatibilityReport) -> None:
        self._last_report = report
        self._last_signature_paths = list(self._path_order)
        self._analysis_by_path = {item.path: item for item in report.files}
        self._stream_groups_cache = [list(g) for g in report.stream_compatible_groups]
        self._rebuild_stream_sidebar_from_report(report)
        self._refresh_file_summaries(report)
        total_size = sum(item.size for item in report.files if item.size > 0)
        size_hint = f" — Dung lượng: {format_size(total_size)}" if total_size > 0 else ""
        self.status_label.setText(
            f"{report.message} Tổng thời lượng: {format_duration(report.total_duration)}{size_hint}"
        )
        self.append_log(report.message)
        if report.files:
            self.append_log(f"File chuẩn so sánh: {Path(report.files[0].path).name}")
        duration_by_path = {item.path: item.duration for item in report.files}
        self.append_log(
            "Luồng tương thích (cùng chữ ký stream — mỗi luồng có thể nối nhanh riêng, "
            "thứ tự file trong luồng = thứ tự danh sách):"
        )
        for index, group_paths in enumerate(report.stream_compatible_groups, start=1):
            names = ", ".join(Path(p).name for p in group_paths)
            total_sec = sum(duration_by_path.get(p, 0.0) for p in group_paths)
            self.append_log(
                f"  Luồng {index} — {len(group_paths)} file, ~{format_duration(total_sec)}: {names}"
            )
        self.append_log("Phân nhóm theo độ phân giải (thứ tự nối trong từng nhóm = thứ tự danh sách):")
        for label, analyses in group_analyses_by_resolution(report.files):
            names = ", ".join(Path(item.path).name for item in analyses)
            self.append_log(f"  • {label}: {len(analyses)} file — {names}")
        self.append_log(f"Tổng thời lượng: {format_duration(report.total_duration)}")
        if report.issues:
            if report.incompatible_paths:
                names = ", ".join(Path(path).name for path in report.incompatible_paths)
                self.append_log(f"File nên xóa khỏi nhóm nối nhanh: {names}")
            self.append_log("Các khác biệt phát hiện:")
            for issue in report.issues:
                self.append_log(f"- {issue}")
        self._highlight_incompatible_files(report)
        self._update_preview_placeholder()
        self._set_busy(False)

    def _analysis_failed(self, message: str) -> None:
        self._last_report = None
        self._stream_groups_cache.clear()
        self.stream_sidebar.blockSignals(True)
        self.stream_sidebar.clear()
        self.stream_sidebar_frame.setVisible(False)
        self.stream_sidebar.blockSignals(False)
        self._refresh_file_list_display()
        self.status_label.setText("Phân tích thất bại.")
        self.append_log(f"Lỗi phân tích: {message}")
        QMessageBox.critical(self, "Lỗi phân tích", message)
        self._set_busy(False)

    def _clear_analysis_thread(self) -> None:
        self._analysis_thread = None
        self._analysis_worker = None
        self._update_buttons()

    def _refresh_file_summaries(self, report: CompatibilityReport) -> None:
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            path = item.data(Qt.UserRole)
            item.setText(self._file_tile_caption(path))
            item.setToolTip(self._file_tile_tooltip(path))

    def _highlight_incompatible_files(self, report: CompatibilityReport) -> None:
        incompatible_keys = {path_key(p) for p in report.incompatible_paths}
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            path = item.data(Qt.UserRole)
            if path and path_key(path) in incompatible_keys:
                item.setBackground(QBrush(QColor("#4c1d2f")))
                item.setForeground(QBrush(QColor("#fecdd3")))
            else:
                item.setBackground(QBrush())
                item.setForeground(QBrush())
        self.remove_incompatible_button.setEnabled(bool(incompatible_keys))

    def _queue_thumbnails(self, paths: list[str]) -> None:
        queued = set(self._thumbnail_queue)
        for path in paths:
            if path not in queued:
                self._thumbnail_queue.append(path)
                queued.add(path)
        if not self._thumbnail_thread:
            self._start_thumbnail_worker()

    def _start_thumbnail_worker(self) -> None:
        if not self._thumbnail_queue:
            return

        paths = self._thumbnail_queue
        self._thumbnail_queue = []
        self._thumbnail_thread = QThread(self)
        self._thumbnail_worker = ThumbnailWorker(paths)
        self._thumbnail_worker.moveToThread(self._thumbnail_thread)

        self._thumbnail_thread.started.connect(self._thumbnail_worker.run)
        self._thumbnail_worker.log.connect(self.append_log)
        self._thumbnail_worker.thumbnail_ready.connect(self._set_thumbnail)
        self._thumbnail_worker.finished.connect(self._thumbnail_thread.quit)
        self._thumbnail_thread.finished.connect(self._thumbnail_worker.deleteLater)
        self._thumbnail_thread.finished.connect(self._thumbnail_thread.deleteLater)
        self._thumbnail_thread.finished.connect(self._thumbnail_finished)
        self._thumbnail_thread.start()

    def _thumbnail_finished(self) -> None:
        self._thumbnail_thread = None
        self._thumbnail_worker = None
        self._start_thumbnail_worker()

    def _set_thumbnail(self, video_path: str, thumbnail_path: str) -> None:
        pixmap = QPixmap(thumbnail_path)
        if pixmap.isNull():
            return
        self._thumbnail_path_by_video[video_path] = thumbnail_path
        icon = QIcon(pixmap)
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            row_path = item.data(Qt.UserRole)
            if row_path and same_path(row_path, video_path):
                item.setIcon(icon)
        for item in self.file_list.selectedItems():
            row_path = item.data(Qt.UserRole)
            if row_path and same_path(row_path, video_path):
                self._update_preview_placeholder()
                break

    def remove_incompatible_files(self) -> None:
        if not self._last_report or not self._last_report.incompatible_paths:
            return
        incompatible_keys = {path_key(p) for p in self._last_report.incompatible_paths}
        removed_count = sum(1 for path in self._path_order if path_key(path) in incompatible_keys)
        self._path_order = [p for p in self._path_order if path_key(p) not in incompatible_keys]
        self.append_log(f"Đã xóa {removed_count} file không thuộc nhóm tương thích lớn nhất.")
        self._mark_analysis_dirty()

    def start_concat(self) -> None:
        self._start_concat_job(safe_mode=False)

    def start_safe_concat(self) -> None:
        self._start_concat_job(safe_mode=True)

    def _start_concat_job(self, *, safe_mode: bool) -> None:
        paths = self._current_paths()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm file video trước khi nối.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        output_folder = Path(output_dir).resolve()
        if output_folder.exists() and not output_folder.is_dir():
            QMessageBox.critical(self, "Thư mục không hợp lệ", "Đường dẫn output phải là thư mục.")
            return
        output_path = self._make_output_path(output_folder)
        if any(same_path(path, output_path) for path in paths):
            QMessageBox.critical(self, "Output không hợp lệ", "Output không được trùng file input.")
            return

        if not self._last_report or self._last_signature_paths != paths:
            QMessageBox.warning(
                self,
                "Chưa phân tích",
                "Hãy bấm Phân tích sau khi thêm hoặc đổi thứ tự file.",
            )
            return
        if not self._last_report.is_compatible:
            QMessageBox.critical(
                self,
                "Không thể nối toàn danh sách",
                "Các file không cùng một luồng tương thích — không thể gộp thành một file bằng stream copy. "
                "Dùng «Nối tất cả các luồng» hoặc xem log chi tiết.",
            )
            return

        mode_label = "nối an toàn" if safe_mode else "nối siêu nhanh"
        self._concat_status_hint = "Đang nối an toàn" if safe_mode else "Đang nối toàn danh sách"
        self._set_busy(True, mode="concat")
        self.status_label.setText(f"Đang {mode_label}...")
        self.append_log(
            "Bắt đầu nối an toàn (remux MKV tạm + genpts + stream copy) bằng FFmpeg."
            if safe_mode
            else "Bắt đầu nối toàn danh sách (stream copy) bằng FFmpeg."
        )
        self.append_log(f"Output: {output_path}")

        durations = self._durations_for_paths(paths)
        expected = self._expected_duration_from_durations(durations)
        self._concat_thread = QThread(self)
        self._concat_worker = ConcatWorker(
            paths,
            str(output_path),
            expected_duration=expected,
            file_durations=durations,
            safe_mode=safe_mode,
        )
        self._concat_worker.moveToThread(self._concat_thread)

        self._concat_thread.started.connect(self._concat_worker.run)
        self._concat_worker.log.connect(self.append_log)
        self._concat_worker.progress.connect(self._concat_progress)
        self._concat_worker.finished.connect(self._concat_finished)
        self._concat_worker.finished.connect(self._concat_thread.quit)
        self._concat_thread.finished.connect(self._concat_worker.deleteLater)
        self._concat_thread.finished.connect(self._concat_thread.deleteLater)
        self._concat_thread.finished.connect(self._clear_concat_thread)
        self._concat_thread.start()

    def _can_concat_all_streams(self) -> bool:
        if not self._last_report or not self._path_order:
            return False
        if self._last_signature_paths != list(self._path_order):
            return False
        return any(len(group) >= 2 for group in self._last_report.stream_compatible_groups)

    def start_all_streams_concat(self) -> None:
        paths = self._current_paths()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm file video trước khi nối.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        output_folder = Path(output_dir).resolve()
        if output_folder.exists() and not output_folder.is_dir():
            QMessageBox.critical(self, "Thư mục không hợp lệ", "Đường dẫn output phải là thư mục.")
            return

        if not self._last_report or self._last_signature_paths != paths:
            QMessageBox.warning(
                self,
                "Chưa phân tích",
                "Hãy bấm Phân tích sau khi thêm hoặc đổi thứ tự file.",
            )
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        groups = self._last_report.stream_compatible_groups
        jobs: list[tuple[str, list[str], str, float | None, list[float | None] | None]] = []
        skipped_single = 0

        for stream_idx, group_paths in enumerate(groups):
            ordered = path_order_intersect_group(self._path_order, group_paths)
            if len(ordered) < 2:
                if len(group_paths) >= 2:
                    self.append_log(
                        f"Bỏ qua Luồng {stream_idx + 1}: phân tích có {len(group_paths)} đường dẫn nhưng "
                        f"trong danh sách chỉ khớp {len(ordered)} file — hãy Phân tích lại hoặc kiểm tra "
                        f"đường dẫn (ổ, tên, liên kết)."
                    )
                skipped_single += 1
                continue
            label = f"Luồng {stream_idx + 1}"
            out_path = self._make_output_path_for_stream(output_folder, stream_idx + 1, stamp)
            if any(same_path(p, out_path) for p in paths):
                QMessageBox.critical(
                    self,
                    "Output không hợp lệ",
                    f"File output trùng input cho {label}. Chọn thư mục khác.",
                )
                return
            durations = self._durations_for_paths(ordered)
            expected = self._expected_duration_from_durations(durations)
            jobs.append((label, ordered, str(out_path), expected, durations))

        if skipped_single:
            self.append_log(f"Đã bỏ qua {skipped_single} luồng chỉ có 1 file (không nối).")

        if not jobs:
            QMessageBox.information(
                self,
                "Không có luồng để nối",
                "Không có luồng tương thích nào có từ 2 file trở lên.",
            )
            return

        self._concat_status_hint = "Đang nối tất cả các luồng"
        self.append_log(f"Bắt đầu nối tất cả các luồng (stream copy): {len(jobs)} file output.")
        self._set_busy(True, mode="concat")
        self.status_label.setText("Đang nối tất cả các luồng...")

        self._concat_thread = QThread(self)
        self._concat_worker = StreamConcatWorker(jobs, continue_on_error=True)
        self._concat_worker.moveToThread(self._concat_thread)

        self._concat_thread.started.connect(self._concat_worker.run)
        self._concat_worker.log.connect(self.append_log)
        self._concat_worker.progress.connect(self._concat_progress)
        self._concat_worker.finished.connect(self._concat_finished)
        self._concat_worker.finished.connect(self._concat_thread.quit)
        self._concat_thread.finished.connect(self._concat_worker.deleteLater)
        self._concat_thread.finished.connect(self._concat_thread.deleteLater)
        self._concat_thread.finished.connect(self._clear_concat_thread)
        self._concat_thread.start()

    def _make_output_path_for_stream(self, output_folder: Path, stream_one_based: int, stamp: str) -> Path:
        tag = f"luong{stream_one_based:02d}"
        extension = self._output_extension()
        candidate = output_folder / f"VIDEO_{tag}_{stamp}.{extension}"
        suffix = 2
        while candidate.exists():
            candidate = output_folder / f"VIDEO_{tag}_{stamp}_{suffix}.{extension}"
            suffix += 1
        return candidate

    def stop_concat(self) -> None:
        if self._concat_worker:
            self._concat_worker.stop()
        if self._media_worker:
            self._media_worker.stop()

    def _concat_progress(self, value: str) -> None:
        hint = self._concat_status_hint
        if value.isdigit():
            try:
                seconds = int(value) / 1_000_000
                self.status_label.setText(f"{hint}… {format_duration(seconds)}")
                if self._concat_worker and getattr(self._concat_worker, 'expected_duration', None):
                    expected = getattr(self._concat_worker, 'expected_duration', None) or 0
                    if expected > 0:
                        percent = max(0, min(100, int(seconds * 100 / expected)))
                        self.progress_bar.setValue(percent)
                return
            except ValueError:
                pass
        self.status_label.setText(f"{hint}… {value}")

    def _concat_finished(self, ok: bool, message: str) -> None:
        self.append_log(message)
        self.status_label.setText("Hoàn tất." if ok else "Nối video thất bại hoặc đã dừng.")
        self.progress_bar.setValue(100 if ok else 0)
        self._set_busy(False)
        if ok:
            QMessageBox.information(self, "Hoàn tất", message)
        else:
            QMessageBox.warning(self, "Chưa hoàn tất", message)

    def _clear_concat_thread(self) -> None:
        self._concat_thread = None
        self._concat_worker = None
        self._concat_status_hint = "Đang nối video"
        self._update_buttons()

    def _paths_for_media_action(self) -> list[str]:
        selected = [item.data(Qt.UserRole) for item in self.file_list.selectedItems() if item.data(Qt.UserRole)]
        return selected or self._current_paths()

    def start_split_videos(self) -> None:
        paths = self._paths_for_media_action()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm hoặc chọn video trước khi băm nhỏ.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        if self.split_mode_count_radio.isChecked():
            self._start_media_job(
                mode="split_count",
                split_count=self.split_count_spin.value(),
                split_accurate=self.split_accurate_check.isChecked(),
            )
            return
        if self.split_mode_markers_radio.isChecked():
            self._start_media_job(mode="split_markers", split_marker_text=self.split_marker_edit.toPlainText())
            return
        self._start_media_job(
            mode="split_duration",
            split_seconds=self.split_seconds_spin.value(),
            split_accurate=self.split_accurate_check.isChecked(),
        )

    def start_extract_audio(self) -> None:
        paths = self._paths_for_media_action()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm hoặc chọn video trước khi tách audio.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        if self.audio_ai_radio.isChecked():
            if not self.demucs_available:
                QMessageBox.information(
                    self,
                    "AI tách giọng/nhạc nền",
                    "Demucs chưa được cài. Cài bằng: python -m pip install demucs. Sau đó tool sẽ chạy mode AI thật.",
                )
                return
            self._start_media_job(mode="audio_ai")
            return
        audio_format = self.audio_format_combo.currentText()
        self.append_log(
            "Lưu ý: chức năng này tách audio hiện có trong video. Nếu cần tách riêng giọng và nhạc nền bằng AI thì phải tích hợp module nặng hơn như Demucs/UVR."
        )
        self._start_media_job(mode="audio", audio_format=audio_format)

    def start_transform_video(self) -> None:
        paths = self._paths_for_media_action()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm hoặc chọn video trước khi zoom/crop/pad.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        pos_x = self.pos_x_spin.value() if hasattr(self, "pos_x_spin") else 0
        pos_y = self.pos_y_spin.value() if hasattr(self, "pos_y_spin") else 0
        self._start_media_job(
            mode="transform",
            zoom_percent=self.zoom_slider.value(),
            pos_x=pos_x,
            pos_y=pos_y,
        )

    def start_apply_effects(self) -> None:
        paths = self._paths_for_media_action()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm hoặc chọn video trước khi áp dụng hiệu ứng.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        effect_flags = [key for key, checkbox in self.effect_checks.items() if checkbox.isChecked()]
        if not effect_flags:
            QMessageBox.information(self, "Chưa chọn hiệu ứng", "Hãy chọn ít nhất một hiệu ứng trong tab Video.")
            return
        self._start_media_job(mode="effects", effect_flags=effect_flags)

    def start_normalize_videos(self) -> None:
        paths = self._paths_for_media_action()
        output_dir = self.output_edit.text().strip()
        if not paths:
            QMessageBox.warning(self, "Thiếu file", "Hãy thêm hoặc chọn video trước khi chuẩn hóa.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Hãy chọn thư mục lưu output.")
            return
        resolution = self.normalize_resolution_combo.currentText().replace(" ", "")
        width, height = 1920, 1080
        if "x" in resolution.lower() and resolution.lower() != "giữnguyên":
            try:
                width_text, height_text = resolution.lower().split("x", 1)
                width, height = int(width_text), int(height_text)
            except ValueError:
                pass
        fit_mode = "keep"
        if self.normalize_crop_radio.isChecked():
            fit_mode = "crop"
        elif self.normalize_pad_radio.isChecked():
            fit_mode = "pad"
        self._start_media_job(
            mode="normalize",
            normalize_width=width,
            normalize_height=height,
            normalize_fps=self.normalize_fps_combo.currentText(),
            normalize_codec=self.normalize_codec_combo.currentText(),
            normalize_format=self.normalize_format_combo.currentText(),
            normalize_bitrate=self.normalize_bitrate_combo.currentText(),
            normalize_fit_mode=fit_mode,
        )

    def _start_media_job(self, *, mode: str, split_seconds: int = 0, split_count: int = 0, split_marker_text: str = "", split_accurate: bool = False, audio_format: str = "mp3", normalize_width: int = 1920, normalize_height: int = 1080, normalize_fps: str = "30", normalize_codec: str = "H.264", normalize_format: str = "MP4", normalize_bitrate: str = "Tự động", normalize_fit_mode: str = "keep", effect_flags: list[str] | None = None, zoom_percent: int = 100, pos_x: int = 0, pos_y: int = 0) -> None:
        paths = self._paths_for_media_action()
        output_dir = self.output_edit.text().strip()
        if not paths or not output_dir:
            return
        hint_map = {
            "split_duration": "Đang băm nhỏ video theo thời gian",
            "split_count": "Đang băm nhỏ video theo số lượng",
            "split_markers": "Đang băm nhỏ video theo mốc tùy chọn",
            "normalize": "Đang chuẩn hóa video",
            "effects": "Đang áp dụng hiệu ứng video",
            "transform": "Đang zoom/crop/pad video",
            "audio_ai": "Đang AI tách giọng / nhạc nền",
            "audio": "Đang tách audio / nhạc nền",
        }
        self._media_status_hint = hint_map.get(mode, "Đang xử lý media")
        self.progress_bar.setValue(5)
        self._set_busy(True, mode="media")
        self.status_label.setText(f"{self._media_status_hint}...")
        self._media_thread = QThread(self)
        self._media_worker = MediaWorker(
            paths,
            output_dir,
            mode=mode,
            split_seconds=split_seconds,
            split_count=split_count,
            split_accurate=split_accurate,
            split_marker_text=split_marker_text,
            audio_format=audio_format,
            normalize_width=normalize_width,
            normalize_height=normalize_height,
            normalize_fps=normalize_fps,
            normalize_codec=normalize_codec,
            normalize_format=normalize_format,
            normalize_bitrate=normalize_bitrate,
            normalize_fit_mode=normalize_fit_mode,
            effect_flags=effect_flags,
            zoom_percent=zoom_percent,
            pos_x=pos_x,
            pos_y=pos_y,
        )
        self._media_worker.moveToThread(self._media_thread)
        self._media_thread.started.connect(self._media_worker.run)
        self._media_worker.log.connect(self.append_log)
        self._media_worker.finished.connect(self._media_finished)
        self._media_worker.finished.connect(self._media_thread.quit)
        self._media_thread.finished.connect(self._media_worker.deleteLater)
        self._media_thread.finished.connect(self._media_thread.deleteLater)
        self._media_thread.finished.connect(self._clear_media_thread)
        self._media_thread.start()

    def _media_finished(self, ok: bool, message: str) -> None:
        self.append_log(message)
        self.status_label.setText("Hoàn tất." if ok else "Xử lý media chưa hoàn tất.")
        self.progress_bar.setValue(100 if ok else 0)
        self._set_busy(False)
        if ok:
            QMessageBox.information(self, "Hoàn tất", message)
        else:
            QMessageBox.warning(self, "Chưa hoàn tất", message)

    def _clear_media_thread(self) -> None:
        self._media_thread = None
        self._media_worker = None
        self._media_status_hint = "Đang xử lý media"
        self._update_buttons()

    def open_output_folder(self) -> None:
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "Thiếu thư mục lưu", "Chưa chọn thư mục lưu output.")
            return
        folder = Path(output_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _make_output_path(self, output_folder: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = self._output_extension()
        candidate = output_folder / f"VIDEO_{stamp}.{extension}"
        suffix = 2
        while candidate.exists():
            candidate = output_folder / f"VIDEO_{stamp}_{suffix}.{extension}"
            suffix += 1
        return candidate

    def _output_extension(self) -> str:
        extension = self.output_format_combo.currentData()
        if isinstance(extension, str) and extension in {"mp4", "mkv"}:
            return extension
        return "mkv"

    def _durations_for_paths(self, paths: list[str]) -> list[float | None]:
        durations: list[float | None] = []
        for path in paths:
            analysis = self._analysis_by_path.get(path)
            if analysis is None:
                analysis = self._analysis_by_path.get(str(Path(path).resolve()))
            duration = analysis.duration if analysis and analysis.duration > 0 else None
            durations.append(duration)
        return durations

    def _expected_duration_from_durations(self, durations: list[float | None]) -> float | None:
        values = [duration for duration in durations if duration and duration > 0]
        if not values:
            return None
        return sum(values)

    def _confirm_long_mp4(
        self,
        expected_duration: float | None,
        *,
        context: str = "output",
    ) -> bool:
        if self._output_extension() != "mp4":
            return True
        if not expected_duration or expected_duration < 24 * 3600:
            return True
        if expected_duration >= 100 * 3600:
            QMessageBox.critical(
                self,
                "Không xuất MP4 trên 100 tiếng",
                f"{context} dài khoảng {format_duration(expected_duration)}.\n"
                "MP4 rất dài dễ lỗi khi mux/finalize và có thể làm FFmpeg treo lâu. "
                "Hãy đổi định dạng sang MKV rồi chạy lại để giữ nguyên stream và thời lượng gốc.",
            )
            return False

        answer = QMessageBox.question(
            self,
            "MP4 rất dài",
            f"{context} dài khoảng {format_duration(expected_duration)}.\n"
            "MP4 trên 24 giờ có thể bị một số trình phát hiển thị sai duration "
            "hoặc tua không ổn định, ví dụ chỉ hiện phần dư sau mốc 24 giờ.\n\n"
            "Nên chọn định dạng MKV rồi chạy lại. Bạn vẫn muốn tiếp tục xuất MP4?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _current_paths(self) -> list[str]:
        return list(self._path_order)

    def reset_session(self) -> None:
        if self._analysis_thread or self._concat_thread or self._media_thread:
            QMessageBox.information(
                self,
                "Đang bận",
                "Đang phân tích/nối/xử lý media. Chờ xong rồi bấm «Làm mới» lại.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Làm mới",
            "Xóa toàn bộ danh sách file, kết quả phân tích và nội dung log?\n"
            "Thư mục lưu output được giữ nguyên.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._path_order.clear()
        self._thumbnail_queue.clear()
        if self._thumbnail_worker:
            self._thumbnail_worker.stop()
        self.log_edit.clear()
        self._mark_analysis_dirty()
        self.status_label.setText("Chưa có file. Hãy bấm «Thêm file».")
        self.append_log("Đã làm mới — danh sách và log đã xóa.")
        self._log_tool_status()

    def _mark_analysis_dirty(self) -> None:
        self._last_report = None
        self._last_signature_paths = []
        self._analysis_by_path.clear()
        self._stream_groups_cache.clear()
        self.stream_sidebar.blockSignals(True)
        self.stream_sidebar.clear()
        self.stream_sidebar_frame.setVisible(False)
        self.stream_sidebar.blockSignals(False)
        self.status_label.setText("Danh sách đã thay đổi. Hãy phân tích lại.")
        self._refresh_file_list_display()
        self._update_buttons()

    def _set_busy(self, busy: bool, mode: str = "") -> None:
        is_concat = busy and mode in {"concat", "media"}
        self.file_list.setEnabled(not busy)
        self.stream_sidebar.setEnabled(not busy)
        for button in (
            self.add_button,
            self.remove_button,
            self.remove_incompatible_button,
            self.up_button,
            self.down_button,
            self.random_button,
            self.output_button,
            self.analyze_button,
            self.start_button,
            self.safe_concat_button,
            self.streams_concat_button,
            self.split_button,
            self.extract_audio_button,
            self.export_button,
            self.normalize_button,
            self.effects_button,
            self.zoom_button,
            self.cut_at_cursor_button,
            self.set_range_start_button,
            self.set_range_end_button,
            self.cut_range_button,
            self.reset_button,
        ):
            button.setEnabled(not busy)
        self.output_edit.setEnabled(not busy)
        self.output_format_combo.setEnabled(not busy)
        self.stop_button.setEnabled(is_concat)
        self.open_folder_button.setEnabled(not busy and bool(self.output_edit.text().strip()))

    def _update_buttons(self) -> None:
        busy = bool(self._analysis_thread or self._concat_thread or self._media_thread)
        if busy:
            return
        has_files = bool(self._path_order)
        has_output = bool(self.output_edit.text().strip())
        self.remove_button.setEnabled(has_files)
        self.remove_incompatible_button.setEnabled(
            bool(self._last_report and self._last_report.incompatible_paths)
        )
        self.up_button.setEnabled(has_files)
        self.down_button.setEnabled(has_files)
        self.random_button.setEnabled(len(self._paths_for_current_sidebar()) >= 2)
        self.analyze_button.setEnabled(has_files)
        self.start_button.setEnabled(has_files and has_output)
        self.export_button.setEnabled(has_files and has_output)
        self.safe_concat_button.setEnabled(has_files and has_output)
        self.streams_concat_button.setEnabled(
            has_files and has_output and self._can_concat_all_streams()
        )
        self.split_button.setEnabled(has_files and has_output)
        self.extract_audio_button.setEnabled(has_files and has_output)
        self.normalize_button.setEnabled(has_files and has_output)
        self.effects_button.setEnabled(has_files and has_output)
        self.zoom_button.setEnabled(has_files and has_output)
        self.cut_at_cursor_button.setEnabled(has_files and has_output)
        self.set_range_start_button.setEnabled(has_files and has_output)
        self.set_range_end_button.setEnabled(has_files and has_output)
        self.cut_range_button.setEnabled(has_files and has_output)
        self.stop_button.setEnabled(False)
        self.open_folder_button.setEnabled(has_output)

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{timestamp}] {message}")
