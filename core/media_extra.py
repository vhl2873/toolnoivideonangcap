from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from core.ffmpeg_tools import hidden_subprocess_kwargs, media_duration_from_probe, run_concat_copy, run_ffprobe


def _run_ffmpeg(
    command: list[str],
    *,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> int:
    emit_log(" ".join(f'"{part}"' if " " in part else part for part in command))
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **hidden_subprocess_kwargs(),
    )
    active_processes.append(process)
    assert process.stdout is not None
    try:
        for raw_line in process.stdout:
            if stop_check():
                try:
                    process.terminate()
                except OSError:
                    pass
                return 130
            line = raw_line.strip()
            if line:
                emit_log(line)
        return process.wait()
    finally:
        try:
            active_processes.remove(process)
        except ValueError:
            pass


def _safe_stem(path: str | Path) -> str:
    text = Path(path).stem.strip() or "video"
    for ch in '\\/:*?"<>|':
        text = text.replace(ch, "_")
    return text


def split_video_by_duration(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    segment_seconds: int,
    accurate: bool = False,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    out_root = Path(output_dir).resolve() / f"{_safe_stem(src)}_parts"
    out_root.mkdir(parents=True, exist_ok=True)
    if segment_seconds <= 0:
        return False, "Thời lượng mỗi đoạn phải lớn hơn 0 giây."

    try:
        payload = run_ffprobe(src, ffprobe_path=ffprobe_path)
        duration = media_duration_from_probe(payload)
    except Exception as exc:
        return False, f"Không đọc được duration: {exc}"

    if duration <= 0:
        return False, "Không xác định được thời lượng video."

    ext = src.suffix.lower() if src.suffix else ".mp4"
    if ext not in {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".ts"}:
        ext = ".mp4"
    pattern = out_root / f"{_safe_stem(src)}_part_%03d{ext}"
    emit_log(f"Băm nhỏ: {src.name} -> mỗi đoạn {segment_seconds}s ({'chính xác/re-encode' if accurate else 'nhanh/stream copy'})")
    cmd = [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(src), "-map", "0"]
    if accurate:
        force_expr = f"expr:gte(t,n_forced*{segment_seconds})"
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-force_key_frames",
            force_expr,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    else:
        cmd += ["-c", "copy"]
    cmd += [
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        str(pattern),
    ]
    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0:
        return False, f"FFmpeg băm nhỏ thất bại: exit {rc}"
    count = len(list(out_root.glob(f"{_safe_stem(src)}_part_*{ext}")))
    return True, f"Đã băm nhỏ {src.name}: {count} đoạn trong {out_root}"


def extract_audio(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    *,
    audio_format: str,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    fmt = audio_format.lower().strip()
    if fmt not in {"mp3", "wav", "aac"}:
        return False, "Định dạng audio chỉ hỗ trợ mp3, wav, aac."
    out_root = Path(output_dir).resolve() / "audio_extracted"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{_safe_stem(src)}.{fmt}"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{_safe_stem(src)}_{suffix}.{fmt}"
        suffix += 1

    emit_log(f"Tách audio/nhạc nền: {src.name} -> {out_path.name}")
    cmd = [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(src), "-vn"]
    if fmt == "mp3":
        cmd += ["-codec:a", "libmp3lame", "-q:a", "2"]
    elif fmt == "wav":
        cmd += ["-codec:a", "pcm_s16le"]
    else:
        cmd += ["-codec:a", "aac", "-b:a", "192k"]
    cmd.append(str(out_path))

    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0 or not out_path.exists():
        return False, f"FFmpeg tách audio thất bại: exit {rc}"
    return True, f"Đã tách audio từ {src.name}: {out_path}"


def separate_vocals_background(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    out_root = Path(output_dir).resolve() / "ai_separated"
    out_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems=vocals",
        "-o",
        str(out_root),
        str(src),
    ]
    emit_log(f"AI tách giọng/nhạc nền: {src.name}")
    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0:
        return False, f"Demucs tách giọng/nhạc nền thất bại: exit {rc}"
    return True, f"Đã chạy AI tách giọng/nhạc nền cho {src.name}. Xem thư mục {out_root}"


def transform_video_zoom(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    *,
    zoom_percent: int,
    pos_x: int,
    pos_y: int,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    out_root = Path(output_dir).resolve() / "transforms"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{_safe_stem(src)}_zoom.mp4"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{_safe_stem(src)}_zoom_{suffix}.mp4"
        suffix += 1

    zoom = max(25, min(300, int(zoom_percent))) / 100
    # Scale lên/xuống theo zoom, sau đó crop/pad về kích thước gốc input.
    # iw/ih trong crop là kích thước sau scale; ow/oh là kích thước output crop.
    if zoom >= 1:
        crop_x = f"(iw-ow)/2+{int(pos_x)}"
        crop_y = f"(ih-oh)/2+{int(pos_y)}"
        vf = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f},crop=trunc(iw/{zoom:.4f}/2)*2:trunc(ih/{zoom:.4f}/2)*2:{crop_x}:{crop_y}"
    else:
        pad_x = f"(ow-iw)/2+{int(pos_x)}"
        pad_y = f"(oh-ih)/2+{int(pos_y)}"
        vf = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f},pad=trunc(iw/{zoom:.4f}/2)*2:trunc(ih/{zoom:.4f}/2)*2:{pad_x}:{pad_y}:black"

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-nostdin",
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_path),
    ]
    emit_log(f"Zoom/crop/pad: {src.name} -> {out_path.name} (zoom {zoom_percent}%, X={pos_x}, Y={pos_y})")
    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0 or not out_path.exists():
        return False, f"FFmpeg zoom/crop/pad thất bại: exit {rc}"
    return True, f"Đã xuất video zoom/crop/pad {src.name}: {out_path}"


