# Fast Video Concatenator

Ứng dụng desktop Windows dùng để nối nhiều video thành một file duy nhất bằng FFmpeg concat demuxer và stream copy.

Mục tiêu của app là nối nhanh, ổn định với file lớn hoặc rất nhiều file dài. App không phải editor, không có timeline, không thêm hiệu ứng. Bản V1 ưu tiên output MKV cho video dài và có thêm chế độ nối theo batch an toàn hơn khi danh sách rất lớn.

## Nguyên tắc hoạt động

App chỉ nối nhanh khi các file đầu vào tương thích về stream:

- codec video
- codec audio
- số lượng và thứ tự stream
- độ phân giải
- fps
- pixel format
- sample rate, số channel audio
- time base và một số thông số liên quan khác

Nếu metadata khác nhau, app sẽ báo rõ trong log và không chạy nối nhanh. Bạn cần tự chuẩn bị các file cùng thông số trước.

Ghi chú về fps: app dùng `r_frame_rate` trong nhóm kiểm tra chặn. `avg_frame_rate` có thể lệch nhẹ giữa các file do cách FFprobe tính trung bình theo thời lượng từng video, nhất là với file tải từ web hoặc video biến thiên frame rate. Vì vậy app không chặn nối chỉ vì `avg_frame_rate` khác.

Ghi chú về audio: `AAC LC` và `HE-AAC` đều có thể hiện là codec `aac`, nhưng profile khác nhau. App xem đây là không tương thích cho nối nhanh vì stream copy không sửa lại audio header.

Lệnh FFmpeg cốt lõi:

```powershell
ffmpeg -f concat -safe 0 -i list.txt -map 0 -c copy -avoid_negative_ts make_zero output.mp4
```

File `list.txt` do app tạo có ghi cả duration đã phân tích từ video gốc:

```text
file 'D:/video/part01.mp4'
duration 1234.567000
file 'D:/video/part02.mp4'
duration 987.654000
```

App có thể xuất `.mp4` hoặc `.mkv`. Với output rất dài, ví dụ 50-100 tiếng hoặc file có nhiều stream, `.mkv` thường dễ mux và dễ phát ổn định hơn. Một số trình phát hoặc Windows Explorer có thể hiển thị sai duration của file rất dài, ví dụ chỉ hiện phần dư sau mốc 24 giờ; app dùng ffprobe để kiểm tra thời lượng thật sau khi nối. Nếu chọn `.mp4` cho video trên 24 giờ, app sẽ cảnh báo. Với output từ 100 tiếng trở lên, app chặn xuất `.mp4` và yêu cầu dùng `.mkv` để tránh FFmpeg treo lâu khi mux/finalize MP4.

Khi phân tích, app không chỉ lấy `format.duration` vì metadata container có thể sai với video dài/remux. App lấy duration lớn nhất hợp lệ từ `format.duration`, `stream.duration`, `duration_ts + time_base`, tag `DURATION`, và fallback `nb_frames / frame_rate` cho stream video/audio. Sau khi nối xong, app dùng cùng cách này để kiểm tra lại duration file output và so với tổng thời lượng đã phân tích. Nếu output ngắn bất thường, app sẽ báo lỗi trong log thay vì báo hoàn tất giả.

## Cấu trúc project

```text
main.py
ui/
core/
workers/
utils/
requirements.txt
README.md
```

## Cài FFmpeg trên Windows

Cách đơn giản nhất nếu máy có `winget`:

```powershell
winget install Gyan.FFmpeg
```

Hoặc tải bản build Windows từ:

- https://www.gyan.dev/ffmpeg/builds/
- https://www.ffmpeg.org/download.html

Sau khi cài hoặc giải nén, hãy thêm thư mục `bin` của FFmpeg vào biến môi trường `PATH`, ví dụ:

```text
C:\ffmpeg\bin
```

Kiểm tra trong PowerShell:

```powershell
ffmpeg -version
ffprobe -version
```

Nếu hai lệnh trên chạy được, app sẽ tìm thấy FFmpeg và FFprobe.

App cũng tự tìm FFmpeg portable trong project theo dạng:

```text
tools\ffmpeg\bin\ffmpeg.exe
tools\ffmpeg\bin\ffprobe.exe
```

Hoặc nếu bạn giải nén bản zip có thư mục lồng nhau, app cũng sẽ tìm trong:

```text
tools\ffmpeg\**\bin\ffmpeg.exe
tools\ffmpeg\**\bin\ffprobe.exe
```

## Cài Python dependency

Yêu cầu Python 3.10 hoặc mới hơn.

Khuyến nghị dùng virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Chạy app

```powershell
python main.py
```

Trước lần mở desktop đầu tiên, đặt mật khẩu thiết lập bằng biến môi trường
`FAST_VIDEO_CONCAT_SETUP_PASSWORD`. Không lưu mật khẩu trực tiếp trong mã nguồn:

```powershell
$env:FAST_VIDEO_CONCAT_SETUP_PASSWORD = "mat-khau-cua-ban"
python main.py
```

Quy trình sử dụng:

1. Bấm **Thêm file** và chọn nhiều video.
2. Kéo thả các dòng video để đổi thứ tự nối, hoặc dùng **Lên** / **Xuống**.
3. Chọn thư mục lưu output.
4. Bấm **Phân tích** để kiểm tra tương thích bằng ffprobe.
5. Nếu muốn tạo một file duy nhất, bấm **Nối nhanh 1 file**.
6. Nếu danh sách có nhiều luồng tương thích khác nhau, bấm **Nối dài an toàn** để xuất từng luồng thành file riêng trước.
7. App sẽ tự tạo file dạng `VIDEO_yyyyMMdd_HHmmss.mp4` hoặc `.mkv` trong thư mục đã chọn.
8. Khi xong, bấm **Mở thư mục output**.

Danh sách video có thumbnail preview để dễ nhận biết từng phần. Thumbnail được tạo bằng FFmpeg trong worker riêng nên không làm treo giao diện.

## Build file `.exe` bằng PyInstaller

Kích hoạt virtual environment rồi chạy:

```powershell
pyinstaller --noconfirm --clean --windowed --onefile --name FastVideoConcat --icon "assets\app_icon.ico" --add-data "tools;tools" --add-data "assets;assets" main.py
```

File `.exe` sẽ nằm tại:

```text
dist\FastVideoConcat.exe
```

Lưu ý:

- Lệnh trên đóng gói cả thư mục `tools`, gồm `ffmpeg.exe` và `ffprobe.exe`, vào file `.exe`.
- Icon app nằm tại `assets\app_icon.ico` và được nhúng vào file `.exe`.
- File `.exe` sẽ lớn vì chứa PySide6 và FFmpeg.
- Khi mở bản `--onefile`, Windows có thể mất vài giây để giải nén nội bộ trước khi cửa sổ hiện ra.

## Giới hạn có chủ đích

- Stream copy là đường xử lý chính; app không render lại video để tự sửa file lỗi.
- Không tự sửa được mọi file không tương thích hoặc bitstream hỏng nặng.
- Không phải video editor và không render lại toàn bộ video theo kiểu timeline.
- Không đảm bảo nối đúng nếu file khác codec, độ phân giải, fps, audio layout hoặc stream layout.
- Với video gần 100 tiếng, hãy lưu output vào ổ đĩa còn nhiều dung lượng và định dạng file system hỗ trợ file lớn, ví dụ NTFS.
