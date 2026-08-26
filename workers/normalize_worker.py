from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from core.ffmpeg_tools import check_required_tools, hidden_subprocess_kwargs


class NormalizeWorker(QObject):
    """Re-encode các video KHÔNG tương thích để khớp độ phân giải/fps/audio của nhóm video chuẩn (nhóm
    lớn nhất cùng chữ ký stream) — sau khi chuẩn hóa xong, các file sẽ nối được bằng stream copy."""

    log = Signal(str)
    file_done = Signal(str, str)  # (đường dẫn gốc, đường dẫn đã chuẩn hóa)
    finished = Signal(bool, str)

    def __init__(
        self,
        paths: list[str],
        *,
        width: int,
        height: int,
        fps: str,
        sample_rate: int,
        channels: int,
        output_dir: str,
        pix_fmt: str = "",
        profile: str = "",
        level: str = "",
    ) -> None:
        super().__init__()
        self.paths = paths
        self.width = max(2, width)
        self.height = max(2, height)
        self.fps = fps or "30"
        self.sample_rate = sample_rate or 44100
        self.channels = channels or 2
        self.output_dir = Path(output_dir)
        self.pix_fmt = pix_fmt or "yuv420p"
        self.profile = profile
        self.level = level
        self._stop_requested = False
        self._active_processes: list[subprocess.Popen] = []

    def stop(self) -> None:
        self._stop_requested = True
        for process in list(self._active_processes):
            if process.poll() is None:
                process.terminate()

    @Slot()
    def run(self) -> None:
        ok, tools = check_required_tools()
        ffmpeg = tools.get("ffmpeg")
        if not ffmpeg:
            self.finished.emit(False, "Không tìm thấy FFmpeg.")
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        done = 0
        vf = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,fps={self.fps},format={self.pix_fmt}"
        )
        # Chỉ khớp resolution/fps/audio thôi CHƯA đủ — check_compatibility còn so cả profile/level H.264,
        # libx264 tự chọn profile mặc định (thường "High") có thể khác file chuẩn (vd "Main") và vẫn báo
        # không tương thích dù mọi thứ khác đã khớp. Ép đúng profile/level của file chuẩn nếu biết được.
        codec_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
        if self.profile:
            codec_args += ["-profile:v", self.profile.lower()]
        if self.level:
            codec_args += ["-level:v", self.level]
        for path in self.paths:
            if self._stop_requested:
                self.finished.emit(False, "Đã dừng theo yêu cầu.")
                return
            source = Path(path)
            target = self.output_dir / f"{source.stem}_chuanhoa.mp4"
            self.log.emit(f"Đang chuẩn hóa {source.name} -> {target.name}...")
            cmd = [
                ffmpeg, "-hide_banner", "-y", "-nostdin", "-i", str(source),
                "-vf", vf,
            ] + codec_args + [
                "-c:a", "aac", "-ar", str(self.sample_rate), "-ac", str(self.channels),
                "-movflags", "+faststart", str(target),
            ]
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", **hidden_subprocess_kwargs(),
            )
            self._active_processes.append(process)
            for line in process.stdout or []:
                if line.strip():
                    self.log.emit(line.rstrip())
            rc = process.wait()
            if process in self._active_processes:
                self._active_processes.remove(process)
            if rc != 0 or not target.is_file():
                self.finished.emit(False, f"Chuẩn hóa {source.name} thất bại (exit {rc}).")
                return
            self.file_done.emit(str(source), str(target))
            done += 1
        self.finished.emit(True, f"Đã chuẩn hóa {done} file — hãy Phân tích lại để xác nhận.")
