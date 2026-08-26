# Fast Video Studio

Ứng dụng desktop Windows (PySide6) xử lý video tự động theo pipeline:

```text
Tách giọng / bỏ nhạc nền (AI - Demucs)  ->  Cắt đoạn  ->  Zoom xen kẽ từng đoạn  ->  Nối final.mp4
```

Mỗi dự án chỉ cần 1 video nguồn. Toàn bộ dự án dùng chung một pipeline duy nhất, không cần chọn loại tác vụ.

## Dùng bản portable (khuyến nghị cho người nhận)

1. Giải nén file zip.
2. Chạy `FastVideoStudio.exe`.
3. Dùng luôn — không cần cài Python, FFmpeg hay Demucs, tất cả đã có sẵn trong gói.

Cấu trúc gói portable:

```text
FastVideoStudio.exe
tools\ffmpeg\bin\ffmpeg.exe / ffprobe.exe   # FFmpeg đi kèm
.venv-demucs\                                # môi trường AI tách giọng (nếu có kèm theo)
assets\                                      # icon, tài nguyên giao diện
```

Nếu gói không có `.venv-demucs`, app vẫn chạy bình thường — bước tách giọng sẽ tự động fallback dùng audio gốc (không lỗi, không dừng batch), chỉ là không tách được nhạc nền.

Dữ liệu dự án (SQLite) được lưu tại `%USERPROFILE%\.fast_video_studio\projects.db`, không ghi vào thư mục cài đặt — xóa/di chuyển thư mục app không mất dữ liệu, có thể copy file này sang máy khác để mang theo lịch sử dự án.

## Quy trình sử dụng

1. **Tạo dự án mới** — chỉ cần đặt tên.
2. Mở dự án, bấm **Chọn video…** ở khung "VIDEO GỐC" để chọn video nguồn.
3. (Tùy chọn) đặt **Thư mục đầu ra chung** ở tab Dự án — áp dụng cho mọi dự án, mỗi dự án vẫn có thư mục con riêng nên không đụng độ nhau.
4. Chỉnh **THIẾT LẬP XỬ LÝ**: thời lượng mỗi đoạn (1-5 phút), % zoom đoạn lẻ/chẵn, bộ mã hóa (tự động ưu tiên GPU NVIDIA), tách giọng AI, hiệu ứng hạt phim + render 4K (tùy chọn).
5. Bấm **BẮT ĐẦU XỬ LÝ**. Có thể xếp nhiều dự án chạy tuần tự qua **Chạy tất cả** ở tab Tổng quan.
6. Khi xong, khung **VIDEO ĐÃ XỬ LÝ** hiện video final để đối chiếu trực tiếp với video gốc.

Kết quả mỗi dự án:

```text
{Thư mục đầu ra}\{id}_{tên dự án}\{tên video}\final.mp4   # bản đầy đủ, có các file trung gian (voice.wav, segment...)
{Thư mục đầu ra}\{tên dự án}.mp4                            # bản sao phẳng, dễ tìm — vẫn còn sau khi xóa dự án
```

Xóa dự án trong app sẽ xóa dữ liệu dự án + toàn bộ file trung gian, nhưng **giữ lại** file `{tên dự án}.mp4` phẳng ở trên.

## Cấu trúc mã nguồn (cho người phát triển)

```text
main.py                 # entry desktop app
core/
  batch_pipeline.py      # pipeline xử lý chính (tách giọng -> cắt -> zoom -> final.mp4)
  ffmpeg_tools.py         # helper FFmpeg/FFprobe
  project_store.py        # SQLite project store
workers/
  batch_pipeline_worker.py  # bọc pipeline vào QThread
ui/
  dashboard_window.py     # màn hình quản lý dự án
  pipeline_window.py       # màn hình xử lý 1 dự án
  editor_common.py         # widget/helper dùng chung
  styles.py
utils/
  resources.py             # resource_path (tương thích PyInstaller frozen)
assets/                   # icon, hiệu ứng hạt phim
tools/ffmpeg/             # FFmpeg portable đi kèm
.venv-demucs/              # (tùy chọn) môi trường Python riêng chạy Demucs AI
```

## Build lại `.exe`

Yêu cầu Python 3.10+, đã cài `requirements.txt`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Build:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Script sẽ build `FastVideoStudio.exe` bằng PyInstaller, gom `tools\ffmpeg`, `.venv-demucs` (nếu có) vào `dist\FastVideoStudio_Portable\`, rồi nén thành `dist\FastVideoStudio_Portable.zip` — gửi nguyên file zip này cho người nhận.

### Cài môi trường Demucs (AI tách giọng) trước khi build

```powershell
py -3.10 -m venv .venv-demucs
.\.venv-demucs\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-demucs\Scripts\python.exe -m pip install numpy demucs
```

Nếu máy build có GPU NVIDIA, cài thêm bản PyTorch có CUDA để tách giọng chạy nhanh hơn (bản portable vẫn chạy bình thường trên máy không có GPU, chỉ là chạy CPU chậm hơn):

```powershell
.\.venv-demucs\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cu126 torch
```

## Giới hạn có chủ đích

- Không phải video editor timeline; chỉ chạy đúng 1 pipeline cố định (tách giọng -> cắt -> zoom so le -> final.mp4).
- Cần FFmpeg hỗ trợ `h264_nvenc` + driver NVIDIA hợp lệ để dùng chế độ mã hóa GPU; nếu không có, app tự fallback CPU (libx264).
- Bước tách giọng AI cần `.venv-demucs` hợp lệ (có `numpy`, `demucs`, `torch`); thiếu một trong số này sẽ fallback giữ audio gốc thay vì báo lỗi dừng batch.
