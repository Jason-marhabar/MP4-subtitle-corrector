# -*- coding: utf-8 -*-
"""
语音边界提取（第四板块基础层）

用 ffmpeg 的 silencedetect 从**音频波形**中提取真实的语音/静音边界，
作为与任何 ASR 模型无关的客观时间参照。

为什么需要它
------------
实测（docs/第四板块-探针实验结论.md）表明 Whisper 的时间戳会系统性偏早
（small 中位 −92ms、large-v3 中位 −267ms），且标准差 130~160ms。
Aboboo 官方明确"字幕时间戳用作断点"，所以这些偏差会直接变成断点错位。
波形边界是唯一独立于模型的"真值"参照，用于：
  1. 校正阶段把字幕起止吸附到真实语音边界（snap）
  2. 把 Whisper 合并了多句的长段按静音切开（split）
"""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

_SIL_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


class FFmpegNotFound(RuntimeError):
    """找不到 ffmpeg/ffprobe 可执行文件。"""


@dataclass
class SpeechBoundaries:
    """语音边界集合（单位：秒）"""

    speech: List[Tuple[float, float]]      # 语音区间 [(start, end), ...]
    silences: List[Tuple[float, float]]    # 静音区间
    duration: float                        # 媒体总时长

    @property
    def onsets(self) -> List[float]:
        """所有语音起点（校正时的吸附锚点）"""
        return [s for s, _ in self.speech]

    @property
    def offsets(self) -> List[float]:
        """所有语音终点"""
        return [e for _, e in self.speech]

    def summary(self) -> str:
        if not self.speech:
            return "未检测到任何语音段"
        durs = [b - a for a, b in self.speech]
        sil = [e - s for s, e in self.silences] or [0.0]
        return (f"总时长 {self.duration:.3f}s；语音段 {len(self.speech)} 个"
                f"（中位 {statistics.median(durs)*1000:.0f}ms）；"
                f"静音段 {len(self.silences)} 个（中位 {statistics.median(sil)*1000:.0f}ms）")


