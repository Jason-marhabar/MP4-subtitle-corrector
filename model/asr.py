# -*- coding: utf-8 -*-
"""
ASR 转写层（第四板块）

封装 faster-whisper，产出**带词级时间戳**的转写结果，供字幕校正使用。

为什么必须开词级时间戳
----------------------
校正阶段要把 Whisper 合并了多句的长段按静音切开，切分点必须落在词边界上，
否则会把一个单词劈成两半。段级时间戳做不到这一点。

模型必须复用
------------
批量处理多个视频时，若每个文件都重新 load_model()，一次 large-v3 加载在
CPU 上实测需 281 秒。因此本模块提供 ``AsrEngine`` 持有模型实例，由业务
调度层创建一次、多次调用。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

# 取消检查回调：返回 True 表示应当中止
CancelFlag = Callable[[], bool]
# 进度回调：(0.0~1.0, 描述文本)
ProgressCb = Callable[[float, str], None]

# 抑制静音段幻觉复读与失败循环的默认解码参数（实测验证）
DEFAULT_DECODE = dict(
    word_timestamps=True,
    vad_filter=True,
    condition_on_previous_text=False,
    no_speech_threshold=0.6,
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
    temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
)


class AsrCancelled(RuntimeError):
    """转写被调用方主动取消。"""


def _cuda_hint_if_relevant(exc: BaseException) -> Optional[str]:
    """若异常与 CUDA/GPU 有关，返回可操作建议，否则返回 None。

    注意：``WhisperModel(device='cuda')`` 的构造是**惰性**的 —— 缺 CUDA 库时
    构造可能"成功"，真正的报错要到 ``transcribe()`` 第一次推理才出现
    （实测：构造成功 -> 推理时抛 cublas64_12.dll not found）。
    所以加载与转写两处都要做这个转换。
    """
    try:
        from .gpu_env import cuda_error_hint
        return cuda_error_hint(exc)
    except Exception:
        msg = str(exc)
        low = msg.lower()
        if "cublas" in low or "cudnn" in low or "cuda" in low:
            return ("GPU 加速不可用。可改用 CPU："
                    "AsrEngine(device='cpu', compute_type='int8')")
        return None


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: Optional[float] = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)

    @property
    def first_word_start(self) -> Optional[float]:
        return self.words[0].start if self.words else None


@dataclass
class Transcript:
    segments: List[Segment]
    language: str
    language_probability: float
    duration: float
    elapsed: float
    model: str
    device: str

    def summary(self) -> str:
        return (f"{len(self.segments)} 段 / "
                f"{sum(len(s.words) for s in self.segments)} 词；"
                f"语言 {self.language}({self.language_probability:.2f})；"
                f"耗时 {self.elapsed:.1f}s（{self.model}/{self.device}）")


class AsrEngine:
    """持有 faster-whisper 模型实例的转写引擎（模型只加载一次）。

    示例
    ----
        engine = AsrEngine(model="small", device="cpu", compute_type="int8")
        tr = engine.transcribe("audio.wav", language="en")
    """

    def __init__(
        self,
        model: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = "en",
        decode_options: Optional[dict] = None,
    ):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.decode_options = {**DEFAULT_DECODE, **(decode_options or {})}
        self._model = None

    # ------------------------------------------------------------ 模型加载
    def load(self, progress_cb: Optional[ProgressCb] = None) -> None:
        if self._model is not None:
            return

        # 关键：在创建模型**之前**准备好 CUDA 运行库路径。
        # ctranslate2 是 C++ 扩展，内部按 PATH 解析 cublas64_12.dll /
        # cudnn64_9.dll；只调 os.add_dll_directory 无效（实测）。
        # 若用 pip 分发的 nvidia-cublas-cu12 / nvidia-cudnn-cu12，
        # 下面这行会自动把它们的 bin 目录前置到 PATH。
        if self.device == "cuda":
            try:
                from .gpu_env import ensure_cuda_dll_path
                ensure_cuda_dll_path()
            except Exception:
                pass

        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "未安装 faster-whisper。请执行：\n"
                "  venv\\Scripts\\python.exe -m pip install faster-whisper "
                "-i https://mirrors.aliyun.com/pypi/simple/"
            ) from e

        if progress_cb:
            progress_cb(0.0, f"加载模型 {self.model_name}（首次需下载，请耐心等待）")
        t0 = time.time()
        try:
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        except Exception as e:
            hint = _cuda_hint_if_relevant(e)
            if hint:
                raise RuntimeError(f"加载模型失败：{e}\n{hint}") from e
            raise
        log.info("模型 %s 加载完成，用时 %.1fs", self.model_name, time.time() - t0)
        if progress_cb:
            progress_cb(0.0, f"模型加载完成（{time.time()-t0:.1f}s）")

    # ------------------------------------------------------------ 转写
    def transcribe(
        self,
        media: str,
        language: Optional[str] = None,
        progress_cb: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelFlag] = None,
    ) -> Transcript:
        """转写媒体文件。

        为什么用线程池消费生成器
        ------------------------
        faster-whisper 的 ``transcribe()`` 返回的是**惰性生成器**，真正的推理
        发生在迭代过程中。若在主线程里迭代，调用方（例如 Qt 事件循环里的
        进度回调）就无法做别的事。这里把消费放到工作线程，主线程只做
        "取结果 + 报进度 + 响应取消"。

        注意：取消是"停止继续消费"而非"中断当前推理"——ctranslate2 正在算的
        那一段无法打断，所以取消响应最坏延迟为一段音频（约 30s 窗口）的处理时间。
        """
        self.load(progress_cb)
        assert self._model is not None

        lang = language if language is not None else self.language
        opts = dict(self.decode_options)
        if lang:
            opts["language"] = lang

        t0 = time.time()
        if progress_cb:
            progress_cb(0.0, "开始转写")

        # 惰性生成器：在子线程里迭代，避免阻塞调用方
        def _open():
            seg_iter, info = self._model.transcribe(str(media), **opts)
            return seg_iter, info

        with ThreadPoolExecutor(max_workers=1) as pool:
            seg_iter, info = pool.submit(_open).result()
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            segments: List[Segment] = []
            try:
                for raw in seg_iter:
                    if cancel_flag and cancel_flag():
                        raise AsrCancelled("转写已被取消")

                    words = []
                    for w in (getattr(raw, "words", None) or []):
                        words.append(Word(start=float(w.start), end=float(w.end),
                                          text=w.word,
                                          probability=getattr(w, "probability", None)))
                    text = (raw.text or "").strip()
                    if not text and not words:
                        continue
                    segments.append(Segment(start=float(raw.start), end=float(raw.end),
                                            text=text, words=words))

                    if progress_cb and duration > 0:
                        pct = min(0.999, float(raw.end) / duration)
                        progress_cb(pct, f"转写中 {raw.end:.1f}s / {duration:.1f}s")
            except AsrCancelled:
                raise
            except Exception as e:
                # 惰性构造导致 CUDA 缺库错误在这里才暴露，补上可操作提示
                hint = _cuda_hint_if_relevant(e)
                if hint:
                    raise RuntimeError(f"转写失败：{e}\n{hint}") from e
                raise

        elapsed = time.time() - t0
        if progress_cb:
            progress_cb(1.0, f"转写完成（{elapsed:.1f}s）")
        log.info("转写完成：%d 段，用时 %.1fs", len(segments), elapsed)

        return Transcript(
            segments=segments,
            language=getattr(info, "language", lang or "unknown"),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=duration,
            elapsed=elapsed,
            model=self.model_name,
            device=self.device,
        )


def free_gpu_cache() -> None:
    """尽力释放 CUDA 显存缓存（无 GPU / 无 torch 时静默跳过）。"""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
