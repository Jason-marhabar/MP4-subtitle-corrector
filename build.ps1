# ============================================
# MP4字幕矫正工具 - 一键打包脚本
# 用法：PowerShell 中执行  .\build.ps1
# 依赖：已运行 setup.ps1（venv + PyInstaller）
# ============================================

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "===== 开始打包 =====" -ForegroundColor Cyan

# 使用虚拟环境 Python（优先），否则用系统 Python
$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "D:\python3.11\python.exe"
}
Write-Host "使用 Python: $py" -ForegroundColor Green

# 检查 PyInstaller
& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 未安装 PyInstaller，请先运行 setup.ps1 或执行：" -ForegroundColor Red
    Write-Host "  & $py -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple"
    Read-Host "按回车退出"
    exit 1
}

# 执行打包
& $py "$root\build.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 打包失败，请查看上方日志" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

Write-Host ""
Write-Host "===== 打包完成！ =====" -ForegroundColor Cyan
$exe = "$root\dist\MP4字幕矫正工具\MP4字幕矫正工具.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "产物：$exe（$size MB）" -ForegroundColor Green
}
Read-Host "按回车退出"
