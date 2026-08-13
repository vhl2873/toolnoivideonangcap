from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.ffmpeg_tools import media_duration_from_probe, run_ffprobe


@dataclass(slots=True)
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str
    signature: dict[str, str]


@dataclass(slots=True)
class VideoAnalysis:
    path: str
    format_name: str = ""
    duration: float = 0.0
    size: int = 0
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def stream_signature(self) -> list[dict[str, str]]:
        return [stream.signature for stream in self.streams]


@dataclass(slots=True)
class CompatibilityReport:
    is_compatible: bool
    message: str
    issues: list[str]
    files: list[VideoAnalysis]
    compatible_paths: list[str] = field(default_factory=list)
    incompatible_paths: list[str] = field(default_factory=list)
    """Mỗi phần tử là một nhóm đường dẫn có cùng chữ ký stream — nối nhanh được trong nhóm."""
    stream_compatible_groups: list[list[str]] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return sum(file.duration for file in self.files)


COMMON_KEYS = (
    "codec_type",
    "codec_name",
    "codec_tag_string",
    "profile",
    "level",
    "time_base",
)

VIDEO_KEYS = (
    "width",
    "height",
    "coded_width",
    "coded_height",
    "pix_fmt",
    "sample_aspect_ratio",
    "display_aspect_ratio",
    "r_frame_rate",
    "field_order",
    "color_range",
    "color_space",
    "color_transfer",
    "color_primaries",
    "chroma_location",
)

AUDIO_KEYS = (
    "sample_fmt",
    "sample_rate",
    "channels",
    "channel_layout",
)

SUBTITLE_KEYS = (
    "width",
    "height",
)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _stream_signature(stream: dict[str, Any]) -> dict[str, str]:
    codec_type = _to_text(stream.get("codec_type"))
    keys = list(COMMON_KEYS)
    if codec_type == "video":
        keys.extend(VIDEO_KEYS)
    elif codec_type == "audio":
        keys.extend(AUDIO_KEYS)
    elif codec_type == "subtitle":
        keys.extend(SUBTITLE_KEYS)

    signature = {key: _to_text(stream.get(key)) for key in keys}
    signature["disposition_attached_pic"] = _to_text(
        stream.get("disposition", {}).get("attached_pic")
    )
    return signature


def analyze_video(path: str | Path, ffprobe_path: str = "ffprobe") -> VideoAnalysis:
    payload = run_ffprobe(path, ffprobe_path=ffprobe_path)
    fmt = payload.get("format", {})
    streams = []
    for raw_stream in payload.get("streams", []):
        streams.append(
            StreamInfo(
                index=int(raw_stream.get("index", len(streams))),
                codec_type=_to_text(raw_stream.get("codec_type")),
                codec_name=_to_text(raw_stream.get("codec_name")),
                signature=_stream_signature(raw_stream),
            )
        )

    duration = media_duration_from_probe(payload)

    try:
        size = int(fmt.get("size") or 0)
    except (TypeError, ValueError):
        size = 0

    return VideoAnalysis(
        path=str(Path(path).resolve()),
        format_name=_to_text(fmt.get("format_name")),
        duration=duration,
        size=size,
        streams=streams,
    )


def analyze_files(paths: list[str], ffprobe_path: str = "ffprobe") -> CompatibilityReport:
    files: list[VideoAnalysis] = []
    failed_issues: list[str] = []
    failed_paths: list[str] = []

    for path in paths:
        try:
            files.append(analyze_video(path, ffprobe_path=ffprobe_path))
        except Exception as exc:
            failed_paths.append(_safe_resolved_path(path))
            failed_issues.append(_format_analysis_failure(path, exc))

    report = check_compatibility(files)
    if not failed_issues:
        return report

    if not files:
        return CompatibilityReport(
            is_compatible=False,
            message=f"Không phân tích được {len(failed_paths)} file video.",
            issues=failed_issues,
            files=[],
            incompatible_paths=failed_paths,
            stream_compatible_groups=[],
        )

    message = (
        f"{len(failed_paths)} file không phân tích được; các file còn lại "
        "tương thích để nối nhanh bằng stream copy."
        if report.is_compatible
        else f"{len(failed_paths)} file không phân tích được; các file còn lại "
        "chưa cùng một nhóm nối nhanh."
    )

    return CompatibilityReport(
        is_compatible=False,
        message=message,
        issues=[*failed_issues, *report.issues],
        files=files,
        compatible_paths=report.compatible_paths,
        incompatible_paths=_unique_paths([*failed_paths, *report.incompatible_paths]),
        stream_compatible_groups=report.stream_compatible_groups,
    )


