from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from core.batch_pipeline import process_voice_split_alternate_zoom_batch
from core.ffmpeg_tools import check_required_tools, media_duration_from_probe, run_concat_copy, run_ffprobe
from core.media_extra import (
    apply_video_effects, extract_audio, separate_vocals_background, split_video_by_duration, transform_video_zoom,
)

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_FILE = ROOT / "data" / "projects.json"
HOST = "127.0.0.1"
PORT = 8765

STATUSES = (
    "Bản nháp", "Chưa chạy", "Đang chờ", "Đang chạy",
    "Tạm dừng", "Hoàn thành", "Lỗi", "Đã hủy",
)
TASKS = (
    "Nối video", "Chuẩn hóa video", "Chia nhỏ video",
    "Phóng to/thu nhỏ", "Thêm hiệu ứng", "Batch voice + cut + zoom",
)
PRIORITIES = ("Khẩn cấp", "Cao", "Bình thường", "Thấp")


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"version": 1, "projects": [], "settings": self.default_settings()})

    @staticmethod
    def default_settings() -> dict:
        return {
            "max_concurrent_jobs": 2,
            "ffmpeg_threads": 4,
            "use_gpu": True,
            "auto_start_next": True,
        }

    def _read(self) -> dict:
        with self.lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                backup = self.path.with_suffix(".broken.json")
                if self.path.exists():
                    shutil.copy2(self.path, backup)
                payload = {"version": 1, "projects": [], "settings": self.default_settings()}
                self._write(payload)
            payload.setdefault("projects", [])
            payload.setdefault("settings", self.default_settings())
            return payload

    def _write(self, payload: dict) -> None:
        with self.lock:
            temp = self.path.with_name(f"{self.path.stem}.{uuid.uuid4().hex}.tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            last_error: OSError | None = None
            for _attempt in range(8):
                try:
                    os.replace(temp, self.path)
                    return
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.08)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            if last_error:
                raise last_error

    def list_projects(self) -> list[dict]:
        return self._read()["projects"]

    def create(self, values: dict) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        project = {
            "id": uuid.uuid4().hex,
            "name": str(values.get("name", "")).strip() or "Dự án chưa đặt tên",
            "task_type": values.get("task_type") if values.get("task_type") in TASKS else TASKS[0],
            "status": "Bản nháp",
            "progress": 0,
            "priority": values.get("priority") if values.get("priority") in PRIORITIES else "Bình thường",
            "input_paths": list(values.get("input_paths") or []),
            "output_path": str(values.get("output_path", "")).strip(),
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "completed_at": "",
            "elapsed_seconds": 0,
            "remaining_seconds": 0,
            "error_message": "",
            "settings": dict(values.get("settings") or {}),
            "logs": [{"time": now, "level": "info", "message": "Đã tạo dự án."}],
        }
        payload = self._read()
        payload["projects"].insert(0, project)
        self._write(payload)
        return project

    def update(self, project_id: str, changes: dict) -> dict | None:
        payload = self._read()
        for project in payload["projects"]:
            if project["id"] != project_id:
                continue
            allowed = {
                "name", "task_type", "status", "progress", "priority",
                "input_paths", "output_path", "settings", "error_message",
                "job_stage", "current_video", "current_step", "processed_videos", "total_videos",
            }
            for key, value in changes.items():
                if key in allowed:
                    project[key] = value
            project["progress"] = max(0, min(100, int(project.get("progress", 0))))
            project["updated_at"] = datetime.now().isoformat(timespec="seconds")
            project.setdefault("logs", []).append({
                "time": project["updated_at"],
                "level": "error" if project.get("status") == "Lỗi" else "info",
                "message": f"Cập nhật dự án: {project.get('status', '')}.",
            })
            self._write(payload)
            return project
        return None

    def delete(self, project_id: str) -> bool:
        payload = self._read()
        original = len(payload["projects"])
        payload["projects"] = [p for p in payload["projects"] if p["id"] != project_id]
        if len(payload["projects"]) == original:
            return False
        self._write(payload)
        return True

    def duplicate(self, project_id: str) -> dict | None:
        source = next((p for p in self.list_projects() if p["id"] == project_id), None)
        if not source:
            return None
        return self.create({
            "name": f"{source['name']} — Bản sao",
            "task_type": source["task_type"],
            "priority": source["priority"],
            "input_paths": source.get("input_paths", []),
            "output_path": source.get("output_path", ""),
            "settings": source.get("settings", {}),
        })

    def settings(self) -> dict:
        return self._read()["settings"]

    def append_log(self, project_id: str, level: str, message: str) -> None:
        payload = self._read()
        for project in payload["projects"]:
            if project["id"] == project_id:
                project.setdefault("logs", []).append({
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "level": level,
                    "message": message,
                })
                project["logs"] = project["logs"][-500:]
                self._write(payload)
                return

    def update_settings(self, values: dict) -> dict:
        payload = self._read()
        payload["settings"].update(values)
        self._write(payload)
        return payload["settings"]


