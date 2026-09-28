# -*- coding: utf-8 -*-
"""程序入口

运行方式（在 my_app 目录下）：
    python main.py

说明：
    当前接入任务2开发的真实业务调度器（controller/real_scheduler.py）。
    第1周版本：调度框架已完成，FFmpeg/ASR 模块为模拟实现，待后续联调接入。
"""

import sys

from PyQt6.QtWidgets import QApplication

from controller.real_scheduler import RealScheduler
from view.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    window = MainWindow()

    # 把 GUI 的输入/输出信号交给业务调度层
    scheduler = RealScheduler(window.gui_signals, window.worker_signals)
    window._scheduler = scheduler  # 持有引用，避免被垃圾回收

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
