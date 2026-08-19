from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from core.ffmpeg_tools import hidden_subprocess_kwargs, media_duration_from_probe, run_ffprobe, write_concat_list


def _probe_has_stream(payload: dict, codec_type: str) -> bool:
    return any(stream.get("codec_type") == codec_type for stream in payload.get("streams", []))


def _validate_final_output(
    src: Path,
    final_path: Path,
    ffprobe_path: str,
    *,
    emit_log: Callable[[str], None],
) -> None:
    source_probe = run_ffprobe(src, ffprobe_path=ffprobe_path)
    final_probe = run_ffprobe(final_path, ffprobe_path=ffprobe_path)
    source_duration = media_duration_from_probe(source_probe)
    final_duration = media_duration_from_probe(final_probe)
    duration_diff = abs(final_duration - source_duration)

    has_video = _probe_has_stream(final_probe, "video")
    has_audio = _probe_has_stream(final_probe, "audio")
    if not has_video:
        raise RuntimeError("final.mp4 không có video stream hợp lệ.")
    if not has_audio:
        raise RuntimeError("final.mp4 không có audio stream hợp lệ.")

    emit_log(
        f"Validate final: source={source_duration:.3f}s final={final_duration:.3f}s lệch={duration_diff:.3f}s"
    )
    if duration_diff > 0.1:
        emit_log("Cảnh báo: duration final lệch quá 0.1s so với source.")