STORE = JsonStore(DATA_FILE)


def _safe_stem(path: str | Path) -> str:
    text = Path(path).stem.strip() or "video"
    for ch in '\\/:*?"<>|':
        text = text.replace(ch, "_")
    return text


def _collect_retry_failed_paths(paths: list[str], output: Path) -> list[str]:
    failed: list[str] = []
    for raw_path in paths:
        source = Path(raw_path).resolve()
        state_path = output / _safe_stem(source) / "project_state.json"
        if not state_path.is_file():
            continue
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("status", "")).lower() == "error":
            failed.append(str(source))
    return failed


def _video_output_dir(project: dict, source: str | Path) -> Path | None:
    output_text = str(project.get("output_path", "")).strip()
    if not output_text:
        return None
    output = Path(output_text).resolve()
    if not output.is_dir():
        return None
    return output / _safe_stem(source)


def _batch_results_for_project(project: dict) -> list[dict]:
    results: list[dict] = []
    for index, raw_path in enumerate(project.get("input_paths") or []):
        source = Path(raw_path).resolve()
        video_dir = _video_output_dir(project, source)
        state_path = video_dir / "project_state.json" if video_dir else None
        process_log = video_dir / "process.log" if video_dir else None
        final_path = video_dir / "final.mp4" if video_dir else None
        state: dict = {}
        if state_path and state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {"status": "unknown", "error": "Không đọc được project_state.json"}
        status = str(state.get("status") or "not_run")
        results.append({
            "index": index,
            "source": str(source),
            "name": source.name,
            "status": status,
            "step": state.get("step", ""),
            "error": state.get("error", ""),
            "parts": state.get("parts", 0),
            "encoder_mode": state.get("encoder_mode", ""),
            "encoder_label": state.get("encoder_label", ""),
            "folder_path": str(video_dir) if video_dir and video_dir.is_dir() else "",
            "final_path": str(final_path) if final_path and final_path.is_file() else "",
            "log_path": str(process_log) if process_log and process_log.is_file() else "",
            "updated_at": state.get("updated_at", ""),
        })
    return results


def _latest_final_for_project(project: dict) -> Path | None:
    output_text = str(project.get("output_path", "")).strip()
    if not output_text:
        return None
    output = Path(output_text).resolve()
    if output.is_file():
        return output
    if not output.is_dir():
        return None
    candidates = sorted(
        [item for item in output.glob("**/final.mp4") if item.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)], **hidden_subprocess_kwargs())
