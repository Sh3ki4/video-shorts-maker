$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $project "tools\ffmpeg\bin"
$temp = Join-Path $env:TEMP ("VideoShortsMaker_ffmpeg_" + $PID)
$cache = Join-Path $project "tools\download-cache"
$archive = Join-Path $cache "ffmpeg.zip"
$unpack = Join-Path $temp "unpack"

try {
    New-Item -ItemType Directory -Force -Path $temp, $unpack, $cache | Out-Null
    Write-Host "Загрузка Windows-сборки FFmpeg (BtbN/FFmpeg-Builds)..."
    # Stable 8.1 is used instead of "master": it supports older NVIDIA drivers
    # commonly installed with RTX 20-series cards.
    $archiveReady = $false
    if (Test-Path -LiteralPath $archive) {
        try {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $zipCheck = [System.IO.Compression.ZipFile]::OpenRead($archive)
            $zipCheck.Dispose()
            $archiveReady = $true
            Write-Host "Найден полностью загруженный архив."
        } catch { $archiveReady = $false }
    }
    if (-not $archiveReady) {
        & curl.exe -L --fail --retry 10 --retry-all-errors -C - --output $archive "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip"
        if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать архив FFmpeg (curl: $LASTEXITCODE). Запустите установщик ещё раз — загрузка продолжится." }
    }
    Write-Host "Распаковка..."
    Expand-Archive -LiteralPath $archive -DestinationPath $unpack -Force
    $ffmpeg = Get-ChildItem -LiteralPath $unpack -Filter "ffmpeg.exe" -Recurse | Select-Object -First 1
    $ffprobe = Get-ChildItem -LiteralPath $unpack -Filter "ffprobe.exe" -Recurse | Select-Object -First 1
    if (-not $ffmpeg -or -not $ffprobe) { throw "В архиве не найдены ffmpeg.exe и ffprobe.exe" }
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    Get-ChildItem -LiteralPath $tools -File -ErrorAction SilentlyContinue | Remove-Item -Force
    $sourceBin = Split-Path -Parent $ffmpeg.FullName
    Get-ChildItem -LiteralPath $sourceBin -File | Where-Object { $_.Extension -eq ".dll" -or $_.Name -in @("ffmpeg.exe", "ffprobe.exe") } | Copy-Item -Destination $tools -Force
    Remove-Item -LiteralPath $archive -Force
    if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache -Recurse -Force }
    Write-Host ""
    Write-Host "FFmpeg установлен в $tools" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host ("Ошибка установки: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
