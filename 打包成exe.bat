@echo off
cd /d %~dp0
echo === Step 1: Check Python ===
D:\python3.11\python.exe --version
if errorlevel 1 (echo Python not found at D:\python3.11\python.exe & pause & exit /b 1)
echo === Step 2: Install deps (5-10 min) ===
D:\python3.11\python.exe -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
D:\python3.11\python.exe -m pip install pyinstaller PyQt6 faster-whisper==1.2.1 ctranslate2==4.8.2 tokenizers huggingface_hub onnxruntime av==18.1.0 pysrt srt chardet numpy tqdm PyYAML -i https://mirrors.aliyun.com/pypi/simple/
echo === Step 3: Build exe (2-5 min) ===
D:\python3.11\python.exe build.py
echo === DONE ===
echo exe is in dist\MP4-subtitle-tool\
pause
