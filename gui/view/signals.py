# -*- coding: utf-8 -*-
"""
GUI 与业务调度层之间的信号/槽接口契约（任务1 与 任务2 对接的核心）

本文件是全组联调的"接口协议"，双方必须严格按此处的信号名、参数类型
开发，如需改动必须双方同步确认。

通信规则：
  1. GUI -> 业务层：GUI 只通过 GuiSignals 发信号，绝不直接调用底层模块。
  2. 业务层 -> GUI：业务层只通过 WorkerSignals 发信号，GUI 收到后在主线程更新控件。
  3. 业务层绝不在子线程直接操作 GUI 控件（Qt 线程安全铁律）。

参数约定：
  - task_id / 任务索引：与任务列表中该任务的顺序一致，从 0 开始。
  - 输出目录为空字符串 "" 时，表示"默认使用源文件同目录"，由业务层自行解析。
"""

from PyQt6.QtCore import QObject, pyqtSignal


class GuiSignals(QObject):
    """GUI 发出 -> 业务调度层接收"""

    # 用户点击"开始"，参数为任务列表 [(输入文件路径, 输出目录), ...]
    start_requested = pyqtSignal(list)

    # 用户点击"暂停"
    pause_requested = pyqtSignal()

    # 用户点击"继续"
    resume_requested = pyqtSignal()

    # 用户点击"停止"
    stop_requested = pyqtSignal()

    # 用户点击"清空列表"
    clear_requested = pyqtSignal()


class WorkerSignals(QObject):
    """业务调度层发出 -> GUI 接收"""

    # 日志：级别(info/warning/error), 内容
    log = pyqtSignal(str, str)

    # 单个任务进度：任务索引, 当前阶段文字(显示在状态列，如"提取音频"), 百分比(0-100)
    task_progress = pyqtSignal(int, str, int)

    # 单个任务完成：任务索引, 输出 mp4 路径, 输出 srt 路径
    task_finished = pyqtSignal(int, str, str)

    # 单个任务失败：任务索引, 错误信息
    task_error = pyqtSignal(int, str)

    # 全部任务处理完毕（无论成功/失败/停止，最终都会发出）
    all_finished = pyqtSignal()
