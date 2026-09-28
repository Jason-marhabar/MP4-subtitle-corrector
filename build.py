# -*- coding: utf-8 -*-
"""
PyInstaller 打包脚本（6号 职责）
用法：.\build.ps1  （内部调用本脚本）

打包要点：
1. GUI 入口：gui/main.py（当前使用 MockScheduler，联调后切换真实调度器）
2. FFmpeg 资源：assets/ffmpeg/ffmpeg.exe + ffprobe.exe 嵌入到 exe 同级目录
3. Whisper 模型：不强制嵌入（体积大），运行时通过 WHISPER_MODEL_DIR 或 models/ 加载
4. 运行环境：Python 3.11 + venv
"""
import os
import sys
import importlib.util

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(ROOT, "gui", "main.py")
FFMPEG_DIR = os.path.join(ROOT, "assets", "ffmpeg")

# 打包参数
args = [
    ENTRY,
    "--name", "MP4字幕矫正工具",
    "--windowed",                 # GUI 程序，无控制台窗口
    "--noconfirm",                # 覆盖旧产物
    "--clean",                    # 清理缓存
    "--distpath", os.path.join(ROOT, "dist"),
    "--workpath", os.path.join(ROOT, "build"),
    "--specpath", os.path.join(ROOT, "build"),
]

# ---- FFmpeg 资源嵌入（放到 exe 同级目录）----
if os.path.exists(os.path.join(FFMPEG_DIR, "ffmpeg.exe")):
    args.append("--add-binary")
    args.append(os.path.join(FFMPEG_DIR, "ffmpeg.exe;."))
    args.append("--add-binary")
    args.append(os.path.join(FFMPEG_DIR, "ffprobe.exe;."))
    print("[打包] 已嵌入 ffmpeg.exe / ffprobe.exe")
else:
    print("[警告] assets/ffmpeg/ffmpeg.exe 不存在！")
    print("      请先运行 setup.ps1 或手动放置 ffmpeg，否则目标机需自行安装 FFmpeg")

# ---- Whisper 模型（4号 模块联调后启用）----
if importlib.util.find_spec("whisper"):
    args.append("--collect-all")
    args.append("whisper")
    print("[打包] 已收集 whisper 包")
    # 若存在模型目录则嵌入（注意模型体积大，通常建议运行时外部加载）
    if os.path.isdir(os.path.join(ROOT, "models")):
        args.append("--add-data")
        args.append(os.path.join(ROOT, "models") + os.pathsep + "models")
        print("[打包] 已嵌入 models 目录")
else:
    print("[提示] 未安装 whisper（4号 联调后安装再打包）")

# ---- 隐藏导入（跨目录模块，避免 PyInstaller 漏收集）----
HIDDEN = [
    "scheduler.controller.real_scheduler",
    "scheduler.model.config",
    "scheduler.model.exceptions",
    "ffmpeg_module.model.ffmpeg_processor",
    "ffmpeg_module.model.ffmpeg_utils",
]
for h in HIDDEN:
    if importlib.util.find_spec(h.rsplit(".", 1)[0]):
        args.append("--hidden-import")
        args.append(h)

print("[打包] 参数：", args)
PyInstaller.__main__.run(args)
print("\n[完成] 产物位于 dist\\MP4字幕矫正工具\\")
print("       运行 dist\\MP4字幕矫正工具\\MP4字幕矫正工具.exe")
