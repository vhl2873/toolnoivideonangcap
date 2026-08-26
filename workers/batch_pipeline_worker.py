from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, Signal, Slot

from core.batch_pipeline import process_voice_split_alternate_zoom_batch
from core.ffmpeg_tools import check_required_tools


class BatchPipelineWorker(QObject):
    """Bọc pipeline core/batch_pipeline.py (tách giọng/nhạc nền -> cắt đoạn -> zoom so le -> final.mp4)
    thành QObject chạy trong QThread, dùng chung cho mọi dự án — chỉ 1 pipeline duy nhất."""

    log = Signal(str)
    progress = Signal(int)
    status = Signal(dict)
    finished = Signal(bool, str)

    def __init__(self, paths: list[str], output_dir: str, **options: object) -> None:
        super().__init__()
        self.paths = paths
        self.output_dir = output_dir
        self.options = options
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

    @Slot()
    def run(self) -> None:
        ok, tools = check_required_tools()
        if not ok:
            self.finished.emit(False, "Thiếu FFmpeg/FFprobe. Không thể xử lý video.")
            return
        ffmpeg = tools["ffmpeg"] or "ffmpeg"
        ffprobe = tools["ffprobe"] or "ffprobe"
        try:
            success, message = process_voice_split_alternate_zoom_batch(
                self.paths,
                self.output_dir,
                ffmpeg,
                ffprobe,
                emit_log=self.log.emit,
                emit_progress=self.progress.emit,
                emit_status=self.status.emit,
                stop_check=lambda: self._stop_requested,
                active_processes=self._active_processes,
                **self.options,
            )
        except Exception as exc:
            self.finished.emit(False, str(exc))
            return
        self.finished.emit(success, message)
