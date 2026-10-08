# -*- coding: utf-8 -*-
"""
Demo 端到端真实验证：程序化触发完整信号链（同 GUI 点击"开始"效果），
验证 导入MP4 → 真提取WAV → 真输出矫正MP4 全流程。
"""
import os
import sys

ROOT = r"D:\yinpin\MP4字幕矫正工具"
for _p in (os.path.join(ROOT, "gui"), os.path.join(ROOT, "ffmpeg_module"),
           os.path.join(ROOT, "demo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import QApplication

from view.signals import GuiSignals, WorkerSignals
from real_demo_scheduler import RealDemoScheduler

app = QApplication([])
gui_signals = GuiSignals()
worker_signals = WorkerSignals()

logs = []
results = {}
errors = []
finished = []

worker_signals.log.connect(lambda lv, m: logs.append(f"[{lv}] {m}"))
worker_signals.task_finished.connect(
    lambda idx, mp4, srt: results.update({idx: (mp4, srt)}))
worker_signals.task_error.connect(lambda idx, m: errors.append(f"任务{idx}: {m}"))
worker_signals.all_finished.connect(lambda: finished.append(True))

sched = RealDemoScheduler(gui_signals, worker_signals)

SRC = r"D:\yinpin\5号_测试素材\中文测试 视频(1).mp4"
OUT = r"D:\yinpin\_demo_test_out"
if os.path.exists(OUT):
    import shutil
    shutil.rmtree(OUT)
os.makedirs(OUT, exist_ok=True)

print("=== 触发 start_requested（同界面点开始）===")
gui_signals.start_requested.emit([(SRC, OUT)])

if sched._thread:
    sched._thread.wait(120000)

# 处理排队的跨线程信号（模拟 GUI 事件循环）
for _ in range(5):
    app.processEvents()
    import time
    time.sleep(0.2)

print("\n=== 日志 ===")
for l in logs:
    print(" ", l)
print("\n=== 结果 ===")
print("task_finished:", results)
print("task_error:", errors or "无")
print("all_finished:", bool(finished))

wav = os.path.join(OUT, "中文测试 视频(1).wav")
mp4 = os.path.join(OUT, "中文测试 视频(1)_矫正.mp4")
print("\n=== 文件验证 ===")
print("WAV 生成:", os.path.exists(wav), f"({os.path.getsize(wav)/1024/1024:.1f} MB)" if os.path.exists(wav) else "")
print("矫正MP4 生成:", os.path.exists(mp4), f"({os.path.getsize(mp4)/1024/1024:.1f} MB)" if os.path.exists(mp4) else "")

import subprocess
if os.path.exists(wav):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,sample_rate,channels",
         "-of", "default=noprint_wrappers=1", wav],
        capture_output=True, text=True)
    print("WAV 规格:", probe.stdout.strip().replace("\n", " | ") or "未知")

ok = bool(results) and os.path.exists(wav) and os.path.exists(mp4) and not errors
print("\n=== 结论:", "PASS ✅ Demo 全流程真实跑通" if ok else "FAIL ❌", "===")
