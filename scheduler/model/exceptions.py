# -*- coding: utf-8 -*-
"""
自定义异常体系（任务2 - 第2周）

职责：
  1. 统一定义各模块的异常类型，便于分类处理
  2. 提供错误码和用户友好的错误提示
  3. 支持异常链（保留原始异常信息）

异常继承关系：
  AppBaseError（项目根异常）
  ├── ConfigError          配置相关错误
  ├── TaskError            任务调度相关错误
  │   ├── TaskStoppedError   用户停止任务
  │   └── TaskQueueFullError 任务队列已满
  ├── FFmpegError          FFmpeg 相关错误（供3号使用）
  │   ├── FFmpegNotFoundError  ffmpeg 未找到
  │   ├── FFmpegProcessError   ffmpeg 执行失败
  │   └── InvalidMediaError    无效的媒体文件
  ├── ASRError             语音识别相关错误（供4号使用）
  │   ├── ModelNotFoundError   模型文件未找到
  │   └── TranscribeError      识别失败
  └── SubtitleError        字幕处理相关错误（供4号使用）
      ├── InvalidSRTError      无效的 SRT 格式
      └── SubtitleEditError    SubtitleEdit 调用失败

使用方式：
    from model.exceptions import FFmpegError, InvalidMediaError

    try:
        ffmpeg.extract_audio(path)
    except InvalidMediaError as e:
        # 处理无效文件
        log.error(f"文件损坏：{e.user_message}")
    except FFmpegError as e:
        # 处理其他 FFmpeg 错误
        log.error(f"FFmpeg 错误：{e}")
"""


import os


# ---------------------------------------------------------------------------
# 错误码常量
# ---------------------------------------------------------------------------
class ErrorCode:
    """错误码常量

    编码规则：
      1xxx - 配置/通用错误
      2xxx - 任务调度错误
      3xxx - FFmpeg 错误
      4xxx - ASR/Whisper 错误
      5xxx - 字幕处理错误
    """
    # 配置/通用
    CONFIG_INVALID = 1001
    CONFIG_SAVE_FAILED = 1002
    CONFIG_LOAD_FAILED = 1003

    # 任务调度
    TASK_STOPPED = 2001
    TASK_QUEUE_FULL = 2002
    TASK_NOT_FOUND = 2003
    TASK_ALREADY_RUNNING = 2004

    # FFmpeg
    FFMPEG_NOT_FOUND = 3001
    FFMPEG_PROCESS_FAILED = 3002
    INVALID_MEDIA_FILE = 3003
    AUDIO_EXTRACT_FAILED = 3004
    REMUX_FAILED = 3005

    # ASR / Whisper
    MODEL_NOT_FOUND = 4001
    TRANSCRIBE_FAILED = 4002
    AUDIO_TOO_SHORT = 4003

    # 字幕处理
    INVALID_SRT_FORMAT = 5001
    SUBTITLE_EDIT_FAILED = 5002
    SUBTITLE_ALIGN_FAILED = 5003


# ---------------------------------------------------------------------------
# 根异常
# ---------------------------------------------------------------------------
class AppBaseError(Exception):
    """项目所有自定义异常的基类

    Attributes:
        error_code: 错误码（见 ErrorCode 常量）
        user_message: 面向用户的友好错误提示
        details: 详细错误信息（用于调试）
    """

    def __init__(
        self,
        message: str = "",
        error_code: int = 0,
        user_message: str = "",
        details: str = "",
    ):
        super().__init__(message)
        self.error_code = error_code
        self.user_message = user_message or message
        self.details = details

    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self.user_message}"
        return self.user_message


# ---------------------------------------------------------------------------
# 配置相关异常
# ---------------------------------------------------------------------------
class ConfigError(AppBaseError):
    """配置错误"""

    def __init__(self, message: str = "", details: str = ""):
        super().__init__(
            message=message,
            error_code=ErrorCode.CONFIG_INVALID,
            user_message=f"配置错误：{message}",
            details=details,
        )


