# -*- coding: utf-8 -*-
"""
Aboboo 字幕兼容性探针（第四板块前置实验，不依赖任何第三方库）

目的：把"Aboboo 字幕对齐"这个未知项变成可验证的实验。
     需求文档只说"字幕时间戳用作断点"，并未规定编码 / BOM / 间隔 / 行长等硬规则，
     这些必须实测确定，否则第四板块的校正参数只能靠猜。

用法（在项目根目录执行）：
    python scripts/probe_aboboo.py                       # 默认素材 test.mp4
    python scripts/probe_aboboo.py <视频路径> [输出目录]
    python scripts/probe_aboboo.py --srt-only            # 只生成 SRT，快速自检用

产出：test_output/probe/ 下
    <stem>_A_utf8.srt          UTF-8 无 BOM，标准格式，留 200ms 间隙  ← 基准
    <stem>_B_utf8bom.srt       UTF-8 带 BOM
    <stem>_C_gbk.srt           GBK（ANSI）
    <stem>_D_overlap.srt       段落互相重叠 300ms，含 1 处 end<start
    <stem>_E_shift0.srt        时间戳整体提前 500ms（模拟 whisper 提前量）
    <stem>_F_nogap.srt         段间零间隙且紧贴
    <stem>_G_longline.srt      单行超长文本（>80 字符）
    <stem>_H_blank.srt         含空文本段、只有空白的段、时长 0ms 的段

外加两个测试用 MP4（从原视频各截 90 秒，-c copy 不编码，秒级完成）：
    <stem>_remux.mp4           纯流拷贝封装（验证 -c copy 是否真的不产生音画偏移）
    <stem>_norm.mp4            音频 loudnorm 标准化封装

观察要点见脚本末尾打印的"实验记录表"。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认代码页常为 GBK/936，直接 print 非 GBK 字符（→ ✓ 等）会抛
# UnicodeEncodeError 并中断脚本。这里把输出流切到 UTF-8，无法编码的字符降级为 ?，
# 保证脚本在重定向、CI、不同区域设置的机器上都不会因为打印而失败。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------- 时间轴小工具

def ts(seconds: float) -> str:
    """秒 -> SRT 时间戳 hh:mm:ss,mmm（对总毫秒取整，避免 59.9995 -> 60 的越界）"""
    total_ms = int(round(seconds * 1000))
    if total_ms < 0:
        total_ms = 0
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(cues) -> str:
    """cues: [(start_s, end_s, text), ...] -> SRT 文本"""
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{ts(start)} --> {ts(end)}\n{text}\n")
    return "\n".join(blocks)


def sample_cues(n=6):
    """针对 test.mp4（313 秒）取 6 个时间点，文本用中英混排以同时检验编码与断句。

    注意：第 2 个锚点特意取 16.0 而不是 30.0 —— 变体 D 需要把第 2 段前移 2.7s
    来制造"与第 1 段重叠 300ms"。若锚点间距过大（如 30s），前移后根本不会重叠，
    该变体就失去了验证意义。
    """
    anchors = [2.0, 16.0, 80.0, 140.0, 210.0, 280.0]
    cues = []
    for i, a in enumerate(anchors[:n], 1):
        text = f"Probe line {i}. 第{i}行中文字幕。"
        cues.append((a, a + 3.0, text))
    return cues


def write_variants(out_dir: Path, stem: str) -> dict:
    base = sample_cues()
    written = {}

    def dump(tag, text, encoding, use_bom):
        p = out_dir / f"{stem}_{tag}.srt"
        with open(p, "w", encoding=encoding, newline="\n") as f:
            if use_bom:
                f.write("\ufeff")
            f.write(text)
        written[tag] = p
        return p

    # A 基准：UTF-8 无 BOM，标准 3 秒长句，段间 200ms 间隙
    dump("A_utf8", build_srt(base), "utf-8", False)

    # B 带 BOM —— 排查 Aboboo 是否会把 BOM 渲染成首字符乱码
    dump("B_utf8bom", build_srt(base), "utf-8", True)

    # C GBK —— 排查非 Unicode 程序把 UTF-8 当 ANSI 解读导致的乱码
    dump("C_gbk", build_srt(base), "gbk", False)

    # D 重叠 + 非法时间戳（end < start）：Aboboo 的断点如何处理重叠
    d = [(a, b, t) for a, b, t in base]
    # 让第 2 段直接压到第 1 段上，制造 300ms 重叠。
    # 注意必须把起始时间基于**上一段的实际结束时间**来算，而不是"前移若干秒"——
    # 后者只有在锚点间距足够近时才会真的重叠，否则该变体形同虚设。
    d[1] = (d[0][1] - 0.3, d[1][1], d[1][2])
    # 第 3 段造一个 end < start 的非法区间
    d[2] = (base[2][0], base[2][0] - 0.5, base[2][2])
    dump("D_overlap", build_srt(d), "utf-8", False)

    # E 整体提前 500ms —— 校准 whisper 时间戳是否普遍偏早
    e = [(max(0.0, a - 0.5), b - 0.5, t) for a, b, t in base]
    dump("E_shift0", build_srt(e), "utf-8", False)

    # F 零间隙且首尾紧贴 —— 排查 Aboboo 是否因无间隙而合并相邻字幕
    f = []
    cursor = 2.0
    for _, _, t in base:
        f.append((cursor, cursor + 3.0, t))
        cursor += 3.0            # 上一段结束 == 下一段开始
    dump("F_nogap", build_srt(f), "utf-8", False)

    # G 超长单行 —— 排查 Aboboo 文本模式下的截断/换行
    long_text = ("This is a deliberately overlong subtitle line used to probe "
                 "how Aboboo wraps or clips a single line that exceeds eighty "
                 "characters in text rendering mode.")
    g = [(a, a + 4.0, long_text if i == 1 else t)
         for i, (a, _, t) in enumerate(base, 1)]
    dump("G_longline", build_srt(g), "utf-8", False)

    # H 空文本 / 纯空白 / 零时长 —— 排查无效段的处理（校正模块要剔除的对象）
    h = list(base)
    h[1] = (base[1][0], base[1][1], "")          # 空文本
    h[2] = (base[2][0], base[2][1], "   ")       # 纯空白
    h[3] = (base[3][0], base[3][0], base[3][2])  # 零时长
    dump("H_blank", build_srt(h), "utf-8", False)

    return written


# ---------------------------------------------------------------- MP4 制造

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败：{' '.join(cmd)}\n{r.stderr[:600]}")
    return r


def make_mp4_pair(ffmpeg: str, ffprobe: str, src: Path, out_dir: Path, stem: str):
    made = []
    # 纯流拷贝：这是第 3 板块已经实现的路径，检验它是否真能保持音画同步
    remux = out_dir / f"{stem}_remux.mp4"
    run([ffmpeg, "-y", "-i", str(src), "-t", "90",
         "-c", "copy", "-movflags", "+faststart", str(remux)])
    made.append(remux)

    # 音频标准化：显式锁定 44100Hz，避免 loudnorm 默认重采样到 96kHz
    norm = out_dir / f"{stem}_norm.mp4"
    run([ffmpeg, "-y", "-i", str(src), "-t", "90",
         "-c:v", "copy",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ar", "44100", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(norm)])
    made.append(norm)

    # 打印两条输出的音频参数与时长，供比对是否与源一致
    for f in made:
        p = run([ffprobe, "-v", "error",
                 "-show_entries", "format=duration",
                 "-show_entries", "stream=codec_type,codec_name,sample_rate,channels",
                 "-of", "default=noprint_wrappers=1", str(f)])
        print(f"  [probe] {f.name}\n    " + p.stdout.strip().replace("\n", "\n    "))
    return made


# ---------------------------------------------------------------- main

def main():
    root = Path(__file__).resolve().parent.parent
    argv = [a for a in sys.argv[1:] if a != "--srt-only"]
    srt_only = "--srt-only" in sys.argv
    src = Path(argv[0]) if len(argv) > 0 else root / "test.mp4"
    out_dir = Path(argv[1]) if len(argv) > 1 else root / "test_output" / "probe"

    if not src.exists():
        print(f"[x] 素材不存在：{src}")
        sys.exit(1)

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not srt_only and (not ffmpeg or not ffprobe):
        print("[x] 未找到 ffmpeg/ffprobe，请确认已加入 PATH")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    print("=" * 64)
    print(f"素材：{src}")
    print(f"输出：{out_dir}")
    print("=" * 64)

    print("\n[1/2] 生成 8 种 SRT 变体")
    written = write_variants(out_dir, stem)
    for tag, p in written.items():
        print(f"  ✓ {p.name:<34} {p.stat().st_size:>7} bytes")

    print("\n[2/2] 生成 2 个测试 MP4（各 90 秒，-c copy 不重新编码）")
    if srt_only:
        print("  (--srt-only：已跳过)")
    else:
        make_mp4_pair(ffmpeg, ffprobe, src, out_dir, stem)

    print("\n" + "=" * 64)
    print("实验记录表（逐个导入 Aboboo，把观察结果填进最后一列）")
    print("=" * 64)
    rows = [
        ("A_utf8",     "UTF-8 无 BOM 基准",     "文本正常、断点对齐 → 它就是标准输出格式"),
        ("B_utf8bom",  "UTF-8 带 BOM",          "首行是否出现 ﻿ 乱码字符？→ 决定要不要 -no-bom"),
        ("C_gbk",      "GBK 编码",              "是否整片乱码？→ 确认必须强制 UTF-8"),
        ("D_overlap",  "重叠 + end<start",      "重叠段是否丢句/时间轴错乱？→ 决定必须消重叠"),
        ("E_shift0",   "整体提前 500ms",         "断点是否系统性偏早？→ 校准 whisper 提前量"),
        ("F_nogap",    "零间隙紧贴",             "相邻段是否被合并？→ 决定最小间隙阈值"),
        ("G_longline", "超长单行",               "是否溢出/截断？→ 决定行长上限"),
        ("H_blank",    "空文本/零时长",           "是否报错或产生空断点？→ 决定必须剔除"),
    ]
    print(f"{'文件':<12}{'变量':<22}{'观察目的'}")
    print("-" * 64)
    for tag, var, obs in rows:
        print(f"{tag:<12}{var:<22}{obs}")
    print("-" * 64)
    print("""
另外两个 MP4 的验证方法：
  1. 分别用 <stem>_remux.mp4 / <stem>_norm.mp4 配 A_utf8.srt 导入 Aboboo，
     确认首次打开后 Aboboo 自动采样/断句能正常完成（不应报解码失败）。
  2. 用播放器跳到同一句话，比对原片与 remux 版是否有可见的音画偏移；
     再把 A_utf8.srt 换成"整体提前 200ms"的版本，若顿挫感消失，
     说明再封装本身存在固定延迟，需要给字幕加统一补偿量。
  3. 记下 Aboboo 首次打开每个文件时"采样/断句"的耗时，
     用于第 4 板块决定是否值得输出音频标准化版本。

把上表填完后，第四板块的校正参数（编码、最小间隙、行长上限、剔除规则、
时间戳补偿量）就有实测依据了，不用再靠猜。
""")


if __name__ == "__main__":
    main()