def compute_split_zoom_segments(
    duration: float, segment_seconds: int, odd_percent: int, even_percent: int
) -> list[tuple[int, float, float, int]]:
    """Trả về danh sách (số thứ tự, bắt đầu, kết thúc, % zoom) — dùng chung cho preview UI và xử lý thật."""
    if duration <= 0 or segment_seconds <= 0:
        return []
    segments: list[tuple[int, float, float, int]] = []
    index = 1
    start = 0.0
    while start < duration - 0.01:
        end = min(duration, start + segment_seconds)
        percent = odd_percent if index % 2 == 1 else even_percent
        segments.append((index, start, end, percent))
        start = end
        index += 1
    return segments


def _zoom_crop_filter(percent: int, avoid_pad: bool, width: int, height: int) -> str:
    """Filter scale+crop/pad luôn trả về đúng kích thước width x height gốc — bắt buộc để
    mọi đoạn (dù zoom % khác nhau) có cùng độ phân giải, cho phép nối lại bằng stream copy."""
    zoom = max(25, min(300, int(percent))) / 100
    if zoom < 1 and avoid_pad:
        zoom = 1.0  # tránh viền đen: không thu nhỏ dưới 100%, chỉ crop-fill
    scaled_w = max(2, round(width * zoom / 2) * 2)
    scaled_h = max(2, round(height * zoom / 2) * 2)
    if zoom >= 1:
        offset_x = (scaled_w - width) // 2
        offset_y = (scaled_h - height) // 2
        return f"scale={scaled_w}:{scaled_h},crop={width}:{height}:{offset_x}:{offset_y}"
    offset_x = (width - scaled_w) // 2
    offset_y = (height - scaled_h) // 2
    return f"scale={scaled_w}:{scaled_h},pad={width}:{height}:{offset_x}:{offset_y}:black"


def split_and_zoom_alternating(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    segment_seconds: int,
    odd_percent: int,
    even_percent: int,
    avoid_pad: bool = True,
    emit_log: Callable[[str], None],
    emit_segment: Callable[[int, str, str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str, list[Path]]:
    """Cắt video thành các đoạn liên tiếp theo thời lượng, zoom so le từng đoạn (đoạn lẻ/chẵn),
    rồi nối toàn bộ (stream copy) thành final.mp4 — giữ nguyên thứ tự, FPS, độ phân giải gốc, không lệch tiếng."""
    src = Path(input_path).resolve()
    if segment_seconds <= 0:
        return False, "Thời lượng mỗi đoạn phải lớn hơn 0 giây.", []

    try:
        payload = run_ffprobe(src, ffprobe_path=ffprobe_path)
        duration = media_duration_from_probe(payload)
    except Exception as exc:
        return False, f"Không đọc được duration: {exc}", []
    if duration <= 0:
        return False, "Không xác định được thời lượng video.", []

    video_stream = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), None)
    try:
        src_width = int(video_stream.get("width")) if video_stream else 0
        src_height = int(video_stream.get("height")) if video_stream else 0
    except (TypeError, ValueError):
        src_width = src_height = 0
    if src_width <= 0 or src_height <= 0:
        return False, "Không xác định được độ phân giải video.", []

    segments = compute_split_zoom_segments(duration, segment_seconds, odd_percent, even_percent)
    if not segments:
        return False, "Không tính được đoạn nào từ video.", []

    out_root = Path(output_dir).resolve() / _safe_stem(src)
    out_root.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for index, start, end, percent in segments:
        if stop_check():
            return False, "Đã dừng theo yêu cầu.", outputs
        length = end - start
        out_path = out_root / f"segment_{index:03d}.mp4"
        emit_segment(index, "Đang xử lý", str(out_path))

        vf = _zoom_crop_filter(percent, avoid_pad, src_width, src_height)
        cmd = [
            ffmpeg_path, "-hide_banner", "-y", "-nostdin",
            "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(out_path),
        ]
        emit_log(f"Đoạn {index}/{len(segments)}: {start:.1f}s -> {end:.1f}s, zoom {percent}%")
        rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
        if stop_check():
            return False, "Đã dừng theo yêu cầu.", outputs
        if rc != 0 or not out_path.exists():
            emit_segment(index, "Lỗi", str(out_path))
            return False, f"FFmpeg xử lý đoạn {index} thất bại: exit {rc}", outputs
        emit_segment(index, "Đã xử lý", str(out_path))
        outputs.append(out_path)

    final_path = out_root / "final.mp4"
    emit_log(f"Đang nối {len(outputs)} đoạn thành {final_path.name} (stream copy, không render lại)...")
    ok, message = run_concat_copy(
        [str(p) for p in outputs],
        final_path,
        ffmpeg_path,
        emit_log=emit_log,
        emit_progress=lambda _value: None,
        stop_check=stop_check,
        active_processes=active_processes,
        expected_duration=duration,
    )
    if not ok:
        return False, f"Đã zoom xong {len(outputs)} đoạn nhưng nối final.mp4 thất bại: {message}", outputs

    emit_log(f"Đã tạo {final_path}")
    return True, f"Đã cắt + zoom so le {src.name}: {len(outputs)} đoạn, xuất {final_path}", [*outputs, final_path]


