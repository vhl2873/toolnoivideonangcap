$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$portableRoot = Join-Path $PSScriptRoot 'dist\FastVideoStudio_Portable'
$zipPath = Join-Path $PSScriptRoot 'dist\FastVideoStudio_Portable.zip'

if (Test-Path $portableRoot) { Remove-Item $portableRoot -Recurse -Force }
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

python -m PyInstaller --noconfirm --clean FastVideoConcat.spec
python -m PyInstaller --noconfirm --clean FastVideoWeb.spec

New-Item -ItemType Directory -Force -Path $portableRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $portableRoot 'tools\ffmpeg\bin') | Out-Null

Copy-Item 'dist\FastVideoConcat.exe' (Join-Path $portableRoot 'FastVideoDesktop.exe')
Copy-Item 'dist\FastVideoWeb.exe' (Join-Path $portableRoot 'FastVideoWeb.exe')
Copy-Item 'START_WEB.bat' (Join-Path $portableRoot 'START_WEB.bat')
Copy-Item 'README.md' (Join-Path $portableRoot 'README.md')
Copy-Item 'tools\ffmpeg\bin\ffmpeg.exe' (Join-Path $portableRoot 'tools\ffmpeg\bin\ffmpeg.exe')
Copy-Item 'tools\ffmpeg\bin\ffprobe.exe' (Join-Path $portableRoot 'tools\ffmpeg\bin\ffprobe.exe')

if (Test-Path '.venv-demucs') {
    Copy-Item '.venv-demucs' (Join-Path $portableRoot '.venv-demucs') -Recurse
}

Compress-Archive -Path $portableRoot -DestinationPath $zipPath -Force
Write-Host "DONE: $zipPath"
