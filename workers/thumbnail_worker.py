from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from core.ffmpeg_tools import check_required_tools, hidden_subprocess_kwargs


class ThumbnailWorker(QObject):
    log = Signal(str)
    thumbnail_ready = Signal(str, str)
    finished = Signal()

    def __init__(self, paths: list[str]) -> None:
        super().__init__()
        self.paths = paths
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    @Slot()
    def run(self) -> None:
        try:
            ok, tools = check_required_tools()
            ffmpeg = tools.get("ffmpeg")
            if not ffmpeg:
                self.log.emit("Không thể tạo thumbnail vì thiếu ffmpeg.")
                return

            cache_dir = Path(tempfile.gettempdir()) / "fast_video_concat_thumbs"
            cache_dir.mkdir(parents=True, exist_ok=True)

            for path in self.paths:
                if self._stop_requested:
                    return
                source = Path(path)
                if not source.exists():
                    continue
                thumbnail_path = self._thumbnail_path(cache_dir, source)
                if not thumbnail_path.exists():
                    self._create_thumbnail(ffmpeg, source, thumbnail_path)
                if thumbnail_path.exists():
                    self.thumbnail_ready.emit(str(source.resolve()), str(thumbnail_path))
        finally:
            self.finished.emit()

    def _thumbnail_path(self, cache_dir: Path, source: Path) -> Path:
        try:
            stat = source.stat()
            cache_key = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            cache_key = str(source.resolve())
        digest = hashlib.sha1(cache_key.encode("utf-8", errors="ignore")).hexdigest()
        return cache_dir / f"{digest}.jpg"

    def _create_thumbnail(self, ffmpeg: str, source: Path, thumbnail_path: Path) -> None:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=180:-1",
            "-q:v",
            "3",
            "-y",
            str(thumbnail_path),
        ]
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
