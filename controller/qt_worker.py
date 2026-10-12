# -*- coding: utf-8 -*-
"""
Qt 适配层（第二板块）—— 把调度核心的回调桥接成 Qt 信号

为什么单独一层
--------------
``controller/scheduler.py`` 是不依赖 Qt 的纯标准库实现，好处是：
可打包（少一层耦合）、可测试（无 GUI 环境）、可替换（换框架不改核心）。

本模块只做一件事：**把工作线程里的回调转成 Qt 信号**。
Qt 的跨线程信号默认是 QueuedConnection，槽函数会在**接收者所在线程**（即
GUI 线程）执行，因此界面更新天然安全，不会出现"在工作线程里改控件"的崩溃。

依赖是可选的
------------
未安装 PySide6 时导入本模块不会抛错，只是 ``available()`` 返回 False。
这样测试脚本和 CI 无需为了跑调度测试而安装 Qt。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .scheduler import (BatchConfig, BatchProgress, BatchScheduler, Job,
                        JobState)

try:                                    # pragma: no cover - 环境相关
    from PySide6.QtCore import QObject, Signal

    _QT_BACKEND = "PySide6"
except ImportError:                     # pragma: no cover
    try:
        from PyQt6.QtCore import QObject, Signal  # type: ignore

        _QT_BACKEND = "PyQt6"
    except ImportError:
        QObject = object                # type: ignore
        Signal = None                   # type: ignore
        _QT_BACKEND = None


def available() -> bool:
    """当前环境是否具备 Qt 绑定。"""
    return _QT_BACKEND is not None


def backend_name() -> Optional[str]:
    return _QT_BACKEND


if _QT_BACKEND is not None:

    class SchedulerWorker(QObject):
        """把 ``BatchScheduler`` 包装成信号源，供 GUI 直接连接。

        信号与槽的对应关系：

            jobUpdated(Job)           -> 更新任务列表对应行
            progressChanged(object)   -> 更新总进度条（BatchProgress）
            logMessage(str, str)      -> 追加日志（级别, 文本）
            batchFinished(list)       -> 批次结束，恢复按钮状态
            failed(str)               -> 批次级异常（如模型加载失败）

        用法::

            self.worker = SchedulerWorker(config)
            self.worker.jobUpdated.connect(self.on_job_updated)
            self.worker.progressChanged.connect(self.on_progress)
            self.worker.logMessage.connect(self.append_log)
            self.worker.add_files(files)
            self.worker.start()          # 非阻塞，立即返回
        """

        jobUpdated = Signal(object)          # Job
        progressChanged = Signal(object)     # BatchProgress
        logMessage = Signal(str, str)        # (level, message)
        batchFinished = Signal(list)         # List[Job]
        failed = Signal(str)                 # 批次级错误文本

        def __init__(self, config: Optional[BatchConfig] = None,
                     parent: Optional[QObject] = None):
            super().__init__(parent)
            self.scheduler = BatchScheduler(config)
            # 回调在工作线程被调用；发信号本身是线程安全的，
            # 而槽会在 GUI 线程执行（QueuedConnection）。
            self.scheduler.on_job_update = self.jobUpdated.emit
            self.scheduler.on_progress = self.progressChanged.emit
            self.scheduler.on_log = self.logMessage.emit
            self.scheduler.on_batch_done = self._on_batch_done
            self.scheduler.attach_logging()

        # ---------------------------------------------------- 转发
        def _on_batch_done(self, jobs: List[Job]) -> None:
            self.batchFinished.emit(list(jobs))

        # ---------------------------------------------------- 对外 API
        @property
        def config(self) -> BatchConfig:
            return self.scheduler.config

        def add_files(self, paths, out_dir: Optional[str] = None) -> List[Job]:
            return self.scheduler.add_files(paths, out_dir)

        def start(self) -> None:
            """非阻塞启动批次。立即返回，进度通过信号上报。"""
            try:
                self.scheduler.reset_cancel()
                self.scheduler.run(block=False)
            except Exception as e:
                # 批次级异常（例如配置错误）单独上报，不影响后续操作
                self.failed.emit(f"{type(e).__name__}: {e}")

        def cancel_job(self, job_id: int) -> None:
            self.scheduler.cancel_job(job_id)

        def cancel_all(self) -> None:
            self.scheduler.cancel_all()

        def clear_finished(self) -> int:
            return self.scheduler.clear_finished()

        def snapshot(self) -> BatchProgress:
            return self.scheduler.snapshot()

        def jobs(self) -> List[Job]:
            return self.scheduler.jobs

        def shutdown(self) -> None:
            self.scheduler.shutdown()


else:                                   # pragma: no cover

    class SchedulerWorker:               # type: ignore
        """未安装 Qt 时的占位实现：调用即报错，并给出安装指引。"""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "未安装 Qt 绑定，无法使用 SchedulerWorker。\n"
                "请执行：\n"
                "  venv\\Scripts\\python.exe -m pip install PySide6 "
                "-i https://mirrors.aliyun.com/pypi/simple/\n"
                "（纯调度功能可以直接使用 controller.scheduler.BatchScheduler，"
                "它不依赖 Qt。）"
            )


@dataclass
class JobRow:
    """给 QAbstractTableModel 用的行数据（GUI 表格列定义）。

    放在这里而不是 view 层，是为了让"任务列表显示哪些字段"这件事
    由调度层定义一次，避免 GUI 与调度对字段名产生分歧。
    """

    job_id: int
    name: str
    state_text: str
    progress_text: str
    detail: str

    @classmethod
    def from_job(cls, job: Job, state_text_map=None) -> "JobRow":
        from .scheduler import STATE_TEXT as DEFAULT_MAP
        mp = state_text_map or DEFAULT_MAP
        state = mp.get(job.state, str(job.state))
        if job.state == JobState.RUNNING:
            prog = f"{job.progress * 100:.0f}%"
            detail = job.stage
        elif job.state == JobState.DONE:
            prog = "100%"
            detail = f"{job.cue_count} 条字幕"
        elif job.state in (JobState.FAILED, JobState.SKIPPED):
            prog = "-"
            detail = job.error or ""
        elif job.state == JobState.CANCELLED:
            prog = "-"
            detail = "已取消"
        else:
            prog = "0%"
            detail = job.stage or "排队中"
        return cls(job_id=job.job_id, name=job.name, state_text=state,
                   progress_text=prog, detail=detail)

    COLUMNS = ("文件名", "状态", "进度", "说明")

    def as_row(self) -> List[str]:
        return [self.name, self.state_text, self.progress_text, self.detail]
