# -*- coding: utf-8 -*-
"""
第四板块统一入口（供第二板块业务调度调用）

对外契约：
    result = process(video, out_dir, progress_cb=..., cancel_flag=...)

职责：提取音频 -> ASR 转写 -> 字幕校正 -> 输出 SRT（+ 可选 remux MP4）

设计要点
--------
· **模型复用**：``AsrEngine`` 可在多次调用间复用，避免每个文件重新加载
  模型（CPU 上 large-v3 单次加载实测 281s）。批量处理请自建引擎传入。
· **进度与取消**：全流程通过 ``progress_cb(0~1, 描述)`` 上报进度，
  ``cancel_flag()`` 返回 True 时尽快中止。
· **音频与视频解耦**：转写用 16kHz 单声道 WAV（Whisper 的原生输入格式，
  体积小、解码快）；输出 MP4 用 ``-c copy`` 重新封装，**不重新编码**，
  以严格保持时间轴（实测逐帧偏差 0.000000ms）。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .asr import AsrEngine, AsrCancelled
from .ffmpeg_utils import get_ffmpeg_path
from .subtitle_fix import (FixConfig, FixReport, fix_subtitles,
                           normalize_with_seconv, self_check, write_srt)
from .waveform import extract_boundaries

log = logging.getLogger(__name__)

ProgressCb = Callable[[float, str], None]
CancelFlag = Callable[[], bool]


class PipelineCancelled(RuntimeError):
    """流程被调用方取消。"""


@dataclass
class ProcessResult:
    """单次处理结果"""

    video: str
    srt_path: str
    mp4_path: Optional[str] = None
    audio_path: Optional[str] = None
    cue_count: int = 0
    duration: float = 0.0
    elapsed: float = 0.0
    report: Optional[FixReport] = None
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.srt_path) and not self.problems

    def summary(self) -> str:
        base = (f"{Path(self.video).name} -> {Path(self.srt_path).name}"
                f"（{self.cue_count} 条字幕，{self.elapsed:.1f}s）")
        if self.problems:
            base += f"  警告 {len(self.problems)} 项"
        return base


# ---------------------------------------------------------------- 媒体处理

def _run_ffmpeg(cmd: List[str], timeout: int = 3600) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout, errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{' '.join(cmd[:6])}...\n{r.stderr[-600:]}")


def extract_audio(input_media: str, output_wav: str,
                  sample_rate: int = 16000, channels: int = 1) -> str:
    """提取音频为 Whisper 可用的 WAV（16kHz 单声道 pcm_s16le）。"""
    ffmpeg = get_ffmpeg_path()
    out = Path(output_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([ffmpeg, "-y", "-i", str(input_media),
                 "-vn", "-acodec", "pcm_s16le",
                 "-ar", str(sample_rate), "-ac", str(channels), str(out)])
    return str(out)


def remux_mp4(input_media: str, output_mp4: str) -> str:
    """流拷贝封装输出新 MP4（不重新编码，严格保持时间轴）。

    实测：与源文件逐帧 PTS 比对 2702/2702 完全一致，最大偏差 0.000000ms。
    因此这一步**不会**引入音画偏移，也是"对齐"物理基础的一部分。
    """
    ffmpeg = get_ffmpeg_path()
    out = Path(output_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([ffmpeg, "-y", "-i", str(input_media),
                 "-c", "copy", "-movflags", "+faststart", str(out)])
    return str(out)


# ---------------------------------------------------------------- 主流程

def process(
    video: str,
    out_dir: str,
    engine: Optional[AsrEngine] = None,
    fix_config: Optional[FixConfig] = None,
    language: Optional[str] = "en",
    progress_cb: Optional[ProgressCb] = None,
    cancel_flag: Optional[CancelFlag] = None,
    make_mp4: bool = True,
    keep_audio: bool = False,
    srt_name: Optional[str] = None,
) -> ProcessResult:
    """处理单个视频：输出校正后的 SRT（并可选输出 remux 的 MP4）。

    参数
    ----
    video
        输入 MP4 路径。
    out_dir
        输出目录（由 GUI 的"选择保存文件夹"传入）。
    engine
        复用的 ``AsrEngine``。为 ``None`` 时按默认参数临时创建。
    fix_config
        校正参数，默认取探针实验实测值。
    progress_cb
        ``(0.0~1.0, 描述)`` 进度回调，用于驱动 GUI 进度条。
    cancel_flag
        返回 True 时中止；已产出的部分文件会被清理。
    make_mp4
        是否输出 remux 后的 MP4。
    keep_audio
        是否保留中间 WAV（默认删除，避免占空间）。
    srt_name
        自定义 SRT 文件名；默认与视频同名。
    """
    t_start = time.time()
    src = Path(video)
    if not src.exists():
        raise FileNotFoundError(f"输入文件不存在：{video}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def step(p: float, msg: str) -> None:
        log.debug("进度 %.0f%% %s", p * 100, msg)
        if progress_cb:
            progress_cb(p, msg)

    def check_cancel() -> None:
        if cancel_flag and cancel_flag():
            raise PipelineCancelled("处理已被取消")

    stem = src.stem
    srt_path = out / (srt_name or f"{stem}.srt")
    mp4_path = out / f"{stem}_remux.mp4" if make_mp4 else None

    tmpdir = Path(tempfile.mkdtemp(prefix="dsh_sub_", dir=str(out)))
    wav_path = tmpdir / f"{stem}.wav"
    result = ProcessResult(video=str(src), srt_path=str(srt_path),
                           mp4_path=str(mp4_path) if mp4_path else None)

    try:
        # --- 1. 提取音频
        step(0.02, "提取音频")
        extract_audio(str(src), str(wav_path))
        check_cancel()

        # --- 2. 波形边界（与转写相互独立，先算好供校正使用）
        step(0.08, "分析音频波形边界")
        boundaries = extract_boundaries(str(wav_path))
        log.info("波形边界：%s", boundaries.summary())
        check_cancel()

        # --- 3. ASR 转写
        own_engine = engine is None
        eng = engine or AsrEngine(language=language)
        step(0.10, "准备 ASR 模型")

        def asr_progress(p: float, msg: str) -> None:
            # 把转写进度映射到总进度的 10%~75%
            step(0.10 + 0.65 * max(0.0, min(1.0, p)), msg)

        transcript = eng.transcribe(str(wav_path), language=language,
                                    progress_cb=asr_progress,
                                    cancel_flag=cancel_flag)
        result.duration = transcript.duration
        log.info("转写：%s", transcript.summary())
        check_cancel()

        # --- 4. 字幕校正
        def fix_progress(p: float, msg: str) -> None:
            step(0.75 + 0.15 * max(0.0, min(1.0, p)), msg)

        cues, report = fix_subtitles(transcript.segments, boundaries,
                                     fix_config, fix_progress)
        result.cue_count = len(cues)
        result.report = report
        result.problems = list(report.issues)
        check_cancel()

        # --- 5. 写出 SRT
        step(0.90, "写出 SRT")
        write_srt(cues, str(srt_path))

        # --- 6. Subtitle Edit（seconv）规范化 —— 需求第 3 条
        # 放在吸附之后：吸附负责对齐（决定因素），seconv 负责字幕工程规范收尾。
        # 内部会校验结果，不合格自动回滚，不影响已对齐的产物。
        if fix_config is None or getattr(fix_config, "seconv_enabled", True):
            step(0.93, "Subtitle Edit 规范化")
            normalize_with_seconv(
                str(srt_path), fix_config or FixConfig(), fix_progress)

        # --- 7. remux MP4
        if mp4_path is not None:
            step(0.96, "封装 MP4（流拷贝，不重新编码）")
            remux_mp4(str(src), str(mp4_path))

        step(1.0, "完成")
        result.elapsed = time.time() - t_start
        if own_engine:
            del eng
        return result

    except (PipelineCancelled, AsrCancelled):
        # 清理半成品，避免用户看到一个残缺的 SRT
        for p in (srt_path, mp4_path):
            try:
                if p and Path(p).exists():
                    Path(p).unlink()
            except OSError:
                pass
        raise
    finally:
        if not keep_audio:
            shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            log.info("中间音频保留在：%s", wav_path)


def srt_for_aboboo(srt_path: str, video_path: str) -> str:
    """把 SRT 复制到视频同目录并改成同名，便于 Aboboo 自动加载。

    Aboboo 会自动加载视频同目录下的**同名字幕**，所以对外交付时这个命名
    约定比路径更重要。
    """
    v = Path(video_path)
    dst = v.with_suffix(".srt")
    shutil.copyfile(srt_path, dst)
    return str(dst)
