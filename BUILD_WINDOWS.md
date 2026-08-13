# Build Windows Release

Muc tieu: tao goi zip cho nguoi dung chi can giai nen va bam `FastVideoConcat.exe`.

Goi release gom:

```text
FastVideoConcat_Setup/
  FastVideoConcat.exe
  bin/
    ffmpeg.exe
    ffprobe.exe
```

Build:

```powershell
cd C:\Users\ADMIN\Downloads\toolnoivideonangcap
pip install -r requirements.txt
pyinstaller --noconfirm --clean FastVideoConcat.spec
```

Dong goi zip:

```powershell
New-Item -ItemType Directory -Force dist\FastVideoConcat_Setup\bin
Copy-Item dist\FastVideoConcat.exe dist\FastVideoConcat_Setup\
Copy-Item tools\ffmpeg\bin\ffmpeg.exe dist\FastVideoConcat_Setup\bin\
Copy-Item tools\ffmpeg\bin\ffprobe.exe dist\FastVideoConcat_Setup\bin\
Compress-Archive -Path dist\FastVideoConcat_Setup -DestinationPath dist\FastVideoConcat_Setup.zip -Force
```

Nguoi dung khong can cai FFmpeg rieng vi app tu tim trong `bin` canh file exe.

Preview noi bo bang VLC:
- App da ho tro fallback mo bang trinh phat ngoai neu thieu `python-vlc`.
- Neu muon preview/phat trong app, cai them dependency trong `requirements.txt` (`python-vlc`) va dam bao may co VLC runtime/phat media tuong thich.
