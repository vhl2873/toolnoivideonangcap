# Build Windows Portable Release

Mục tiêu: tạo gói **portable** có thể copy sang máy Windows khác, giải nén và chạy ngay.

## Gói phát hành

```text
FastVideoStudio_Portable/
  FastVideoDesktop.exe
  FastVideoWeb.exe
  START_WEB.bat
  README.md
  tools/
    ffmpeg/
      bin/
        ffmpeg.exe
        ffprobe.exe
  .venv-demucs/            # nếu đã cài Demucs local
```

## Ý nghĩa

- `FastVideoDesktop.exe`: giao diện desktop PySide6 cũ
- `FastVideoWeb.exe`: local web control panel tại `http://127.0.0.1:8765`
- `START_WEB.bat`: mở bản web nhanh, ưu tiên `FastVideoWeb.exe`
- `tools/ffmpeg/bin`: người nhận **không cần tự cài FFmpeg**
- `.venv-demucs/`: nếu có, bản portable sẽ dùng để tách voice/xóa nhạc nền thật thay vì fallback audio gốc

## Chuẩn bị

Khuyến nghị build trên chính máy đã test app:

```powershell
cd C:\Users\ADMIN\Downloads\toolnoivideonangcap
python -m pip install --upgrade pip
python -m pip install pyinstaller
python -m pip install -r requirements.txt
```

Nếu muốn mang luôn AI tách voice vào bản portable, tạo/cài local venv riêng:

```powershell
py -3.10 -m venv .venv-demucs
.\.venv-demucs\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-demucs\Scripts\python.exe -m pip install demucs
```

## Build tự động

Chạy 1 lệnh:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Script sẽ:
- build `FastVideoDesktop.exe`
- build `FastVideoWeb.exe`
- copy FFmpeg portable
- copy `.venv-demucs` nếu có
- tạo file zip:

```text
dist\FastVideoStudio_Portable.zip
```

## Ghi chú kỹ thuật

### 1) Demucs / AI voice

Pipeline batch sẽ ưu tiên dùng:

```text
.venv-demucs\Scripts\python.exe
```

nếu thư mục này tồn tại cạnh app/project. Nếu không có, app fallback về Python hiện tại.

### 2) Data của web app

Khi chạy bản `FastVideoWeb.exe`, app sẽ:
- đọc resource web từ bundle PyInstaller
- ghi dữ liệu project/output vào thư mục cạnh file `.exe`, không ghi vào thư mục tạm `_MEIPASS`

### 3) Nếu máy nhận không dùng AI voice

Vẫn chạy bình thường nếu thiếu `.venv-demucs`, chỉ là phần xóa nhạc nền sẽ fallback sang audio gốc.

## Cách gửi cho máy khác

Gửi nguyên file:

```text
dist\FastVideoStudio_Portable.zip
```

Bên nhận chỉ cần:
1. giải nén
2. chạy `FastVideoDesktop.exe` hoặc `START_WEB.bat`
3. dùng luôn, không cần cài thêm FFmpeg