class JobRunner:
    def __init__(self) -> None:
        self._running: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_processes: dict[str, list[subprocess.Popen[str]]] = {}
        self._lock = threading.Lock()

    def start(self, project_id: str, operation: str, options: dict) -> tuple[bool, str]:
        with self._lock:
            if project_id in self._running:
                return False, "Dự án đang có tác vụ chạy."
            self._running.add(project_id)
            self._cancel_events[project_id] = threading.Event()
            self._active_processes[project_id] = []
        threading.Thread(
            target=self._run,
            args=(project_id, operation, options),
            daemon=True,
        ).start()
        return True, "Đã bắt đầu tác vụ."

    def cancel(self, project_id: str) -> tuple[bool, str]:
        with self._lock:
            event = self._cancel_events.get(project_id)
            processes = list(self._active_processes.get(project_id, []))
            if not event:
                return False, "Dự án không có tác vụ đang chạy."
            event.set()
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except OSError:
                pass
        STORE.append_log(project_id, "warning", "Người dùng yêu cầu dừng/hủy tác vụ.")
        STORE.update(project_id, {"status": "Đã hủy", "error_message": "Người dùng đã hủy tác vụ."})
        return True, "Đã gửi lệnh dừng/hủy tác vụ."

    def _run(self, project_id: str, operation: str, options: dict) -> None:
        try:
            project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
            if not project:
                return
            requested_paths = options.get("timeline_paths") if "timeline_paths" in options else project.get("input_paths", [])
            paths = [str(Path(p).resolve()) for p in requested_paths if Path(p).is_file()]
            if not paths:
                raise RuntimeError("Không có file video nguồn hợp lệ trên máy.")
            output_text = project.get("output_path", "").strip()
            if not output_text:
                output = (ROOT / "data" / "outputs" / project_id).resolve()
                output.mkdir(parents=True, exist_ok=True)
                STORE.update(project_id, {"output_path": str(output)})
                project["output_path"] = str(output)
            else:
                output = Path(output_text).resolve()
            ok_tools, tools = check_required_tools()
            if not ok_tools:
                raise RuntimeError("Không tìm thấy FFmpeg/FFprobe.")
            ffmpeg = str(tools["ffmpeg"])
            ffprobe = str(tools["ffprobe"])
            if operation == "batch_voice_cut_zoom" and bool(options.get("retry_failed_only", False)):
                retry_paths = _collect_retry_failed_paths(paths, output)
                if not retry_paths:
                    raise RuntimeError("Không tìm thấy video lỗi nào để chạy lại.")
                paths = retry_paths
                STORE.append_log(project_id, "info", f"Retry failed only: chạy lại {len(paths)} video lỗi.")

            STORE.update(project_id, {
                "status": "Đang chạy", "progress": 1, "error_message": "",
                "job_stage": "Chuẩn bị", "current_video": "", "current_step": "Kiểm tra đầu vào",
                "processed_videos": 0, "total_videos": len(paths),
            })
            STORE.append_log(project_id, "info", f"Bắt đầu: {operation}.")
            active_processes = self._active_processes.get(project_id, [])
            cancel_event = self._cancel_events.get(project_id)
            emit = lambda message: STORE.append_log(project_id, "info", str(message))
            stopped = lambda: bool(cancel_event and cancel_event.is_set())

            if operation == "concat":
                if output.suffix.lower() not in {".mp4", ".mkv"}:
                    output.mkdir(parents=True, exist_ok=True)
                    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project["name"])
                    extension = "mp4" if options.get("format") == "mp4" else "mkv"
                    output = output / f"{safe_name or 'video'}_long.{extension}"
                success, message = run_concat_copy(
                    paths, output, ffmpeg, emit_log=emit, emit_progress=lambda _value: None,
                    stop_check=stopped, active_processes=active_processes, ffprobe_path=ffprobe,
                    safe_mode=bool(options.get("safe_mode", False)),
                )
            elif operation == "batch_voice_cut_zoom":
                output.mkdir(parents=True, exist_ok=True)
                success, message = process_voice_split_alternate_zoom_batch(
                    paths,
                    output,
                    ffmpeg,
                    ffprobe,
                    enable_ai_voice=bool(options.get("enable_ai_voice", True)),
                    remove_background=bool(options.get("remove_background", True)),
                    segment_seconds=float(options.get("segment_seconds", 5)) * 60,
                    odd_zoom_percent=int(options.get("odd_zoom_percent", 100)),
                    even_zoom_percent=int(options.get("even_zoom_percent", 110)),
                    zoom_mode=str(options.get("zoom_mode", "center")),
                    pos_x=int(options.get("pos_x", 0)),
                    pos_y=int(options.get("pos_y", 0)),
                    crf=str(options.get("crf", "20")),
                    bitrate=str(options.get("bitrate", "auto")),
                    final_concat_mode=str(options.get("final_concat_mode", "fast")),
                    encoder_mode=str(options.get("encoder_mode", "auto")),
                    resume_enabled=bool(options.get("resume_enabled", True)),
                    emit_log=emit,
                    emit_progress=lambda value: STORE.update(project_id, {"progress": max(1, min(95, int(value)))}) or None,
                    emit_status=lambda changes: STORE.update(project_id, changes) or None,
                    stop_check=stopped,
                    active_processes=active_processes,
                )
            else:
                output.mkdir(parents=True, exist_ok=True)
                results: list[tuple[bool, str]] = []
                for index, input_path in enumerate(paths, 1):
                    if operation == "split":
                        result = split_video_by_duration(
                            input_path, output, ffmpeg, ffprobe,
                            segment_seconds=max(1, int(options.get("segment_seconds", 60))),
                            accurate=bool(options.get("accurate", False)), emit_log=emit,
                            stop_check=stopped, active_processes=active_processes,
                        )
                    elif operation == "zoom":
                        result = transform_video_zoom(
                            input_path, output, ffmpeg,
                            zoom_percent=int(options.get("zoom_percent", 110)),
                            pos_x=int(options.get("pos_x", 0)), pos_y=int(options.get("pos_y", 0)),
                            emit_log=emit, stop_check=stopped, active_processes=active_processes,
                        )
                    elif operation == "effects":
                        result = apply_video_effects(
                            input_path, output, ffmpeg,
                            effects=list(options.get("effects") or ["fade_in"]), emit_log=emit,
                            stop_check=stopped, active_processes=active_processes,
                        )
                    elif operation == "audio_ai":
                        result = separate_vocals_background(
                            input_path, output, emit_log=emit,
                            stop_check=stopped, active_processes=active_processes,
                        )
                    elif operation == "audio":
                        result = extract_audio(
                            input_path, output, ffmpeg,
                            audio_format=str(options.get("audio_format", "mp3")), emit_log=emit,
                            stop_check=stopped, active_processes=active_processes,
                        )
                    else:
                        raise RuntimeError("Tác vụ không được hỗ trợ.")
                    results.append(result)
                    STORE.update(project_id, {
                        "progress": int(index * 95 / len(paths)),
                        "processed_videos": index,
                        "current_video": Path(input_path).name,
                        "current_step": operation,
                    })
                    if not result[0]:
                        break
                success = bool(results) and all(item[0] for item in results)
                message = results[-1][1] if results else "Không có kết quả."
            if stopped():
                STORE.append_log(project_id, "warning", "Tác vụ đã bị hủy trước khi hoàn tất.")
                STORE.update(project_id, {"status": "Đã hủy", "progress": 0, "error_message": "Người dùng đã hủy tác vụ."})
            else:
                STORE.append_log(project_id, "info" if success else "error", message)
                STORE.update(project_id, {
                    "status": "Hoàn thành" if success else "Lỗi",
                    "progress": 100 if success else 0,
                    "error_message": "" if success else message,
                    "job_stage": "Hoàn tất" if success else "Lỗi",
                    "current_step": "Xong" if success else "Có lỗi",
                })
        except Exception as exc:
            STORE.append_log(project_id, "error", str(exc))
            if not stopped():
                STORE.update(project_id, {"status": "Lỗi", "progress": 0, "error_message": str(exc)})
        finally:
            with self._lock:
                self._running.discard(project_id)
                self._cancel_events.pop(project_id, None)
                self._active_processes.pop(project_id, None)


