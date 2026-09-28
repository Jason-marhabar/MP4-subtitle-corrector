# -*- coding: utf-8 -*-
"""演示用假调度器（任务2 的占位实现）

作用：让 GUI 单独运行时也能看到完整的处理效果，并演示 GUI 与业务层
之间约定的信号/槽调用方式。

联调时请用任务2写好的真实业务调度模块替换本文件：
  - 真实调度器应在子线程/线程池中调用 FFmpeg 模块、ASR 字幕模块；
  - 通过 WorkerSignals 回传进度、日志、结果。
"""

import os
import time

from PyQt6.QtCore import QThread

from view.signals import GuiSignals, WorkerSignals


class MockScheduler:
    """模拟调度器：收到开始信号后，用后台线程假跑一遍流程"""

    STAGES = ["提取音频", "音视频封装", "Whisper 识别", "字幕校正"]

    def __init__(self, gui_signals: GuiSignals, worker_signals: WorkerSignals):
        self.gui_signals = gui_signals
        self.worker_signals = worker_signals
        self._thread = None

        self.gui_signals.start_requested.connect(self._on_start)
        self.gui_signals.stop_requested.connect(self._on_stop)
        self.gui_signals.pause_requested.connect(self._on_pause)
        self.gui_signals.resume_requested.connect(self._on_resume)

    def _on_start(self, tasks):
        if self._thread and self._thread.isRunning():
            self.worker_signals.log.emit("warning", "已有任务在处理中")
            return
        self._thread = _MockWorkerThread(tasks, self.worker_signals)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_stop(self):
        if self._thread and self._thread.isRunning():
            self._thread.stop()

    def _on_pause(self):
        if self._thread and self._thread.isRunning():
            self._thread.pause()
        else:
            self.worker_signals.log.emit("warning", "当前没有正在处理的任务")

    def _on_resume(self):
        if self._thread and self._thread.isRunning():
            self._thread.resume()

    def _on_thread_finished(self):
        self.worker_signals.all_finished.emit()


class _MockWorkerThread(QThread):
    """后台线程：逐个任务模拟 4 个处理阶段"""

    def __init__(self, tasks, signals, parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self.signals = signals
        self._stop = False
        self._pause = False

    def stop(self):
        self._stop = True
        self._pause = False  # 解除暂停，让线程能退出

    def pause(self):
        self._pause = True

    def resume(self):
        self._pause = False

    def run(self):
        stages = MockScheduler.STAGES
        for idx, (path, out_dir) in enumerate(self._tasks):
            if self._stop:
                self.signals.log.emit("warning", "已停止，剩余任务未处理")
                break

            fname = os.path.basename(path)
            out_dir = out_dir or os.path.dirname(path)  # 空目录则用源文件目录
            self.signals.log.emit("info", f"开始处理：{fname}")

            for j, stage in enumerate(stages):
                while self._pause and not self._stop:
                    time.sleep(0.1)
                if self._stop:
                    break

                time.sleep(0.5)  # 模拟耗时
                percent = int((j + 1) / len(stages) * 100)
                self.signals.log.emit("info", f"[{fname}] {stage} 完成")
                self.signals.task_progress.emit(idx, stage, percent)

            if self._stop:
                self.signals.task_error.emit(idx, "已停止")
                break

            base = os.path.splitext(fname)[0]
            out_mp4 = os.path.join(out_dir, base + "_矫正.mp4")
            out_srt = os.path.join(out_dir, base + ".srt")
            self.signals.task_finished.emit(idx, out_mp4, out_srt)
            self.signals.log.emit("info", f"[{fname}] 处理完成，输出：{out_srt}")

        if not self._stop:
            self.signals.log.emit("info", "全部任务处理完毕")
