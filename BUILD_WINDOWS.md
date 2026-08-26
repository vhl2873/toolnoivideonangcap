# Build Windows Portable Release

Mục tiêu: tạo gói **portable** desktop app, copy sang máy Windows khác, giải nén và chạy ngay.

## Gói phát hành

```text
FastVideoStudio_Portable.zip
  FastVideoStudio.exe
  README.md
  tools/
    ffmpeg/
      bin/
        ffmpeg.exe
        ffprobe.exe
  .venv-demucs/            # nếu đã cài Demucs local trước khi build
```

## Ý nghĩa

- `FastVideoStudio.exe`: giao diện desktop PySide6 (pipeline tách giọng -> cắt -> zoom so le -> final.mp4)
- `tools/ffmpeg/bin`: người nhận **không cần tự cài FFmpeg**
- `.venv-demucs/`: nếu có, bản portable sẽ dùng để tách voice/xóa nhạc nền AI thật thay vì fallback audio gốc

## Chuẩn bị

Khuyến nghị build trên chính máy đã test app:

```powershell
cd C:\Users\ADMIN\Downloads\toolnoivideonangcap
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Nếu muốn mang luôn AI tách voice vào bản portable, tạo/cài local venv riêng:

```powershell
py -3.10 -m venv .venv-demucs
.\.venv-demucs\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-demucs\Scripts\python.exe -m pip install numpy demucs
```

Nếu máy build có GPU NVIDIA, cài thêm bản PyTorch CUDA để tách giọng nhanh hơn nhiều (vẫn portable — máy
nhận không có GPU NVIDIA vẫn chạy được, chỉ tự động dùng CPU):

```powershell
.\.venv-demucs\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cu126 torch
```

## Build tự động

Chạy 1 lệnh:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Script sẽ:
- build `FastVideoStudio.exe` bằng PyInstaller (`FastVideoStudio.spec`)
- copy FFmpeg portable
- copy `.venv-demucs` nếu có
- tạo file zip:

```text
dist\FastVideoStudio_Portable.zip
```

## Ghi chú kỹ thuật

### 1) Demucs / AI voice

Pipeline batch (`core/batch_pipeline.py`) sẽ ưu tiên dùng:

```text
.venv-demucs\Scripts\python.exe
```

nằm CẠNH file `.exe` (không phải cạnh source `.py`) — quan trọng khi đã đóng gói bằng PyInstaller, vì
`__file__` của module Python bên trong archive không còn là đường dẫn thật trên đĩa. Hàm `_app_dir()`
trong `core/batch_pipeline.py` xử lý việc này bằng `sys.executable` khi `sys.frozen` (đã đóng gói).

Nếu `.venv-demucs` không tồn tại cạnh exe, app vẫn chạy bình thường, chỉ là bước xóa nhạc nền sẽ fallback
sang audio gốc thay vì báo lỗi dừng batch.

### 2) File hiệu ứng hạt phim (film_grain_overlay.mp4)

Nằm trong `assets/`, được PyInstaller đóng gói vào bên trong `.exe` qua `datas` trong spec — dùng
`sys._MEIPASS` (`_bundle_dir()`) để tìm đúng đường dẫn khi đã đóng gói.

### 3) Dữ liệu dự án

`ProjectStore` mặc định lưu tại `%USERPROFILE%\.fast_video_studio\projects.db` — không phụ thuộc thư mục
cài đặt/giải nén, nên xóa/di chuyển thư mục app không mất dữ liệu.

## Cách gửi cho máy khác

Gửi nguyên file:

```text
dist\FastVideoStudio_Portable.zip
```

Bên nhận chỉ cần:
1. giải nén
2. chạy `FastVideoStudio.exe`
3. dùng luôn, không cần cài thêm gì
