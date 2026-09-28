# -*- coding: utf-8 -*-
"""
第二周任务验证脚本 - 配置模块 & 自定义异常体系

测试内容：
  1. AppConfig 配置模块功能测试
     - 单例模式验证
     - 默认配置读取
     - 配置读写（get/set）
     - 配置文件保存与加载
     - 配置校验
     - 重置默认值
  2. 自定义异常体系测试
     - 异常继承关系
     - 错误码
     - 用户友好提示
  3. 调度器集成测试
     - 配置参数生效（continue_on_error）
     - 自定义异常捕获

运行方式：
    python test_week2_config_exceptions.py
"""

import os
import sys
import tempfile
import json

from PyQt6.QtCore import QCoreApplication, QTimer

# 确保能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model.config import AppConfig
from model.exceptions import (
    AppBaseError,
    ErrorCode,
    ConfigError,
    ConfigSaveError,
    TaskError,
    TaskStoppedError,
    TaskAlreadyRunningError,
    FFmpegError,
    FFmpegNotFoundError,
    InvalidMediaError,
    ASRError,
    ModelNotFoundError,
    SubtitleError,
    InvalidSRTError,
)
from view.signals import GuiSignals, WorkerSignals
from controller.real_scheduler import RealScheduler


# ===========================================================================
# 测试 1：配置模块
# ===========================================================================
def test_config_singleton():
    """测试1.1：单例模式"""
    print("\n" + "=" * 60)
    print("测试1.1：AppConfig 单例模式")
    print("=" * 60)

    # 重置单例（测试用，正常使用不要这样做）
    AppConfig._instance = None
    AppConfig._initialized = False

    config1 = AppConfig()
    config2 = AppConfig()

    assert config1 is config2, "两次调用应该返回同一个实例"
    print("  ✓ 单例模式验证通过")


def test_config_defaults():
    """测试1.2：默认配置读取"""
    print("\n" + "=" * 60)
    print("测试1.2：默认配置读取")
    print("=" * 60)

    AppConfig._instance = None
    AppConfig._initialized = False
    config = AppConfig()

    # 检查几个关键配置项的默认值
    assert config.whisper_model_size == "base", "默认模型大小应为 base"
    assert config.continue_on_error is True, "默认应继续处理"
    assert config.default_output_dir == "", "默认输出目录应为空"
    assert config.get("ffmpeg.audio_sample_rate") == 16000, "采样率默认16000"
    assert config.get("subtitle.output_encoding") == "utf-8", "默认编码UTF-8"

    print(f"  模型大小：{config.whisper_model_size}")
    print(f"  失败继续：{config.continue_on_error}")
    print(f"  默认输出目录：'{config.default_output_dir}'")
    print("  ✓ 默认配置验证通过")


def test_config_get_set():
    """测试1.3：配置读写（get/set）"""
    print("\n" + "=" * 60)
    print("测试1.3：配置读写（get/set）")
    print("=" * 60)

    AppConfig._instance = None
    AppConfig._initialized = False
    config = AppConfig()

    # 嵌套路径读写
    config.set("whisper.model_size", "small")
    assert config.get("whisper.model_size") == "small"

    config.set("output.default_output_dir", "D:/output")
    assert config.get("output.default_output_dir") == "D:/output"

    # 不存在的键返回默认值
    assert config.get("nonexistent.key", "fallback") == "fallback"

    # dirty 标记
    assert config.is_dirty is True, "修改后 dirty 应为 True"

    print(f"  设置模型大小：{config.whisper_model_size}")
    print(f"  设置输出目录：{config.default_output_dir}")
    print(f"  dirty 标记：{config.is_dirty}")
    print("  ✓ 配置读写验证通过")


def test_config_save_load():
    """测试1.4：配置文件保存与加载"""
    print("\n" + "=" * 60)
    print("测试1.4：配置文件保存与加载")
    print("=" * 60)

    tmpdir = tempfile.mkdtemp()
    config_file = os.path.join(tmpdir, "test_config.json")

    # 创建第一个实例，修改后保存
    AppConfig._instance = None
    AppConfig._initialized = False
    config1 = AppConfig(config_file=config_file)
    config1.set("whisper.model_size", "medium")
    config1.set("output.default_output_dir", "/test/output")
    config1.set("ffmpeg.audio_sample_rate", 44100)

    assert config1.is_dirty is True
    config1.save()
    assert config1.is_dirty is False
    assert os.path.isfile(config_file), "配置文件应该已创建"

    # 验证文件内容
    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["whisper"]["model_size"] == "medium"
    assert data["output"]["default_output_dir"] == "/test/output"

    # 重置单例，创建新实例，应从文件加载
    AppConfig._instance = None
    AppConfig._initialized = False
    config2 = AppConfig(config_file=config_file)

    assert config2.whisper_model_size == "medium"
    assert config2.default_output_dir == "/test/output"
    assert config2.get("ffmpeg.audio_sample_rate") == 44100
    assert config2.is_dirty is False, "刚加载的配置 dirty 应为 False"

    print(f"  配置文件：{config_file}")
    print(f"  加载后模型大小：{config2.whisper_model_size}")
    print(f"  加载后输出目录：{config2.default_output_dir}")
    print("  ✓ 配置保存与加载验证通过")


