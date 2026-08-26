from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_STATUSES = ("Bản nháp", "Chưa chạy", "Đang chờ", "Đang chạy", "Tạm dừng", "Hoàn thành", "Lỗi", "Đã hủy")
# Chỉ còn 1 pipeline duy nhất cho mọi dự án: tách giọng/nhạc nền -> cắt đoạn -> zoom so le -> nối final.mp4
# (giống hệt luồng batch của bản web). Giữ dạng tuple để chỗ nào còn tham chiếu TASK_TYPES không vỡ.
TASK_TYPES = ("Xử lý video",)
PRIORITIES = ("Khẩn cấp", "Cao", "Bình thường", "Thấp")

@dataclass(slots=True)
class Project:
    id: int
    name: str
    task_type: str
    status: str
    progress: int
    priority: str
    input_path: str
    output_path: str
    file_count: int
    created_at: str
    started_at: str = ""
    completed_at: str = ""
    last_run_at: str = ""
    elapsed_seconds: int = 0
    remaining_seconds: int = 0
    error_message: str = ""
    settings_json: str = "{}"

    @property
    def settings(self) -> dict:
        try:
            value = json.loads(self.settings_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

class ProjectStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or (Path.home() / ".fast_video_studio" / "projects.db"))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    task_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Bản nháp',
                    progress INTEGER NOT NULL DEFAULT 0, priority TEXT NOT NULL DEFAULT 'Bình thường',
                    input_path TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '',
                    file_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT '',
                    last_run_at TEXT NOT NULL DEFAULT '', elapsed_seconds INTEGER NOT NULL DEFAULT 0,
                    remaining_seconds INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '',
                    settings_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS project_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                    level TEXT NOT NULL DEFAULT 'INFO', message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
                CREATE INDEX IF NOT EXISTS idx_project_logs_project ON project_logs(project_id);
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT ''
                );
            """)

    def get_app_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_app_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def list_projects(self) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT * FROM projects ORDER BY
                CASE priority WHEN 'Khẩn cấp' THEN 0 WHEN 'Cao' THEN 1
                WHEN 'Bình thường' THEN 2 ELSE 3 END, id DESC""").fetchall()
        return [Project(**dict(row)) for row in rows]

    def get_project(self, project_id: int) -> Project | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return Project(**dict(row)) if row else None

    def create_project(self, *, name: str, task_type: str, priority: str = "Bình thường",
                       input_path: str = "", output_path: str = "", file_count: int = 0,
                       settings: dict | None = None) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute("""INSERT INTO projects(name, task_type, status, progress,
                priority, input_path, output_path, file_count, created_at, settings_json)
                VALUES (?, ?, 'Bản nháp', 0, ?, ?, ?, ?, ?, ?)""",
                (name.strip(), task_type, priority, input_path, output_path, max(0, file_count),
                 now, json.dumps(settings or {}, ensure_ascii=False)))
            project_id = int(cursor.lastrowid)
        self.add_log(project_id, "INFO", "Đã tạo dự án.")
        return project_id

    def update_status(self, project_id: int, status: str, progress: int | None = None) -> None:
        if status not in PROJECT_STATUSES:
            raise ValueError(status)
        value = max(0, min(100, int(progress))) if progress is not None else None
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("""UPDATE projects SET status=?, progress=COALESCE(?, progress),
                last_run_at=?, started_at=CASE WHEN ?='Đang chạy' AND started_at='' THEN ? ELSE started_at END,
                completed_at=CASE WHEN ?='Hoàn thành' THEN ? ELSE completed_at END WHERE id=?""",
                (status, value, now, status, now, status, now, project_id))
        self.add_log(project_id, "INFO", f"Trạng thái: {status}.")

    def delete_project(self, project_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM project_logs WHERE project_id=?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def duplicate_project(self, project_id: int) -> int | None:
        p = self.get_project(project_id)
        if p is None:
            return None
        return self.create_project(name=f"{p.name} — Bản sao", task_type=p.task_type,
            priority=p.priority, input_path=p.input_path, output_path=p.output_path,
            file_count=p.file_count, settings=p.settings)

    def update_fields(self, project_id: int, **fields: object) -> None:
        allowed = {"name", "output_path", "input_path", "priority", "task_type"}
        columns = [key for key in fields if key in allowed and fields[key] is not None]
        if not columns:
            return
        assignments = ", ".join(f"{col}=?" for col in columns)
        values = [fields[col] for col in columns]
        with self._connect() as connection:
            connection.execute(f"UPDATE projects SET {assignments} WHERE id=?", (*values, project_id))

    def update_settings(self, project_id: int, **patch: object) -> None:
        project = self.get_project(project_id)
        if project is None:
            return
        settings = project.settings
        settings.update(patch)
        with self._connect() as connection:
            connection.execute("UPDATE projects SET settings_json=? WHERE id=?",
                (json.dumps(settings, ensure_ascii=False), project_id))

    def add_log(self, project_id: int, level: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO project_logs(project_id,level,message,created_at) VALUES(?,?,?,?)",
                (project_id, level.upper(), message, datetime.now().isoformat(timespec="seconds")))

    def project_logs(self, project_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute("SELECT level,message,created_at FROM project_logs WHERE project_id=? ORDER BY id", (project_id,)).fetchall()