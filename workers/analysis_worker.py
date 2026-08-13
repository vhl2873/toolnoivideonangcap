from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from core.ffmpeg_tools import check_required_tools
from core.video_analyzer import CompatibilityReport, analyze_files


class AnalysisWorker(QObject):
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[str]) -> None:
        super().__init__()
        self.paths = paths

    @Slot()
    def run(self) -> None:
        try:
            ok, tools = check_required_tools()
            if not tools.get("ffprobe"):
                raise RuntimeError(
                    "Không tìm thấy ffprobe. Hãy cài FFmpeg và thêm thư mục bin vào PATH."
                )

            self.log.emit(f"ffprobe: {tools['ffprobe']}")
            self.log.emit(f"Đang phân tích {len(self.paths)} file...")
            report: CompatibilityReport = analyze_files(
                self.paths,
                ffprobe_path=tools["ffprobe"] or "ffprobe",
            )
            self.finished.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc))