def apply_video_effects(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    *,
    effects: list[str],
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    if not effects:
        return False, "Chưa chọn hiệu ứng nào."
    out_root = Path(output_dir).resolve() / "effects"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{_safe_stem(src)}_effects.mp4"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{_safe_stem(src)}_effects_{suffix}.mp4"
        suffix += 1

    video_filters: list[str] = []
    audio_filters: list[str] = []
    if "fade_in" in effects:
        video_filters.append("fade=t=in:st=0:d=1")
    if "fade_out" in effects:
        video_filters.append("fade=t=out:st=3:d=1")
    if "blur" in effects:
        video_filters.append("boxblur=6:1")
    if "brightness" in effects:
        video_filters.append("eq=brightness=0.08")
    if "contrast" in effects:
        video_filters.append("eq=contrast=1.25")
    if "sharpen" in effects:
        video_filters.append("unsharp=5:5:1.0:5:5:0.0")
    if "grayscale" in effects:
        video_filters.append("hue=s=0")
    if "flip" in effects:
        video_filters.append("hflip")
    if "rotate" in effects:
        video_filters.append("transpose=1")
    if "speed" in effects:
        video_filters.append("setpts=0.8*PTS")
        audio_filters.append("atempo=1.25")

    cmd = [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(src)]
    if video_filters:
        cmd += ["-vf", ",".join(video_filters)]
    if audio_filters:
        cmd += ["-af", ",".join(audio_filters)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", str(out_path)]
    emit_log(f"Áp dụng hiệu ứng: {src.name} -> {out_path.name}")
    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0 or not out_path.exists():
        return False, f"FFmpeg hiệu ứng thất bại: exit {rc}"
    return True, f"Đã xuất video hiệu ứng {src.name}: {out_path}"


def _parse_time_to_seconds(text: str) -> float:
    value = text.strip().replace(",", ".")
    if not value:
        raise ValueError("mốc thời gian rỗng")
    if ":" not in value:
        return float(value)
    parts = [float(part) for part in value.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    raise ValueError(f"mốc thời gian không hợp lệ: {text}")


def split_video_by_markers(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    *,
    marker_text: str,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    out_root = Path(output_dir).resolve() / f"{_safe_stem(src)}_custom_parts"
    out_root.mkdir(parents=True, exist_ok=True)
    ranges: list[tuple[float, float]] = []
    for raw_line in marker_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "-" not in line:
            return False, f"Mốc không hợp lệ: {line}. Dùng dạng 00:00:00-00:01:30"
        start_text, end_text = line.split("-", 1)
        try:
            start = _parse_time_to_seconds(start_text)
            end = _parse_time_to_seconds(end_text)
        except ValueError as exc:
            return False, str(exc)
        if end <= start:
            return False, f"Mốc kết thúc phải lớn hơn mốc bắt đầu: {line}"
        ranges.append((start, end))
    if not ranges:
        return False, "Chưa nhập mốc cắt tùy chọn."

    ext = src.suffix.lower() if src.suffix else ".mp4"
    if ext not in {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".ts"}:
        ext = ".mp4"
    ok_count = 0
    for index, (start, end) in enumerate(ranges, 1):
        if stop_check():
            return False, "Đã dừng theo yêu cầu."
        out_path = out_root / f"{_safe_stem(src)}_custom_{index:03d}{ext}"
        duration = end - start
        emit_log(f"Cắt mốc {index}: {start:.3f}s -> {end:.3f}s")
        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-y",
            "-nostdin",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{duration:.3f}",
            "-map",
            "0",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(out_path),
        ]
        rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
        if rc != 0 or not out_path.exists():
            return False, f"FFmpeg cắt mốc {index} thất bại: exit {rc}"
        ok_count += 1
    return True, f"Đã cắt {src.name} theo {ok_count} mốc trong {out_root}"


def split_video_by_count(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    part_count: int,
    accurate: bool = False,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    if part_count < 2:
        return False, "Số lượng đoạn phải từ 2 trở lên."
    src = Path(input_path).resolve()
    try:
        payload = run_ffprobe(src, ffprobe_path=ffprobe_path)
        duration = media_duration_from_probe(payload)
    except Exception as exc:
        return False, f"Không đọc được duration: {exc}"
    if duration <= 0:
        return False, "Không xác định được thời lượng video."
    segment_seconds = max(1, int(duration / part_count))
    return split_video_by_duration(
        src,
        output_dir,
        ffmpeg_path,
        ffprobe_path,
        segment_seconds=segment_seconds,
        accurate=accurate,
        emit_log=emit_log,
        stop_check=stop_check,
        active_processes=active_processes,
    )


def normalize_video(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    *,
    width: int,
    height: int,
    fps: str,
    codec: str,
    out_format: str,
    bitrate: str,
    fit_mode: str,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    src = Path(input_path).resolve()
    fmt = out_format.lower().strip()
    if fmt not in {"mp4", "mkv", "mov"}:
        fmt = "mp4"
    out_root = Path(output_dir).resolve() / "normalized"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{_safe_stem(src)}_normalized.{fmt}"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{_safe_stem(src)}_normalized_{suffix}.{fmt}"
        suffix += 1

    codec_map = {"h.264": "libx264", "h.265": "libx265", "av1": "libaom-av1", "giữ nguyên": "libx264"}
    vcodec = codec_map.get(codec.lower(), "libx264")
    scale_filter = f"scale={width}:{height}:force_original_aspect_ratio={'decrease' if fit_mode == 'keep' else 'increase'}"
    if fit_mode == "pad":
        vf = f"{scale_filter},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    elif fit_mode == "crop":
        vf = f"{scale_filter},crop={width}:{height}"
    else:
        vf = scale_filter

    cmd = [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(src), "-vf", vf, "-c:v", vcodec]
    if fps and fps != "Giữ nguyên":
        cmd += ["-r", fps]
    if bitrate and bitrate != "Tự động":
        cmd += ["-b:v", bitrate]
    cmd += ["-c:a", "aac", "-b:a", "192k", str(out_path)]
    emit_log(f"Chuẩn hóa video: {src.name} -> {out_path.name}")
    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0 or not out_path.exists():
        return False, f"FFmpeg chuẩn hóa thất bại: exit {rc}"
    return True, f"Đã chuẩn hóa {src.name}: {out_path}"


def convert_format(
    input_path: str | Path,
    output_dir: str | Path,
    ffmpeg_path: str,
    *,
    out_format: str,
    codec: str,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    """Chuyển đổi container/codec. Codec 'Giữ nguyên (copy nhanh)' chỉ remux bằng stream copy, không re-encode."""
    src = Path(input_path).resolve()
    fmt = out_format.lower().strip()
    if fmt not in {"mp4", "mkv", "mov", "webm"}:
        fmt = "mp4"
    out_root = Path(output_dir).resolve() / "converted"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{_safe_stem(src)}.{fmt}"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{_safe_stem(src)}_{suffix}.{fmt}"
        suffix += 1

    codec_map = {"h.264": "libx264", "h.265": "libx265", "av1": "libaom-av1"}
    vcodec = codec_map.get(codec.lower().strip())
    cmd = [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(src)]
    if vcodec is None:
        cmd += ["-map", "0", "-c", "copy"]
    else:
        cmd += ["-c:v", vcodec, "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
    cmd.append(str(out_path))
    emit_log(f"Chuyển đổi định dạng: {src.name} -> {out_path.name} ({'copy nhanh' if vcodec is None else codec})")
    rc = _run_ffmpeg(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if stop_check():
        return False, "Đã dừng theo yêu cầu."
    if rc != 0 or not out_path.exists():
        return False, f"FFmpeg chuyển đổi định dạng thất bại: exit {rc}"
    return True, f"Đã chuyển đổi {src.name}: {out_path}"
