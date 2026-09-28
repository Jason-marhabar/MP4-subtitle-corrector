# ============================================
# MP4字幕矫正工具 - 环境一键安装脚本
# 用法：PowerShell 中执行  .\setup.ps1
# 功能：检查 Python/FFmpeg、创建虚拟环境、安装依赖
# ============================================

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Write-Host "===== MP4字幕矫正工具 环境一键安装 =====" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. 检查 Python ----------
Write-Host "[1/5] 检查 Python..." -ForegroundColor Yellow
$py = $null
# 优先使用 D:\python3.11
if (Test-Path "D:\python3.11\python.exe") {
    $py = "D:\python3.11\python.exe"
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    Write-Host "  [错误] 未找到 Python，请先安装 Python 3.11" -ForegroundColor Red
    Write-Host "  下载地址：https://www.python.org/downloads/"
    Read-Host "按回车退出"
    exit 1
}
$ver = & $py --version 2>&1
Write-Host "  使用 Python: $py ($ver)" -ForegroundColor Green

# ---------- 2. 检查 FFmpeg ----------
Write-Host "[2/5] 检查 FFmpeg..." -ForegroundColor Yellow
$ffmpegFound = $false
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    $ffmpegFound = $true
    Write-Host "  FFmpeg 已在 PATH 中: $((Get-Command ffmpeg).Source)" -ForegroundColor Green
} elseif (Test-Path "$root\assets\ffmpeg\ffmpeg.exe") {
    $ffmpegFound = $true
    Write-Host "  FFmpeg 位于 assets\ffmpeg\（打包嵌入用）" -ForegroundColor Green
} else {
    Write-Host "  [警告] 未检测到 FFmpeg" -ForegroundColor DarkYellow
    Write-Host "  请安装 FFmpeg 并加入 PATH，或下载放到 assets\ffmpeg\ffmpeg.exe"
}

# ---------- 3. 创建虚拟环境 ----------
Write-Host "[3/5] 创建虚拟环境 .venv..." -ForegroundColor Yellow
if (-not (Test-Path "$root\.venv")) {
    & $py -m venv "$root\.venv"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [错误] 创建虚拟环境失败" -ForegroundColor Red
        Read-Host "按回车退出"
        exit 1
    }
    Write-Host "  .venv 已创建" -ForegroundColor Green
} else {
    Write-Host "  .venv 已存在，跳过" -ForegroundColor Green
}
$pip = "$root\.venv\Scripts\pip.exe"

# ---------- 4. 安装依赖 ----------
Write-Host "[4/5] 安装 Python 依赖（清华镜像）..." -ForegroundColor Yellow
& $pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple | Out-Null
& $pip install -r "$root\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [错误] 依赖安装失败，请检查网络后重试" -ForegroundColor Red
} else {
    Write-Host "  依赖安装完成" -ForegroundColor Green
}

# ---------- 5. 外部软件与模型提示 ----------
Write-Host "[5/5] 外部组件说明..." -ForegroundColor Yellow
Write-Host "  • Aboboo（音字对齐验收）：请手动安装"
Write-Host "  • Subtitle Edit（字幕微调）：请手动安装"
Write-Host "  • Whisper 模型（4号 联调时）：放到 models\ 目录"
Write-Host ""
Write-Host "===== 环境准备完成！ =====" -ForegroundColor Cyan
Write-Host "运行 GUI：.\.venv\Scripts\python.exe .\gui\main.py"
Read-Host "按回车退出"
