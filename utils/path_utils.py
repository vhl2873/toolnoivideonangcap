from __future__ import annotations

from pathlib import Path


def path_key(path: str | Path) -> str:
    """Khóa so khớp đường dẫn (resolve + casefold) — tránh lệch hoa/thường trên Windows."""
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return str(Path(path).absolute()).casefold()


def path_order_intersect_group(path_order: list[str], group_paths: list[str]) -> list[str]:
    """Giữ thứ tự trong path_order; lọc theo nhóm phân tích (so khớp path_key)."""
    keys = {path_key(p) for p in group_paths}
    return [p for p in path_order if path_key(p) in keys]


def same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return Path(left).absolute() == Path(right).absolute()


def is_video_extension(path: str | Path) -> bool:
    return Path(path).suffix.lower() in {
        ".mp4",
        ".mov",
        ".mkv",
        ".m4v",
        ".avi",
        ".ts",
        ".mts",
        ".m2ts",
        ".webm",
    }