def _usable_file(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_file() and candidate.stat().st_size > 0


def _safe_stem(path: str | Path) -> str:
    text = Path(path).stem.strip() or "video"
    for ch in '\\/:*?"<>|':
        text = text.replace(ch, "_")
    return text


def _run(
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
        for raw in process.stdout:
            if stop_check():
                try:
                    process.terminate()
                except OSError:
                    pass
                return 130
            line = raw.strip()
            if line:
                emit_log(line)
        return process.wait()
    finally:
        try:
            active_processes.remove(process)
        except ValueError:
            pass


def _run_demucs_vocal(
    src: Path,
    work_dir: Path,
    *,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> Path | None:
    demucs_root = work_dir / "demucs"
    demucs_root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-o", str(demucs_root), str(src)]
    emit_log("AI: tách giọng chính khỏi nhạc nền bằng Demucs...")
    rc = _run(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if rc != 0 or stop_check():
        emit_log(f"Demucs không chạy thành công (exit {rc}); sẽ giữ audio gốc.")
        return None
    candidates = list(demucs_root.glob(f"**/{src.stem}/vocals.*")) + list(demucs_root.glob("**/vocals.*"))
    for item in candidates:
        if item.is_file():
            return item.resolve()
    emit_log("Không tìm thấy vocals.* sau khi chạy Demucs; sẽ giữ audio gốc.")
    return None


def _prepare_voice_track(
    src: Path,
    audio_dir: Path,
    ffmpeg_path: str,
    *,
    enable_ai_voice: bool,
    remove_background: bool,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> Path | None:
    original_audio = audio_dir / "original_audio.wav"
    voice_wav = audio_dir / "voice.wav"

    if _usable_file(voice_wav):
        emit_log(f"Resume: dùng lại voice.wav đã có cho {src.name}.")
        return voice_wav

    if not _usable_file(original_audio):
        emit_log("Trích xuất original_audio.wav từ video nguồn...")
        rc = _run(
            [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", str(original_audio)],
            emit_log=emit_log,
            stop_check=stop_check,
            active_processes=active_processes,
        )
        if rc != 0:
            emit_log(f"Cảnh báo: không xuất được original_audio.wav (exit {rc}).")
            return None

    vocal_audio: Path | None = None
    if enable_ai_voice or remove_background:
        vocal_audio = _run_demucs_vocal(
            src,
            audio_dir,
            emit_log=emit_log,
            stop_check=stop_check,
            active_processes=active_processes,
        )

    source_audio = vocal_audio if vocal_audio and vocal_audio.is_file() else original_audio
    if source_audio == original_audio:
        emit_log("Không dùng AI voice hoặc AI không khả dụng: dùng audio gốc làm voice tham chiếu.")
    rc = _run(
        [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(source_audio), "-ac", "2", "-ar", "44100", str(voice_wav)],
        emit_log=emit_log,
        stop_check=stop_check,
        active_processes=active_processes,
    )
    if rc != 0:
        emit_log(f"Cảnh báo: không xuất được voice.wav (exit {rc}).")
        return None
    return voice_wav


def _split_exact_segments(
    src: Path,
    segments_dir: Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    segment_seconds: float,
    mute_audio: bool = False,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> list[Path]:
    probe = run_ffprobe(src, ffprobe_path=ffprobe_path)
    duration = media_duration_from_probe(probe)
    if duration <= 0:
        raise RuntimeError("Không xác định được thời lượng video để cắt đoạn.")
    if segment_seconds <= 0:
        raise RuntimeError("Thời lượng mỗi đoạn phải lớn hơn 0.")
    total = int((duration + segment_seconds - 0.001) // segment_seconds)
    segments: list[Path] = []
    for index in range(total):
        if stop_check():
            raise RuntimeError("Đã dừng theo yêu cầu.")
        start = index * segment_seconds
        length = min(segment_seconds, duration - start)
        if length <= 0:
            continue
        out = segments_dir / f"segment_{index + 1:03d}_raw.mp4"
        if _usable_file(out):
            emit_log(f"Resume: dùng lại đoạn raw {out.name}")
            segments.append(out)
            continue
        emit_log(f"Cắt đoạn {index + 1}/{total}: {start:.3f}s + {length:.3f}s")
        cmd = [
            ffmpeg_path, "-hide_banner", "-y", "-nostdin",
            "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}",
            "-map", "0:v:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        ]
        if mute_audio:
            cmd += ["-an"]
        else:
            cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-avoid_negative_ts", "make_zero", str(out)]
        rc = _run(
            cmd,
            emit_log=emit_log,
            stop_check=stop_check,
            active_processes=active_processes,
        )
        if rc != 0 or not out.exists():
            raise RuntimeError(f"Cắt đoạn {index + 1} thất bại: exit {rc}")
        segments.append(out)
    return segments


def _zoom_segment(
    src: Path,
    output_path: Path,
    ffmpeg_path: str,
    *,
    zoom_percent: int,
    zoom_mode: str,
    pos_x: int,
    pos_y: int,
    crf: str,
    bitrate: str,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> None:
    if _usable_file(output_path):
        emit_log(f"Resume: dùng lại segment đã zoom {output_path.name}")
        return
    zoom = max(25, min(300, int(zoom_percent))) / 100
    if zoom == 1:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        if zoom_mode == "custom":
            crop_x = f"(iw-ow)/2+{int(pos_x)}"
            crop_y = f"(ih-oh)/2+{int(pos_y)}"
        else:
            crop_x = "(iw-ow)/2"
            crop_y = "(ih-oh)/2"
        vf = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f},crop=trunc(iw/{zoom:.4f}/2)*2:trunc(ih/{zoom:.4f}/2)*2:{crop_x}:{crop_y}"
    cmd = [
        ffmpeg_path, "-hide_banner", "-y", "-nostdin",
        "-i", str(src), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast",
    ]
    if bitrate and bitrate.lower() not in {"auto", "tự động", "giu nguyen", "giữ nguyên"}:
        cmd += ["-b:v", bitrate]
    else:
        cmd += ["-crf", str(crf or "20")]
    cmd += ["-an", str(output_path)]
    emit_log(f"Zoom đoạn {src.name}: {zoom_percent}% -> {output_path.name}")
    rc = _run(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if rc != 0 or not output_path.exists():
        raise RuntimeError(f"Zoom đoạn {src.name} thất bại: exit {rc}")


def _mux_final_audio(
    final_path: Path,
    audio_path: Path,
    ffmpeg_path: str,
    *,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> None:
    temp_path = final_path.with_name(final_path.stem + "_with_audio.mp4")
    rc = _run(
        [
            ffmpeg_path, "-hide_banner", "-y", "-nostdin",
            "-i", str(final_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(temp_path),
        ],
        emit_log=emit_log,
        stop_check=stop_check,
        active_processes=active_processes,
    )
    if rc != 0 or not temp_path.exists():
        raise RuntimeError(f"Ghép voice.wav vào final thất bại: exit {rc}")
    temp_path.replace(final_path)


def _concat_segments(
    segments: list[Path],
    final_path: Path,
    ffmpeg_path: str,
    *,
    final_concat_mode: str = "fast",
    audio_path: Path | None = None,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> None:
    if _usable_file(final_path):
        emit_log(f"Resume: final đã tồn tại, bỏ qua ghép lại: {final_path.name}")
        return
    list_path = final_path.parent / "_concat_segments.txt"
    write_concat_list(segments, list_path)
    cmd_copy = [
        ffmpeg_path, "-hide_banner", "-y", "-nostdin",
        "-fflags", "+genpts+igndts",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-map", "0", "-c", "copy",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(final_path),
    ]
    final_concat_mode = (final_concat_mode or "fast").lower().strip()
    if final_concat_mode != "safe":
        emit_log("Ghép final theo chế độ nhanh (stream copy + genpts)...")
        rc = _run(
            cmd_copy,
            emit_log=emit_log,
            stop_check=stop_check,
            active_processes=active_processes,
        )
        if rc == 0 and final_path.exists():
            if audio_path and _usable_file(audio_path):
                emit_log("Ghép lại voice.wav vào final..." )
                _mux_final_audio(final_path, audio_path, ffmpeg_path, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
            return
        emit_log(f"Ghép nhanh báo exit {rc}; fallback sang ghép an toàn bằng re-encode final.")
    else:
        emit_log("Ghép final theo chế độ an toàn (re-encode final, ít lỗi timestamp hơn)...")

    safe_final = final_path.with_name(final_path.stem + "_safe.mp4")
    cmd_safe = [
        ffmpeg_path, "-hide_banner", "-y", "-nostdin",
        "-fflags", "+genpts+igndts",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-map", "0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(safe_final),
    ]
    rc = _run(
        cmd_safe,
        emit_log=emit_log,
        stop_check=stop_check,
        active_processes=active_processes,
    )
    if rc != 0 or not safe_final.exists():
        raise RuntimeError(f"Ghép final.mp4 thất bại: exit {rc}")
    if final_path.exists():
        try:
            final_path.unlink()
        except OSError:
            pass
    safe_final.replace(final_path)
    if audio_path and _usable_file(audio_path):
        emit_log("Ghép lại voice.wav vào final...")
        _mux_final_audio(final_path, audio_path, ffmpeg_path, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)


def process_voice_split_alternate_zoom_batch(
    input_paths: list[str | Path],
    output_dir: str | Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    enable_ai_voice: bool = True,
    remove_background: bool = True,
    segment_seconds: float = 5,
    odd_zoom_percent: int = 100,
    even_zoom_percent: int = 110,
    zoom_mode: str = "center",
    pos_x: int = 0,
    pos_y: int = 0,
    crf: str = "20",
    bitrate: str = "auto",
    final_concat_mode: str = "fast",
    resume_enabled: bool = True,
    emit_log: Callable[[str], None],
    emit_progress: Callable[[int], None],
    emit_status: Callable[[dict], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> tuple[bool, str]:
    """Batch pipeline: voice/background -> split consecutive segments -> alternating zoom -> final.mp4.

    Giữ nguyên concat cũ; đây là pipeline mới cho web control panel.
    Nếu AI không khả dụng, pipeline giữ audio gốc và tiếp tục xử lý để không kẹt cả batch.
    """
    paths = [Path(p).resolve() for p in input_paths if Path(p).is_file()]
    if not paths:
        return False, "Không có video nguồn hợp lệ."
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ok_count = 0
    errors: list[str] = []
    for video_index, src in enumerate(paths, 1):
        if stop_check():
            return False, "Đã dừng theo yêu cầu."
        video_dir = root / _safe_stem(src)
        if not resume_enabled:
            suffix = 2
            base_video_dir = video_dir
            while video_dir.exists() and any(video_dir.iterdir()):
                video_dir = root / f"{base_video_dir.name}_{suffix}"
                suffix += 1
        video_dir.mkdir(parents=True, exist_ok=True)
        raw_segments_dir = video_dir / "_raw_segments"
        raw_segments_dir.mkdir(parents=True, exist_ok=True)
        emit_log(f"===== Xử lý video {video_index}/{len(paths)}: {src.name} =====")
        emit_status({
            "job_stage": "Đang xử lý batch",
            "current_video": src.name,
            "current_step": "Chuẩn bị video",
            "processed_videos": max(0, video_index - 1),
            "total_videos": len(paths),
        })
        try:
            audio_dir = video_dir / "audio"
            parts_dir = video_dir / "parts"
            audio_dir.mkdir(parents=True, exist_ok=True)
            parts_dir.mkdir(parents=True, exist_ok=True)
            emit_status({"current_step": "Tách voice / chuẩn bị audio"})
            voice_path = _prepare_voice_track(
                src,
                audio_dir,
                ffmpeg_path,
                enable_ai_voice=enable_ai_voice,
                remove_background=remove_background,
                emit_log=emit_log,
                stop_check=stop_check,
                active_processes=active_processes,
            )
            if voice_path:
                emit_log(f"Đã có voice.wav: {voice_path}")
            emit_status({"current_step": "Cắt đoạn video không audio"})
            raw_segments = _split_exact_segments(
                src,
                raw_segments_dir,
                ffmpeg_path,
                ffprobe_path,
                segment_seconds=float(segment_seconds),
                mute_audio=True,
                emit_log=emit_log,
                stop_check=stop_check,
                active_processes=active_processes,
            )
            final_segments: list[Path] = []
            emit_status({"current_step": "Zoom xen kẽ từng đoạn"})
            for idx, raw in enumerate(raw_segments, 1):
                zoom = odd_zoom_percent if idx % 2 == 1 else even_zoom_percent
                out = parts_dir / f"part_{idx:03d}.mp4"
                _zoom_segment(
                    raw,
                    out,
                    ffmpeg_path,
                    zoom_percent=zoom,
                    zoom_mode=zoom_mode,
                    pos_x=pos_x,
                    pos_y=pos_y,
                    crf=crf,
                    bitrate=bitrate,
                    emit_log=emit_log,
                    stop_check=stop_check,
                    active_processes=active_processes,
                )
                final_segments.append(out)
            final_path = video_dir / "final.mp4"
            emit_status({"current_step": "Ghép final.mp4"})
            _concat_segments(
                final_segments,
                final_path,
                ffmpeg_path,
                final_concat_mode=final_concat_mode,
                audio_path=voice_path,
                emit_log=emit_log,
                stop_check=stop_check,
                active_processes=active_processes,
            )
            emit_status({"current_step": "Kiểm tra final.mp4"})
            _validate_final_output(src, final_path, ffprobe_path, emit_log=emit_log)
            ok_count += 1
            emit_status({
                "current_step": "Hoàn thành video",
                "processed_videos": video_index,
                "current_video": src.name,
            })
            emit_log(f"Hoàn thành {src.name}: {final_path}")
        except Exception as exc:
            message = f"Lỗi {src.name}: {exc}"
            emit_log(message)
            errors.append(message)
        emit_progress(int(video_index * 100 / len(paths)))
    if ok_count == 0:
        return False, errors[-1] if errors else "Không xử lý được video nào."
    if errors:
        return True, f"Đã xử lý {ok_count}/{len(paths)} video; {len(errors)} video lỗi. Xem log chi tiết."
    return True, f"Đã xử lý xong {ok_count}/{len(paths)} video. Output: {root}"
