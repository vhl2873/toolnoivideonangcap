from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from math import isfinite
from pathlib import Path
from typing import Iterable


def hidden_subprocess_kwargs() -> dict[str, object]:
    """Hide child console windows when launching FFmpeg tools on Windows."""
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def find_executable(name: str) -> str | None:
    from_path = shutil.which(name)
    if from_path:
        return from_path

    executable_name = name if name.lower().endswith(".exe") else f"{name}.exe"
    roots = _candidate_roots()
    for root in roots:
        direct_candidates = (
            root / executable_name,
            root / "bin" / executable_name,
            root / "tools" / "ffmpeg" / "bin" / executable_name,
        )
        for candidate in direct_candidates:
            if candidate.is_file():
                return str(candidate)

        ffmpeg_root = root / "tools" / "ffmpeg"
        if ffmpeg_root.is_dir():
            for candidate in ffmpeg_root.glob("**/bin/" + executable_name):
                if candidate.is_file():
                    return str(candidate)

    return None


def _candidate_roots() -> list[Path]:
    roots = [Path.cwd()]

    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            roots.append(Path(bundle_dir).resolve())
    else:
        roots.append(Path(__file__).resolve().parents[1])

    unique: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in unique:
            unique.append(resolved)
    return unique


def check_required_tools() -> tuple[bool, dict[str, str | None]]:
    tools = {
        "ffmpeg": find_executable("ffmpeg"),
        "ffprobe": find_executable("ffprobe"),
    }
    return all(tools.values()), tools


def run_ffprobe(path: str | Path, ffprobe_path: str = "ffprobe") -> dict:
    input_path = Path(path)
    if not input_path.is_file():
        raise RuntimeError(f"Không tìm thấy file hoặc không đọc được: {path}")

    command = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        reason = _friendly_ffprobe_error(message)
        raise RuntimeError(f"ffprobe không đọc được {path}: {reason}")

    import json

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc


def _friendly_ffprobe_error(message: str) -> str:
    text = message.strip()
    lower = text.lower()
    if "invalid data found when processing input" in lower:
        return "file không phải video hợp lệ, bị hỏng, hoặc tải/chuyển đổi chưa xong"
    if "moov atom not found" in lower:
        return "file MP4 bị thiếu metadata moov atom, thường do tải chưa xong hoặc file bị hỏng"
    if "no such file or directory" in lower:
        return "không tìm thấy file"
    return text or "lỗi không xác định từ ffprobe"


def _positive_float(value: object) -> float | None:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return None
    if number > 0 and isfinite(number):
        return number
    return None


