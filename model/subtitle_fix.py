# -*- coding: utf-8 -*-
"""
字幕校正流水线（第四板块核心）

把 Whisper 的原始转写结果转换为**可直接导入 Aboboo 且对齐**的 SRT。

Aboboo 的硬约束
---------------
官方文档明确"字幕时间戳用作断点"、"原文字幕和译文字幕的时间戳必须相近，
最好相同"。所以字幕的对齐质量完全由时间戳精度决定。

校正顺序不能错
--------------
顺序错了各步会互相破坏。本模块固定按以下顺序执行：

    1. 剔除      空/纯空白/零时长/幻觉重复/文本不含有效字符
    2. 按静音切分  Whisper 会把多句合并进一段（实测波形 123 个语句
                 vs Whisper 54 段），必须先切开，否则整段无法对齐
    3. 波形吸附    把每段起止吸到真实语音边界（实测残差中位 0ms）
    4. 时长/间隙约束
    5. 行长约束

**第 2 步必须在第 3 步之前**：切分会改变段边界，若先吸附再切分，
新产生的边界就失去了吸附效果。

第 2 步和第 3 步都依赖波形边界，所以两者是同一套"归属"逻辑的两面：
先判断每段覆盖了哪几条语音区间并切开，切开后的每段天然只对应一条区间，
吸附就退化为把边界对齐到该区间的起止。
"""
from __future__ import annotations

import bisect
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .asr import Segment, Word
from .waveform import SpeechBoundaries, filter_speech

log = logging.getLogger(__name__)

ProgressCb = Callable[[float, str], None]

# 含字母/数字/汉字才算有效文本
_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ---------------------------------------------------------------- 配置与结果

@dataclass
class FixConfig:
    """校正参数。默认值均来自 Aboboo 对齐探针实验实测
    （见 docs/第四板块-探针实验结论.md 与 config/subtitle_params.json）。"""

    max_line_chars: int = 43          # seconv 默认 subtitleLineMaximumLength
    max_lines: int = 2                # seconv 默认 maxNumberOfLines
    # 最短时长默认不强制（0 = 关闭）。
    # 早期设为 800ms 并把字幕终点撑到 800ms，结果把「Project」的终点从真实的
    # 268.093 拉到 268.200 —— 为了凑时长而延伸到静音里。静音处没有语音，
    # 多显示一会儿并不会盖住别的句子，但**破坏了对波形的忠实**，
    # 也让"终点是否覆盖真实语音"这类检查失真。
    # 因此默认不填充，短段由 advisories() 作为提示给出。
    min_duration_ms: int = 0
    max_duration_ms: int = 10000
    min_gap_ms: int = 24              # seconv 默认 minimumMillisecondsBetweenLines
    suggested_gap_ms: int = 150       # 更安全的段间隙

    # 波形相关
    min_speech: float = 0.10          # 短于此的语音段视为噪声碎片（切分用）
    min_anchor_speech: float = 0.30   # 锚点用的更严阈值（吸附用）
    merge_gap: float = 0.30           # 间隔小于此的语音段合并（供边界分析使用）
    split_gap: float = 0.80           # 词间停顿超过此值即视为句间边界（切分兜底）
    snap_tolerance: float = 3.0       # 吸附容差（秒），超出则放弃吸附
    split_enabled: bool = True        # 是否按静音切分合并段

    # 剔除规则
    drop_no_meaningful: bool = True   # 剔除不含字母/数字/汉字的段
    drop_hallucination_repeat: bool = True  # 剔除连续重复的幻觉段

    # ---- Subtitle Edit（seconv）规范化（需求第 3 条）----
    # 放在波形吸附**之后**：吸附负责把时间戳对到真实语音边界（这是对齐的
    # 决定因素），seconv 负责按字幕工程规范收尾。
    # 默认只开"不重排内容、不破坏已对齐时间轴"的安全操作；
    # fix_common_errors 会重排内容，默认关闭（实测会与吸附成果互相干扰）。
    seconv_enabled: bool = True
    seconv_min_gap_ms: Optional[int] = None   # None=沿用 min_gap_ms
    seconv_apply_duration_limits: bool = False
    seconv_split_long_lines: bool = False
    seconv_balance_lines: bool = False
    seconv_fix_common_errors: bool = False


