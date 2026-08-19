from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.ffmpeg_tools import hidden_subprocess_kwargs, media_duration_from_probe, run_ffprobe, write_concat_list

GPU_ENCODER_MODES = {"auto", "nvidia", "cpu"}


def _normalize_encoder_mode(value: str) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in GPU_ENCODER_MODES else "auto"


def _video_encode_args(
    *,
    encoder_mode: str,
    bitrate: str,
    crf: str,
    encoder_preset: str = "p4",
    allow_bitrate: bool = True,
) -> list[str]:
    mode = _normalize_encoder_mode(encoder_mode)
    bitrate_text = str(bitrate or "auto").strip()
    auto_bitrate = bitrate_text.lower() in {"", "auto", "tự động", "giu nguyen", "giữ nguyên"}
    if mode == "nvidia":
        preset = str(encoder_preset or "p4").strip().lower()
        if preset not in {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}:
            preset = "p4"
        args = ["-c:v", "h264_nvenc", "-preset", preset]
        if allow_bitrate and not auto_bitrate:
            args += ["-b:v", bitrate_text]
        else:
            args += ["-cq", str(crf or "20")]
        return args
    args = ["-c:v", "libx264", "-preset", "veryfast"]
    if allow_bitrate and not auto_bitrate:
        args += ["-b:v", bitrate_text]
    else:
        args += ["-crf", str(crf or "20")]
    return args


def _probe_has_stream(payload: dict, codec_type: str) -> bool:
    return any(stream.get("codec_type") == codec_type for stream in payload.get("streams", []))


