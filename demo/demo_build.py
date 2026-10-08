# -*- coding: utf-8 -*-
"""
真实功能 Demo 打包脚本（6号）
用法：python demo/demo_build.py
产物：dist_demo\MP4字幕矫正工具Demo\MP4字幕矫正工具Demo.exe
"""
import os
import sys

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "demo", "demo_main.py")
FFMPEG_DIR = os.path.join(ROOT, "assets", "ffmpeg")
GUI_DIR = os.path.join(ROOT, "gui")
FF_DIR = os.path.join(ROOT, "ffmpeg_module")

args = [
    ENTRY,
    "--name", "MP4字幕矫正工具Demo",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--distpath", os.path.join(ROOT, "dist_demo"),
    "--workpath", os.path.join(ROOT, "build_demo"),
    "--specpath", os.path.join(ROOT, "build_demo"),
    # 确保能找到 gui 与 ffmpeg_module 下的模块
    "--paths", GUI_DIR,
    "--paths", FF_DIR,
]

if os.path.exists(os.path.join(FFMPEG_DIR, "ffmpeg.exe")):
    args += ["--add-binary", os.path.join(FFMPEG_DIR, "ffmpeg.exe;."),
             "--add-binary", os.path.join(FFMPEG_DIR, "ffprobe.exe;.")]

print("[demo 打包] 参数：", args)
PyInstaller.__main__.run(args)
print("\n[完成] 产物位于 dist_demo\\MP4字幕矫正工具Demo\\")