@dataclass
class Cue:
    """一条字幕"""

    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)
    origin: str = "asr"               # asr | split | snap

    @property
    def start_ms(self) -> int:
        return int(round(self.start * 1000))

    @property
    def end_ms(self) -> int:
        return int(round(self.end * 1000))

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class FixReport:
    """校正过程统计，便于排错与展示"""

    dropped: int = 0
    split: int = 0
    merged: int = 0
    duplicates: int = 0
    snapped_start: int = 0
    snapped_end: int = 0
    snap_giveup: int = 0
    duration_fixed: int = 0
    gap_fixed: int = 0
    wrapped: int = 0
    snap_shift_ms: List[int] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"剔除 {self.dropped}", f"切分 {self.split}",
                 f"合并 {self.merged}",
                 f"重复保留 {self.duplicates}",
                 f"吸附起/止 {self.snapped_start}/{self.snapped_end}",
                 f"放弃吸附 {self.snap_giveup}",
                 f"时长修正 {self.duration_fixed}", f"间隙修正 {self.gap_fixed}",
                 f"换行 {self.wrapped}"]
        if self.snap_shift_ms:
            s = sorted(self.snap_shift_ms)
            parts.append(f"吸附位移中位 {s[len(s)//2]}ms")
        return "；".join(parts)


# ---------------------------------------------------------------- 1. 剔除

def _dedupe_repeats(text: str) -> str:
    """仅压缩**整段完全重复**的极限情况，不做短语级去重。

    为什么必须如此保守
    ------------------
    早期实现会把同一段内重复的短语压掉（"Gardening. Gardening." ->
    "Gardening."）。但本项目的素材是**英语跟读材料**，"Gardening. Gardening."
    是刻意跟读两遍的真实内容，对应波形上两个独立的语音起点（实测 11.698s
    与 11.698→13.208s）。压掉重复短语后，后半段的文本被并到前一个起点上，
    表现为"字幕比实际读音晚一整句"。实测第 5 条就是这样被破坏的。

    因此这里只在整段**逐字完全相同**时才折叠，宁可留下重复，也不要吃掉
    真实内容——重复的字幕在 Aboboo 里只是断点密一点，远好过文本错位。
    """
    parts = [p.strip() for p in re.split(r"(?<=[.!?。！？])\s*", text) if p.strip()]
    if len(parts) < 2:
        return text
    # 只有"整体就是同一句重复两遍"时才折叠为一遍
    half = len(parts) // 2
    if len(parts) % 2 == 0 and half > 0:
        if [p.lower() for p in parts[:half]] == [p.lower() for p in parts[half:]]:
            return " ".join(parts[:half])
    return text


def drop_invalid(segments: Sequence[Segment], cfg: FixConfig,
                 report: FixReport) -> List[Cue]:
    """第 1 步：剔除无效段。"""
    cues: List[Cue] = []
    prev_text = None
    for seg in segments:
        text = (seg.text or "").strip()
        if not text and not seg.words:
            report.dropped += 1
            continue
        if not text.strip(" \t\u3000"):
            report.dropped += 1
            continue
        if seg.end <= seg.start:
            report.dropped += 1
            continue
        if _CTRL_RE.search(text):
            text = _CTRL_RE.sub("", text)
        if cfg.drop_no_meaningful and not _MEANINGFUL_RE.search(text):
            report.dropped += 1
            continue

        if cfg.drop_hallucination_repeat:
            cleaned = _dedupe_repeats(text)
            if cleaned != text:
                log.debug("压缩完全重复文本：%r -> %r", text, cleaned)
                text = cleaned

        # 相邻整段文本完全相同：**只记提示，不自动删除**。
        # 跟读类素材里"同一句读两遍"是刻意设计、对应两个真实语音起点，
        # 自动删除会造成文本与语音错位；而真正的 Whisper 幻觉会因为没有
        # 对应的语音起点，在吸附阶段自然暴露（起点无法吸附）。把判断权
        # 交给用户，比静默删掉真实内容安全得多。
        if cfg.drop_hallucination_repeat and prev_text == text.lower():
            report.duplicates += 1
        prev_text = text.lower()

        cues.append(Cue(start=seg.start, end=seg.end, text=text,
                        words=list(seg.words)))
    if report.duplicates:
        report.issues.append(
            f"检测到 {report.duplicates} 处相邻重复文本，可能是跟读素材的刻意重复，"
            f"也可能是 Whisper 幻觉；已全部保留，请人工抽查")
    return cues


# ---------------------------------------------------------------- 2. 切分

