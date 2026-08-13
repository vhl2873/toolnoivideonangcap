from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, Signal, Slot

from core.ffmpeg_tools import check_required_tools, run_concat_copy


class ConcatWorker(QObject):
    log = Signal(str)
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        paths: list[str],
        output_path: str,
        *,
        expected_duration: float | None = None,
        file_durations: list[float | None] | None = None,
        safe_mode: bool = False,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.output_path = output_path
        self.expected_duration = expected_duration
        self.file_durations = file_durations
        self.safe_mode = safe_mode
        self._stop_requested = False
        self._active_processes: list[subprocess.Popen[str]] = []

    def stop(self) -> None:
        self._stop_requested = True
        for process in list(self._active_processes):
            if process.poll() is None:
                self.log.emit("Đang dừng FFmpeg...")
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.log.emit("FFmpeg chưa dừng, ép tắt tiến trình...")
                    process.kill()

    @Slot()
    def run(self) -> None:
        try:
            _tools_ok, tools = check_required_tools()
            if not tools.get("ffmpeg"):
                raise RuntimeError(
                    "Không tìm thấy ffmpeg. Hãy cài FFmpeg và thêm thư mục bin vào PATH."
                )

            self._active_processes.clear()
            ok, message = run_concat_copy(
                self.paths,
                self.output_path,
                tools["ffmpeg"] or "ffmpeg",
                emit_log=self.log.emit,
                emit_progress=self.progress.emit,
                stop_check=lambda: self._stop_requested,
                active_processes=self._active_processes,
                expected_duration=self.expected_duration,
                ffprobe_path=tools.get("ffprobe"),
                file_durations=self.file_durations,
                safe_mode=self.safe_mode,
            )
            self.finished.emit(ok, message)
        except Exception as exc:
            self.finished.emit(False, str(exc))
        finally:
            self._active_processes.clear()