def test_config_validate():
    """测试1.5：配置校验"""
    print("\n" + "=" * 60)
    print("测试1.5：配置校验")
    print("=" * 60)

    AppConfig._instance = None
    AppConfig._initialized = False
    config = AppConfig()

    # 默认配置应该能通过校验（不存在的路径配置为空就不校验）
    errors = config.validate()
    print(f"  默认配置校验错误数：{len(errors)}")
    # 默认配置可能没有错误，也可能有一些告警，这里只验证不崩溃

    # 设置一个不存在的 ffmpeg 路径，应该报错
    config.set("ffmpeg.ffmpeg_path", "Z:/nonexistent/ffmpeg.exe")
    errors = config.validate()
    assert len(errors) >= 1, "不存在的路径应该报错"
    print(f"  无效路径校验错误：{errors[0]}")

    # 设置无效的模型大小
    config.set("whisper.model_size", "invalid_model")
    errors = config.validate()
    assert any("模型大小" in e for e in errors), "无效模型大小应该报错"

    print("  ✓ 配置校验验证通过")


def test_config_reset():
    """测试1.6：重置默认配置"""
    print("\n" + "=" * 60)
    print("测试1.6：重置默认配置")
    print("=" * 60)

    AppConfig._instance = None
    AppConfig._initialized = False
    config = AppConfig()

    config.set("whisper.model_size", "large")
    assert config.whisper_model_size == "large"

    config.reset_defaults()
    assert config.whisper_model_size == "base", "重置后应恢复默认值"
    assert config.is_dirty is True, "重置后 dirty 应为 True"

    print(f"  重置后模型大小：{config.whisper_model_size}")
    print("  ✓ 重置默认配置验证通过")


# ===========================================================================
# 测试 2：自定义异常体系
# ===========================================================================
def test_exception_hierarchy():
    """测试2.1：异常继承关系"""
    print("\n" + "=" * 60)
    print("测试2.1：异常继承关系")
    print("=" * 60)

    # 所有异常都应继承自 AppBaseError
    assert issubclass(ConfigError, AppBaseError)
    assert issubclass(TaskError, AppBaseError)
    assert issubclass(FFmpegError, AppBaseError)
    assert issubclass(ASRError, AppBaseError)
    assert issubclass(SubtitleError, AppBaseError)

    # 子类继承关系
    assert issubclass(TaskStoppedError, TaskError)
    assert issubclass(TaskAlreadyRunningError, TaskError)
    assert issubclass(FFmpegNotFoundError, FFmpegError)
    assert issubclass(InvalidMediaError, FFmpegError)
    assert issubclass(ModelNotFoundError, ASRError)
    assert issubclass(InvalidSRTError, SubtitleError)

    print("  ✓ 异常继承关系验证通过")


def test_exception_attributes():
    """测试2.2：异常属性（错误码、用户提示）"""
    print("\n" + "=" * 60)
    print("测试2.2：异常属性（错误码、用户提示）")
    print("=" * 60)

    # FFmpegNotFoundError
    err = FFmpegNotFoundError("D:/ffmpeg.exe")
    assert err.error_code == ErrorCode.FFMPEG_NOT_FOUND
    assert "FFmpeg" in err.user_message
    print(f"  FFmpegNotFoundError: 错误码={err.error_code}, 提示='{err.user_message}'")

    # InvalidMediaError
    err = InvalidMediaError("D:/test.mp4", "文件损坏")
    assert err.error_code == ErrorCode.INVALID_MEDIA_FILE
    assert "test.mp4" in err.user_message
    assert "文件损坏" in err.details
    print(f"  InvalidMediaError: 错误码={err.error_code}, 提示='{err.user_message}'")

    # ModelNotFoundError
    err = ModelNotFoundError("large", "D:/models")
    assert err.error_code == ErrorCode.MODEL_NOT_FOUND
    assert "large" in err.user_message
    print(f"  ModelNotFoundError: 错误码={err.error_code}, 提示='{err.user_message}'")

    # TaskStoppedError
    err = TaskStoppedError()
    assert err.error_code == ErrorCode.TASK_STOPPED
    print(f"  TaskStoppedError: 错误码={err.error_code}, 提示='{err.user_message}'")

    print("  ✓ 异常属性验证通过")