def _assign_words_to_regions(words: Sequence[Word],
                             regions: Sequence[Tuple[float, float]]
                             ) -> List[List[Word]]:
    """把词按"中点落在哪条语音区间"分组，返回**按时间排序**的非空分组。

    用中点而不是起点：词的起点可能落在前一条区间的尾巴上（Whisper 偏早），
    用中点更稳健。

    返回分组按第一条词的时间升序排序 —— 这一点很重要：区间列表本身可能
    因过滤/合并而与词的时间顺序不一致，若直接按区间顺序输出，会把后面的
    句子排到前面，导致时间轴乱序。
    """
    if not regions:
        return []
    starts = [r[0] for r in regions]
    buckets: List[List[Word]] = [[] for _ in regions]
    for w in words:
        mid = (w.start + w.end) / 2.0
        i = bisect.bisect_right(starts, mid) - 1
        if i < 0:
            i = 0
        elif i >= len(regions):
            i = len(regions) - 1
        # 中点落在区间之间的静音里时，归给更近的一端
        while i < len(regions) - 1 and mid > regions[i][1]:
            nxt = i + 1
            if abs(mid - regions[i][1]) <= abs(mid - regions[nxt][0]):
                break
            i = nxt
        buckets[i].append(w)
    groups = [ws for ws in buckets if ws]
    groups.sort(key=lambda ws: ws[0].start)
    return groups


def _group_contiguous(words: Sequence[Word],
                      max_gap: float) -> List[List[Word]]:
    """把词序列按时间间隔切成连续短语。

    用途：处理"区间空间不一致"的残留情况 —— 当一段覆盖了多条语音区间，
    但按区间分派只得到一组词时，说明区间过滤/合并的粒度与实际语音不匹配。
    此时改用词之间的时间间隔来切分（间隔超过 max_gap 视为句间停顿），
    这样不会漏掉该切而未切的段（实测中正是漏切导致过时间轴乱序与重叠）。
    """
    if not words:
        return []
    groups = [[words[0]]]
    for w in words[1:]:
        if w.start - groups[-1][-1].end > max_gap:
            groups.append([w])
        else:
            groups[-1].append(w)
    return groups


def split_by_silence(cues: Sequence[Cue], boundaries: SpeechBoundaries,
                     cfg: FixConfig, report: FixReport) -> List[Cue]:
    """第 2 步：把跨越多条语音区间的 Whisper 段按静音切开。

    实测依据：本素材波形上有 123 个语句，而 Whisper 只输出 54 段。
    未切分的段起点落在句中，导致 5/54 条字幕偏差 ≥1s，无法对齐。

    切分依据词级时间戳把词分派到各语音区间；无词级数据时按字符数
    比例切分（尽力而为，并在报告里记为可疑）。
    """
    if not cfg.split_enabled or not boundaries.speech:
        return list(cues)

    # 只做"滤除噪声碎片"，**不做合并**。
    # 关键教训：判断"本段是否跨多句"与"把词分派到哪一句"必须用**同一个**
    # 区间空间。若一边用合并后的区间（较少）、一边用未合并的区间（较多），
    # 就会出现"判定为不需要切分、但词却分进了多条区间"的不一致，
    # 结果是把部分词留在原段里，造成时间轴乱序与重叠（实测 1600ms）。
    regions = filter_speech(boundaries.speech, cfg.min_speech)

    out: List[Cue] = []
    for cue in cues:
        # 与本段时间范围重叠的语音区间
        overlapping = [(a, b) for a, b in regions
                       if b > cue.start and a < cue.end]

        if cue.words:
            groups = _assign_words_to_regions(cue.words, overlapping)
            # 兜底：区间粒度不匹配时（分组数不足但段明显跨多句），
            # 改用词间停顿切分，避免漏切。
            if len(groups) < len(overlapping):
                by_gap = _group_contiguous(cue.words, cfg.split_gap)
                if len(by_gap) > len(groups):
                    groups = by_gap

            if len(groups) <= 1:
                out.append(cue)
                continue
            for ws in groups:
                text = "".join(w.text for w in ws).strip()
                if not text:
                    continue
                out.append(Cue(start=ws[0].start, end=ws[-1].end, text=text,
                               words=list(ws), origin="split"))
            report.split += len(groups) - 1
        else:
            # 无词级时间戳：按字符数比例分配时间轴（尽力而为）
            pieces = _split_text_proportional(cue.text, overlapping)
            if len(pieces) <= 1:
                out.append(cue)
                continue
            for reg, text in pieces:
                if not text.strip():
                    continue
                out.append(Cue(start=reg[0], end=reg[1], text=text.strip(),
                               origin="split"))
            report.split += len(pieces) - 1
            report.issues.append(
                f"段「{cue.text[:20]}…」缺少词级时间戳，按字符比例切分，需人工复核")

    # 切分可能打乱顺序，统一按时间重排后再进入吸附
    out.sort(key=lambda c: (c.start, c.end))
    return out