class ConfigSaveError(ConfigError):
    """配置保存失败"""

    def __init__(self, message: str = ""):
        super().__init__(message=message)
        self.error_code = ErrorCode.CONFIG_SAVE_FAILED
        self.user_message = f"保存配置失败：{message}"


class ConfigLoadError(ConfigError):
    """配置加载失败"""

    def __init__(self, message: str = ""):
        super().__init__(message=message)
        self.error_code = ErrorCode.CONFIG_LOAD_FAILED
        self.user_message = f"加载配置失败：{message}"


# ---------------------------------------------------------------------------
# 任务调度相关异常
# ---------------------------------------------------------------------------
class TaskError(AppBaseError):
    """任务调度错误基类"""
    pass


class TaskStoppedError(TaskError):
    """用户请求停止任务（用于跳出深层嵌套）"""

    def __init__(self, message: str = "任务已停止"):
        super().__init__(
            message=message,
            error_code=ErrorCode.TASK_STOPPED,
            user_message=message,
        )


class TaskQueueFullError(TaskError):
    """任务队列已满"""

    def __init__(self, max_size: int = 0):
        msg = f"任务队列已满（最大 {max_size} 个）" if max_size else "任务队列已满"
        super().__init__(
            message=msg,
            error_code=ErrorCode.TASK_QUEUE_FULL,
            user_message=msg,
        )


class TaskNotFoundError(TaskError):
    """任务不存在"""

    def __init__(self, task_id: int):
        msg = f"任务 {task_id} 不存在"
        super().__init__(
            message=msg,
            error_code=ErrorCode.TASK_NOT_FOUND,
            user_message=msg,
        )


class TaskAlreadyRunningError(TaskError):
    """任务已在运行中"""

    def __init__(self):
        msg = "已有任务在处理中"
        super().__init__(
            message=msg,
            error_code=ErrorCode.TASK_ALREADY_RUNNING,
            user_message=msg,
        )


# ---------------------------------------------------------------------------
# FFmpeg 相关异常（供3号模块使用）
# ---------------------------------------------------------------------------
class FFmpegError(AppBaseError):
    """FFmpeg 错误基类"""

    def __init__(
        self,
        message: str = "",
        error_code: int = ErrorCode.FFMPEG_PROCESS_FAILED,
        details: str = "",
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            user_message=f"音视频处理错误：{message}",
            details=details,
        )


class FFmpegNotFoundError(FFmpegError):
    """FFmpeg 可执行文件未找到"""

    def __init__(self, path: str = ""):
        msg = "未找到 FFmpeg" if not path else f"未找到 FFmpeg：{path}"
        super().__init__(
            message=msg,
            error_code=ErrorCode.FFMPEG_NOT_FOUND,
            details="请检查 ffmpeg_path 配置，或确保 ffmpeg 已加入系统 PATH",
        )
        self.user_message = (
            "未找到 FFmpeg 工具，请确认 FFmpeg 已安装并正确配置路径"
        )


class FFmpegProcessError(FFmpegError):
    """FFmpeg 进程执行失败"""

    def __init__(self, command: str = "", stderr: str = "", return_code: int = -1):
        msg = f"FFmpeg 执行失败（返回码 {return_code}）"
        details = f"命令：{command}\n错误输出：{stderr}" if command else stderr
        super().__init__(
            message=msg,
            error_code=ErrorCode.FFMPEG_PROCESS_FAILED,
            details=details,
        )
        self.return_code = return_code
        self.stderr = stderr


class InvalidMediaError(FFmpegError):
    """无效的媒体文件（损坏、不支持的格式等）"""

    def __init__(self, file_path: str = "", reason: str = ""):
        msg = f"无效的媒体文件：{os.path.basename(file_path) if file_path else ''}"
        if reason:
            msg += f"（{reason}）"
        super().__init__(
            message=msg,
            error_code=ErrorCode.INVALID_MEDIA_FILE,
            details=f"文件：{file_path}\n原因：{reason}",
        )
        self.user_message = (
            f"文件无法处理：{os.path.basename(file_path) if file_path else '未知文件'}"
            f"，可能是文件损坏或格式不支持"
        )
        self.file_path = file_path


