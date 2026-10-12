@echo off
rem 在当前命令行窗口设置好本项目需要的环境变量。
rem 用法：先运行本脚本，再在同一窗口里执行命令；或直接 cmd /k set_env.bat
chcp 65001 >nul
cd /d "%~dp0"

set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HOME=%CD%\.hf"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PIP_CACHE_DIR=%CD%\.pipcache"
set "TMP=%CD%\.tmp"
set "TEMP=%CD%\.tmp"

rem 把 pip 分发的 CUDA 运行库加入 PATH。
rem ctranslate2 是 C++ 扩展，内部按 PATH 解析 cublas64_12.dll；
rem 只调 os.add_dll_directory() 无效（实测）。
set "NVDIR=%CD%\venv\Lib\site-packages\nvidia"
if exist "%NVDIR%\cublas\bin" set "PATH=%NVDIR%\cublas\bin;%PATH%"
if exist "%NVDIR%\cudnn\bin"  set "PATH=%NVDIR%\cudnn\bin;%PATH%"

echo 环境变量已设置：
echo   HF_ENDPOINT=%HF_ENDPOINT%
echo   HF_HOME=%HF_HOME%
echo   PIP_CACHE_DIR=%PIP_CACHE_DIR%
echo   PATH 中的 CUDA 目录：
if exist "%NVDIR%\cublas\bin" echo     %NVDIR%\cublas\bin
if exist "%NVDIR%\cudnn\bin"  echo     %NVDIR%\cudnn\bin
if not exist "%NVDIR%\cublas\bin" echo     ^(未安装 CUDA 运行库，GPU 将不可用^)
echo.
echo 提示：直接用 venv\Scripts\python.exe 运行脚本即可（无需 activate）。