def _duration_from_tag(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return None
    direct = _positive_float(text)
    if direct is not None:
        return direct
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    except ValueError:
        return None


def _rate_to_float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text or text == "0/0":
        return None
    if "/" not in text:
        return _positive_float(text)
    left, right = text.split("/", 1)
    try:
        numerator = float(left)
        denominator = float(right)
    except ValueError:
        return None
    if denominator <= 0:
        return None
    return _positive_float(numerator / denominator)


def _duration_from_time_base(duration_ts: object, time_base: object) -> float | None:
    ticks = _positive_float(duration_ts)
    seconds_per_tick = _rate_to_float(time_base)
    if ticks is None or seconds_per_tick is None:
        return None
    return _positive_float(ticks * seconds_per_tick)


def media_duration_from_probe(payload: dict) -> float:
    values: list[float] = []
    fmt = payload.get("format", {})
    duration = _positive_float(fmt.get("duration"))
    if duration is not None:
        values.append(duration)

    for stream in payload.get("streams", []):
        if stream.get("codec_type") not in {"video", "audio"}:
            continue

        duration = _positive_float(stream.get("duration"))
        if duration is not None:
            values.append(duration)

        duration = _duration_from_time_base(stream.get("duration_ts"), stream.get("time_base"))
        if duration is not None:
            values.append(duration)

        frame_rate = _rate_to_float(stream.get("avg_frame_rate")) or _rate_to_float(
            stream.get("r_frame_rate")
        )
        frame_count = _positive_float(stream.get("nb_frames"))
        if frame_rate and frame_count:
            duration = _positive_float(frame_count / frame_rate)
            if duration is not None:
                values.append(duration)

        tags = stream.get("tags", {})
        if isinstance(tags, dict):
            for key, value in tags.items():
                if str(key).upper().startswith("DURATION"):
                    duration = _duration_from_tag(value)
                    if duration is not None:
                        values.append(duration)

    return max(values) if values else 0.0


def escape_concat_path(path: str | Path) -> str:
    normalized = Path(path).resolve().as_posix()
    return normalized.replace("\\", "\\\\").replace("'", "\\'")


def write_concat_list(
    paths: Iterable[str | Path],
    list_path: str | Path,
    durations: Iterable[float | None] | None = None,
) -> None:
    """Ghi concat demuxer list. `durations` chỉ dùng cho stream không có độ dài cố định (vd. ảnh).
    Với file video thường: để `durations=None` — nếu ghi duration lệch so với thật, FFmpeg cắt ngắn segment."""
    list_file = Path(list_path)
    duration_values = list(durations) if durations is not None else None
    with list_file.open("w", encoding="utf-8", newline="\n") as handle:
        for index, path in enumerate(paths):
            handle.write(f"file '{escape_concat_path(path)}'\n")
            if duration_values is None or index >= len(duration_values):
                continue
            duration = duration_values[index]
            if duration and duration > 0:
                handle.write(f"duration {duration:.6f}\n")


def _emit_concat_line(
    line: str,
    emit_log: Callable[[str], None],
    emit_progress: Callable[[str], None],
) -> None:
    if line.startswith("out_time="):
        emit_progress(line.removeprefix("out_time=").strip())
        return
    if line.startswith("out_time_ms="):
        emit_progress(line.removeprefix("out_time_ms=").strip())
        return
    # Bỏ qua các dòng progress key=value không quan trọng
    _SKIP_PREFIXES = (
        "frame=", "fps=", "stream_", "bitrate=", "total_size=",
        "out_time_us=", "dup_frames=", "drop_frames=", "speed=",
        "progress=",
    )
    if any(line.startswith(p) for p in _SKIP_PREFIXES):
        return
    emit_log(line)


def _log_file_size(path: Path, emit_log: Callable[[str], None], *, label: str = "") -> None:
    try:
        sz = path.stat().st_size
        emit_log(f"{label}: {path.name} — {sz / (1024*1024):.1f} MB")
    except OSError:
        emit_log(f"{label}: khong doc duoc kich thuoc {path}")


def _log_total_input_size(paths: list[str], emit_log: Callable[[str], None]) -> None:
    try:
        total = sum(Path(p).stat().st_size for p in paths)
        emit_log(f"Tong kich thuoc {len(paths)} file input: {total / (1024*1024):.1f} MB")
    except OSError:
        pass


def _log_average_speed(
    paths: list[str],
    started_at: float,
    emit_log: Callable[[str], None],
) -> None:
    elapsed = max(0.001, time.perf_counter() - started_at)
    try:
        total_mb = sum(Path(p).stat().st_size for p in paths) / (1024 * 1024)
    except OSError:
        return
    emit_log(f"Toc do trung binh: {total_mb / elapsed:.1f} MB/s trong {elapsed:.1f}s")


def _run_ffmpeg_pipe_progress(
    command: list[str],
    *,
    emit_log: Callable[[str], None],
    emit_progress: Callable[[str], None],
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
            line = raw_line.strip()
            if not line:
                continue
            _emit_concat_line(line, emit_log, emit_progress)
        return process.wait()
    finally:
        try:
            active_processes.remove(process)
        except ValueError:
            pass


def _run_concat_demuxer(
    ffmpeg_path: str,
    list_path: Path,
    output_path: Path,
    *,
    emit_log: Callable[[str], None],
    emit_progress: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
    genpts: bool = False,
    auto_convert: bool = True,
) -> int:
    """Chạy lệnh concat demuxer chuẩn. Nếu genpts=True thì thêm +genpts để fix timestamp lỗi."""
    fflags = "+igndts+genpts" if genpts else "+igndts"
    cmd = [
        ffmpeg_path, "-hide_banner", "-y", "-nostdin",
        "-fflags", fflags,
        "-f", "concat", "-safe", "0",
    ]
    if not auto_convert:
        cmd.extend(["-auto_convert", "0"])
    cmd.extend([
        "-i", str(list_path),
        "-map", "0", "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-progress", "pipe:1", "-nostats",
        str(output_path),
    ])
    return _run_ffmpeg_pipe_progress(
        cmd,
        emit_log=emit_log,
        emit_progress=emit_progress,
        stop_check=stop_check,
        active_processes=active_processes,
    )


def _remux_mp4_to_mkv_dir(
    paths: list[str],
    mkv_dir: Path,
    ffmpeg_path: str,
    *,
    emit_log: Callable[[str], None],
    emit_progress: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
    genpts: bool = False,
) -> list[str] | None:
    """Remux moi MP4 -> MKV (stream copy, cung AVCC, khong can h264_mp4toannexb).
    Muc dich: tranh FFmpeg concat demuxer tu dong chen h264_mp4toannexb vao input MP4.
    Khi input la MKV, concat demuxer KHONG insert bsf nay."""
    mkv_paths: list[str] = []
    for i, input_path in enumerate(paths):
        if stop_check():
            return None
        mkv_path = mkv_dir / f"seg_{i:05d}.mkv"
        emit_log(f"Remux {i + 1}/{len(paths)} -> MKV: {Path(input_path).name}")
        cmd = [
            ffmpeg_path, "-hide_banner", "-y", "-nostdin",
            "-fflags", "+igndts+genpts" if genpts else "+igndts",
            "-i", input_path,
            "-map", "0",
            "-c", "copy",
            str(mkv_path),
        ]
        rc = _run_ffmpeg_pipe_progress(
            cmd,
            emit_log=emit_log,
            emit_progress=emit_progress,
            stop_check=stop_check,
            active_processes=active_processes,
        )
        if stop_check():
            return None
        if rc != 0:
            emit_log(f"Loi remux file {i + 1} sang MKV: exit {rc}")
            return None
        # Sanity check: MKV phai co duration gan bang MP4 goc
        try:
            src_size = Path(input_path).stat().st_size
            mkv_size = mkv_path.stat().st_size
            if mkv_size < src_size * 0.5:
                emit_log(
                    f"Canh bao: MKV seg {i+1} nho bat thuong "
                    f"({mkv_size/(1024*1024):.1f} MB vs input {src_size/(1024*1024):.1f} MB)"
                )
        except OSError:
            pass
        mkv_paths.append(str(mkv_path))
    return mkv_paths


def run_concat_copy(
    paths: list[str],
    output: str | Path,
    ffmpeg_path: str,
    *,
    emit_log: Callable[[str], None],
    emit_progress: Callable[[str], None],
    stop_check: Callable[[], bool],
    active_processes: list[subprocess.Popen[str]],
    expected_duration: float | None = None,
    ffprobe_path: str | None = None,
    file_durations: list[float | None] | None = None,
    safe_mode: bool = False,
) -> tuple[bool, str]:
    """Stream-copy concat (khong render lai).

    Chien luoc:
    - safe_mode=True: uu tien remux moi input sang MKV tam roi concat voi +genpts
      de giam loi dung hinh / timestamp / bitstream o diem noi.
    - Input MP4 + output MKV: remux moi MP4 -> MKV roi concat demuxer cac MKV.
      Cach nay tranh h264_mp4toannexb (vi MKV->MKV khong can bsf).
    - Input MP4 + output MP4: thu Python mp4_concat (non-frag) truoc.
    - Input khong phai MP4: concat demuxer truc tiep.
    """
    import shutil as _shutil
    from core.mp4_concat import mp4_concat as _py_mp4_concat

    started_at = time.perf_counter()
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    is_mp4_input = any(Path(p).suffix.lower() in {".mp4", ".m4v", ".mov"} for p in paths)
    is_mp4_output = output_path.suffix.lower() in {".mp4", ".m4v"}
    is_mkv_output = output_path.suffix.lower() in {".mkv", ".webm"}

    if safe_mode:
        emit_log("Che do an toan: remux tung file sang MKV tam + noi voi genpts de giam loi dung hinh.")
        mkv_dir = output_path.parent / f"_fvc_safe_mkv_{output_path.stem}"
        try:
            mkv_dir.mkdir(parents=True, exist_ok=True)
            emit_log(f"B1/2: Remux an toan {len(paths)} file -> MKV tam...")
            mkv_paths = _remux_mp4_to_mkv_dir(
                paths, mkv_dir, ffmpeg_path,
                emit_log=emit_log, emit_progress=emit_progress,
                stop_check=stop_check, active_processes=active_processes,
                genpts=True,
            )
            if stop_check():
                return False, "Đã dừng theo yêu cầu."
            if mkv_paths is None:
                return False, "Remux an toan sang MKV that bai."

            list_path = mkv_dir / "list.txt"
            write_concat_list(mkv_paths, list_path)
            emit_log(f"B2/2: Noi an toan {len(mkv_paths)} MKV -> {output_path.name} (+genpts)...")
            rc = _run_concat_demuxer(
                ffmpeg_path, list_path, output_path,
                emit_log=emit_log, emit_progress=emit_progress,
                stop_check=stop_check, active_processes=active_processes,
                genpts=True,
                auto_convert=False,
            )
            if stop_check():
                return False, "Đã dừng theo yêu cầu."
            if rc != 0:
                return False, f"FFmpeg exit {rc} o che do noi an toan."
        finally:
            _shutil.rmtree(mkv_dir, ignore_errors=True)

        _log_file_size(output_path, emit_log, label="Output")
        _log_total_input_size(paths, emit_log)
        _log_average_speed(paths, started_at, emit_log)
        return _verify_concat_output(
            output_path, expected_duration, ffprobe_path, emit_log, input_paths=paths
        )

    # ── Nhánh 1: MP4 → MP4 cho non-fragmented (Python mp4_concat) ────────────
    if is_mp4_input and is_mp4_output:
        emit_log(f"[mp4_concat] Thu Python-level concat {len(paths)} file...")
        try:
            ok = _py_mp4_concat(paths, str(output_path), emit_log=emit_log)
        except Exception as exc:
            emit_log(f"[mp4_concat] Loi: {exc} — fallback MKV intermediate")
            ok = False
        if ok:
            _log_file_size(output_path, emit_log, label="Output")
            _log_total_input_size(paths, emit_log)
            _log_average_speed(paths, started_at, emit_log)
            return _verify_concat_output(
                output_path, expected_duration, ffprobe_path, emit_log, input_paths=paths
            )
        # Neu MP4 output ma fail, khuyen dung MKV
        emit_log("Khuyen dung output MKV cho file MP4 fragmented (fMP4).")

    # ── Nhánh 2: MP4 input + MKV output → thử concat trực tiếp trước ─────────
    # Direct concat voi -auto_convert 0 chi doc/ghi 1 vong nen nhanh hon nhieu.
    # Neu FFmpeg/nguon file khong chap nhan, fallback ve cach remux MKV trung gian cu.
    if is_mp4_input and is_mkv_output:
        with tempfile.TemporaryDirectory(prefix="fast_concat_direct_") as tmp_dir:
            direct_list = Path(tmp_dir) / "list.txt"
            write_concat_list(paths, direct_list)
            emit_log(
                f"Thu noi truc tiep MP4 -> MKV {len(paths)} file "
                "(1 vong copy, -auto_convert 0)..."
            )
            direct_rc = _run_concat_demuxer(
                ffmpeg_path, direct_list, output_path,
                emit_log=emit_log, emit_progress=emit_progress,
                stop_check=stop_check, active_processes=active_processes,
                genpts=False,
                auto_convert=False,
            )
            if stop_check():
                return False, "Đã dừng theo yêu cầu."
            if direct_rc == 0:
                _log_file_size(output_path, emit_log, label="Output")
                _log_total_input_size(paths, emit_log)
                _log_average_speed(paths, started_at, emit_log)
                ok, message = _verify_concat_output(
                    output_path, expected_duration, ffprobe_path, emit_log, input_paths=paths
                )
                if ok:
                    return ok, message
                emit_log(f"Noi truc tiep tao output khong dat: {message}")

            reason = f"exit {direct_rc}" if direct_rc != 0 else "verify output khong dat"
            emit_log(
                f"Noi truc tiep MP4 -> MKV khong thanh cong ({reason}). "
                "Fallback sang remux MKV trung gian an toan."
            )
            try:
                if output_path.exists():
                    output_path.unlink()
            except OSError:
                pass

        mkv_dir = output_path.parent / f"_fvc_mkv_{output_path.stem}"
        try:
            mkv_dir.mkdir(parents=True, exist_ok=True)
            emit_log(f"B1/2: Remux {len(paths)} file MP4 -> MKV (tranh h264_mp4toannexb)...")
            mkv_paths = _remux_mp4_to_mkv_dir(
                paths, mkv_dir, ffmpeg_path,
                emit_log=emit_log, emit_progress=emit_progress,
                stop_check=stop_check, active_processes=active_processes,
            )
            if stop_check():
                return False, "Đã dừng theo yêu cầu."
            if mkv_paths is None:
                return False, "Remux MP4 sang MKV that bai."

            list_path = mkv_dir / "list.txt"
            write_concat_list(mkv_paths, list_path)
            emit_log(f"B2/2: Noi {len(mkv_paths)} MKV -> {output_path.name}...")
            rc = _run_concat_demuxer(
                ffmpeg_path, list_path, output_path,
                emit_log=emit_log, emit_progress=emit_progress,
                stop_check=stop_check, active_processes=active_processes,
                genpts=False,
            )
            if stop_check():
                return False, "Đã dừng theo yêu cầu."
            if rc != 0:
                emit_log(f"Concat MKV that bai (exit {rc}). Thu +genpts...")
                try:
                    if output_path.exists():
                        output_path.unlink()
                except OSError:
                    pass
                rc = _run_concat_demuxer(
                    ffmpeg_path, list_path, output_path,
                    emit_log=emit_log, emit_progress=emit_progress,
                    stop_check=stop_check, active_processes=active_processes,
                    genpts=True,
                )
                if rc != 0:
                    return False, f"FFmpeg exit {rc} (da thu +genpts)."
        finally:
            _shutil.rmtree(mkv_dir, ignore_errors=True)

        _log_file_size(output_path, emit_log, label="Output")
        _log_total_input_size(paths, emit_log)
        _log_average_speed(paths, started_at, emit_log)
        return _verify_concat_output(
            output_path, expected_duration, ffprobe_path, emit_log, input_paths=paths
        )

    # ── Nhánh 3: Non-MP4 input → concat demuxer truc tiep ────────────────────
    with tempfile.TemporaryDirectory(prefix="fast_concat_") as tmp_dir:
        list_path = Path(tmp_dir) / "list.txt"
        write_concat_list(paths, list_path, durations=file_durations)
        emit_log(f"FFmpeg concat {len(paths)} file -> {output_path.name}")

        rc = _run_concat_demuxer(
            ffmpeg_path, list_path, output_path,
            emit_log=emit_log, emit_progress=emit_progress,
            stop_check=stop_check, active_processes=active_processes,
            genpts=False,
        )
        if stop_check():
            return False, "Đã dừng theo yêu cầu."

        if rc != 0:
            emit_log(f"FFmpeg concat that bai (exit {rc}). Thu +genpts...")
            try:
                if output_path.exists():
                    output_path.unlink()
            except OSError:
                pass
            rc = _run_concat_demuxer(
                ffmpeg_path, list_path, output_path,
                emit_log=emit_log, emit_progress=emit_progress,
                stop_check=stop_check, active_processes=active_processes,
                genpts=True,
            )
            if stop_check():
                return False, "Đã dừng theo yêu cầu."
            if rc != 0:
                return False, f"FFmpeg exit {rc} (đã thử cả +genpts)."

    _log_file_size(output_path, emit_log, label="Output")
    _log_total_input_size(paths, emit_log)
    _log_average_speed(paths, started_at, emit_log)
    return _verify_concat_output(
        output_path, expected_duration, ffprobe_path, emit_log, input_paths=paths
    )


def _format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _verify_concat_output(
    output_path: Path,
    expected_duration: float | None,
    ffprobe_path: str | None,
    emit_log: Callable[[str], None],
    *,
    input_paths: list[str] | None = None,
) -> tuple[bool, str]:
    """Verify output: tồn tại, không rỗng, duration gần với expected (nếu có)."""
    try:
        size = output_path.stat().st_size
    except OSError:
        return False, f"Không tìm thấy file output: {output_path}"
    if size == 0:
        return False, f"File output rỗng: {output_path}"

    out_duration = 0.0
    if ffprobe_path:
        try:
            payload = run_ffprobe(output_path, ffprobe_path=ffprobe_path)
            out_duration = media_duration_from_probe(payload)
            if out_duration > 0:
                emit_log(f"ffprobe output: {_format_seconds(out_duration)}")
        except Exception:
            pass

    # Neu khong co expected, suy ra tu input_paths
    if (not expected_duration or expected_duration <= 0) and input_paths and ffprobe_path:
        total = 0.0
        for p in input_paths:
            try:
                payload = run_ffprobe(p, ffprobe_path=ffprobe_path)
                total += media_duration_from_probe(payload)
            except Exception:
                total = 0.0
                break
        if total > 0:
            expected_duration = total

    size_mb = size / (1024 * 1024)

    # Strict check: output duration phai >= 90% expected
    if expected_duration and expected_duration > 0 and out_duration > 0:
        ratio = out_duration / expected_duration
        if ratio < 0.9:
            return False, (
                f"Output bị CẮT NGẮN: {_format_seconds(out_duration)} "
                f"(mong đợi ~{_format_seconds(expected_duration)}, chỉ được {ratio*100:.0f}%). "
                f"File: {output_path.name} ({size_mb:.1f} MB). "
                f"Nguyên nhân có thể do file gốc bị fMP4/timestamp lỗi."
            )
        emit_log(
            f"Duration OK: {_format_seconds(out_duration)} / mong đợi ~"
            f"{_format_seconds(expected_duration)} ({ratio*100:.0f}%)"
        )

    return True, f"Hoàn tất: {output_path} ({size_mb:.1f} MB)"
