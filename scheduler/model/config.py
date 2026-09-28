# -*- coding: utf-8 -*-
"""
全局配置管理模块（任务2 - 第2周）

职责：
  1. 统一管理所有配置项（FFmpeg路径、Whisper模型、输出设置等）
  2. 支持从 JSON 配置文件加载和保存
  3. 单例模式，全局只有一份配置实例
  4. 提供配置校验和默认值

使用方式：
    from model.config import AppConfig

    config = AppConfig()          # 获取单例
    config.ffmpeg_path = "..."    # 修改配置
    config.save()                 # 保存到文件
    config.load()                 # 从文件重新加载

说明：
  当前阶段（第2周）配置项为初版，待与3号、4号、6号确认后补充完善。
  不确定的配置项已标注 TODO，联调时补充。
"""

import os
import json
import copy
from typing import Any, Dict, Optional

from model.exceptions import ConfigError, ConfigSaveError, ConfigLoadError


# ---------------------------------------------------------------------------
# 默认配置项
# ---------------------------------------------------------------------------
# 所有配置项及其默认值都集中在这里定义，方便维护
# TODO: 待与3号、4号、6号确认后，补充完整配置项
DEFAULT_CONFIG: Dict[str, Any] = {
    # ===== FFmpeg 相关（需与3号确认） =====
    "ffmpeg": {
        # ffmpeg 可执行文件路径。为空字符串表示：
        #   1. 先尝试在程序目录下找 bin/ffmpeg.exe
        #   2. 再尝试从系统 PATH 中找
        "ffmpeg_path": "",
        # ffprobe 可执行文件路径（用于获取视频信息）
        "ffprobe_path": "",
        # 提取音频时的目标采样率（Hz）
        "audio_sample_rate": 16000,
        # 提取音频时的声道数（1=单声道，适合Whisper）
        "audio_channels": 1,
        # 输出MP4时是否使用 -c copy（流拷贝，避免音画偏移）
        "use_stream_copy": True,
    },

    # ===== Whisper / ASR 相关（需与4号确认） =====
    "whisper": {
        # 模型大小：tiny / base / small / medium / large
        "model_size": "base",
        # 模型文件目录。为空表示使用 whisper 默认路径
        "model_dir": "",
        # 识别语言。为空表示自动检测
        "language": "",
        # 计算设备：cpu / cuda / auto
        "device": "auto",
    },

    # ===== 字幕处理相关（需与4号确认） =====
    "subtitle": {
        # 单条字幕最大字符数（超过则分割）
        "max_chars_per_line": 40,
        # 字幕之间最小间隙（毫秒）
        "min_gap_ms": 200,
        # 是否调用 SubtitleEdit 做时间轴微调
        "enable_subtitle_edit": True,
        # SubtitleEdit 可执行文件路径（如果启用的话）
        "subtitle_edit_path": "",
        # 输出 SRT 编码
        "output_encoding": "utf-8",
    },

    # ===== 输出设置 =====
    "output": {
        # 默认输出目录。为空表示"与源文件同目录"
        "default_output_dir": "",
        # 输出 MP4 文件名后缀
        "mp4_suffix": "_矫正",
        # 输出 SRT 文件名后缀（空表示与视频同名）
        "srt_suffix": "",
        # 是否在输出目录下创建子文件夹（按任务名）
        "create_subfolder": False,
    },

    # ===== 调度/线程设置 =====
    "scheduler": {
        # 同时处理的最大任务数（目前为1，即串行；后续可支持并行）
        "max_concurrent_tasks": 1,
        # 进度更新最小间隔（毫秒），避免信号太频繁导致界面卡顿
        "progress_update_interval_ms": 100,
        # 任务失败后是否继续处理下一个
        "continue_on_error": True,
    },

    # ===== 日志设置 =====
    "log": {
        # 是否将日志写入文件
        "enable_file_log": False,
        # 日志文件目录（空表示程序目录下的 logs/）
        "log_dir": "",
        # 日志级别：debug / info / warning / error
        "level": "info",
        # 单个日志文件最大大小（MB）
        "max_file_size_mb": 10,
        # 最多保留几个日志文件
        "max_file_count": 5,
    },
}


