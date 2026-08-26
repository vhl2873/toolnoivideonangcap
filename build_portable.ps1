$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$portableRoot = Join-Path $PSScriptRoot 'dist\FastVideoStudio_Portable'
$zipPath = Join-Path $PSScriptRoot 'dist\FastVideoStudio_Portable.zip'

if (Test-Path $portableRoot) { Remove-Item $portableRoot -Recurse -Force }
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

python -m PyInstaller --noconfirm --clean FastVideoStudio.spec

New-Item -ItemType Directory -Force -Path $portableRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $portableRoot 'tools\ffmpeg\bin') | Out-Null

Copy-Item 'dist\FastVideoStudio.exe' (Join-Path $portableRoot 'FastVideoStudio.exe')
Copy-Item 'README.md' (Join-Path $portableRoot 'README.md')
Copy-Item 'tools\ffmpeg\bin\ffmpeg.exe' (Join-Path $portableRoot 'tools\ffmpeg\bin\ffmpeg.exe')
Copy-Item 'tools\ffmpeg\bin\ffprobe.exe' (Join-Path $portableRoot 'tools\ffmpeg\bin\ffprobe.exe')

if (Test-Path '.venv-demucs') {
    Copy-Item '.venv-demucs' (Join-Path $portableRoot '.venv-demucs') -Recurse
}

Compress-Archive -Path (Join-Path $portableRoot '*') -DestinationPath $zipPath -Force
Write-Host "DONE: $zipPath"
