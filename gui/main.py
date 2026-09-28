# -*- coding: utf-8 -*-
"""程序入口

运行方式（在 my_app 目录下）：
    python main.py

说明：
    当前接入的是演示用假调度器（controller/mock_scheduler.py），用于让 GUI
    单独运行时也能看到完整效果。联调时，请把这里的 MockScheduler 替换为
    任务2 写好的真实业务调度模块。
"""

import sys

from PyQt6.QtWidgets import QApplication

# TODO: 联调时替换为真实业务调度器
from controller.mock_scheduler import MockScheduler
from view.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    window = MainWindow()

    # 把 GUI 的输入/输出信号交给业务调度层。
    # 联调时只需替换这一行为真实调度器：
    #   scheduler = RealScheduler(window.gui_signals, window.worker_signals)
    scheduler = MockScheduler(window.gui_signals, window.worker_signals)
    window._scheduler = scheduler  # 持有引用，避免被垃圾回收

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
