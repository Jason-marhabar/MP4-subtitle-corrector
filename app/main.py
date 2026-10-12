# -*- coding: utf-8 -*-
"""MP4字幕矫正工具 - GUI 主程序（整合 4号 pipeline）"""
import os
import sys
import datetime
from pathlib import Path

# 确保能 import model / controller（打包后也兼容）
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

# 国内模型镜像（必须在 import faster_whisper 之前设好）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HOME", str(Path.home() / ".mp4_subtitle_models"))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QListWidget,
    QProgressBar, QTextEdit, QMessageBox, QGroupBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from model.pipeline import process, PipelineCancelled


def _now():
    return datetime.datetime.now().strftime("%H:%M:%S")


class Worker(QThread):
    progress = pyqtSignal(float, str)
    log = pyqtSignal(str)
    one_done = pyqtSignal(str, bool, str)
    all_done = pyqtSignal()

    def __init__(self, files, out_dir):
        super().__init__()
        self.files = files
        self.out_dir = out_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        for f in self.files:
            if self._cancel:
                self.log.emit("已取消")
                break
            name = Path(f).name
            self.log.emit(f"开始处理: {name}")
            try:
                r = process(
                    f, self.out_dir,
                    progress_cb=lambda p, msg: self.progress.emit(p, msg),
                    cancel_flag=lambda: self._cancel,
                    make_mp4=True,
                )
                self.log.emit(f"完成: {r.summary()}")
                self.one_done.emit(name, r.ok, r.summary())
            except PipelineCancelled:
                self.log.emit(f"已取消: {name}")
                self.one_done.emit(name, False, "已取消")
            except Exception as e:
                self.log.emit(f"出错: {e}")
                self.one_done.emit(name, False, str(e))
        self.all_done.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MP4字幕矫正工具")
        self.resize(960, 720)
        self.files = []
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        gb = QGroupBox("输入")
        v = QVBoxLayout()
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("MP4文件:"))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        r1.addWidget(self.file_edit)
        b = QPushButton("选择MP4")
        b.clicked.connect(self._pick_files)
        r1.addWidget(b)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("输出目录:"))
        self.out_edit = QLineEdit()
        r2.addWidget(self.out_edit)
        b2 = QPushButton("选择目录")
        b2.clicked.connect(self._pick_out)
        r2.addWidget(b2)
        v.addLayout(r2)
        gb.setLayout(v)
        layout.addWidget(gb)

        layout.addWidget(QLabel("任务列表:"))
        self.task_list = QListWidget()
        layout.addWidget(self.task_list, 2)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = QLabel("就绪")
        layout.addWidget(self.status)

        layout.addWidget(QLabel("日志:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 3)

        rb = QHBoxLayout()
        self.btn_start = QPushButton("开始")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        rb.addWidget(self.btn_start)
        rb.addWidget(self.btn_stop)
        layout.addLayout(rb)

        self.setCentralWidget(central)

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择MP4", "", "视频 (*.mp4)")
        if files:
            self.files = files
            self.file_edit.setText("; ".join(Path(f).name for f in files))
            self.task_list.clear()
            for f in files:
                self.task_list.addItem(f"{Path(f).name} - 等待")

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.out_edit.setText(d)

    def _start(self):
        if not self.files:
            QMessageBox.warning(self, "提示", "请先选择MP4文件")
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "提示", "请先选择输出目录")
            return
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        self.worker = Worker(self.files, out)
        self.worker.progress.connect(lambda p, m: (self.progress.setValue(int(p*100)), self.status.setText(m)))
        self.worker.log.connect(lambda m: self.log.append(f"[{_now()}] {m}"))
        self.worker.one_done.connect(self._one_done)
        self.worker.all_done.connect(self._all_done)
        self.worker.start()

    def _one_done(self, name, ok, msg):
        for i in range(self.task_list.count()):
            it = self.task_list.item(i)
            if name in it.text():
                it.setText(f"{name} - {'完成' if ok else '失败'}: {msg}")
                it.setForeground(Qt.GlobalColor.darkGreen if ok else Qt.GlobalColor.red)

    def _all_done(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status.setText("全部完成")
        QMessageBox.information(self, "完成", f"全部处理完毕！\n输出目录: {self.out_edit.text()}")

    def _stop(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText("正在停止...")


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
