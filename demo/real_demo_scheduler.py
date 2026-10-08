# -*- coding: utf-8 -*-
"""
真实功能 Demo 调度器（6号 制作，不修改 1号 原代码）

相比 mock_scheduler 的改进：
  - 阶段1「提取音频」：真实调用 3号 FFmpegProcessor.extract_audio（16kHz 单声道 WAV）
  - 阶段2「音视频封装」：真实调用 copy_mp4_stream（-c copy 防音画偏移，输出 _矫正.mp4）
  - 阶段3/4「Whisper 识别 / 字幕校正」：待 4号 ASR 模块接入，demo 中占位提示

接口与 MockScheduler 完全一致（GuiSignals/WorkerSignals 契约），GUI 无需改动。
"""

import os

from PyQt6.QtCore import QThread

from view.signals import GuiSignals, WorkerSignals
from model.ffmpeg_processor import FFmpegProcessor


class RealDemoScheduler:
    """真实功能 Demo 调度器：接入 3号 FFmpeg，字幕部分待 4号"""

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
        self._thread = _RealWorkerThread(tasks, self.worker_signals)
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


class _RealWorkerThread(QThread):
    """后台线程：真实调用 FFmpeg 提取音频 + 流拷贝封装"""

    def __init__(self, tasks, signals, parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self.signals = signals
        self._stop = False
        self._pause = False
        self._processor = FFmpegProcessor()

    def stop(self):
        self._stop = True
        self._pause = False

    def pause(self):
        self._pause = True

    def resume(self):
        self._pause = False

    def _wait_if_paused(self):
        import time
        while self._pause and not self._stop:
            time.sleep(0.1)

    def run(self):
        for idx, (path, out_dir) in enumerate(self._tasks):
            if self._stop:
                self.signals.log.emit("warning", "已停止，剩余任务未处理")
                break

            fname = os.path.basename(path)
            if not os.path.exists(path):
                self.signals.task_error.emit(idx, f"文件不存在：{path}")
                continue

            out_dir = out_dir or os.path.dirname(path)
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(fname)[0]

            # ---- 阶段1：真实提取音频 ----
            self._wait_if_paused()
            if self._stop:
                break
            wav_path = os.path.join(out_dir, base + ".wav")
            self.signals.log.emit("info", f"[{fname}] 开始提取音频（16kHz 单声道）")
            try:
                real_wav = self._processor.extract_audio(path, wav_path)
                self.signals.task_progress.emit(idx, "提取音频", 25)
                self.signals.log.emit("info", f"[{fname}] 音频提取成功：{real_wav}")
            except Exception as e:
                msg = str(e).splitlines()[0][:100] if str(e) else "未知错误"
                self.signals.task_error.emit(idx, f"提取音频失败：{msg}")
                self.signals.log.emit("error", f"[{fname}] 提取音频失败：{msg}")
                continue

            # ---- 阶段2：真实流拷贝封装 ----
            self._wait_if_paused()
            if self._stop:
                break
            out_mp4 = os.path.join(out_dir, base + "_矫正.mp4")
            self.signals.log.emit("info", f"[{fname}] 开始音视频封装（-c copy 防偏移）")
            try:
                real_mp4 = self._processor.copy_mp4_stream(path, out_mp4)
                self.signals.task_progress.emit(idx, "音视频封装", 50)
                self.signals.log.emit("info", f"[{fname}] 封装成功：{real_mp4}")
            except Exception as e:
                msg = str(e).splitlines()[0][:100] if str(e) else "未知错误"
                self.signals.task_error.emit(idx, f"封装失败：{msg}")
                continue

            # ---- 阶段3/4：字幕（待 4号）----
            self._wait_if_paused()
            if self._stop:
                break
            self.signals.task_progress.emit(idx, "Whisper 识别", 75)
            self.signals.log.emit(
                "warning",
                f"[{fname}] Whisper 识别待 4号 ASR 模块接入，demo 暂不生成字幕",
            )
            self.signals.task_progress.emit(idx, "字幕校正", 100)

            out_srt = os.path.join(out_dir, base + ".srt")
            self.signals.task_finished.emit(idx, out_mp4, out_srt)
            self.signals.log.emit(
                "info",
                f"[{fname}] Demo 处理完成。字幕文件 {out_srt} 待 4号 模块交付后生成",
            )

        if not self._stop:
            self.signals.log.emit("info", "全部任务处理完毕")
