# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本 - 在 Windows 上运行"""
import os
import sys
from pathlib import Path
from PyInstaller.__main__ import run

ROOT = Path(__file__).resolve().parent

args = [
    str(ROOT / "app" / "main.py"),
    "--name", "MP4字幕矫正工具",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--paths", str(ROOT),
    # 业务代码
    "--add-data", f"{ROOT/'model'}{os.pathsep}model",
    "--add-data", f"{ROOT/'controller'}{os.pathsep}controller",
    "--add-data", f"{ROOT/'config'}{os.pathsep}config",
    # hidden imports
    "--hidden-import", "model.pipeline",
    "--hidden-import", "model.asr",
    "--hidden-import", "model.subtitle_fix",
    "--hidden-import", "model.waveform",
    "--hidden-import", "model.ffmpeg_utils",
    "--hidden-import", "model.seconv_tools",
    "--hidden-import", "model.gpu_env",
    "--hidden-import", "model.ffmpeg_processor",
    "--hidden-import", "controller.scheduler",
    "--hidden-import", "controller.qt_worker",
    # 大依赖整包收集
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "onnxruntime",
]

# ffmpeg / ffprobe（放在 tools/ffmpeg/ 下，可选）
for exe in ["ffmpeg.exe", "ffprobe.exe"]:
    p = ROOT / "tools" / "ffmpeg" / exe
    if p.exists():
        args += ["--add-binary", f"{p}{os.pathsep}."]

run(args)
print("\n打包完成！exe 在 dist\\MP4字幕矫正工具\\ 目录下")
