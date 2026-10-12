# -*- coding: utf-8 -*-
"""
验证 Subtitle Edit（seconv）规范化不会破坏已完成的对齐

这是接入需求第 3 条前必须过的关：seconv 会重写整个文件，
必须证明"启用它之后，对齐精度、结构、文本完整性都没有退化"。

对比两组：
  A  仅波形吸附（seconv_enabled=False）
  B  波形吸附 + seconv 规范化（seconv_enabled=True）

用法：
    python scripts/verify_seconv_safe.py
"""
import shutil
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from model.subtitle_fix import (FixConfig, normalize_with_seconv,  # noqa: E402
                               read_srt_cues, self_check)
from model.waveform import extract_boundaries, filter_speech  # noqa: E402


def analyze(srt: Path, anchors):
    cues = read_srt_cues(str(srt))
    raw = srt.read_bytes()
    onsets = [a for a, _ in anchors]
    hit = sum(1 for c in cues
              if min((abs(c.start - o) for o in onsets), default=9) <= 0.005)
    early = 0
    for c in cues:
        reg = [(a, o) for a, o in anchors if a - 0.02 <= c.start <= o + 0.02]
        if reg and reg[0][1] - c.end > 0.25:
            early += 1
    overlap = sum(1 for i in range(len(cues) - 1)
                  if cues[i].end > cues[i + 1].start)
    words = []
    import re
    for c in cues:
        words += re.findall(r"[A-Za-z0-9']+", c.text.lower())
    return {
        "条数": len(cues),
        "起点命中": hit,
        "终点提前": early,
        "重叠": overlap,
        "结构错误": len(self_check(cues, FixConfig())),
        "词数": len(words),
        "词表": Counter(words),
        "BOM": raw[:3] == b"\xef\xbb\xbf",
        "CRLF": b"\r\n" in raw,
        "时长中位": statistics.median([c.duration_ms for c in cues]) if cues else 0,
    }


def main():
    wav = ROOT / "test_output" / "test.wav"
    base_srt = ROOT / "test_output" / "gpu_final" / "test.srt"
    if not wav.exists() or not base_srt.exists():
        print(f"[x] 缺少素材：{wav} 或 {base_srt}")
        sys.exit(1)

    b = extract_boundaries(str(wav))
    anchors = filter_speech(b.speech, 0.30)

    work = ROOT / "test_output" / "seconv_safety"
    work.mkdir(parents=True, exist_ok=True)

    # A：仅吸附（直接复制已对齐的产出）
    a_srt = work / "A_snap_only.srt"
    shutil.copyfile(base_srt, a_srt)

    # B：吸附 + seconv
    b_srt = work / "B_with_seconv.srt"
    shutil.copyfile(base_srt, b_srt)
    normalize_with_seconv(str(b_srt), FixConfig(
        seconv_enabled=True, seconv_min_gap_ms=24))

    A, B = analyze(a_srt, anchors), analyze(b_srt, anchors)

    print("=" * 78)
    print("seconv 规范化安全性验证")
    print("=" * 78)
    rows = ["条数", "起点命中", "终点提前", "重叠", "结构错误", "词数",
            "时长中位", "BOM", "CRLF"]
    print(f"  {'指标':<12}{'A 仅吸附':>14}{'B 吸附+seconv':>16}{'判定':>10}")
    verdicts = {}
    for k in rows:
        va, vb = A[k], B[k]
        if k == "起点命中":
            ok = vb >= va          # 命中数不能减少
        elif k in ("终点提前", "重叠", "结构错误", "BOM", "CRLF"):
            ok = vb <= va          # 问题项不能增加
        elif k == "词数":
            ok = vb >= va * 0.98   # 允许极小差异
        elif k == "条数":
            ok = vb >= va * 0.98
        else:
            ok = True
        verdicts[k] = ok
        print(f"  {k:<12}{str(va):>14}{str(vb):>16}{'OK' if ok else '退化':>10}")

    lost = A["词表"] - B["词表"]
    gained = B["词表"] - A["词表"]
    n_lost = sum(lost.values())
    print(f"\n  文本差异：丢失 {n_lost} 个词次，新增 {sum(gained.values())} 个词次")
    if lost:
        print("    丢失样例：", dict(list(lost.items())[:8]))
    if gained:
        print("    新增样例：", dict(list(gained.items())[:8]))

    # 时间戳偏移统计
    ca, cb = read_srt_cues(str(a_srt)), read_srt_cues(str(b_srt))
    if len(ca) == len(cb):
        shifts = [abs(x.start - y.start) * 1000 for x, y in zip(ca, cb)]
        print(f"\n  起点位移：中位 {statistics.median(shifts):.0f}ms  "
              f"最大 {max(shifts):.0f}ms  未改动 {sum(1 for s in shifts if s == 0)}/{len(shifts)}")
    else:
        print(f"\n  条数变化，无法逐条比对时间戳（{len(ca)} -> {len(cb)}）")

    bad = [k for k, v in verdicts.items() if not v]
    print()
    print("=" * 78)
    if bad:
        print(f"  结论：seconv 规范化导致退化：{', '.join(bad)}")
        print("  建议：将 FixConfig.seconv_enabled 置 False，或调整 seconv 操作集")
        sys.exit(1)
    print("  结论：seconv 规范化未造成退化，可以保留在默认流程中。")
    print("=" * 78)


if __name__ == "__main__":
    main()
