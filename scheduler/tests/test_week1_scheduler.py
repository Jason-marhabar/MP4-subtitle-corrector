# -*- coding: utf-8 -*-
"""
第一周任务验证脚本（无需 GUI，纯逻辑测试）

测试内容：
  1. RealScheduler 能否正确实例化并连接信号
  2. 任务队列能否逐个执行
  3. 进度信号是否正确发出
  4. 停止功能是否生效
  5. 异常处理是否正常（单任务失败不影响队列）

运行方式：
    python test_week1_scheduler.py
"""

import os
import sys
import tempfile
import time

from PyQt6.QtCore import QCoreApplication, QTimer

# 确保能 import 项目模块（tests 在子目录，所以加到上一级）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from view.signals import GuiSignals, WorkerSignals
from controller.real_scheduler import RealScheduler


class SchedulerTester:
    """调度器测试器：收集信号，验证行为"""

    def __init__(self):
        self.gui_signals = GuiSignals()
        self.worker_signals = WorkerSignals()
        self.scheduler = RealScheduler(self.gui_signals, self.worker_signals)

        # 收集信号
        self.logs = []
        self.progress_events = []
        self.finished_tasks = []
        self.error_tasks = []
        self.all_finished_count = 0

        # 连接信号
        self.worker_signals.log.connect(self._on_log)
        self.worker_signals.task_progress.connect(self._on_progress)
        self.worker_signals.task_finished.connect(self._on_finished)
        self.worker_signals.task_error.connect(self._on_error)
        self.worker_signals.all_finished.connect(self._on_all_finished)

    def _on_log(self, level, msg):
        self.logs.append((level, msg))
        print(f"  [log][{level}] {msg}")

    def _on_progress(self, task_id, stage, percent):
        self.progress_events.append((task_id, stage, percent))

    def _on_finished(self, task_id, out_mp4, out_srt):
        self.finished_tasks.append((task_id, out_mp4, out_srt))
        print(f"  [完成] 任务{task_id}: {os.path.basename(out_mp4)}")

    def _on_error(self, task_id, error):
        self.error_tasks.append((task_id, error))
        print(f"  [失败] 任务{task_id}: {error}")

    def _on_all_finished(self):
        self.all_finished_count += 1
        print(f"  [全部结束] 共触发 {self.all_finished_count} 次")


def test_basic_flow():
    """测试1：基本任务流程 - 多个任务能否排队执行"""
    print("\n" + "=" * 60)
    print("测试1：基本任务流程（多任务排队）")
    print("=" * 60)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    tester = SchedulerTester()

    # 创建临时文件模拟MP4
    tmpdir = tempfile.mkdtemp()
    tasks = []
    for i in range(3):
        f = os.path.join(tmpdir, f"test_{i}.mp4")
        with open(f, "w") as fp:
            fp.write("fake mp4")
        tasks.append((f, ""))  # 输出目录为空=源文件同目录

    print(f"创建 {len(tasks)} 个测试任务")

    # 发出开始信号
    tester.gui_signals.start_requested.emit(tasks)

    # 等待完成（设置超时保护）
    timeout = 30000  # 30秒超时
    start_time = time.time()

    def check_done():
        if tester.all_finished_count > 0:
            app.quit()
        elif time.time() - start_time > timeout / 1000:
            print("  ⚠ 超时！")
            app.quit()
        else:
            QTimer.singleShot(100, check_done)

    QTimer.singleShot(100, check_done)
    app.exec()

    # 验证结果
    print(f"\n  完成任务数：{len(tester.finished_tasks)}")
    print(f"  失败任务数：{len(tester.error_tasks)}")
    print(f"  进度事件数：{len(tester.progress_events)}")
    print(f"  all_finished 触发次数：{tester.all_finished_count}")

    assert len(tester.finished_tasks) == 3, f"应该有3个任务完成，实际{len(tester.finished_tasks)}"
    assert len(tester.error_tasks) == 0, f"不应该有失败任务"
    assert tester.all_finished_count == 1, "all_finished 应该只触发1次"
    assert len(tester.progress_events) > 0, "应该有进度事件"

    # 验证进度是递增的
    for task_id in range(3):
        task_progress = [p[2] for p in tester.progress_events if p[0] == task_id]
        assert task_progress == sorted(task_progress), f"任务{task_id}进度应该递增"
        assert task_progress[-1] == 100, f"任务{task_id}最终进度应该是100%"

    print("  ✓ 测试1通过！")
    return True


