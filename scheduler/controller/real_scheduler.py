# -*- coding: utf-8 -*-
"""
真实业务调度器（任务2 - 业务调度 & 多线程控制）

职责：
  1. 接收 GUI 发出的操作指令（开始/暂停/停止）
  2. 在后台线程中执行耗时任务，防止界面卡死
  3. 串联整个处理流程：FFmpeg 提取音频 → 音视频封装 → Whisper 识别 → 字幕校正
  4. 通过 WorkerSignals 回传进度、日志、结果给 GUI
  5. 统一异常捕获，单任务失败不影响整个队列
  6. 读取全局配置，管理调度参数

版本：第2周
  - 接入 AppConfig 全局配置模块
  - 接入自定义异常体系（model.exceptions）
  - FFmpeg 和 ASR 模块仍为模拟实现，待联调接入
"""

import os
import time
import traceback

from PyQt6.QtCore import QThread, QMutex, QMutexLocker

from view.signals import GuiSignals, WorkerSignals
from model.config import AppConfig
from model.exceptions import (
    AppBaseError,
    TaskStoppedError,
    TaskAlreadyRunningError,
    ConfigError,
)


# ---------------------------------------------------------------------------
# 处理阶段定义（进度百分比分配）
# ---------------------------------------------------------------------------
# 四个阶段的权重（合计100%），可根据实际耗时调整
STAGE_WEIGHTS = [
    ("提取音频", 25),    # 阶段1：FFmpeg 提取音频
    ("音视频封装", 15),  # 阶段2：FFmpeg 音视频流拷贝封装
    ("Whisper 识别", 35),  # 阶段3：Whisper 语音转文字
    ("字幕校正", 25),    # 阶段4：字幕后处理 + 时间轴微调
]


def _calc_progress(stage_idx: int, stage_inner_percent: int = 0) -> int:
    """计算整体进度百分比
    stage_idx: 当前阶段索引（0~3）
    stage_inner_percent: 当前阶段内的进度（0~100）
    """
    done = sum(w for _, w in STAGE_WEIGHTS[:stage_idx])
    current_weight = STAGE_WEIGHTS[stage_idx][1]
    return done + int(current_weight * stage_inner_percent / 100)


# ---------------------------------------------------------------------------
# 调度器主类
# ---------------------------------------------------------------------------
class RealScheduler:
    """真实业务调度器

    用法：
        scheduler = RealScheduler(gui_signals, worker_signals)
        # 之后 GUI 的操作信号会自动触发调度器

    配置：
        调度器从 AppConfig() 单例读取配置，包括：
        - scheduler.continue_on_error：失败后是否继续
        - scheduler.max_concurrent_tasks：最大并发数（暂未实现并行）
        - output.default_output_dir：默认输出目录
    """

    def __init__(
        self,
        gui_signals: GuiSignals,
        worker_signals: WorkerSignals,
        config: AppConfig = None,
    ):
        self.gui_signals = gui_signals
        self.worker_signals = worker_signals
        self._worker = None  # 后台工作线程

        # 配置（传入则使用传入的，否则用全局单例）
        self.config = config or AppConfig()

        # 连接 GUI 发出的信号
        self.gui_signals.start_requested.connect(self._on_start)
        self.gui_signals.stop_requested.connect(self._on_stop)
        self.gui_signals.pause_requested.connect(self._on_pause)
        self.gui_signals.resume_requested.connect(self._on_resume)
        # clear_requested 由 GUI 自己处理，调度层无需响应

    # ------------------------------------------------------------------
    # GUI 信号处理函数（在主线程执行，只做轻量操作）
    # ------------------------------------------------------------------
    def _on_start(self, tasks: list):
        """用户点击「开始」
        tasks: [(输入文件路径, 输出目录), ...]
        """
        if self._worker and self._worker.isRunning():
            err = TaskAlreadyRunningError()
            self.worker_signals.log.emit("warning", err.user_message)
            return

        if not tasks:
            self.worker_signals.log.emit("warning", "任务列表为空")
            return

        # 启动前做一次配置校验（非致命，只告警）
        config_errors = self.config.validate()
        if config_errors:
            for err_msg in config_errors:
                self.worker_signals.log.emit("warning", f"[配置告警] {err_msg}")

        self._worker = _ProcessWorkerThread(tasks, self.worker_signals, self.config)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_stop(self):
        """用户点击「停止」"""
        if self._worker and self._worker.isRunning():
            self.worker_signals.log.emit("warning", "正在停止当前任务...")
            self._worker.request_stop()
        else:
            self.worker_signals.log.emit("warning", "当前没有正在处理的任务")

    def _on_pause(self):
        """用户点击「暂停」"""
        if self._worker and self._worker.isRunning():
            self._worker.request_pause()
            self.worker_signals.log.emit("warning", "已请求暂停")
        else:
            self.worker_signals.log.emit("warning", "当前没有正在处理的任务")

    def _on_resume(self):
        """用户点击「继续」"""
        if self._worker and self._worker.isRunning():
            self._worker.request_resume()
            self.worker_signals.log.emit("info", "已请求继续")
        else:
            self.worker_signals.log.emit("warning", "当前没有正在处理的任务")

    def _on_worker_finished(self):
        """后台线程结束（无论成功/失败/停止都会触发）"""
        self.worker_signals.all_finished.emit()
        self._worker = None


