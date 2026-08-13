from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, Signal, Slot

from core.ffmpeg_tools import check_required_tools
from core.media_extra import (
    apply_video_effects,
    extract_audio,
    normalize_video,
    separate_vocals_background,
    split_video_by_count,
    split_video_by_duration,
    split_video_by_markers,
    transform_video_zoom,
)


class MediaWorker(QObject):
    log = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        paths: list[str],
        output_dir: str,
        *,
        mode: str,
        split_seconds: int = 0,
        split_count: int = 0,
        split_accurate: bool = False,
        audio_format: str = "mp3",
        normalize_width: int = 1920,
        normalize_height: int = 1080,
        split_marker_text: str = "",
        effect_flags: list[str] | None = None,
        zoom_percent: int = 100,
        pos_x: int = 0,
        pos_y: int = 0,
        normalize_fps: str = "30",
        normalize_codec: str = "H.264",
        normalize_format: str = "MP4",
        normalize_bitrate: str = "Tự động",
        normalize_fit_mode: str = "keep",
    ) -> None:
        super().__init__()
        self.paths = paths
        self.output_dir = output_dir
        self.mode = mode
        self.split_seconds = split_seconds
        self.split_count = split_count
        self.split_accurate = split_accurate
        self.audio_format = audio_format
        self.normalize_width = normalize_width
        self.normalize_height = normalize_height
        self.split_marker_text = split_marker_text
        self.effect_flags = effect_flags or []
        self.zoom_percent = zoom_percent
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.normalize_fps = normalize_fps
        self.normalize_codec = normalize_codec
        self.normalize_format = normalize_format
        self.normalize_bitrate = normalize_bitrate
        self.normalize_fit_mode = normalize_fit_mode
        self._stop_requested = False
        self._active_processes: list[subprocess.Popen[str]] = []

    def stop(self) -> None:
        self._stop_requested = True
        for process in list(self._active_processes):
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def _emit(self, message: str) -> None:
        self.log.emit(message)

    @Slot()
    def run(self) -> None:
        ok, tools = check_required_tools()
        if not ok:
            self.finished.emit(False, "Thiếu FFmpeg/FFprobe. Không thể xử lý media.")
            return

        ffmpeg_path = tools["ffmpeg"] or "ffmpeg"
        ffprobe_path = tools["ffprobe"] or "ffprobe"
        total = len(self.paths)
        if total == 0:
            self.finished.emit(False, "Không có file nào để xử lý.")
            return

        success = 0
        errors: list[str] = []
        for index, path in enumerate(self.paths, 1):
            if self._stop_requested:
                self.finished.emit(False, "Đã dừng theo yêu cầu.")
                return
            self._emit(f"[{index}/{total}] Xử lý: {path}")
            if self.mode == "split_duration":
                item_ok, msg = split_video_by_duration(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    ffprobe_path,
                    segment_seconds=max(1, int(self.split_seconds)),
                    accurate=self.split_accurate,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            elif self.mode == "split_count":
                item_ok, msg = split_video_by_count(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    ffprobe_path,
                    part_count=max(2, int(self.split_count)),
                    accurate=self.split_accurate,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            elif self.mode == "split_markers":
                item_ok, msg = split_video_by_markers(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    marker_text=self.split_marker_text,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            elif self.mode == "normalize":
                item_ok, msg = normalize_video(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    width=self.normalize_width,
                    height=self.normalize_height,
                    fps=self.normalize_fps,
                    codec=self.normalize_codec,
                    out_format=self.normalize_format,
                    bitrate=self.normalize_bitrate,
                    fit_mode=self.normalize_fit_mode,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            elif self.mode == "effects":
                item_ok, msg = apply_video_effects(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    effects=self.effect_flags,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            elif self.mode == "transform":
                item_ok, msg = transform_video_zoom(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    zoom_percent=self.zoom_percent,
                    pos_x=self.pos_x,
                    pos_y=self.pos_y,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            elif self.mode == "audio_ai":
                item_ok, msg = separate_vocals_background(
                    path,
                    self.output_dir,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            else:
                item_ok, msg = extract_audio(
                    path,
                    self.output_dir,
                    ffmpeg_path,
                    audio_format=self.audio_format,
                    emit_log=self._emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                )
            self._emit(msg)
            if item_ok:
                success += 1
            else:
                errors.append(msg)

        if errors:
            self.finished.emit(False, f"Hoàn tất một phần: thành công {success}/{total}. Lỗi đầu tiên: {errors[0]}")
            return

        mode_map = {
            "split_duration": "băm nhỏ video theo thời gian",
            "split_count": "băm nhỏ video theo số lượng",
            "split_markers": "băm nhỏ video theo mốc tùy chọn",
            "normalize": "chuẩn hóa video",
            "effects": "áp dụng hiệu ứng video",
            "transform": "zoom/crop/pad video",
            "audio_ai": "AI tách giọng/nhạc nền",
            "audio": "tách audio/nhạc nền",
        }
        self.finished.emit(True, f"Đã {mode_map.get(self.mode, 'xử lý media')} cho {success}/{total} file.")