JOBS = JobRunner()


class Handler(BaseHTTPRequestHandler):
    server_version = "FastVideoStudio/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")

    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/projects":
            self._json({"projects": STORE.list_projects()})
            return
        if parsed.path.endswith("/batch-results") and parsed.path.startswith("/api/projects/"):
            project_id = parsed.path.split("/")[3]
            project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
            self._json({"results": _batch_results_for_project(project)} if project else {"error": "Không tìm thấy dự án"}, 200 if project else 404)
            return
        if parsed.path.startswith("/api/projects/") and "/media/" in parsed.path:
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 6 and parts[0] == "api" and parts[1] == "projects" and parts[3] == "media" and parts[5] == "info":
                self._serve_project_media_info(parts[2], parts[4])
                return
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "projects" and parts[3] == "media":
                self._serve_project_media(parts[2], parts[4])
                return
        if parsed.path == "/api/settings":
            self._json(STORE.settings())
            return
        if parsed.path == "/api/system":
            ok, tools = check_required_tools()
            usage = shutil.disk_usage(ROOT)
            self._json({
                "ffmpeg_ready": ok,
                "ffmpeg": tools.get("ffmpeg"),
                "ffprobe": tools.get("ffprobe"),
                "disk_free_gb": round(usage.free / (1024 ** 3), 1),
                "data_file": str(DATA_FILE),
            })
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/projects":
            self._json(STORE.create(self._body()), HTTPStatus.CREATED)
            return
        if path.endswith("/upload") and path.startswith("/api/projects/"):
            project_id = path.split("/")[3]
            project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
            if not project:
                self._json({"error": "Không tìm thấy dự án"}, 404)
                return
            filename = unquote(self.headers.get("X-File-Name", "video.mp4"))
            filename = Path(filename).name.strip() or "video.mp4"
            upload_dir = ROOT / "data" / "uploads" / project_id
            upload_dir.mkdir(parents=True, exist_ok=True)
            destination = upload_dir / filename
            stem, suffix = destination.stem, destination.suffix
            counter = 2
            while destination.exists():
                destination = upload_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            remaining = int(self.headers.get("Content-Length", "0") or 0)
            if remaining <= 0:
                self._json({"error": "File upload rỗng"}, 400)
                return
            with destination.open("wb") as output:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    output.write(chunk)
                    remaining -= len(chunk)
            paths = list(project.get("input_paths") or [])
            paths.append(str(destination.resolve()))
            updated = STORE.update(project_id, {"input_paths": paths})
            STORE.append_log(project_id, "info", f"Đã import video: {destination.name}")
            self._json({"project": updated, "path": str(destination.resolve())}, 201)
            return
        if path.endswith("/run") and path.startswith("/api/projects/"):
            project_id = path.split("/")[3]
            body = self._body()
            ok, message = JOBS.start(project_id, str(body.get("operation", "")), dict(body.get("options") or {}))
            self._json({"ok": ok, "message": message}, 202 if ok else 409)
            return
        if path.endswith("/cancel") and path.startswith("/api/projects/"):
            project_id = path.split("/")[3]
            ok, message = JOBS.cancel(project_id)
            self._json({"ok": ok, "message": message}, 202 if ok else 409)
            return
        if path.endswith("/open-output") and path.startswith("/api/projects/"):
            self._open_project_output(path.split("/")[3])
            return
        if path.endswith("/open-final") and path.startswith("/api/projects/"):
            self._open_project_final(path.split("/")[3])
            return
        if "/open-result/" in path and path.startswith("/api/projects/"):
            parts = path.strip("/").split("/")
            if len(parts) == 5:
                self._open_project_result(parts[2], parts[4])
                return
        if path.endswith("/duplicate") and path.startswith("/api/projects/"):
            project_id = path.split("/")[3]
            result = STORE.duplicate(project_id)
            self._json(result or {"error": "Không tìm thấy dự án"}, 201 if result else 404)
            return
        self._json({"error": "API không tồn tại"}, 404)

    def _open_project_output(self, project_id: str) -> None:
        project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
        if not project:
            self._json({"error": "Không tìm thấy dự án"}, 404)
            return
        output_text = str(project.get("output_path", "")).strip()
        if not output_text:
            self._json({"error": "Dự án chưa có thư mục output"}, 404)
            return
        output = Path(output_text).resolve()
        target = output if output.is_dir() else output.parent
        if not target.exists():
            self._json({"error": "Thư mục output chưa tồn tại"}, 404)
            return
        _open_path(target)
        STORE.append_log(project_id, "info", f"Đã mở thư mục output: {target}")
        self._json({"ok": True, "message": "Đã mở thư mục output", "path": str(target)})

    def _open_project_final(self, project_id: str) -> None:
        project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
        if not project:
            self._json({"error": "Không tìm thấy dự án"}, 404)
            return
        final_path = _latest_final_for_project(project)
        if not final_path:
            self._json({"error": "Chưa tìm thấy final.mp4 trong output"}, 404)
            return
        _open_path(final_path)
        STORE.append_log(project_id, "info", f"Đã mở final gần nhất: {final_path}")
        self._json({"ok": True, "message": "Đã mở final.mp4 gần nhất", "path": str(final_path)})

    def _open_project_result(self, project_id: str, result_index_text: str) -> None:
        project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
        if not project:
            self._json({"error": "Không tìm thấy dự án"}, 404)
            return
        try:
            result_index = int(result_index_text)
        except ValueError:
            self._json({"error": "Index kết quả không hợp lệ"}, 400)
            return
        results = _batch_results_for_project(project)
        if result_index < 0 or result_index >= len(results):
            self._json({"error": "Không tìm thấy kết quả video"}, 404)
            return
        result = results[result_index]
        target_key = str(urlparse(self.path).query or "final").strip().lower()
        if target_key == "log":
            target_path = result.get("log_path")
            success_message = "Đã mở process.log"
        elif target_key == "folder":
            target_path = result.get("folder_path")
            success_message = "Đã mở thư mục output của video"
        else:
            target_path = result.get("final_path")
            success_message = "Đã mở final.mp4 của video"
        if not target_path:
            self._json({"error": "File kết quả chưa tồn tại"}, 404)
            return
        resolved = Path(target_path).resolve()
        if not resolved.exists():
            self._json({"error": "File kết quả không còn tồn tại"}, 404)
            return
        _open_path(resolved)
        STORE.append_log(project_id, "info", f"Đã mở {resolved}")
        self._json({"ok": True, "message": success_message, "path": str(resolved)})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/settings":
            self._json(STORE.update_settings(self._body()))
            return
        if path.startswith("/api/projects/"):
            result = STORE.update(path.split("/")[3], self._body())
            self._json(result or {"error": "Không tìm thấy dự án"}, 200 if result else 404)
            return
        self._json({"error": "API không tồn tại"}, 404)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/projects/"):
            ok = STORE.delete(path.split("/")[3])
            self._json({"ok": ok}, 200 if ok else 404)
            return
        self._json({"error": "API không tồn tại"}, 404)

    def _serve_project_media_info(self, project_id: str, index_text: str) -> None:
        project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
        try:
            index = int(index_text)
            media_path = Path(project["input_paths"][index]).resolve() if project else None
        except (ValueError, IndexError, TypeError):
            media_path = None
        if media_path is None or not media_path.is_file():
            self._json({"error": "Video not found"}, 404)
            return
        try:
            ok, tools = check_required_tools()
            if not ok:
                raise RuntimeError("FFprobe unavailable")
            probe = run_ffprobe(media_path, ffprobe_path=str(tools["ffprobe"]))
            self._json({"index": index, "duration": media_duration_from_probe(probe), "name": media_path.name})
        except Exception as exc:
            self._json({"error": str(exc)}, 422)
    def _serve_project_media(self, project_id: str, index_text: str) -> None:
        project = next((p for p in STORE.list_projects() if p["id"] == project_id), None)
        try:
            index = int(index_text)
            media_path = Path(project["input_paths"][index]).resolve() if project else None
        except (ValueError, IndexError, TypeError):
            media_path = None
        if media_path is None or not media_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Video not found")
            return
        size = media_path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range", "")
        status = HTTPStatus.OK
        if range_header.startswith("bytes="):
            try:
                left, right = range_header[6:].split("-", 1)
                start = int(left) if left else 0
                end = min(int(right), size - 1) if right else size - 1
                if start > end or start >= size:
                    raise ValueError
                status = HTTPStatus.PARTIAL_CONTENT
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        content_type = mimetypes.guess_type(media_path.name)[0] or "video/mp4"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with media_path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining > 0:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
    def _serve_static(self, raw_path: str) -> None:
        relative = unquote(raw_path).lstrip("/") or "index.html"
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Fast Video Studio Web: {url}")
    print(f"Data file: {DATA_FILE}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
