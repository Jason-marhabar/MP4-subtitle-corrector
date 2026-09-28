# -*- coding: utf-8 -*-
"""主窗口：MP4 字幕矫正工具（任务1 - GUI 界面）

界面分为 5 个区域：
  1. 文件选择区：选择 MP4 文件、选择输出目录（全局设置）
  2. 任务列表  ：QTableWidget，逐行显示每个文件的状态/进度
  3. 总进度条  ：整体处理进度
  4. 日志窗口  ：实时输出处理日志
  5. 按钮区    ：开始 / 暂停 / 停止 / 清空

重要约定（与任务2对接）：
  - 本窗口不直接调用 FFmpeg / Whisper 等底层模块；
  - 用户操作通过 GuiSignals 发出信号，业务调度层接收；
  - 业务调度层通过 WorkerSignals 回传进度/日志/结果，本窗口在主线程更新控件。
"""

import os
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from view.signals import GuiSignals, WorkerSignals

# 任务状态常量（用于内部逻辑判断，与业务层约定）
STATUS_PENDING = "排队"
STATUS_RUNNING = "处理中"
STATUS_DONE = "完成"
STATUS_ERROR = "失败"


class MainWindow(QMainWindow):
    """程序主窗口"""

    COLUMNS = ["文件名", "状态", "进度", "输出文件"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MP4 字幕矫正工具")
        self.resize(960, 680)

        # 信号对象：gui_signals 发给业务层，worker_signals 接收业务层
        self.gui_signals = GuiSignals()
        self.worker_signals = WorkerSignals()

        # 任务数据：列表中每个 dict 对应任务列表的一行
        # {path, status, stage, percent, output_mp4, output_srt}
        self._tasks = []

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 1. 文件选择区
        root.addWidget(self._build_file_area())

        # 2. 任务列表
        root.addWidget(QLabel("任务列表："))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 110)
        root.addWidget(self.table, stretch=1)

        # 3. 总进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("总进度：%p%")
        root.addWidget(self.progress_bar)

        # 4. 日志窗口
        root.addWidget(QLabel("日志："))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)  # 防止日志过多占用内存
        root.addWidget(self.log_view, stretch=1)

        # 5. 按钮区
        root.addWidget(self._build_button_area())

    def _build_file_area(self):
        """文件选择区：输入文件 + 输出目录"""
        area = QHBoxLayout()

        area.addWidget(QLabel("输入文件："))
        self.btn_add_files = QPushButton("选择 MP4 文件")
        self.btn_add_files.clicked.connect(self._on_choose_files)
        area.addWidget(self.btn_add_files)
        area.addStretch(1)

        area.addWidget(QLabel("输出目录："))
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("未选择（默认与源文件同目录）")
        self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setMinimumWidth(260)
        area.addWidget(self.output_dir_edit)

        self.btn_output_dir = QPushButton("选择目录")
        self.btn_output_dir.clicked.connect(self._on_choose_output_dir)
        area.addWidget(self.btn_output_dir)

        return self._wrap(area)

    def _build_button_area(self):
        area = QHBoxLayout()
        area.addStretch(1)

        self.btn_start = QPushButton("开始")
        self.btn_start.setMinimumWidth(90)
        area.addWidget(self.btn_start)

        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setMinimumWidth(90)
        self.btn_pause.setEnabled(False)
        area.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setMinimumWidth(90)
        self.btn_stop.setEnabled(False)
        area.addWidget(self.btn_stop)

        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.setMinimumWidth(90)
        area.addWidget(self.btn_clear)

        return self._wrap(area)

    def _wrap(self, layout) -> QWidget:
        """把一个 layout 装进 QWidget 返回，方便 addWidget"""
        w = QWidget()
        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connect_signals(self):
        # 按钮 -> 本窗口内部处理
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_clear.clicked.connect(self._on_clear_clicked)

        # 业务层 -> 更新界面
        self.worker_signals.log.connect(self._on_log)
        self.worker_signals.task_progress.connect(self._on_task_progress)
        self.worker_signals.task_finished.connect(self._on_task_finished)
        self.worker_signals.task_error.connect(self._on_task_error)
        self.worker_signals.all_finished.connect(self._on_all_finished)

    # ------------------------------------------------------------------
    # 用户操作 -> 发 gui_signals
    # ------------------------------------------------------------------
    def _on_choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 MP4 文件", "", "MP4 视频 (*.mp4);;所有文件 (*.*)"
        )
        for path in files:
            self._add_task(path)

    def _on_choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.output_dir_edit.setText(d)
            self._log("info", f"输出目录已设置为：{d}")

    def _on_start_clicked(self):
        pending = [t for t in self._tasks if t["status"] == STATUS_PENDING]
        if not pending:
            QMessageBox.warning(self, "提示", "没有待处理的任务，请先选择 MP4 文件。")
            return

        # 全局输出目录（为空则由业务层使用源文件同目录）
        out_dir = self.output_dir_edit.text().strip()
        payload = [(t["path"], out_dir) for t in pending]

        for t in pending:
            t["status"] = STATUS_RUNNING
            t["stage"] = ""
            t["percent"] = 0
            self._refresh_task_row(t)

        self._set_running(True)
        self._log("info", f"开始处理 {len(payload)} 个任务")
        self.gui_signals.start_requested.emit(payload)

    def _on_pause_clicked(self):
        if self.btn_pause.text() == "暂停":
            self.btn_pause.setText("继续")
            self.gui_signals.pause_requested.emit()
            self._log("warning", "已请求暂停")
        else:
            self.btn_pause.setText("暂停")
            self.gui_signals.resume_requested.emit()
            self._log("info", "已请求继续")

    def _on_stop_clicked(self):
        self.gui_signals.stop_requested.emit()
        self._log("warning", "已请求停止")

    def _on_clear_clicked(self):
        self._tasks.clear()
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        self._set_running(False)
        self.gui_signals.clear_requested.emit()
        self._log("info", "任务列表已清空")

    # ------------------------------------------------------------------
    # 业务层信号 -> 更新界面
    # ------------------------------------------------------------------
    def _on_log(self, level, msg):
        self._log(level, msg)

    def _on_task_progress(self, task_id, stage, percent):
        if not (0 <= task_id < len(self._tasks)):
            return
        t = self._tasks[task_id]
        t["status"] = STATUS_RUNNING
        t["stage"] = stage
        t["percent"] = percent
        self._refresh_task_row(t)
        self._refresh_overall_progress()

    def _on_task_finished(self, task_id, out_mp4, out_srt):
        if not (0 <= task_id < len(self._tasks)):
            return
        t = self._tasks[task_id]
        t["status"] = STATUS_DONE
        t["percent"] = 100
        t["output_mp4"] = out_mp4
        t["output_srt"] = out_srt
        self._refresh_task_row(t)
        self._refresh_overall_progress()
        self._log("info", f"任务完成：{os.path.basename(t['path'])}")

    def _on_task_error(self, task_id, error):
        if not (0 <= task_id < len(self._tasks)):
            return
        t = self._tasks[task_id]
        t["status"] = STATUS_ERROR
        self._refresh_task_row(t)
        self._refresh_overall_progress()
        self._log("error", f"任务失败：{os.path.basename(t['path'])} - {error}")

    def _on_all_finished(self):
        self._set_running(False)
        self._log("info", "全部任务处理完毕")

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    def _add_task(self, path):
        # 校验文件是否存在
        if not os.path.isfile(path):
            QMessageBox.warning(self, "错误", f"文件不存在：{path}")
            return
        # 校验扩展名
        if not path.lower().endswith(".mp4"):
            QMessageBox.warning(self, "格式错误", f"不是 MP4 文件：{path}")
            return
        # 去重
        for t in self._tasks:
            if t["path"] == path:
                self._log("warning", f"文件已在任务列表中，跳过：{path}")
                return

        task = {
            "path": path,
            "status": STATUS_PENDING,
            "stage": "",
            "percent": 0,
            "output_mp4": "",
            "output_srt": "",
        }
        self._tasks.append(task)
        self._refresh_task_row(task)
        self._log("info", f"已添加任务：{path}")

    def _refresh_task_row(self, task):
        idx = self._tasks.index(task)
        if self.table.rowCount() <= idx:
            self.table.insertRow(idx)

        name = os.path.basename(task["path"])

        # 状态列：处理中显示当前阶段，否则显示状态
        if task["status"] == STATUS_RUNNING and task["stage"]:
            status_text = task["stage"]
        else:
            status_text = task["status"]

        out_file = os.path.basename(task["output_srt"]) if task["output_srt"] else "—"
        values = [name, status_text, f"{task['percent']}%", out_file]

        for col, val in enumerate(values):
            item = QTableWidgetItem(str(val))
            if col == 2:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(idx, col, item)

        # 状态着色：完成=绿，失败=红
        status_item = self.table.item(idx, 1)
        if task["status"] == STATUS_DONE:
            status_item.setForeground(QBrush(QColor("#2e7d32")))
        elif task["status"] == STATUS_ERROR:
            status_item.setForeground(QBrush(QColor("#c62828")))

    def _refresh_overall_progress(self):
        if not self._tasks:
            self.progress_bar.setValue(0)
            return
        done = sum(
            1 for t in self._tasks if t["status"] in (STATUS_DONE, STATUS_ERROR)
        )
        self.progress_bar.setValue(int(done / len(self._tasks) * 100))

    def _set_running(self, running):
        self.btn_start.setEnabled(not running)
        self.btn_add_files.setEnabled(not running)
        self.btn_output_dir.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)

    def _log(self, level, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}][{level}] {msg}")
