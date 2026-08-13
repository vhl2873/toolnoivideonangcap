from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, Signal, Slot

from core.ffmpeg_tools import check_required_tools, run_concat_copy


class StreamConcatWorker(QObject):
    """Nối tuần tự nhiều luồng tương thích, mỗi luồng một output."""

    log = Signal(str)
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        jobs: list[tuple[str, list[str], str, float | None, list[float | None] | None]],
        *,
        continue_on_error: bool = False,
    ) -> None:
        super().__init__()
        self.jobs = jobs
        self.continue_on_error = continue_on_error
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
            ffmpeg = tools["ffmpeg"] or "ffmpeg"
            outputs: list[str] = []
            failures: list[str] = []
            total = len(self.jobs)
            for index, (
                label,
                paths,
                output_path,
                expected_duration,
                file_durations,
            ) in enumerate(
                self.jobs,
                start=1,
            ):
                if self._stop_requested:
                    self.finished.emit(False, "Đã dừng theo yêu cầu.")
                    return
                self.log.emit(
                    f"--- Bước {index}/{total} [{label}] (stream copy) — {len(paths)} file → {output_path}"
                )
                self._active_processes.clear()
                ok, message = run_concat_copy(
                    paths,
                    output_path,
                    ffmpeg,
                    emit_log=self.log.emit,
                    emit_progress=self.progress.emit,
                    stop_check=lambda: self._stop_requested,
                    active_processes=self._active_processes,
                    expected_duration=expected_duration,
                    ffprobe_path=tools.get("ffprobe"),
                    file_durations=file_durations,
                )
                if not ok:
                    failures.append(f"[{label}] {message}")
                    self.log.emit(f"LỖI [{label}]: {message}")
                    if self.continue_on_error:
                        continue
                    self.finished.emit(False, message)
                    return
                outputs.append(output_path)

            if failures:
                parts = ["Một số bước thất bại:"]
                parts.extend(failures)
                parts.append("")
                parts.append("Các file đã tạo được:")
                parts.extend(outputs if outputs else ["(không có)"])
                summary = "\n".join(parts)
                self.finished.emit(False, summary)
                return

            summary = "Hoàn tất nối lần lượt các file output:\n" + "\n".join(outputs)
            self.finished.emit(True, summary)
        except Exception as exc:
            self.finished.emit(False, str(exc))
        finally:
            self._active_processes.clear()
