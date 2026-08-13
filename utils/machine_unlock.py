"""First-run password unlock for each Windows machine."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import uuid
from pathlib import Path

_SETUP_PASSWORD_ENV = "FAST_VIDEO_CONCAT_SETUP_PASSWORD"
_APP_ID = "LovangGroup.FastVideoConcat"
_MARKER_NAME = "first_run.ok"
_MARKER_VERSION = 2


def _marker_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "LovangGroup" / "FastVideoConcat" / _MARKER_NAME


def _windows_machine_guid() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except OSError:
        return ""


def _machine_fingerprint() -> str:
    parts = [
        _APP_ID,
        f"machine_guid={_windows_machine_guid()}",
        f"computer={os.environ.get('COMPUTERNAME', '')}",
        f"node={uuid.getnode()}",
        f"system={platform.system()}",
        f"release={platform.release()}",
        f"machine={platform.machine()}",
    ]
    return "|".join(parts)


def _unlock_token() -> str:
    payload = f"{_machine_fingerprint()}|password={_setup_password()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _setup_password() -> str:
    return os.environ.get(_SETUP_PASSWORD_ENV, "").strip()


def is_setup_complete() -> bool:
    marker = _marker_path()
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("app") == _APP_ID
        and payload.get("version") == _MARKER_VERSION
        and payload.get("token") == _unlock_token()
    )


def verify_password(phrase: str) -> bool:
    password = _setup_password()
    return bool(password) and phrase.strip() == password


def complete_setup() -> None:
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app": _APP_ID,
        "version": _MARKER_VERSION,
        "token": _unlock_token(),
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
