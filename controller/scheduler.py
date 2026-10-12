# -*- coding: utf-8 -*-
"""
业务调度层（第二板块）—— 调度核心

职责
----
GUI 与底层音视频/字幕模块之间的中间层，核心目标是**不让界面卡死**：

  1. 多线程执行：耗时任务（转写可达数十秒）放在工作线程，GUI 线程只收回调
  2. 批量任务队列：支持一次导入多个 MP4 排队处理
  3. 串联流程：接收 GUI 任务 -> 调用音视频模块 -> 调用字幕模块 -> 返回结果
  4. 全局配置管理、统一异常捕获、日志向前端转发

为什么本模块**不依赖 PySide6**
------------------------------
这个决定是有意为之：

  · 打包（第六板块）：少一层 Qt 耦合，PyInstaller 更省事
  · 可测试（第五板块）：并发/批量/取消这些逻辑必须在无 GUI 环境可测
  · 可替换：将来换 GUI 框架或改接口，调度核心不用重写

Qt 接入放在 ``controller/qt_worker.py``，用信号槽把这里的回调桥接过去。

线程模型
--------
    GUI 线程 ──submit()──> 队列 ──> 工作线程池(N 个)
       ↑                                   │
       └──────── 回调（工作线程触发）────────┘

**重要**：所有回调都在**工作线程**中被调用。若回调里要更新界面，
必须自行跨线程投递（Qt 的 queued signal/slot 天然满足；
``qt_worker.InProcessWorker`` 已处理好）。
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 任务状态

class JobState(str, Enum):
    WAITING = "waiting"      # 排队中
    RUNNING = "running"      # 处理中
    DONE = "done"            # 成功
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消
    SKIPPED = "skipped"      # 被跳过（输入不合法等）


TERMINAL_STATES = {JobState.DONE, JobState.FAILED,
                   JobState.CANCELLED, JobState.SKIPPED}

STATE_TEXT = {
    JobState.WAITING: "等待中",
    JobState.RUNNING: "处理中",
    JobState.DONE: "已完成",
    JobState.FAILED: "失败",
    JobState.CANCELLED: "已取消",
    JobState.SKIPPED: "已跳过",
}


# ---------------------------------------------------------------- 数据模型

@dataclass
class Job:
    """一个待处理的视频任务（GUI 的"任务列表"里的一行）"""

    job_id: int
    video: str
    out_dir: str
    state: JobState = JobState.WAITING
    progress: float = 0.0            # 0.0~1.0
    stage: str = ""                  # 当前阶段描述
    srt_path: Optional[str] = None
    mp4_path: Optional[str] = None
    cue_count: int = 0
    elapsed: float = 0.0
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    started_at: Optional[float] = None

    @property
    def name(self) -> str:
        return Path(self.video).name

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass
class BatchConfig:
    """全局配置（由 GUI 的"设置"面板或 config/ 文件提供）"""

    out_dir: str = ""                  # 输出目录（GUI 的"保存文件夹"）
    workers: int = 1                   # 并发工作线程数
    make_mp4: bool = True              # 是否输出 remux 的 MP4
    keep_audio: bool = False           # 是否保留中间 WAV
    language: str = "en"
    model: str = "small"
    # device="auto" 时在首次加载模型前探测 GPU，可用则用 cuda，否则回退 cpu。
    # 实测（RTX 4060 Laptop 8GB，313s 英语音频）：
    #   small    CPU 42.0s (RTF 7.5x)  |  GPU  7.8s (RTF 40.3x)   提速 5.4x
    #   large-v3 CPU 极慢（不可用）     |  GPU 59.2s (RTF  5.3x)
    # 即 large-v3 只有靠 GPU 才具可用性。
    device: str = "auto"
    compute_type: str = ""             # 留空则按 device 自动选择

    # 校正参数（透传给第四板块）
    max_line_chars: int = 43
    max_lines: int = 2
    min_gap_ms: int = 24
    split_enabled: bool = True

    # 调度行为
    copy_srt_next_to_video: bool = False   # 是否额外复制成"视频同名.srt"便于 Aboboo
    continue_on_error: bool = True         # 单个任务失败是否继续队列
    overwrite: bool = False                # 输出已存在时是否覆盖

    @classmethod
    def from_json(cls, path: str) -> "BatchConfig":
        """从 JSON 载入（缺字段用默认值）。文件损坏时回退默认值并告警，不抛异常。"""
        import json
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("配置文件读取失败，使用默认配置：%s", e)
            return cls()
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def to_json(self, path: str) -> str:
        import json
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {f: getattr(self, f)
                for f in self.__dataclass_fields__}  # type: ignore[attr-defined]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return str(p)


@dataclass
class BatchProgress:
    """给 GUI 用的整体进度快照"""

    total: int = 0
    done: int = 0
    failed: int = 0
    cancelled: int = 0
    skipped: int = 0
    running: int = 0
    waiting: int = 0
    overall: float = 0.0            # 0.0~1.0 加权总进度
    current: str = ""               # 当前处理的任务名

    @property
    def finished(self) -> int:
        return self.done + self.failed + self.cancelled + self.skipped

    def text(self) -> str:
        return (f"{self.finished}/{self.total} 完成"
                f"（失败 {self.failed}，取消 {self.cancelled}）")


# 回调类型（全部在工作线程被调用）
JobCb = Callable[[Job], None]
ProgressCb = Callable[[BatchProgress], None]
LogCb = Callable[[str, str], None]        # (级别, 消息)
BatchDoneCb = Callable[[List[Job]], None]


# ---------------------------------------------------------------- 日志桥

class CallbackLogHandler(logging.Handler):
    """把 logging 记录转发给前端日志窗口。

    第六板块/第四板块内部大量使用 logging，这样它们的日志不用改代码
    就能出现在 GUI 日志区。
    """

    def __init__(self, callback: LogCb):
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(record.levelname, self.format(record))
        except Exception:
            # 日志转发失败绝不能影响主流程
            pass


# ---------------------------------------------------------------- 调度核心

class BatchScheduler:
    """批量任务调度器。

    示例
    ----
        sched = BatchScheduler(BatchConfig(out_dir="D:/out", workers=2))
        sched.on_job_update = lambda job: print(job.name, job.state, job.progress)
        sched.on_log = lambda lvl, msg: print(f"[{lvl}] {msg}")
        sched.add_files(["a.mp4", "b.mp4"])
        jobs = sched.run()          # 阻塞直到全部完成
        sched.shutdown()

    GUI 用法见 ``controller/qt_worker.py``（非阻塞，通过信号回主线程）。
    """

    def __init__(self, config: Optional[BatchConfig] = None):
        self.config = config or BatchConfig()
        self.on_job_update: Optional[JobCb] = None
        self.on_progress: Optional[ProgressCb] = None
        self.on_log: Optional[LogCb] = None
        self.on_batch_done: Optional[BatchDoneCb] = None

        self._jobs: List[Job] = []
        self._jobs_lock = threading.RLock()
        self._next_id = 1

        self._cancel_all = threading.Event()
        self._cancel_one: Dict[int, threading.Event] = {}
        self._cancel_lock = threading.Lock()

        self._pool: Optional[ThreadPoolExecutor] = None
        self._engine = None                     # ASR 模型（懒加载，全局复用一次）
        self._engine_lock = threading.Lock()
        # 推理串行锁：ctranslate2 的模型实例不支持多线程并发推理。
        # 实测 2 个线程共用同一实例时结果不可靠（耗时统计异常），且没有任何
        # 吞吐收益 —— GPU 上并发只是争抢同一份显存与算力。
        # 因此把推理串行化：即使 workers>1，界面/队列仍是并发的，
        # 只有"真正推理"这一段排队执行。
        self._infer_lock = threading.Lock()

        self._log_handler: Optional[CallbackLogHandler] = None
        self._own_logger_names: List[str] = []
        # 通知串行化：多个工作线程会同时上报进度，若不串行化，
        # GUI 可能收到交错的进度（甚至倒退）。回调本身也要保证不被并发调用。
        self._notify_lock = threading.RLock()

    # -------------------------------------------------------- 日志接入
    def attach_logging(self, level: int = logging.INFO,
                       logger_names: Sequence[str] = ("model", "controller")) -> None:
        """把本项目各模块的 logging 输出接到 ``on_log`` 回调。

        只挂到本项目自己的 logger 上，避免把第三方库（如 faster-whisper）
        的噪声也灌进 GUI 日志窗口。
        """
        self.detach_logging()
        handler = CallbackLogHandler(lambda lvl, msg: self._emit_log(lvl, msg))
        handler.setFormatter(logging.Formatter("%(message)s"))
        for name in logger_names:
            lg = logging.getLogger(name)
            lg.addHandler(handler)
            if lg.level == logging.NOTSET or lg.level > level:
                lg.setLevel(level)
            self._own_logger_names.append(name)
        self._log_handler = handler

    def detach_logging(self) -> None:
        if self._log_handler is not None:
            for name in self._own_logger_names:
                logging.getLogger(name).removeHandler(self._log_handler)
            self._own_logger_names.clear()
            self._log_handler = None

    def _emit_log(self, level: str, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(level, msg)
            except Exception:
                pass

    # -------------------------------------------------------- 队列管理
    def add_files(self, paths: Sequence[str],
                  out_dir: Optional[str] = None) -> List[Job]:
        """把若干视频加入队列，返回新建的 Job 列表。

        不存在的文件会被标为 SKIPPED（而不是抛异常），
        这样 GUI 可以一次性给出所有问题的反馈。
        """
        added: List[Job] = []
        with self._jobs_lock:
            for p in paths:
                job = Job(job_id=self._next_id,
                          video=str(p),
                          out_dir=out_dir or self.config.out_dir)
                self._next_id += 1
                if not Path(job.video).is_file():
                    job.state = JobState.SKIPPED
                    job.error = "文件不存在或不是文件"
                    self._emit_log("WARNING", f"跳过：{job.name}（{job.error}）")
                elif not job.out_dir:
                    job.state = JobState.SKIPPED
                    job.error = "未指定输出目录"
                    self._emit_log("WARNING", f"跳过：{job.name}（{job.error}）")
                self._jobs.append(job)
                added.append(job)
        for job in added:
            self._notify_job(job)
        self._notify_progress()
        return added

    @property
    def jobs(self) -> List[Job]:
        with self._jobs_lock:
            return list(self._jobs)

    def clear_finished(self) -> int:
        """从列表里移除已结束的任务（GUI 的"清除已完成"按钮）。"""
        with self._jobs_lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if not j.is_terminal]
            removed = before - len(self._jobs)
        if removed:
            self._notify_progress()
        return removed

    def cancel_job(self, job_id: int) -> bool:
        """取消单个任务（排队中被直接标记，运行中通过 cancel_flag 传播）。"""
        with self._cancel_lock:
            ev = self._cancel_one.get(job_id)
        if ev:
            ev.set()
        with self._jobs_lock:
            for j in self._jobs:
                if j.job_id == job_id and j.state == JobState.WAITING:
                    j.state = JobState.CANCELLED
                    j.stage = "已取消"
                    self._notify_job(j)
                    self._notify_progress()
                    return True
        return True

    def cancel_all(self) -> None:
        """取消所有任务：排队中的直接标记，运行中的尽快中止。"""
        self._cancel_all.set()
        with self._cancel_lock:
            for ev in self._cancel_one.values():
                ev.set()
        with self._jobs_lock:
            for j in self._jobs:
                if j.state == JobState.WAITING:
                    j.state = JobState.CANCELLED
                    j.stage = "已取消"
                    self._notify_job(j)
        self._notify_progress()

    def reset_cancel(self) -> None:
        """为新一轮批次清除取消标志。"""
        self._cancel_all.clear()
        with self._cancel_lock:
            self._cancel_one.clear()

    # -------------------------------------------------------- ASR 模型复用
    def resolve_device(self) -> tuple:
        """解析 device/compute_type，把 "auto" 落到实际值。

        规则：
          · device="cuda"  -> compute_type 默认 int8_float16
          · device="cpu"   -> compute_type 默认 int8
          · device="auto"  -> 探测 GPU，可用则 cuda 否则 cpu

        用浅检即可：真正缺库时会在 transcribe 阶段抛出带修复指引的异常，
        而浅检失败已足以说明不能选 cuda。
        """
        dev = (self.config.device or "auto").lower()
        ct = self.config.compute_type or ""
        if dev == "auto":
            try:
                from model.gpu_env import gpu_available
                dev = "cuda" if gpu_available() else "cpu"
            except Exception:
                dev = "cpu"
            self._emit_log("INFO", f"自动选择计算设备：{dev}")
        if not ct:
            ct = "int8_float16" if dev == "cuda" else "int8"
        return dev, ct

    def _get_engine(self):
        """懒加载并**全局复用**一个 ASR 引擎。

        这是本层最重要的性能优化：CPU 上 large-v3 单次加载实测 280+ 秒，
        若每个文件都重新加载，批量处理 10 个文件就要白等几十分钟。
        """
        with self._engine_lock:
            if self._engine is None:
                from model.asr import AsrEngine
                dev, ct = self.resolve_device()
                self._emit_log(
                    "INFO",
                    f"加载 ASR 模型 {self.config.model}"
                    f"（device={dev}, compute_type={ct}）…")
                self._engine = AsrEngine(
                    model=self.config.model,
                    device=dev,
                    compute_type=ct,
                    language=self.config.language,
                )
                self._engine.load()
                self._emit_log("INFO", "模型加载完成，后续任务复用该实例")
            return self._engine

    # -------------------------------------------------------- 单任务执行
    def _run_job(self, job: Job) -> Job:
        from model.pipeline import process, srt_for_aboboo
        from model.subtitle_fix import FixConfig

        cancel_ev = threading.Event()
        with self._cancel_lock:
            self._cancel_one[job.job_id] = cancel_ev

        def is_cancelled() -> bool:
            return self._cancel_all.is_set() or cancel_ev.is_set()

        job.state = JobState.RUNNING
        job.started_at = time.time()
        job.stage = "开始"
        job.progress = 0.0
        self._notify_job(job)
        self._emit_log("INFO", f"开始处理：{job.name}")

        def on_progress(p: float, msg: str) -> None:
            job.progress = max(0.0, min(1.0, p))
            job.stage = msg
            self._notify_job(job)
            self._notify_progress()

        try:
            # 输出已存在且不允许覆盖时直接跳过，避免误覆盖用户成果
            stem = Path(job.video).stem
            expected = Path(job.out_dir) / f"{stem}.srt"
            if expected.exists() and not self.config.overwrite:
                job.state = JobState.SKIPPED
                job.stage = "输出已存在"
                job.error = f"{expected.name} 已存在（未开启覆盖）"
                job.srt_path = str(expected)
                self._emit_log("WARNING", f"跳过：{job.name}（{job.error}）")
                return job

            cfg = FixConfig(
                max_line_chars=self.config.max_line_chars,
                max_lines=self.config.max_lines,
                min_gap_ms=self.config.min_gap_ms,
                split_enabled=self.config.split_enabled,
            )
            # 真正推理这一段串行执行（见 _infer_lock 的说明）。
            # 未持有锁时先报告状态，让用户知道在等什么。
            if self._infer_lock.locked():
                job.stage = "等待上一条转写完成"
                self._notify_job(job)
            with self._infer_lock:
                res = process(
                    video=job.video,
                    out_dir=job.out_dir,
                    engine=self._get_engine(),
                    fix_config=cfg,
                    language=self.config.language,
                    progress_cb=on_progress,
                    cancel_flag=is_cancelled,
                    make_mp4=self.config.make_mp4,
                    keep_audio=self.config.keep_audio,
                )
            job.srt_path = res.srt_path
            job.mp4_path = res.mp4_path
            job.cue_count = res.cue_count
            job.elapsed = res.elapsed
            job.warnings = list(res.problems)
            job.progress = 1.0
            job.state = JobState.DONE
            job.stage = "完成"

            if self.config.copy_srt_next_to_video:
                try:
                    srt_for_aboboo(res.srt_path, job.video)
                except Exception as e:
                    job.warnings.append(f"复制同名 SRT 失败：{e}")

            self._emit_log(
                "INFO",
                f"完成：{job.name} -> {Path(res.srt_path).name}"
                f"（{res.cue_count} 条字幕，{res.elapsed:.1f}s）")
            for w in job.warnings[:5]:
                self._emit_log("WARNING", f"{job.name}：{w}")

        except Exception as e:
            # 统一异常捕获：单个任务失败绝不能让整个批次崩掉
            cancelled = type(e).__name__ in ("PipelineCancelled", "AsrCancelled")
            if cancelled:
                job.state = JobState.CANCELLED
                job.stage = "已取消"
                self._emit_log("INFO", f"已取消：{job.name}")
            else:
                job.state = JobState.FAILED
                job.stage = "失败"
                job.error = f"{type(e).__name__}: {e}"
                self._emit_log("ERROR", f"失败：{job.name} — {job.error}")
                log.debug("任务失败详情\n%s", traceback.format_exc())
        finally:
            with self._cancel_lock:
                self._cancel_one.pop(job.job_id, None)
            self._notify_job(job)
            self._notify_progress()
        return job

    # -------------------------------------------------------- 批次执行
    def run(self, block: bool = True) -> Optional[List[Job]]:
        """执行队列。

        block=True  -> 阻塞直到全部结束，返回全部 Job
        block=False -> 立即返回 None，由回调报告进度
        """
        pending = [j for j in self.jobs if not j.is_terminal]
        if not pending:
            self._emit_log("INFO", "队列中没有待处理任务")
            if self.on_batch_done:
                self.on_batch_done(self.jobs)
            return self.jobs if block else None

        workers = max(1, min(int(self.config.workers), len(pending)))
        self._emit_log("INFO", f"批次开始：{len(pending)} 个任务，{workers} 个线程")
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="subjob")

        def _submit_all() -> List[Job]:
            futures = [self._pool.submit(self._run_job, j) for j in pending]
            return [f.result() for f in futures]

        if block:
            try:
                _submit_all()
            finally:
                self._finish_batch()
            return self.jobs

        # 非阻塞：在独立线程里等所有 future，完成后触发回调
        def _bg() -> None:
            try:
                _submit_all()
            finally:
                self._finish_batch()

        threading.Thread(target=_bg, name="batch-waiter", daemon=True).start()
        return None

    def _finish_batch(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        jobs = self.jobs
        done = sum(1 for j in jobs if j.state == JobState.DONE)
        failed = sum(1 for j in jobs if j.state == JobState.FAILED)
        cancelled = sum(1 for j in jobs if j.state == JobState.CANCELLED)
        self._emit_log(
            "INFO",
            f"批次结束：成功 {done}，失败 {failed}，取消 {cancelled}")
        self._notify_progress()
        if self.on_batch_done:
            try:
                self.on_batch_done(jobs)
            except Exception:
                pass

    # -------------------------------------------------------- 进度与通知
    def snapshot(self) -> BatchProgress:
        with self._jobs_lock:
            jobs = list(self._jobs)
        p = BatchProgress(total=len(jobs))
        total_w = 0.0
        for j in jobs:
            if j.state == JobState.DONE:
                p.done += 1
                total_w += 1.0
            elif j.state == JobState.FAILED:
                p.failed += 1
                total_w += 1.0
            elif j.state == JobState.CANCELLED:
                p.cancelled += 1
                total_w += 1.0
            elif j.state == JobState.SKIPPED:
                p.skipped += 1
                total_w += 1.0
            elif j.state == JobState.RUNNING:
                p.running += 1
                total_w += j.progress
                if not p.current:
                    p.current = j.name
            else:
                p.waiting += 1
        p.overall = (total_w / p.total) if p.total else 0.0
        return p

    def _notify_job(self, job: Job) -> None:
        if not self.on_job_update:
            return
        with self._notify_lock:
            try:
                self.on_job_update(job)
            except Exception:
                log.debug("on_job_update 回调异常", exc_info=True)

    def _notify_progress(self) -> None:
        if not self.on_progress:
            return
        # 先算快照再进锁，避免回调里再取 jobs 造成无谓的长持有
        snap = self.snapshot()
        with self._notify_lock:
            try:
                self.on_progress(snap)
            except Exception:
                log.debug("on_progress 回调异常", exc_info=True)

    # -------------------------------------------------------- 收尾
    def shutdown(self) -> None:
        """释放资源（GUI 关闭时调用）。"""
        self.cancel_all()
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
        self.detach_logging()
        with self._engine_lock:
            self._engine = None


# ---------------------------------------------------------------- 便捷函数

def run_batch(files: Sequence[str], out_dir: str,
              config: Optional[BatchConfig] = None,
              on_job: Optional[JobCb] = None,
              on_progress: Optional[ProgressCb] = None,
              on_log: Optional[LogCb] = None) -> List[Job]:
    """一次性跑完一批文件（阻塞）。适合脚本与自动化测试。"""
    cfg = config or BatchConfig()
    cfg.out_dir = out_dir or cfg.out_dir
    sched = BatchScheduler(cfg)
    sched.on_job_update = on_job
    sched.on_progress = on_progress
    sched.on_log = on_log
    sched.attach_logging()
    try:
        sched.add_files(files)
        return sched.run(block=True) or []
    finally:
        sched.shutdown()


def validate_batch_inputs(paths: Sequence[str]) -> List[str]:
    """导入前的输入校验，返回问题列表（空列表表示全部可用）。

    GUI 在"文件选择"后立即调用，可以在真正排队前就把问题反馈给用户。
    """
    problems: List[str] = []
    from model.waveform import probe_duration

    video_ext = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".flv", ".wmv",
                 ".ts", ".webm", ".mpg", ".mpeg", ".3gp"}
    seen = set()
    for p in paths:
        path = Path(p)
        if not path.exists():
            problems.append(f"{path.name}：文件不存在")
            continue
        if not path.is_file():
            problems.append(f"{path.name}：不是文件")
            continue
        if path.suffix.lower() not in video_ext:
            problems.append(f"{path.name}：扩展名 {path.suffix} 不是支持的视频格式")
            continue
        if path.stat().st_size == 0:
            problems.append(f"{path.name}：文件为空（0 字节）")
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            problems.append(f"{path.name}：重复导入")
            continue
        seen.add(key)
        dur = probe_duration(str(path))
        if dur is None:
            problems.append(f"{path.name}：无法读取时长（文件可能损坏）")
        elif dur <= 0:
            problems.append(f"{path.name}：时长为 0")
    return problems


def has_audio_stream(video: str) -> bool:
    """检查视频是否含音频轨。无音频的视频应提前拒绝，避免白跑一遍转写。

    GUI 在导入时调用；返回 False 时提示用户该文件无法生成字幕。
    """
    import json as _json
    import shutil
    import subprocess
    # 用 shutil.which 直接取 ffprobe，不要对 ffmpeg 路径做字符串替换 ——
    # "C:\\ffmpeg\\bin\\ffmpeg.exe".replace("ffmpeg","ffprobe") 会得到
    # "C:\\ffprobe\\bin\\ffprobe.exe" 这种不存在的路径（探针脚本里踩过）。
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True              # 没有 ffprobe 就不拦，交给后续流程报错
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, errors="replace")
        if r.returncode != 0:
            return True          # 探测失败就不拦，交给后续流程报错
        return bool(_json.loads(r.stdout or "{}").get("streams"))
    except Exception:
        return True
