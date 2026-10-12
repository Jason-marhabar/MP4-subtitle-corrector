@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  环境一键安装
echo ============================================================
echo.

set "PYEXE="
for %%P in (
  "C:\Program Files\Python311\python.exe"
  "C:\Program Files\Python310\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
) do (
  if not defined PYEXE if exist %%P set "PYEXE=%%~P"
)
if not defined PYEXE (
  where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo [错误] 未找到 Python 3.10/3.11。
  echo        请先安装 Python 3.11 ^(推荐^)，安装时勾选 "Add to PATH"。
  echo        下载：https://www.python.org/downloads/
  pause
  exit /b 1
)
echo [1/6] Python: %PYEXE%
"%PYEXE%" -c "import sys;print('      版本',sys.version.split()[0])" || (pause & exit /b 1)

echo.
echo [2/6] 创建虚拟环境 venv ...
if exist venv\Scripts\python.exe (
  echo       已存在，跳过
) else (
  "%PYEXE%" -m venv venv || (echo [错误] 创建 venv 失败 & pause & exit /b 1)
)

REM 关键：pip 缓存默认在 %LOCALAPPDATA%\pip\cache，受限环境会报
REM "WinError 5 拒绝访问" 并卡在 Building wheel。重定向到项目内。
set "PIP_CACHE_DIR=%CD%\.pipcache"
set "TMP=%CD%\.tmp"
set "TEMP=%CD%\.tmp"
if not exist ".pipcache" mkdir ".pipcache"
if not exist ".tmp" mkdir ".tmp"
echo       pip 缓存已重定向到 .pipcache ^(规避权限问题^)

echo.
echo [3/6] 安装 Python 依赖 ^(使用阿里云镜像^) ...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet ^
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com || echo       ^(pip 升级失败，继续^)
venv\Scripts\python.exe -m pip install -r requirements.txt ^
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if errorlevel 1 (
  echo [错误] 依赖安装失败。若卡在 "Building wheel"，确认 PIP_CACHE_DIR 已重定向。
  pause
  exit /b 1
)

echo.
echo [4/6] 配置 HuggingFace 镜像与模型缓存 ...
if not exist ".hf" mkdir ".hf"
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HOME=%CD%\.hf"
echo       HF_ENDPOINT=%HF_ENDPOINT%  ^(国内直连 huggingface.co 不可用^)
echo       HF_HOME=%HF_HOME%  ^(模型缓存放项目内，便于迁移^)

echo.
echo [5/6] 下载 seconv ^(Subtitle Edit 命令行，约 42MB^) ...
if exist "tools\seconv\seconv.exe" (
  echo       已存在，跳过
) else (
  venv\Scripts\python.exe scripts\fetch_file.py ^
    "https://ghproxy.net/https://github.com/SubtitleEdit/subtitleedit/releases/download/v5.2.0/SeConv-Windows-x64.zip" ^
    "tools\SeConv-Windows-x64.zip" ^
    "14c8b467815dd04847e0e7b1e633d222d9c14c6a307acadc03e21112803780b7"
  if errorlevel 1 (
    echo       [警告] 下载失败。本项目可不依赖 seconv 运行 ^(会自动降级^)。
  ) else (
    venv\Scripts\python.exe -c "import zipfile;zipfile.ZipFile(r'tools\SeConv-Windows-x64.zip').extractall(r'tools\seconv')"
    echo       已解压到 tools\seconv\
  )
)

echo.
echo [6/6] 环境自检 ...
venv\Scripts\python.exe scripts\doctor.py

echo.
echo ============================================================
echo  安装完成
echo ============================================================
echo.
echo  后续每次使用前，请先激活环境变量（或直接跑 set_env.bat）：
echo      set HF_ENDPOINT=https://hf-mirror.com
echo      set HF_HOME=%CD%\.hf
echo.
echo  GPU 加速（可选但强烈建议，决定 large-v3 是否可用）：
echo      venv\Scripts\python.exe -m pip install --no-deps ^
echo        -i https://pypi.tuna.tsinghua.edu.cn/simple ^
echo        nvidia-cublas-cu12 nvidia-cudnn-cu12
echo      注意：必须用清华镜像；阿里云拉这两个大 wheel 会卡死。
echo.
echo  验证：
echo      venv\Scripts\python.exe scripts\doctor.py --deep
echo.
pause