def _which(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise FFmpegNotFound(
            f"未找到 {name}。请确认 FFmpeg 已安装并加入 PATH（本项目实测可用版本 8.0.1）。"
        )
    return p


def probe_duration(media: str, timeout: int = 120) -> Optional[float]:
    """用 ffprobe 读取媒体总时长（秒）；失败返回 None。"""
    try:
        r = subprocess.run(
            [_which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            capture_output=True, text=True, timeout=timeout, errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except (FFmpegNotFound, ValueError, subprocess.SubprocessError):
        pass
    return None


def probe_silences(media: str, threshold: float = 0.10,
                   noise_db: float = -32.0, timeout: int = 3600
                   ) -> List[Tuple[float, float]]:
    """用一个很低的阈值探测静音区间，用于分析素材的停顿分布。

    与 ``extract_boundaries`` 的区别：这里只关心静音本身，不做语音段推导。
    """
    ffmpeg = _which("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(media),
           "-af", f"silencedetect=noise={noise_db}dB:d={threshold}",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout, errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg silencedetect 失败：{r.stderr[-800:]}")
    out: List[Tuple[float, float]] = []
    cur: Optional[float] = None
    for m in _SIL_RE.finditer(r.stderr):
        kind, ts = m.group(1), float(m.group(2))
        if kind == "start":
            cur = ts
        elif kind == "end" and cur is not None:
            out.append((cur, ts))
            cur = None
    return out


def adapt_min_silence(media: str, noise_db: float = -32.0,
                      probe: float = 0.10, ratio: float = 0.42,
                      quantile: float = 0.20,
                      lower: float = 0.12, upper: float = 0.30) -> float:
    """从素材自身推导"多长静音才算句间分隔"。

    为什么需要自适应
    ----------------
    固定 ``min_silence=0.25s`` 会在两类素材上表现截然不同（实测）：

      · 跟读素材：句间停顿 2.7s  -> 阈值远小于停顿，切分正常
      · 连续语流：句间停顿 0.34s -> 阈值逼近停顿，**语音段被过度合并**
        （111 段塌成 91 段），锚点变少，起点命中率从 99% 掉到 94%

    把阈值写死成 0.15s 又会在长停顿素材上过度切分。所以改为从信号推导。

    为什么用低分位而不是中位数
    --------------------------
    实测素材的静音时长是**双峰**的：词内换气 ~370ms、句间停顿 2.7s。
    取中位数会落在两峰之间的空档（实测 802ms），据此推出的阈值（337ms）
    过大，把 130 段并成 118 段。改用 **20% 分位**（落在第一峰的左肩）后，
    阈值 ~0.12s，能同时适配两类素材。

      1. 用很低的阈值（0.10s）探测所有静音
      2. 只看落在 [0.12, 3.0]s 的静音 —— "句间停顿"的合理范围
      3. 取 20% 分位 × ``ratio``，夹到 [lower, upper]

    实测：跟读素材 ≈0.12s、连续语流 ≈0.14s，两者边界质量基本一致。
    """
    try:
        sils = [e - s for s, e in probe_silences(media, probe, noise_db)
                if 0.12 <= (e - s) <= 3.0]
    except Exception:
        return lower
    if not sils:
        return lower
    sils.sort()
    idx = min(len(sils) - 1, max(0, int(len(sils) * quantile)))
    val = sils[idx] * ratio
    return float(min(upper, max(lower, val)))


def extract_boundaries(
    media: str,
    noise_db: float = -32.0,
    min_silence: Optional[float] = None,
    timeout: int = 3600,
) -> SpeechBoundaries:
    """提取语音/静音边界。

    参数
    ----
    noise_db
        静音判定阈值。实测 -32dB 适配本项目的英语跟读素材；
        背景噪声大的素材可放宽到 -25dB，安静录音可收紧到 -40dB。
    min_silence
        多长的静音才算"分隔"。**默认 None = 从素材自适应**（推荐），
        见 ``adapt_min_silence``。传具体数值可强制固定阈值。

    说明
    ----
    本函数不做"最短语音段"过滤，把原始结果完整交出去，由调用方按
    ``min_speech`` 决定过滤强度（不同用途阈值不同：切分需要细粒度，
    吸附需要干净锚点）。
    """
    if min_silence is None:
        min_silence = adapt_min_silence(media, noise_db)

    ffmpeg = _which("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(media),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout, errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg silencedetect 失败：{r.stderr[-800:]}")

    # 解析事件流；silence_start 可能没有配对的 silence_end（末尾静音）
    events = [(m.group(1), float(m.group(2))) for m in _SIL_RE.finditer(r.stderr)]
    silences: List[Tuple[float, Optional[float]]] = []
    cur: Optional[float] = None
    for kind, ts in events:
        if kind == "start":
            cur = ts
        elif kind == "end" and cur is not None:
            silences.append((cur, ts))
            cur = None
    if cur is not None:
        silences.append((cur, None))

    total = probe_duration(media) or 0.0

    # 静音之间的空隙即为语音段
    speech: List[Tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor:
            speech.append((cursor, s))
        cursor = e if e is not None else total
    if total and cursor < total:
        speech.append((cursor, total))

    speech = [(a, b) for a, b in speech if b > a]
    closed_sil = [(s, e) for s, e in silences if e is not None]
    return SpeechBoundaries(speech=speech, silences=closed_sil, duration=total)


def filter_speech(
    speech: List[Tuple[float, float]], min_speech: float = 0.10
) -> List[Tuple[float, float]]:
    """滤除过短的语音段（噪声碎片）。

    实测素材中最短语音段仅 21ms —— 那是噪声不是语句。若把它当作吸附锚点，
    会把字幕吸到错误位置。
    """
    return [(a, b) for a, b in speech if (b - a) >= min_speech]


def merge_close_regions(
    speech: List[Tuple[float, float]], max_gap: float = 0.30
) -> List[Tuple[float, float]]:
    """把间隔小于 max_gap 的相邻语音段合并。

    用途：Whisper 的段边界往往跨越多条短语音段，合并后再做归属判断
    可以减少"一句话被拆成两半"的错误。
    """
    if not speech:
        return []
    out = [list(speech[0])]
    for a, b in speech[1:]:
        if a - out[-1][1] <= max_gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]
