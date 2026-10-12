@echo off
cd /d %~dp0
echo === Step 1: Create venv ===
if not exist venv D:\python3.11\python.exe -m venv venv
echo === Step 2: Set env ===
set HF_ENDPOINT=https://hf-mirror.com
set HF_HOME=%CD%\.hf
set HF_HUB_DISABLE_XET=1
echo === Step 3: Install deps ===
venv\Scripts\python.exe -m pip install faster-whisper==1.2.1 ctranslate2==4.8.2 tokenizers huggingface_hub onnxruntime av==18.1.0 pysrt srt chardet numpy tqdm PyYAML -i https://mirrors.aliyun.com/pypi/simple/
echo === Step 4: Run test ===
venv\Scripts\python.exe -c "from model.pipeline import process; r=process(r'D:\yinpin\5号_测试素材\人教版八年级_U1.mp4', r'D:\yinpin\4号测试输出'); print(r.summary()); print('SRT:', r.srt_path)"
pause