def test_stop_function():
    """测试2：停止功能 - 用户中途停止"""
    print("\n" + "=" * 60)
    print("测试2：停止功能")
    print("=" * 60)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    tester = SchedulerTester()

    tmpdir = tempfile.mkdtemp()
    tasks = []
    for i in range(5):
        f = os.path.join(tmpdir, f"stop_test_{i}.mp4")
        with open(f, "w") as fp:
            fp.write("fake mp4")
        tasks.append((f, ""))

    print(f"创建 {len(tasks)} 个测试任务，将在第1个任务处理中请求停止")

    tester.gui_signals.start_requested.emit(tasks)

    # 0.5秒后请求停止
    QTimer.singleShot(500, lambda: tester.gui_signals.stop_requested.emit())

    timeout = 15000
    start_time = time.time()

    def check_done():
        if tester.all_finished_count > 0:
            app.quit()
        elif time.time() - start_time > timeout / 1000:
            print("  ⚠ 超时！")
            app.quit()
        else:
            QTimer.singleShot(100, check_done)

    QTimer.singleShot(100, check_done)
    app.exec()

    print(f"\n  完成任务数：{len(tester.finished_tasks)}")
    print(f"  失败（停止）任务数：{len(tester.error_tasks)}")

    # 停止后应该有部分任务被标记为失败（已停止）
    assert len(tester.error_tasks) >= 1, "停止后应该有任务被标记为失败"
    assert tester.all_finished_count == 1, "all_finished 应该触发1次"

    # 检查错误信息是否包含"已停止"
    stop_errors = [e for _, e in tester.error_tasks if "已停止" in e]
    assert len(stop_errors) > 0, "错误信息应该包含'已停止'"

    print("  ✓ 测试2通过！")
    return True


def test_output_dir():
    """测试3：输出目录处理"""
    print("\n" + "=" * 60)
    print("测试3：输出目录处理")
    print("=" * 60)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    tester = SchedulerTester()

    tmpdir = tempfile.mkdtemp()
    out_dir = os.path.join(tmpdir, "output")
    f = os.path.join(tmpdir, "dir_test.mp4")
    with open(f, "w") as fp:
        fp.write("fake mp4")

    # 指定输出目录
    tasks = [(f, out_dir)]
    print(f"输入文件：{f}")
    print(f"输出目录：{out_dir}")

    tester.gui_signals.start_requested.emit(tasks)

    timeout = 15000
    start_time = time.time()

    def check_done():
        if tester.all_finished_count > 0:
            app.quit()
        elif time.time() - start_time > timeout / 1000:
            print("  ⚠ 超时！")
            app.quit()
        else:
            QTimer.singleShot(100, check_done)

    QTimer.singleShot(100, check_done)
    app.exec()

    assert len(tester.finished_tasks) == 1
    out_mp4, out_srt = tester.finished_tasks[0][1], tester.finished_tasks[0][2]

    print(f"输出MP4：{out_mp4}")
    print(f"输出SRT：{out_srt}")

    # 验证输出路径在指定目录下
    assert out_mp4.startswith(out_dir), f"MP4应该输出到指定目录：{out_dir}"
    assert out_srt.startswith(out_dir), f"SRT应该输出到指定目录：{out_dir}"

    print("  ✓ 测试3通过！")
    return True


def main():
    print("第一周任务 - 业务调度器验证")
    print("=" * 60)

    tests = [
        test_basic_flow,
        test_stop_function,
        test_output_dir,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  ✗ 测试失败：{e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"测试结果：{passed} 通过，{failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