# ---------------------------------------------------------------------------
# 后台工作线程
# ---------------------------------------------------------------------------
class _ProcessWorkerThread(QThread):
    """后台工作线程：逐个任务走完整处理流程

    所有耗时操作（FFmpeg、Whisper、字幕处理）都在这里执行，
    绝不直接操作 GUI 控件，只通过 signals 发信号。
    """

    def __init__(self, tasks: list, signals: WorkerSignals, config: AppConfig, parent=None):
        super().__init__(parent)
        self._tasks = tasks  # [(input_path, out_dir), ...]
        self.signals = signals
        self.config = config  # 全局配置

        # 线程安全的控制标志
        self._mutex = QMutex()
        self._stop_flag = False
        self._pause_flag = False

    # ------------------------------------------------------------------
    # 外部控制方法（主线程调用，需加锁）
    # ------------------------------------------------------------------
    def request_stop(self):
        """请求停止（线程安全）"""
        locker = QMutexLocker(self._mutex)
        self._stop_flag = True
        self._pause_flag = False  # 解除暂停，让线程能退出

    def request_pause(self):
        """请求暂停（线程安全）"""
        locker = QMutexLocker(self._mutex)
        self._pause_flag = True

    def request_resume(self):
        """请求继续（线程安全）"""
        locker = QMutexLocker(self._mutex)
        self._pause_flag = False

    # ------------------------------------------------------------------
    # 内部检查方法（子线程调用）
    # ------------------------------------------------------------------
    def _check_control(self) -> bool:
        """检查暂停/停止状态
        返回 True 表示应该停止当前任务
        """
        while True:
            locker = QMutexLocker(self._mutex)
            if self._stop_flag:
                return True  # 需要停止
            if not self._pause_flag:
                return False  # 正常继续
            # 暂停状态，释放锁后 sleep 一会儿再检查
            locker.unlock()
            self.msleep(100)  # 暂停时每 100ms 检查一次

    # ------------------------------------------------------------------
    # 线程主函数
    # ------------------------------------------------------------------
    def run(self):
        """线程入口：逐个处理任务"""
        total = len(self._tasks)
        self.signals.log.emit("info", f"任务队列开始，共 {total} 个文件")

        # 从配置读取参数
        continue_on_error = self.config.continue_on_error

        for task_idx, (input_path, out_dir) in enumerate(self._tasks):
            # 每个任务开始前检查一次停止/暂停
            if self._check_control():
                self.signals.log.emit("warning", f"已停止，剩余 {total - task_idx} 个任务未处理")
                # 剩余任务标记为失败
                for i in range(task_idx, total):
                    self.signals.task_error.emit(i, "已停止")
                break

            fname = os.path.basename(input_path)
            # 输出目录为空则使用配置的默认目录，再不行用源文件同目录
            if not out_dir:
                out_dir = self.config.default_output_dir or os.path.dirname(input_path)

            try:
                self.signals.log.emit("info", f"[{task_idx + 1}/{total}] 开始处理：{fname}")

                # 执行完整处理流程
                out_mp4, out_srt = self._process_single_file(task_idx, input_path, out_dir)

                # 任务完成
                self.signals.task_finished.emit(task_idx, out_mp4, out_srt)
                self.signals.log.emit("info", f"[{fname}] 处理完成 ✓")
                self.signals.log.emit("info", f"  输出视频：{out_mp4}")
                self.signals.log.emit("info", f"  输出字幕：{out_srt}")

            except TaskStoppedError:
                # 用户请求停止
                self.signals.task_error.emit(task_idx, "已停止")
                self.signals.log.emit("warning", f"[{fname}] 处理被用户停止")
                # 剩余任务也标记为停止
                for i in range(task_idx + 1, total):
                    self.signals.task_error.emit(i, "已停止")
                break

            except AppBaseError as e:
                # 业务异常（有友好的用户提示）
                self.signals.task_error.emit(task_idx, e.user_message)
                self.signals.log.emit("error", f"[{fname}] {e.user_message}")
                if e.details:
                    self.signals.log.emit("debug", f"详细信息：{e.details}")
                # 根据配置决定是否继续
                if not continue_on_error:
                    self.signals.log.emit("error", "配置为失败即停止，剩余任务取消")
                    for i in range(task_idx + 1, total):
                        self.signals.task_error.emit(i, "因前序任务失败而取消")
                    break

            except Exception as e:
                # 未知异常
                error_msg = f"{type(e).__name__}: {e}"
                self.signals.task_error.emit(task_idx, str(e))
                self.signals.log.emit("error", f"[{fname}] 处理失败：{error_msg}")
                # 打印详细堆栈到日志（调试用）
                self.signals.log.emit("debug", traceback.format_exc())
                # 根据配置决定是否继续
                if not continue_on_error:
                    self.signals.log.emit("error", "配置为失败即停止，剩余任务取消")
                    for i in range(task_idx + 1, total):
                        self.signals.task_error.emit(i, "因前序任务失败而取消")
                    break

        # 全部结束（正常完成 / 被停止）
        if not self._stop_flag:
            self.signals.log.emit("info", "全部任务处理完毕")

    # ------------------------------------------------------------------
    # 单文件处理流程
    # ------------------------------------------------------------------
    def _process_single_file(self, task_id: int, input_path: str, out_dir: str):
        """处理单个 MP4 文件，返回 (output_mp4_path, output_srt_path)

        这是串联全流程的核心函数。当前为模拟实现，
        联调时替换各阶段为真实模块调用即可。
        """
        fname = os.path.basename(input_path)
        base_name = os.path.splitext(fname)[0]

        # 确保输出目录存在
        os.makedirs(out_dir, exist_ok=True)

        # ---------- 阶段 1：提取音频（FFmpeg 模块） ----------
        if self._check_control():
            raise TaskStoppedError()
        stage_name = STAGE_WEIGHTS[0][0]
        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(0, 0))
        self.signals.log.emit("info", f"[{fname}] 阶段1：{stage_name}")

        # TODO: 联调时替换为真实 FFmpeg 调用
        # from model.ffmpeg_module import extract_audio
        # audio_path = extract_audio(input_path, out_dir)
        self._simulate_work(1.0, task_id, 0)  # 模拟耗时
        audio_path = os.path.join(out_dir, base_name + ".wav")  # 模拟输出

        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(0, 100))

        # ---------- 阶段 2：音视频封装（FFmpeg 模块） ----------
        if self._check_control():
            raise TaskStoppedError()
        stage_name = STAGE_WEIGHTS[1][0]
        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(1, 0))
        self.signals.log.emit("info", f"[{fname}] 阶段2：{stage_name}")

        # TODO: 联调时替换为真实 FFmpeg 调用
        # from model.ffmpeg_module import remux_mp4
        # output_mp4 = remux_mp4(input_path, out_dir)
        self._simulate_work(0.8, task_id, 1)  # 模拟耗时
        output_mp4 = os.path.join(out_dir, base_name + "_矫正.mp4")  # 模拟输出

        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(1, 100))

        # ---------- 阶段 3：Whisper 识别（ASR 模块） ----------
        if self._check_control():
            raise TaskStoppedError()
        stage_name = STAGE_WEIGHTS[2][0]
        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(2, 0))
        self.signals.log.emit("info", f"[{fname}] 阶段3：{stage_name}")

        # TODO: 联调时替换为真实 Whisper 调用
        # from model.asr_module import transcribe
        # raw_srt_content = transcribe(audio_path)
        self._simulate_work(1.5, task_id, 2)  # 模拟耗时（Whisper 通常较慢）

        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(2, 100))

        # ---------- 阶段 4：字幕校正（ASR 模块） ----------
        if self._check_control():
            raise TaskStoppedError()
        stage_name = STAGE_WEIGHTS[3][0]
        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(3, 0))
        self.signals.log.emit("info", f"[{fname}] 阶段4：{stage_name}")

        # TODO: 联调时替换为真实字幕校正调用
        # from model.subtitle_module import correct_subtitles
        # output_srt = correct_subtitles(raw_srt_content, out_dir, base_name)
        self._simulate_work(1.0, task_id, 3)  # 模拟耗时
        output_srt = os.path.join(out_dir, base_name + ".srt")  # 模拟输出

        self.signals.task_progress.emit(task_id, stage_name, _calc_progress(3, 100))

        return output_mp4, output_srt

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _simulate_work(self, duration: float, task_id: int, stage_idx: int):
        """模拟耗时操作，同时支持暂停/停止检查

        duration: 模拟耗时秒数
        task_id: 任务索引，用于更新进度
        stage_idx: 当前阶段索引，用于计算进度
        """
        steps = 20  # 分20步更新进度，使进度条更平滑
        step_duration = duration / steps
        for i in range(1, steps + 1):
            if self._check_control():
                raise TaskStoppedError()
            time.sleep(step_duration)
            inner_percent = int(i / steps * 100)
            self.signals.task_progress.emit(
                task_id, STAGE_WEIGHTS[stage_idx][0], _calc_progress(stage_idx, inner_percent)
            )