def _probe_primary_stream_duration(payload: dict, codec_type: str) -> float:
    for stream in payload.get("streams", []):
        if stream.get("codec_type") != codec_type:
            continue
        try:
            duration = float(stream.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            return duration
    return 0.0

def _effective_video_duration(payload: dict) -> float:
    format_duration = media_duration_from_probe(payload)
    video_duration = _probe_primary_stream_duration(payload, "video")
    if video_duration > 0:
        return video_duration
    return format_duration

def _media_video_duration(path: Path, ffprobe_path: str) -> float:
    return _effective_video_duration(run_ffprobe(path, ffprobe_path=ffprobe_path))


def _validate_final_output(
    src: Path,
    final_path: Path,
    ffprobe_path: str,
    *,
    emit_log: Callable[[str], None],
) -> None:
    source_probe = run_ffprobe(src, ffprobe_path=ffprobe_path)
    final_probe = run_ffprobe(final_path, ffprobe_path=ffprobe_path)
    source_duration = _effective_video_duration(source_probe)
    final_duration = _effective_video_duration(final_probe)
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


def _append_process_log(log_path: Path, message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _write_video_state(state_path: Path, payload: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if state_path.exists():
        try:
            current = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    current.update(payload)
    current["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temp_path = state_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(state_path)


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


def _demucs_python() -> str:
    project_root = Path(__file__).resolve().parents[1]
    local_python = project_root / ".venv-demucs" / "Scripts" / "python.exe"
    if local_python.is_file():
        return str(local_python)
    return sys.executable


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
    python_path = _demucs_python()
    cmd = [python_path, "-m", "demucs", "--two-stems=vocals", "-o", str(demucs_root), str(src)]
    emit_log(f"AI: tách giọng chính khỏi nhạc nền bằng Demucs ({python_path})...")
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
) -> tuple[Path | None, str]:
    original_audio = audio_dir / "original_audio.wav"
    voice_wav = audio_dir / "voice.wav"

    if _usable_file(voice_wav):
        emit_log(f"Resume: dùng lại voice.wav đã có cho {src.name}.")
        return voice_wav, "resume_voice_wav"

    if not _usable_file(original_audio):
        emit_log("Trích xuất original_audio.wav từ video nguồn...")
        rc = _run(
            [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-err_detect", "ignore_err", "-fflags", "+discardcorrupt", "-i", str(src), "-map", "0:a:0?", "-vn", "-ac", "2", "-ar", "44100", str(original_audio)],
            emit_log=emit_log,
            stop_check=stop_check,
            active_processes=active_processes,
        )
        if rc != 0 and _usable_file(original_audio):
            emit_log(f"Cảnh báo: original_audio.wav có lỗi decode một phần (exit {rc}) nhưng file đã được tạo, vẫn tiếp tục dùng.")
        elif rc != 0:
            emit_log(f"Cảnh báo: không xuất được original_audio.wav (exit {rc}).")
            return None, "no_audio"

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
    ai_voice_status = "ai_vocals" if source_audio != original_audio else "original_audio_fallback"
    if source_audio == original_audio:
        emit_log("Không dùng AI voice hoặc AI không khả dụng: dùng audio gốc làm voice tham chiếu.")
    else:
        emit_log("AI voice OK: dùng vocals đã tách để ghép final.")
    rc = _run(
        [ffmpeg_path, "-hide_banner", "-y", "-nostdin", "-i", str(source_audio), "-ac", "2", "-ar", "44100", str(voice_wav)],
        emit_log=emit_log,
        stop_check=stop_check,
        active_processes=active_processes,
    )
    if rc != 0:
        emit_log(f"Cảnh báo: không xuất được voice.wav (exit {rc}).")
        return None, "voice_export_failed"
    return voice_wav, ai_voice_status


def _split_exact_segments(
    src: Path,
    segments_dir: Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    segment_seconds: float,
    mute_audio: bool = False,
    encoder_mode: str = "cpu",
    encoder_preset: str = "p4",
    crf: str = "20",
    bitrate: str = "auto",
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> list[Path]:
    probe = run_ffprobe(src, ffprobe_path=ffprobe_path)
    duration = _effective_video_duration(probe)
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
        ] + _video_encode_args(encoder_mode=encoder_mode, encoder_preset=encoder_preset, bitrate=bitrate, crf=crf)
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
    encoder_mode: str = "cpu",
    encoder_preset: str = "p4",
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
    ] + _video_encode_args(encoder_mode=encoder_mode, encoder_preset=encoder_preset, bitrate=bitrate, crf=crf)
    cmd += ["-an", str(output_path)]
    emit_log(f"Zoom đoạn {src.name}: {zoom_percent}% -> {output_path.name}")
    rc = _run(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if rc != 0 or not output_path.exists():
        raise RuntimeError(f"Zoom đoạn {src.name} thất bại: exit {rc}")


def _pad_final_duration(
    src: Path,
    final_path: Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    encoder_mode: str,
    encoder_preset: str,
    crf: str,
    bitrate: str,
    emit_log: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
) -> None:
    source_duration = _media_video_duration(src, ffprobe_path)
    final_duration = _media_video_duration(final_path, ffprobe_path)
    missing = source_duration - final_duration
    if missing <= 0.1 or missing > 1.5:
        return
    emit_log(f"Final ngắn hơn source {missing:.3f}s; kéo dài frame cuối để giảm lệch duration.")
    temp_path = final_path.with_name(final_path.stem + "_duration_fix.mp4")
    video_args = _video_encode_args(encoder_mode=encoder_mode, encoder_preset=encoder_preset, bitrate=bitrate, crf=crf)
    cmd = [
        ffmpeg_path, "-hide_banner", "-y", "-nostdin",
        "-i", str(final_path),
        "-vf", f"tpad=stop_mode=clone:stop_duration={missing:.3f}",
        "-af", f"apad=pad_dur={missing:.3f}",
        "-t", f"{source_duration:.3f}",
    ] + video_args + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temp_path)]
    rc = _run(cmd, emit_log=emit_log, stop_check=stop_check, active_processes=active_processes)
    if rc != 0 or not temp_path.exists():
        emit_log(f"Cảnh báo: không kéo dài được final để khớp duration (exit {rc}).")
        return
    temp_path.replace(final_path)


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
    encoder_mode: str = "cpu",
    encoder_preset: str = "p4",
    crf: str = "20",
    bitrate: str = "auto",
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
    ] + _video_encode_args(encoder_mode=encoder_mode, encoder_preset=encoder_preset, bitrate=bitrate, crf=crf) + [
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
    encoder_mode: str = "auto",
    encoder_preset: str = "p4",
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
    encoder_mode = _normalize_encoder_mode(encoder_mode)
    paths = [Path(p).resolve() for p in input_paths if Path(p).is_file()]
    if not paths:
        return False, "Không có video nguồn hợp lệ."
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ok_count = 0
    errors: list[str] = []

    def report_video_progress(video_index: int, fraction: float) -> None:
        total = max(1, len(paths))
        overall = ((video_index - 1) + max(0.0, min(1.0, fraction))) / total
        emit_progress(max(1, min(95, int(overall * 100))))

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
        process_log_path = video_dir / "process.log"
        state_path = video_dir / "project_state.json"

        _append_process_log(process_log_path, f"===== Xử lý video {video_index}/{len(paths)}: {src.name} =====")
        _write_video_state(state_path, {
            "source": str(src),
            "video_name": src.name,
            "status": "running",
            "step": "Chuẩn bị video",
            "processed_videos": max(0, video_index - 1),
            "total_videos": len(paths),
            "video_dir": str(video_dir),
        })

        def video_log(message: str) -> None:
            emit_log(message)
            _append_process_log(process_log_path, message)

        video_log(f"===== Xử lý video {video_index}/{len(paths)}: {src.name} =====")
        emit_status({
            "job_stage": "Đang xử lý batch",
            "current_video": src.name,
            "current_step": "Chuẩn bị video",
            "processed_videos": max(0, video_index - 1),
            "total_videos": len(paths),
        })
        try:
            video_encoder_mode = encoder_mode
            audio_dir = video_dir / "audio"
            parts_dir = video_dir / "parts"
            audio_dir.mkdir(parents=True, exist_ok=True)
            parts_dir.mkdir(parents=True, exist_ok=True)

            emit_status({"current_step": "Tách voice / chuẩn bị audio"})
            _write_video_state(state_path, {"status": "running", "step": "Tách voice / chuẩn bị audio"})
            report_video_progress(video_index, 0.03)
            voice_path, ai_voice_status = _prepare_voice_track(
                src,
                audio_dir,
                ffmpeg_path,
                enable_ai_voice=enable_ai_voice,
                remove_background=remove_background,
                emit_log=video_log,
                stop_check=stop_check,
                active_processes=active_processes,
            )
            if voice_path:
                video_log(f"Đã có voice.wav: {voice_path}")

            emit_status({"current_step": "Cắt đoạn video không audio"})
            _write_video_state(state_path, {"status": "running", "step": "Cắt đoạn video không audio", "voice_path": str(voice_path) if voice_path else "", "ai_voice_status": ai_voice_status})
            report_video_progress(video_index, 0.12)
            if video_encoder_mode == "auto":
                video_log("Encode mode auto: ưu tiên NVIDIA NVENC, lỗi sẽ tự fallback CPU.")
                video_encoder_mode = "nvidia"
            else:
                video_log(f"Encode mode: {video_encoder_mode}")
            encoder_label = "GPU NVIDIA NVENC" if video_encoder_mode == "nvidia" else "CPU libx264"
            emit_status({"current_encoder": encoder_label})
            _write_video_state(state_path, {"encoder_mode": video_encoder_mode, "encoder_label": encoder_label})

            raw_segments = _split_exact_segments(
                src,
                raw_segments_dir,
                ffmpeg_path,
                ffprobe_path,
                segment_seconds=float(segment_seconds),
                mute_audio=True,
                encoder_mode=video_encoder_mode,
                encoder_preset=encoder_preset,
                crf=crf,
                bitrate=bitrate,
                emit_log=video_log,
                stop_check=stop_check,
                active_processes=active_processes,
            )

            report_video_progress(video_index, 0.40)

            final_segments: list[Path] = []
            emit_status({"current_step": "Zoom xen kẽ từng đoạn"})
            _write_video_state(state_path, {"status": "running", "step": "Zoom xen kẽ từng đoạn", "raw_segments": len(raw_segments)})
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
                    encoder_mode=video_encoder_mode,
                    encoder_preset=encoder_preset,
                    emit_log=video_log,
                    stop_check=stop_check,
                    active_processes=active_processes,
                )
                final_segments.append(out)
                if raw_segments:
                    report_video_progress(video_index, 0.40 + 0.35 * idx / len(raw_segments))

            final_path = video_dir / "final.mp4"
            emit_status({"current_step": "Ghép final.mp4"})
            _write_video_state(state_path, {"status": "running", "step": "Ghép final.mp4", "parts": len(final_segments), "final_path": str(final_path)})
            report_video_progress(video_index, 0.78)
            try:
                _concat_segments(
                    final_segments,
                    final_path,
                    ffmpeg_path,
                    final_concat_mode=final_concat_mode,
                    audio_path=voice_path,
                    encoder_mode=video_encoder_mode,
                    encoder_preset=encoder_preset,
                    crf=crf,
                    bitrate=bitrate,
                    emit_log=video_log,
                    stop_check=stop_check,
                    active_processes=active_processes,
                )
            except Exception as exc:
                if encoder_mode == "auto" and video_encoder_mode == "nvidia":
                    video_log(f"NVENC lỗi, fallback CPU: {exc}")
                    video_encoder_mode = "cpu"
                    encoder_label = "CPU libx264"
                    emit_status({"current_encoder": encoder_label})
                    _write_video_state(state_path, {"encoder_mode": video_encoder_mode, "encoder_label": encoder_label})
                    raw_segments = _split_exact_segments(
                        src,
                        raw_segments_dir,
                        ffmpeg_path,
                        ffprobe_path,
                        segment_seconds=float(segment_seconds),
                        mute_audio=True,
                        encoder_mode=video_encoder_mode,
                        encoder_preset=encoder_preset,
                        crf=crf,
                        bitrate=bitrate,
                        emit_log=video_log,
                        stop_check=stop_check,
                        active_processes=active_processes,
                    )
                    final_segments = []
                    for idx, raw in enumerate(raw_segments, 1):
                        zoom = odd_zoom_percent if idx % 2 == 1 else even_zoom_percent
                        out = parts_dir / f"part_{idx:03d}.mp4"
                        if out.exists():
                            try:
                                out.unlink()
                            except OSError:
                                pass
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
                            encoder_mode=video_encoder_mode,
                            encoder_preset=encoder_preset,
                            emit_log=video_log,
                            stop_check=stop_check,
                            active_processes=active_processes,
                        )
                        final_segments.append(out)
                    if final_path.exists():
                        try:
                            final_path.unlink()
                        except OSError:
                            pass
                    _concat_segments(
                        final_segments,
                        final_path,
                        ffmpeg_path,
                        final_concat_mode=final_concat_mode,
                        audio_path=voice_path,
                        encoder_mode=video_encoder_mode,
                        encoder_preset=encoder_preset,
                        crf=crf,
                        bitrate=bitrate,
                        emit_log=video_log,
                        stop_check=stop_check,
                        active_processes=active_processes,
                    )
                else:
                    raise

            report_video_progress(video_index, 0.90)
            _pad_final_duration(
                src,
                final_path,
                ffmpeg_path,
                ffprobe_path,
                encoder_mode=video_encoder_mode,
                encoder_preset=encoder_preset,
                crf=crf,
                bitrate=bitrate,
                emit_log=video_log,
                stop_check=stop_check,
                active_processes=active_processes,
            )

            emit_status({"current_step": "Kiểm tra final.mp4"})
            _write_video_state(state_path, {"status": "running", "step": "Kiểm tra final.mp4"})
            _validate_final_output(src, final_path, ffprobe_path, emit_log=video_log)
            report_video_progress(video_index, 0.98)
            ok_count += 1
            emit_status({
                "current_step": "Hoàn thành video",
                "processed_videos": video_index,
                "current_video": src.name,
            })
            _write_video_state(state_path, {"status": "success", "step": "Hoàn thành", "final_path": str(final_path), "parts": len(final_segments), "encoder_mode": video_encoder_mode, "encoder_label": encoder_label, "ai_voice_status": ai_voice_status})
            video_log(f"Hoàn thành {src.name}: {final_path}")
        except Exception as exc:
            message = f"Lỗi {src.name}: {exc}"
            _write_video_state(state_path, {"status": "error", "step": "Lỗi", "error": str(exc)})
            video_log(message)
            errors.append(message)
        emit_progress(int(video_index * 100 / len(paths)))
    if ok_count == 0:
        return False, errors[-1] if errors else "Không xử lý được video nào."
    if errors:
        return True, f"Đã xử lý {ok_count}/{len(paths)} video; {len(errors)} video lỗi. Xem log chi tiết."
    return True, f"Đã xử lý xong {ok_count}/{len(paths)} video. Output: {root}"