# ---------------------------------------------------------------------------
# 配置管理类
# ---------------------------------------------------------------------------
class AppConfig:
    """全局配置管理类（单例模式）

    用法：
        config = AppConfig()  # 无论调用多少次，返回同一个实例
    """

    _instance: Optional["AppConfig"] = None
    _initialized = False

    def __new__(cls, config_file: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_file: Optional[str] = None):
        """初始化配置

        Args:
            config_file: 配置文件路径，为 None 则使用默认路径
        """
        if self._initialized:
            return  # 单例：只初始化一次

        self._initialized = True
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self._config_file = config_file or self._default_config_path()
        self._dirty = False  # 配置是否被修改过（未保存）

        # 尝试从文件加载
        if os.path.isfile(self._config_file):
            try:
                self.load()
            except ConfigError as e:
                # 加载失败就用默认配置，不崩溃
                # 可以通过日志模块上报，这里先静默处理
                print(f"[配置警告] 加载配置文件失败，使用默认配置：{e}")

    # ------------------------------------------------------------------
    # 配置文件路径
    # ------------------------------------------------------------------
    @staticmethod
    def _default_config_path() -> str:
        """获取默认配置文件路径

        规则：
          - 开发环境：程序目录下 config.json
          - 打包后：用户目录下 .xxx/config.json（避免写入 Program Files）
        TODO: 待与6号确认打包后的配置文件存放位置
        """
        # 先简单放在程序目录下，打包后再调整
        import sys
        if getattr(sys, "frozen", False):
            # PyInstaller 打包后的环境
            base_dir = os.path.dirname(sys.executable)
        else:
            # 开发环境
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "config.json")

    @property
    def config_file(self) -> str:
        """配置文件路径"""
        return self._config_file

    @property
    def is_dirty(self) -> bool:
        """配置是否被修改过但未保存"""
        return self._dirty

    # ------------------------------------------------------------------
    # 读写配置（支持点号路径，如 "ffmpeg.ffmpeg_path"）
    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项

        Args:
            key: 配置键，支持点号分隔的嵌套路径，如 "ffmpeg.ffmpeg_path"
            default: 键不存在时返回的默认值
        """
        keys = key.split(".")
        value = self._data
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value: Any) -> None:
        """设置配置项

        Args:
            key: 配置键，支持点号分隔的嵌套路径
            value: 配置值
        """
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            if k not in data or not isinstance(data[k], dict):
                data[k] = {}
            data = data[k]

        old_value = data.get(keys[-1])
        if old_value != value:
            data[keys[-1]] = value
            self._dirty = True

    # ------------------------------------------------------------------
    # 便捷属性访问（常用配置项可以直接 config.xxx 访问）
    # ------------------------------------------------------------------
    @property
    def ffmpeg_path(self) -> str:
        """FFmpeg 可执行文件路径（自动解析空路径）"""
        path = self.get("ffmpeg.ffmpeg_path", "")
        if path:
            return path
        # TODO: 自动查找逻辑（程序目录/bin/ffmpeg.exe 或系统PATH）
        return "ffmpeg"  # 暂时返回默认值，由系统PATH查找

    @property
    def ffprobe_path(self) -> str:
        """FFprobe 可执行文件路径"""
        path = self.get("ffmpeg.ffprobe_path", "")
        if path:
            return path
        return "ffprobe"

    @property
    def whisper_model_size(self) -> str:
        """Whisper 模型大小"""
        return self.get("whisper.model_size", "base")

    @property
    def default_output_dir(self) -> str:
        """默认输出目录（空字符串表示与源文件同目录）"""
        return self.get("output.default_output_dir", "")

    @property
    def continue_on_error(self) -> bool:
        """任务失败后是否继续"""
        return self.get("scheduler.continue_on_error", True)

    # ------------------------------------------------------------------
    # 加载 & 保存
    # ------------------------------------------------------------------
    def load(self) -> None:
        """从配置文件加载配置

        Raises:
            ConfigError: 配置文件格式错误
        """
        if not os.path.isfile(self._config_file):
            return  # 文件不存在就用默认配置

        try:
            with open(self._config_file, "r", encoding="utf-8") as f:
                user_config = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"配置文件格式错误：{e}") from e
        except OSError as e:
            raise ConfigError(f"读取配置文件失败：{e}") from e

        if not isinstance(user_config, dict):
            raise ConfigError("配置文件根节点必须是对象（dict）")

        # 深度合并：用户配置覆盖默认配置
        self._deep_merge(self._data, user_config)
        self._dirty = False

    def save(self) -> None:
        """保存配置到文件

        Raises:
            ConfigError: 保存失败
        """
        try:
            # 确保目录存在
            dir_path = os.path.dirname(self._config_file)
            if dir_path and not os.path.isdir(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            self._dirty = False
        except OSError as e:
            raise ConfigError(f"保存配置文件失败：{e}") from e

    def reset_defaults(self) -> None:
        """重置为默认配置"""
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._dirty = True

    def to_dict(self) -> Dict[str, Any]:
        """导出全部配置为字典（只读副本）"""
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # 配置校验
    # ------------------------------------------------------------------
    def validate(self) -> list:
        """校验配置是否合法，返回错误信息列表

        Returns:
            错误信息列表，空列表表示全部合法
        """
        errors = []

        # FFmpeg 路径校验（非空时检查文件是否存在）
        ffmpeg_path = self.get("ffmpeg.ffmpeg_path", "")
        if ffmpeg_path and not os.path.isfile(ffmpeg_path):
            errors.append(f"FFmpeg 路径不存在：{ffmpeg_path}")

        # 模型目录校验（非空时检查目录是否存在）
        model_dir = self.get("whisper.model_dir", "")
        if model_dir and not os.path.isdir(model_dir):
            errors.append(f"Whisper 模型目录不存在：{model_dir}")

        # 默认输出目录校验（非空时检查目录是否存在）
        out_dir = self.get("output.default_output_dir", "")
        if out_dir and not os.path.isdir(out_dir):
            errors.append(f"默认输出目录不存在：{out_dir}")

        # 模型大小校验
        valid_models = ["tiny", "base", "small", "medium", "large"]
        model_size = self.get("whisper.model_size", "")
        if model_size and model_size not in valid_models:
            errors.append(f"无效的 Whisper 模型大小：{model_size}，可选：{valid_models}")

        # 日志级别校验
        valid_levels = ["debug", "info", "warning", "error"]
        log_level = self.get("log.level", "")
        if log_level and log_level not in valid_levels:
            errors.append(f"无效的日志级别：{log_level}，可选：{valid_levels}")

        return errors

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """深度合并字典：override 覆盖 base 中的同名键

        直接修改 base 字典，不返回新字典。
        """
        for key, value in override.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                AppConfig._deep_merge(base[key], value)
            else:
                base[key] = value

    def __repr__(self) -> str:
        return f"<AppConfig file={self._config_file} dirty={self._dirty}>"
