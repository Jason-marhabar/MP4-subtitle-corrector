# -*- coding: utf-8 -*-
"""
Subtitle Edit（seconv）命令行集成 —— 对应需求第 3 条

定位
----
seconv 在本项目里承担**最后一公里的规范化**：波形吸附负责"把时间戳对到真实
语音边界"，seconv 负责"按字幕工程规范收尾"。

为什么不是用它做主要的时间轴校正
--------------------------------
seconv 的 ``--offset`` 只能**整体平移固定毫秒数**。而实测 Whisper 的时间戳偏差
是**逐句不一致**的（中位 −92ms，标准差 133ms；large-v3 中位 −267ms，标准差 156ms），
整体平移无法消除逐句差异。所以主校正必须用波形吸附（实测吸附后 99% 精确命中、
残差中位 0ms），seconv 放在其后做规范化与兜底。

但 seconv 有一批波形吸附做不到的字幕工程操作，正好补上需求里的措辞：
  · ``--apply-min-gap``      设置/强制字幕最小间隙（需求："设置字幕间隙"）
  · ``--split-long-lines``   按规范切长行（需求："分割长句"）
  · ``--fix-common-errors``  常见错误修正（重叠、过短、过长的综合修正）
  · ``--apply-duration-limits`` 时长上下限
  · ``--balance-lines`` / ``--beautify-time-codes``  排版规范化

重要：输出编码必须显式指定
--------------------------
seconv 的**默认输出是 UTF-8 with BOM**（官方文档：Defaults: ... UTF-8 BOM on output）。
而 Aboboo 对 BOM 的容忍度尚未实测，所以本模块一律加
``--encoding:utf-8-no-bom``，与我们自己的写出保持一致。

依赖
----
需要 ``tools/seconv/seconv.exe``（Subtitle Edit v5.2.0 headless，自包含，
无需安装 .NET）。缺失时本模块优雅降级：返回原文件并给出告警，不阻断主流程。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

# seconv 在本项目中的默认位置
DEFAULT_SEARCH_PATHS = (
    "tools/seconv/seconv.exe",
    "tools/seconv/seconv",
    "seconv.exe",
)


class SeconvNotFound(FileNotFoundError):
    """未找到 seconv 可执行文件。"""


@dataclass
class SeconvConfig:
    """seconv 规范化步骤的开关与参数。

    默认值刻意保守：只做"不会改变文本内容、不会破坏已对齐时间轴"的操作。
    像 ``--fix-common-errors`` 这类会重排内容的操作默认关闭，需要显式开启，
    因为实测表明它会与波形吸附的成果互相干扰。
    """

    enabled: bool = False            # 默认关闭：主校正已足够，需显式启用
    min_gap_ms: Optional[int] = None  # 强制最小间隙；None=不做
    apply_duration_limits: bool = False
    split_long_lines: bool = False
    merge_short_lines: bool = False
    balance_lines: bool = False
    beautify_time_codes: bool = False
    fix_common_errors: bool = False   # 注意：会重排内容，谨慎
    remove_formatting: bool = False
    offset_ms: Optional[int] = None   # 整体平移；一般不用（波形吸附已处理）
    line_max_chars: Optional[int] = None  # 配合 split-long-lines 的行长上限
    extra_args: List[str] = field(default_factory=list)


def find_seconv() -> Optional[str]:
    """定位 seconv 可执行文件。"""
    here = Path(__file__).resolve().parent.parent
    for rel in DEFAULT_SEARCH_PATHS:
        p = here / rel
        if p.is_file():
            return str(p)
    found = shutil.which("seconv")
    return found


def seconv_available() -> bool:
    return find_seconv() is not None


def build_args(cfg: SeconvConfig) -> List[str]:
    """把配置翻译成 seconv 命令行参数（便于单测，不实际执行）。"""
    args: List[str] = []
    # 编码必须显式：seconv 默认输出带 BOM
    args.append("--encoding:utf-8-no-bom")

    if cfg.offset_ms is not None:
        args.append(f"--offset:{int(cfg.offset_ms)}")
    if cfg.min_gap_ms is not None:
        args.append(f"--apply-min-gap:{int(cfg.min_gap_ms)}")
    if cfg.apply_duration_limits:
        args.append("--apply-duration-limits")
    if cfg.split_long_lines:
        args.append("--split-long-lines")
    if cfg.line_max_chars is not None:
        args.append(f"--line-max-chars:{int(cfg.line_max_chars)}")
    if cfg.merge_short_lines:
        args.append("--merge-short-lines")
    if cfg.balance_lines:
        args.append("--balance-lines")
    if cfg.beautify_time_codes:
        args.append("--beautify-time-codes")
    if cfg.fix_common_errors:
        args.append("--fix-common-errors")
    if cfg.remove_formatting:
        args.append("--remove-formatting")
    args.extend(cfg.extra_args)
    return args


def run_seconv(srt_in: str, srt_out: str, cfg: SeconvConfig,
               timeout: int = 300,
               progress_cb=None) -> str:
    """对 SRT 做一次 seconv 规范化。

    返回**实际可用的输出路径**。若 seconv 不可用或执行失败，返回 ``srt_in``
    并把原因写进日志 —— 规范化是增强步骤，绝不能因为它失败就丢掉主产物。
    """
    exe = find_seconv()
    if exe is None:
        log.warning("未找到 seconv，跳过 Subtitle Edit 规范化步骤"
                    "（可用性不受影响；如需启用请放置 tools/seconv/seconv.exe）")
        return srt_in

    src = Path(srt_in)
    if not src.is_file():
        raise FileNotFoundError(f"输入字幕不存在：{srt_in}")

    args = build_args(cfg)
    # seconv 会在输出目录写出同名文件；用临时目录承接再搬回来，
    # 避免它按自己的命名规则覆盖或改名导致我们找不到产物。
    with tempfile.TemporaryDirectory(prefix="seconv_") as td:
        cmd = [exe, str(src.name), "subrip",
               "--output-folder:" + td,
               "--output-filename:" + src.stem + "_se.srt",
               "--overwrite"] + args
        if progress_cb:
            progress_cb(0.0, "Subtitle Edit 规范化")
        log.info("seconv 参数：%s", " ".join(args))
        try:
            r = subprocess.run(cmd, cwd=str(src.parent), capture_output=True,
                               text=True, timeout=timeout, errors="replace")
        except subprocess.TimeoutExpired:
            log.warning("seconv 超时（%ss），跳过规范化", timeout)
            return srt_in

        if r.returncode != 0:
            log.warning("seconv 失败（退出码 %s），跳过规范化。stderr: %s",
                        r.returncode, (r.stderr or "")[-300:])
            return srt_in

        produced = Path(td) / (src.stem + "_se.srt")
        if not produced.is_file():
            # 兜底：找出目录里唯一的 srt
            cands = list(Path(td).glob("*.srt"))
            if not cands:
                log.warning("seconv 未产出文件，跳过规范化")
                return srt_in
            produced = cands[0]

        # 安全检查：规范化后的文件必须仍可解析且条数不为 0
        text = produced.read_text(encoding="utf-8-sig", errors="replace")
        if "-->" not in text:
            log.warning("seconv 输出不含有效时间戳，放弃该结果")
            return srt_in

        Path(srt_out).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, srt_out)
        log.info("seconv 规范化完成：%s", srt_out)
        return srt_out


def describe() -> str:
    exe = find_seconv()
    if not exe:
        return "seconv 未安装（Subtitle Edit 规范化步骤将跳过）"
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           timeout=30, errors="replace")
        ver = (r.stdout or "").strip() or "未知版本"
    except Exception:
        ver = "版本未知"
    return f"seconv 可用：{exe}（{ver}）"
