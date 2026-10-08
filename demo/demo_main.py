# -*- coding: utf-8 -*-
"""真实功能 Demo 入口（6号 制作，不修改 1号 原代码）

运行方式：
    python demo/demo_main.py

与正式版区别：调度器换成 RealDemoScheduler（真实调用 3号 FFmpeg，
字幕部分待 4号 接入），用于先跑通"导入 MP4 → 提取音频 → 输出矫正 MP4"。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "gui"), os.path.join(ROOT, "ffmpeg_module")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import QApplication

from real_demo_scheduler import RealDemoScheduler
from view.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    scheduler = RealDemoScheduler(window.gui_signals, window.worker_signals)
    window._scheduler = scheduler
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