def _split_text_proportional(text: str,
                             regions: Sequence[Tuple[float, float]]
                             ) -> List[Tuple[Tuple[float, float], str]]:
    """没有词级时间戳时的兜底：按字符数把文本近似均分到各语音区间。"""
    words = text.split()
    if not words or not regions:
        return []
    n = len(regions)
    per = max(1, len(words) // n)
    out = []
    idx = 0
    for i, reg in enumerate(regions):
        take = per if i < n - 1 else len(words) - idx
        chunk = " ".join(words[idx:idx + take])
        idx += take
        if chunk:
            out.append((reg, chunk))
    return out


# ---------------------------------------------------------------- 3. 吸附

def snap_to_waveform(cues: Sequence[Cue], boundaries: SpeechBoundaries,
                     cfg: FixConfig, report: FixReport) -> List[Cue]:
    """第 3 步：把每段起止吸附到真实语音边界。

    关键：吸附到「>= 该起点的第一条语音区间的起点」，而不是"最近的起点"。
    实测证据：Whisper 的段起点绝大多数落在静音里且偏早，用"最近起点"会把
    字幕吸到**上一句**的开头（实测中位修正量 2403ms，完全不可用）。
    """
    if not boundaries.speech:
        return list(cues)

    # 锚点只取"可信语音段"：过滤掉过短的噪声碎片。
    # 实测 waveform 上有 24ms 的伪区间（268.409–268.433），它本身不是语句，
    # 却会被当成合法锚点，把本该结束于 268.093 的字幕终点拉到 268.433。
    # 锚点是"字幕起止的基准"，必须比切分用的边界更干净，所以阈值更严。
    anchors = filter_speech(boundaries.speech, cfg.min_anchor_speech)
    if not anchors:
        anchors = filter_speech(boundaries.speech, cfg.min_speech)
    if not anchors:
        return list(cues)

    onsets = [s for s, _ in anchors]
    offsets = [e for _, e in anchors]
    out: List[Cue] = []

    for cue in cues:
        # --- 起点吸附策略分两种，混淆会毁掉整条字幕：
        #
        # (a) Whisper 原始段（origin="asr"）：起点系统性偏早且落在静音里
        #     （实测中位 −92ms，波形上几乎都在静音中）。此时必须取
        #     "第一个 >= 起点的语音起点"，即**下一个**。
        #
        # (b) 切分产生的段（origin="split"）：起点已经取首词位置，与所属
        #     语音区间的起点只差几十毫秒，可能**略晚于**锚点。此时若还用
        #     "下一个"，会越过紧邻的正确锚点，吸到再下一句的开头。
        #     实测：11.720 被吸到 13.208（越过 11.698），该条被压成 0 时长。
        #     因此改为取**容差内最接近**的语音起点。
        region = None          # 起点所归属的语音区间，用于把终点也对齐过去

        # 规则 1（最可信）：起点落在某条语音区间内部 -> 就归属那一条。
        # 这比对"切分段/原始段"分别用不同规则更普适，也修掉了实测中的典型错误：
        # 「Project」起点 267.360 正好落在区间 [267.351, 268.093] 内部（偏晚 9ms），
        # 但早期按"最近的起点"选中了下一个噪声锚点 268.409，把该字推到了下一句
        # 开头，终点也被带偏 233ms。
        i = bisect.bisect_right(onsets, cue.start) - 1
        if i >= 0 and onsets[i] <= cue.start <= offsets[i]:
            na = onsets[i]
            region = (onsets[i], offsets[i])
            report.snapped_start += 1
            report.snap_shift_ms.append(int(round((na - cue.start) * 1000)))
        else:
            # 规则 2：起点落在静音里。
            #   (a) Whisper 原始段：起点系统性偏早、几乎都在静音中，
            #       取"第一个 >= 起点的语音起点"，即**下一个**锚点。
            #   (b) 切分产生的段：起点已取首词位置，可能略晚于锚点，
            #       取容差内**最接近**的锚点，避免越过紧邻的正确锚点
            #       （实测 11.720 若取"下一个"会跳到 13.208）。
            if cue.origin == "split":
                k = bisect.bisect_left(onsets, cue.start)
                cands = [x for x in (k - 1, k) if 0 <= x < len(onsets)]
                na, dist, best = cue.start, None, None
                for x in cands:
                    d = abs(onsets[x] - cue.start)
                    if d <= cfg.snap_tolerance and (dist is None or d < dist):
                        na, dist, best = onsets[x], d, x
                if best is not None:
                    region = (onsets[best], offsets[best])
                    report.snapped_start += 1
                    report.snap_shift_ms.append(int(round((na - cue.start) * 1000)))
                else:
                    report.snap_giveup += 1
            else:
                k = bisect.bisect_left(onsets, cue.start)
                na = cue.start
                if k < len(onsets) and (onsets[k] - cue.start) <= cfg.snap_tolerance:
                    na = onsets[k]
                    region = (onsets[k], offsets[k])
                    report.snapped_start += 1
                    report.snap_shift_ms.append(int(round((na - cue.start) * 1000)))
                else:
                    report.snap_giveup += 1

        # --- 终点吸附
        #
        # 首选：把终点对齐到**起点所属那条语音区间的结束**。
        # 这是修正"字幕消失得比声音早"的关键 —— Whisper 的段尾系统性偏早
        # （实测全片 103/109 条的真实语音终点更晚，88 条早结束 >50ms，
        # 最大 337ms），而旧条件只接受"不晚于字幕末端"的语音终点，
        # 恰好把每个更晚的真实终点都排除掉了，于是字幕总是提前消失。
        #
        # 字幕的职责是覆盖整句话，所以终点应当延长到该语音区间的结束。
        nb = cue.end
        if region is not None:
            cand = region[1]
            if cand >= cue.end and (cand - cue.end) <= cfg.snap_tolerance:
                if cand - na <= cfg.max_duration_ms / 1000.0:
                    nb = cand
                    report.snapped_end += 1
        if nb == cue.end:
            # 兜底：没有归属区间时，退化为"取 <= 终点的最近语音终点"
            j = bisect.bisect_right(offsets, cue.end)
            if j > 0:
                cand = offsets[j - 1]
                if cand > na and (cue.end - cand) <= cfg.snap_tolerance:
                    nb = cand
                    report.snapped_end += 1
        out.append(Cue(start=na, end=max(nb, na), text=cue.text,
                       words=cue.words, origin=cue.origin))

    # 吸附后若产生重叠或零时长，说明锚点冲突：把受影响的段还原为吸附前的
    # 时间，交给 enforce_constraints 去腾间隙。宁可少吸附，也不要产生
    # 零时长或错误归属。
    fixed: List[Cue] = []
    prev_end = None
    for orig, sn in zip(cues, out):
        if prev_end is not None and (sn.start < prev_end or sn.end <= sn.start):
            report.issues.append(
                f"段「{sn.text[:18]}…」吸附后与相邻段冲突，已还原原始时间")
            sn = Cue(start=orig.start, end=orig.end, text=orig.text,
                     words=orig.words, origin=orig.origin)
        fixed.append(sn)
        prev_end = sn.end
    return fixed


# ---------------------------------------------------------------- 4/5. 约束

def enforce_constraints(cues: Sequence[Cue], cfg: FixConfig,
                        report: FixReport) -> List[Cue]:
    """第 4 步：强制不重叠、间隙达标、时长不超上限。

    优先级原则（很关键）
    --------------------
    下一句的**起点**是吸附得到的真实语音锚点，是 Aboboo 断点的依据，**可信且不动**；
    上一句的**结束**是为了不遮挡下一句而存在的，应当避让。

    因此重叠时修正 ``a.end``，而不是像早期实现那样"朝中点靠拢"——后者会把
    两条各压一半，实测把边界压成 0 时长（"结束时间不大于起始时间"）。
    若避让后 a 已无长度可言，说明这条字幕本身就是碎片，直接剔除更诚实：
    零时长的字幕在 Aboboo 里只会产生一个无意义的断点。

    另外，必须先按时间排序再处理相邻关系：切分可能产生乱序，而"只修相邻
    两两之间"对乱序列表完全无效（实测出现过 1600ms 重叠与起点逆序）。
    """
    items = [Cue(c.start, c.end, c.text, list(c.words), c.origin) for c in cues]
    items.sort(key=lambda c: (c.start, c.end))
    min_gap = cfg.min_gap_ms / 1000.0
    max_dur = cfg.max_duration_ms / 1000.0

    for _ in range(8):
        changed = False
        out: List[Cue] = []
        for c in items:
            if out and c.start - out[-1].end < min_gap:
                prev = out[-1]
                # 优先"合并"而不是"丢弃"。
                # 教训：早期实现让前一条避让后一条的起点，在后一条起点不可信
                # （过度切分产生的碎片）时会把整句话吃掉 —— 实测丢失 40 个词次。
                # 合并能同时保住文本与时间轴。
                #
                # 注意：合并后 **必须 continue，不能把 c 再 append 一次** ——
                # c 的文本已被并入 prev，若仍 append 就造成内容重复
                # （实测词数从 442 膨胀到 1185）。
                if c.end - prev.start <= max_dur:
                    prev.text = f"{prev.text.strip()} {c.text.strip()}".strip()
                    prev.end = max(prev.end, c.end)
                    if c.words or prev.words:
                        prev.words = list(prev.words) + list(c.words)
                    report.merged += 1
                    changed = True
                    continue
                # 合并会超长 -> 只能腾出间隙；实在腾不出才丢弃
                allowed_end = c.start - min_gap
                if allowed_end <= prev.start:
                    out.pop()
                    report.dropped += 1
                else:
                    prev.end = allowed_end
                    report.gap_fixed += 1
                changed = True
            out.append(c)
        items = out

        for c in items:                     # 只修过长；过短交给自检提示
            if c.end - c.start > max_dur:
                c.end = c.start + max_dur
                report.duration_fixed += 1
                changed = True
        if not changed:
            break

    # 结构性不变量：任何非正时长的条目都不可交付（Aboboo 无法定位零长度
    # 断点）。放在最后统一清除，作为兜底 —— 前面的间隙修正用的是
    # "间隔 < min_gap" 判断，当间隔恰好等于 min_gap 时不会触发，
    # 实测正是这个边界条件漏掉了一条零时长字幕（268.409 -> 268.409）。
    before = len(items)
    items = [c for c in items if c.end > c.start]
    report.dropped += before - len(items)

    items.sort(key=lambda c: (c.start, c.end))
    return items


def wrap_lines(cues: Sequence[Cue], cfg: FixConfig,
               report: FixReport) -> List[Cue]:
    """第 5 步：行长/行数约束。

    在词边界换行，避免把英文单词劈成两半 —— 否则会破坏 Aboboo 的
    "字幕取词"与跟读功能。
    """
    out: List[Cue] = []
    for cue in cues:
        text = " ".join(cue.text.split())
        if not text:
            out.append(cue)
            continue
        if len(text) <= cfg.max_line_chars:
            out.append(Cue(cue.start, cue.end, text, cue.words, cue.origin))
            continue

        lines = _wrap_words(text, cfg.max_line_chars, cfg.max_lines)
        if not lines:
            out.append(Cue(cue.start, cue.end, text, cue.words, cue.origin))
            continue
        new_text = "\n".join(lines)
        if new_text != text:
            report.wrapped += 1
        out.append(Cue(cue.start, cue.end, new_text, cue.words, cue.origin))
    return out


def _wrap_words(text: str, max_chars: int, max_lines: int) -> List[str]:
    """按词边界换行。"""
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    # 超过最大行数时，把多余内容并回最后一行（宁可超长也不要丢字）
    if len(lines) > max_lines:
        keep = lines[:max_lines - 1]
        keep.append(" ".join(lines[max_lines - 1:]))
        lines = keep
    return lines


# ---------------------------------------------------------------- 输出与自检

def to_srt(cues: Sequence[Cue]) -> str:
    """序列化为 SRT 文本。

    刻意输出 **UTF-8 无 BOM + LF**：BOM 在 Aboboo 文本模式可能渲染成乱码；
    seconv 默认输出带 BOM，我们不走它的默认值。
    """
    def ts(sec: float) -> str:
        ms = max(0, int(round(sec * 1000)))
        h, r = divmod(ms, 3600000)
        m, r = divmod(r, 60000)
        s, ms = divmod(r, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for n, c in enumerate(cues, 1):
        blocks.append(f"{n}\n{ts(c.start)} --> {ts(c.end)}\n{c.text.strip()}\n")
    return "\n".join(blocks)


def self_check(cues: Sequence[Cue], cfg: FixConfig) -> List[str]:
    """输出前的硬性自检。绝不允许把带重叠/逆序/零时长的字幕交出去。

    只报**真正破坏 Aboboo 对齐**的问题：
      · 重叠    —— Aboboo 用时间戳做断点，重叠会直接导致断点错乱
      · 逆序    —— 时间轴非单调
      · 空文本  —— 产生无内容断点
      · 零/负时长 —— 产生零长度断点，Aboboo 无法定位
    偏短（< min_duration_ms）不算失败：短句本就是素材特征，不影响对齐，
    由 ``advisories()`` 作为提示给出。
    """
    problems: List[str] = []
    for i, c in enumerate(cues):
        if not c.text.strip():
            problems.append(f"第 {i+1} 条文本为空")
        if c.end <= c.start:
            problems.append(
                f"第 {i+1} 条结束时间不大于起始时间"
                f"（{c.start:.3f} -> {c.end:.3f}）")
    for i in range(len(cues) - 1):
        a, b = cues[i], cues[i + 1]
        if a.end > b.start + 1e-6:
            problems.append(
                f"第 {i+1}/{i+2} 条重叠 {(a.end-b.start)*1000:.0f}ms")
        if b.start < a.start - 1e-6:
            problems.append(f"第 {i+2} 条起始时间早于第 {i+1} 条")
    return problems


def advisories(cues: Sequence[Cue], cfg: FixConfig) -> List[str]:
    """非致命的提示项，不影响可交付性。"""
    notes: List[str] = []
    short = [i + 1 for i, c in enumerate(cues)
             if 0 < c.duration_ms < cfg.min_duration_ms]
    if short:
        notes.append(f"{len(short)} 条字幕短于 {cfg.min_duration_ms}ms"
                     f"（短句特征，不影响对齐）")
    long_ = [i + 1 for i, c in enumerate(cues)
             if c.duration_ms > cfg.max_duration_ms]
    if long_:
        notes.append(f"{len(long_)} 条字幕超过 {cfg.max_duration_ms}ms")
    return notes


def verify_against_baseline(cues: Sequence[Cue],
                            baseline: Sequence[Cue]) -> List[str]:
    """内容保真检查：确认校正没有让音频与文本错位。

    时间戳全部合法、格式完全正确，但文本被挪到了别的句子上 —— 这是本模块
    最危险的一类 bug，听感上表现为"字幕和语音对不上"，且所有结构检查都会通过。
    """
    problems: List[str] = []
    cur = [" ".join(c.text.split()) for c in cues]
    base = [" ".join(c.text.split()) for c in baseline]

    if cur == base:
        return problems

    common_cur = [t for t in cur if t in set(base)]
    common_base = [t for t in base if t in set(cur)]
    if common_cur and common_base and common_cur != common_base:
        problems.append("段落顺序相对基线发生变化 —— 音频与文本错位的典型信号")

    from difflib import SequenceMatcher
    a = " ".join(base)
    b = " ".join(cur)
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio < 0.85:
        problems.append(f"与基线文本相似度仅 {ratio:.1%}，文本可能被大规模改动")
    return problems


# ---------------------------------------------------------------- 主流程

def fix_subtitles(segments: Sequence[Segment], boundaries: SpeechBoundaries,
                  cfg: Optional[FixConfig] = None,
                  progress_cb: Optional[ProgressCb] = None) -> Tuple[List[Cue], FixReport]:
    """执行完整校正流水线（顺序不可调换）。"""
    cfg = cfg or FixConfig()
    report = FixReport()

    def step(p: float, msg: str):
        if progress_cb:
            progress_cb(p, msg)

    step(0.00, "剔除无效段")
    cues = drop_invalid(segments, cfg, report)
    baseline = list(cues)          # 供内容保真比对

    step(0.25, "按静音切分合并段")
    cues = split_by_silence(cues, boundaries, cfg, report)

    step(0.50, "吸附到真实语音边界")
    cues = snap_to_waveform(cues, boundaries, cfg, report)

    step(0.70, "强制时长与间隙约束")
    cues = enforce_constraints(cues, cfg, report)

    step(0.85, "行长约束")
    cues = wrap_lines(cues, cfg, report)

    step(0.95, "校验")
    problems = self_check(cues, cfg)
    # 内容保真只在"没有切分"时才要求序列完全一致；
    # 切分后条数必然变化，所以这里只做顺序与相似度告警。
    if report.split == 0:
        problems += verify_against_baseline(cues, baseline)
    else:
        problems += [p for p in verify_against_baseline(cues, baseline)
                     if "顺序" in p]
    report.issues += problems
    step(1.0, "校正完成")
    return cues, report


def write_srt(cues: Sequence[Cue], path: str) -> str:
    """写出 SRT（UTF-8 无 BOM，LF 换行）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_srt(cues), encoding="utf-8", newline="\n")
    return str(p)


def read_srt_cues(path: str) -> List[Cue]:
    """极简 SRT 解析，仅用于"规范化结果是否可用"的校验。

    刻意不依赖 pysrt 或 scripts/ 下的工具：model 层不应反向依赖 scripts，
    也不该为了校验而引入额外依赖。这里只需要时间戳与文本，格式不合规的行
    直接跳过（校验场景下，解析不出内容本身就意味着结果不可用）。
    """
    try:
        raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: List[Cue] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or not s.isdigit():
            i += 1
            continue
        i += 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        try:
            a_s, b_s = [x.strip() for x in lines[i].split("-->")][:2]
            start = _ts_to_sec(a_s)
            end = _ts_to_sec(b_s)
        except Exception:
            i += 1
            continue
        i += 1
        body: List[str] = []
        while i < len(lines) and lines[i].strip() != "":
            body.append(lines[i].rstrip())
            i += 1
        cues.append(Cue(start=start, end=end, text="\n".join(body)))
    return cues


def _ts_to_sec(s: str) -> float:
    h, m, rest = s.split(":")
    sec, ms = rest.split(",")
    return (int(h) * 3600 + int(m) * 60 + int(sec)) + int(ms) / 1000.0


def normalize_with_seconv(srt_path: str, cfg: FixConfig,
                          progress_cb: Optional[ProgressCb] = None) -> str:
    """用 Subtitle Edit（seconv）做最后的规范化 —— 需求第 3 条。

    为什么放在最后、且默认保守
    --------------------------
    波形吸附完成后时间戳已精确对齐（实测 99% 命中、残差中位 0ms）。
    seconv 的价值在于**字幕工程规范收尾**，而不是主校正：
    ``--offset`` 只能整体平移，而 Whisper 偏差是逐句不一致的（标准差 133ms），
    整体平移解决不了。

    安全设计：
      · 先备份当前文件，规范化后**重新解析校验**；条数变少、出现重叠、
        时间轴逆序等任一情况，就回滚到规范化之前的版本。
        字幕是本模块的核心产物，宁可放弃规范化也不能交出坏结果。
      · seconv 不可用或失败时静默降级（只记日志），不影响主流程。
    """
    if not cfg.seconv_enabled:
        return srt_path

    from .seconv_tools import SeconvConfig, find_seconv, run_seconv

    if find_seconv() is None:
        log.info("seconv 不可用，跳过 Subtitle Edit 规范化（不影响主流程）")
        return srt_path

    src = Path(srt_path)
    before = src.read_bytes()
    before_cues = read_srt_cues(str(src))
    if not before_cues:
        return srt_path

    if progress_cb:
        progress_cb(0.0, "Subtitle Edit 规范化")

    scfg = SeconvConfig(
        enabled=True,
        min_gap_ms=(cfg.seconv_min_gap_ms if cfg.seconv_min_gap_ms is not None
                    else cfg.min_gap_ms),
        apply_duration_limits=cfg.seconv_apply_duration_limits,
        split_long_lines=cfg.seconv_split_long_lines,
        balance_lines=cfg.seconv_balance_lines,
        fix_common_errors=cfg.seconv_fix_common_errors,
    )

    tmp_out = src.with_name(src.stem + ".se.srt")
    try:
        produced = run_seconv(str(src), str(tmp_out), scfg)
    except Exception as e:
        log.warning("seconv 规范化异常，保留原文件：%s", e)
        return srt_path

    if produced == str(src) or not Path(produced).is_file():
        return srt_path

    # --- 校验规范化结果，不合格就回滚
    after_cues = read_srt_cues(produced)
    problems: List[str] = []
    if not after_cues:
        problems.append("规范化后无法解析")
    else:
        if len(after_cues) < len(before_cues):
            problems.append(f"条数从 {len(before_cues)} 减少到 {len(after_cues)}")
        for i in range(len(after_cues) - 1):
            if after_cues[i].end > after_cues[i + 1].start:
                problems.append(f"第 {i+1}/{i+2} 条出现重叠")
                break
        for i in range(len(after_cues) - 1):
            if after_cues[i + 1].end < after_cues[i].end:
                problems.append(f"第 {i+2} 条时间轴逆序")
                break

    if problems:
        log.warning("seconv 规范化结果未通过校验，已回滚：%s", "；".join(problems[:3]))
        src.write_bytes(before)
        try:
            Path(produced).unlink()
        except OSError:
            pass
        return srt_path

    # 通过校验：用规范化结果覆盖正式产物
    shutil.copyfile(produced, src)
    try:
        Path(produced).unlink()
    except OSError:
        pass

    # seconv **强制输出 CRLF**（实测：与输入换行无关，见
    # scripts/_probe_seconv_eol.py 的验证），而本项目其余环节统一用 LF，
    # 且用户已按 LF 验收过。这里统一回 LF，避免交付格式因"是否启用 seconv"而变。
    data = src.read_bytes()
    if b"\r\n" in data:
        src.write_bytes(data.replace(b"\r\n", b"\n"))

    # 复核最终字节：编码与换行都要符合规范
    final = src.read_bytes()
    if final[:3] == b"\xef\xbb\xbf":
        log.warning("规范化后出现 BOM，正在移除")
        src.write_bytes(final[3:])
    if b"\r\n" in src.read_bytes():
        log.warning("仍存在 CRLF，换行规范化未完全生效")

    log.info("Subtitle Edit 规范化已应用（%d 条）", len(after_cues))
    return srt_path