def _safe_resolved_path(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def _format_analysis_failure(path: str | Path, exc: Exception) -> str:
    name = Path(path).name or str(path)
    return f"{name}: không phân tích được ({str(exc).strip()})."


def _unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def check_compatibility(files: list[VideoAnalysis]) -> CompatibilityReport:
    stream_compatible_groups = _stream_compatible_group_paths(files)
    if not files:
        return CompatibilityReport(
            is_compatible=False,
            message="Chưa có file video nào.",
            issues=["Danh sách file đang trống."],
            files=[],
            stream_compatible_groups=stream_compatible_groups,
        )

    if len(files) == 1:
        return CompatibilityReport(
            is_compatible=True,
            message="Chỉ có 1 file. Không cần nối, nhưng file có thể copy stream.",
            issues=[],
            files=files,
            compatible_paths=[files[0].path],
            stream_compatible_groups=stream_compatible_groups,
        )

    baseline = files[0]
    baseline_signature = baseline.stream_signature
    issues: list[str] = []
    groups = _group_by_signature(files)
    largest_group = max(groups.values(), key=len)
    compatible_paths = [file.path for file in largest_group]
    incompatible_paths = [
        file.path for file in files if file.path not in set(compatible_paths)
    ]

    for current in files[1:]:
        name = Path(current.path).name
        if len(current.stream_signature) != len(baseline_signature):
            issues.append(
                f"{name}: số stream khác file đầu tiên "
                f"({len(current.stream_signature)} != {len(baseline_signature)})."
            )
            continue

        for stream_index, (expected, actual) in enumerate(
            zip(baseline_signature, current.stream_signature)
        ):
            for key, expected_value in expected.items():
                actual_value = actual.get(key, "")
                if actual_value != expected_value:
                    issues.append(
                        f"{name}: stream #{stream_index} khác {key} "
                        f"({actual_value or 'trống'} != {expected_value or 'trống'})."
                    )
                    if len(issues) >= 60:
                        issues.append("Đã dừng liệt kê vì có quá nhiều khác biệt.")
                        return CompatibilityReport(
                            is_compatible=False,
                            message="Các file không tương thích để nối nhanh.",
                            issues=issues,
                            files=files,
                            compatible_paths=compatible_paths,
                            incompatible_paths=incompatible_paths,
                            stream_compatible_groups=stream_compatible_groups,
                        )

    if issues:
        return CompatibilityReport(
            is_compatible=False,
            message="Các file không tương thích để nối nhanh.",
            issues=issues,
            files=files,
            compatible_paths=compatible_paths,
            incompatible_paths=incompatible_paths,
            stream_compatible_groups=stream_compatible_groups,
        )

    return CompatibilityReport(
        is_compatible=True,
        message="Các file tương thích để nối nhanh bằng stream copy.",
        issues=[],
        files=files,
        compatible_paths=[file.path for file in files],
        stream_compatible_groups=stream_compatible_groups,
    )


def video_resolution_label(analysis: VideoAnalysis) -> str:
    video = next((stream for stream in analysis.streams if stream.codec_type == "video"), None)
    if not video:
        return "Không có video"
    width = video.signature.get("width", "").strip() or "?"
    height = video.signature.get("height", "").strip() or "?"
    return f"{width}x{height}"


def group_analyses_by_resolution(
    files: list[VideoAnalysis],
) -> list[tuple[str, list[VideoAnalysis]]]:
    """Giữ thứ tự file gốc trong từng nhóm; thứ tự nhóm theo lần xuất hiện độ phân giải đầu tiên."""
    buckets: dict[str, list[VideoAnalysis]] = {}
    order: list[str] = []
    for analysis in files:
        label = video_resolution_label(analysis)
        if label not in buckets:
            order.append(label)
            buckets[label] = []
        buckets[label].append(analysis)
    return [(label, buckets[label]) for label in order]


def _signature_key(analysis: VideoAnalysis) -> tuple[tuple[tuple[str, str], ...], ...]:
    return tuple(tuple(sorted(stream.items())) for stream in analysis.stream_signature)


def _group_by_signature(
    files: list[VideoAnalysis],
) -> dict[tuple[tuple[tuple[str, str], ...], ...], list[VideoAnalysis]]:
    groups: dict[tuple[tuple[tuple[str, str], ...], ...], list[VideoAnalysis]] = {}
    for file in files:
        groups.setdefault(_signature_key(file), []).append(file)
    return groups


def compatible_stream_groups(files: list[VideoAnalysis]) -> list[list[VideoAnalysis]]:
    """Các nhóm có cùng chữ ký toàn bộ stream (thứ tự nhóm = lần đầu xuất hiện trong danh sách)."""
    if not files:
        return []
    buckets = _group_by_signature(files)
    seen: set[tuple[tuple[tuple[str, str], ...], ...]] = set()
    ordered: list[list[VideoAnalysis]] = []
    for analysis in files:
        key = _signature_key(analysis)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(buckets[key])
    return ordered


def _stream_compatible_group_paths(files: list[VideoAnalysis]) -> list[list[str]]:
    return [[item.path for item in group] for group in compatible_stream_groups(files)]


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def summarize_file(analysis: VideoAnalysis) -> str:
    video = next((stream for stream in analysis.streams if stream.codec_type == "video"), None)
    audio = next((stream for stream in analysis.streams if stream.codec_type == "audio"), None)
    parts = [Path(analysis.path).name]
    details = []
    if video:
        sig = video.signature
        details.append(
            f"{video.codec_name} {sig.get('width')}x{sig.get('height')} "
            f"{sig.get('avg_frame_rate') or sig.get('r_frame_rate')}"
        )
    if audio:
        sig = audio.signature
        profile = f" {sig.get('profile')}" if sig.get("profile") else ""
        details.append(
            f"audio {audio.codec_name}{profile} {sig.get('sample_rate')}Hz "
            f"{sig.get('channels')}ch"
        )
    details.append(format_duration(analysis.duration))
    details.append(format_size(analysis.size))
    return " | ".join([parts[0], *details])
