@echo off
cd /d "%~dp0"
if exist "FastVideoWeb.exe" (
  start "Fast Video Web" "FastVideoWeb.exe"
) else (
  python web_app.py
)