class AudioExtractError(FFmpegError):
    """音频提取失败"""

    def __init__(self, file_path: str = "", reason: str = ""):
        msg = f"提取音频失败：{os.path.basename(file_path) if file_path else ''}"
        if reason:
            msg += f"（{reason}）"
        super().__init__(
            message=msg,
            error_code=ErrorCode.AUDIO_EXTRACT_FAILED,
            details=f"文件：{file_path}\n原因：{reason}",
        )


class RemuxError(FFmpegError):
    """音视频封装失败"""

    def __init__(self, file_path: str = "", reason: str = ""):
        msg = f"音视频封装失败：{os.path.basename(file_path) if file_path else ''}"
        if reason:
            msg += f"（{reason}）"
        super().__init__(
            message=msg,
            error_code=ErrorCode.REMUX_FAILED,
            details=f"文件：{file_path}\n原因：{reason}",
        )


# ---------------------------------------------------------------------------
# ASR / Whisper 相关异常（供4号模块使用）
# ---------------------------------------------------------------------------
class ASRError(AppBaseError):
    """语音识别错误基类"""

    def __init__(
        self,
        message: str = "",
        error_code: int = ErrorCode.TRANSCRIBE_FAILED,
        details: str = "",
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            user_message=f"语音识别错误：{message}",
            details=details,
        )


class ModelNotFoundError(ASRError):
    """Whisper 模型文件未找到"""

    def __init__(self, model_name: str = "", model_dir: str = ""):
        msg = f"未找到 Whisper 模型：{model_name}"
        details = f"模型目录：{model_dir}" if model_dir else ""
        super().__init__(
            message=msg,
            error_code=ErrorCode.MODEL_NOT_FOUND,
            details=details,
        )
        self.user_message = (
            f"Whisper 模型「{model_name}」未找到，请检查模型路径配置"
        )


class TranscribeError(ASRError):
    """语音识别失败"""

    def __init__(self, reason: str = ""):
        msg = f"语音识别失败"
        if reason:
            msg += f"：{reason}"
        super().__init__(
            message=msg,
            error_code=ErrorCode.TRANSCRIBE_FAILED,
            details=reason,
        )


class AudioTooShortError(ASRError):
    """音频太短，无法识别"""

    def __init__(self, duration: float = 0):
        msg = f"音频太短（{duration:.1f}秒），无法识别"
        super().__init__(
            message=msg,
            error_code=ErrorCode.AUDIO_TOO_SHORT,
        )


# ---------------------------------------------------------------------------
# 字幕处理相关异常（供4号模块使用）
# ---------------------------------------------------------------------------
class SubtitleError(AppBaseError):
    """字幕处理错误基类"""

    def __init__(
        self,
        message: str = "",
        error_code: int = 0,
        details: str = "",
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            user_message=f"字幕处理错误：{message}",
            details=details,
        )


class InvalidSRTError(SubtitleError):
    """无效的 SRT 格式"""

    def __init__(self, reason: str = "", line_no: int = 0):
        msg = "无效的 SRT 字幕格式"
        if line_no:
            msg += f"（第 {line_no} 行）"
        if reason:
            msg += f"：{reason}"
        super().__init__(
            message=msg,
            error_code=ErrorCode.INVALID_SRT_FORMAT,
            details=reason,
        )


class SubtitleEditError(SubtitleError):
    """SubtitleEdit 调用失败"""

    def __init__(self, reason: str = ""):
        msg = "字幕时间轴微调失败"
        if reason:
            msg += f"：{reason}"
        super().__init__(
            message=msg,
            error_code=ErrorCode.SUBTITLE_EDIT_FAILED,
            details=reason,
        )