def test_exception_str():
    """测试2.3：异常字符串表示"""
    print("\n" + "=" * 60)
    print("测试2.3：异常字符串表示")
    print("=" * 60)

    err = FFmpegNotFoundError()
    s = str(err)
    assert str(ErrorCode.FFMPEG_NOT_FOUND) in s
    assert len(s) > 0
    print(f"  str(FFmpegNotFoundError) = {s}")
    print("  ✓ 异常字符串表示验证通过")


# ===========================================================================
# 测试 3：调度器集成测试
# ===========================================================================
def test_scheduler_with_config():
    """测试3.1：调度器使用配置参数（continue_on_error）"""
    print("\n" + "=" * 60)
    print("测试3.1：调度器配置参数 - continue_on_error=False")
    print("=" * 60)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    # 创建自定义配置（失败即停止）
    AppConfig._instance = None
    AppConfig._initialized = False
    config = AppConfig()
    config.set("scheduler.continue_on_error", False)

    gui_signals = GuiSignals()
    worker_signals = WorkerSignals()
    scheduler = RealScheduler(gui_signals, worker_signals, config=config)

    # 收集结果
    finished = []
    errors = []
    all_done = [False]

    def on_finished(tid, mp4, srt):
        finished.append(tid)

    def on_error(tid, err):
        errors.append((tid, err))

    def on_all_done():
        all_done[0] = True
        app.quit()

    worker_signals.task_finished.connect(on_finished)
    worker_signals.task_error.connect(on_error)
    worker_signals.all_finished.connect(on_all_done)

    # 由于当前是模拟实现，不会主动抛错
    # 我们验证配置确实被传入了调度器
    assert scheduler.config is config
    assert scheduler.config.continue_on_error is False

    print(f"  continue_on_error = {scheduler.config.continue_on_error}")
    print("  ✓ 调度器配置集成验证通过")


def test_scheduler_default_output_dir():
    """测试3.2：默认输出目录配置生效"""
    print("\n" + "=" * 60)
    print("测试3.2：默认输出目录配置生效")
    print("=" * 60)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    tmpdir = tempfile.mkdtemp()
    default_out = os.path.join(tmpdir, "default_output")
    os.makedirs(default_out, exist_ok=True)

    AppConfig._instance = None
    AppConfig._initialized = False
    config = AppConfig()
    config.set("output.default_output_dir", default_out)

    gui_signals = GuiSignals()
    worker_signals = WorkerSignals()
    scheduler = RealScheduler(gui_signals, worker_signals, config=config)

    result = {}

    def on_finished(tid, mp4, srt):
        result["mp4"] = mp4
        result["srt"] = srt
        app.quit()

    worker_signals.task_finished.connect(on_finished)

    # 创建一个假MP4，输出目录为空（应使用默认输出目录）
    fake_mp4 = os.path.join(tmpdir, "test.mp4")
    with open(fake_mp4, "w") as f:
        f.write("fake")

    gui_signals.start_requested.emit([(fake_mp4, "")])

    # 超时保护
    QTimer.singleShot(10000, lambda: app.quit())
    app.exec()

    # 验证输出路径在默认输出目录下
    assert "mp4" in result, "任务应该完成"
    assert result["mp4"].startswith(default_out), f"MP4应输出到默认目录：{default_out}，实际：{result['mp4']}"
    assert result["srt"].startswith(default_out), f"SRT应输出到默认目录：{default_out}"

    print(f"  默认输出目录：{default_out}")
    print(f"  实际输出MP4：{result['mp4']}")
    print("  ✓ 默认输出目录配置生效验证通过")


# ===========================================================================
# 主函数
# ===========================================================================
def main():
    print("第二周任务 - 配置模块 & 自定义异常体系 验证")
    print("=" * 60)

    all_tests = [
        # 配置模块
        test_config_singleton,
        test_config_defaults,
        test_config_get_set,
        test_config_save_load,
        test_config_validate,
        test_config_reset,
        # 异常体系
        test_exception_hierarchy,
        test_exception_attributes,
        test_exception_str,
        # 集成测试
        test_scheduler_with_config,
        test_scheduler_default_output_dir,
    ]

    passed = 0
    failed = 0

    for test in all_tests:
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
