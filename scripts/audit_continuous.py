# -*- coding: utf-8 -*-
"""
连续语流鲁棒性审计

背景
----
本项目的探针素材（test.mp4）语句间有 2.7~2.8 秒的大停顿，属于理想的
"跟读练习"形态。但真实学习素材（尤其电影、播客）语音是连续的。
第四板块的切分依赖"词间停顿 0.8 秒"这一启发式，因此**必须验证**：
当停顿被压缩到 300~400ms 时，校正质量是否会崩。

本脚本对同一素材的"连续语流变体"跑完整校正，并对比两者的关键指标：
  · 起点是否仍精确落在真实语音起点上
  · 终点是否仍覆盖到真实语音结束（用户反馈过的"字幕提前消失"）
  · 结构是否仍无重叠/逆序/零时长
  · 是否出现"文本与音频错位"（adjacency：字幕文本长度与语音段时长严重不符）

用法：
    python scripts/audit_continuous.py [原始音频] [连续变体音频]
"""
import statistics
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

from model.asr import AsrEngine                    # noqa: E402
from model.subtitle_fix import (FixConfig, Cue, fix_subtitles,  # noqa: E402
                               self_check, to_srt)
from model.waveform import extract_boundaries, filter_speech  # noqa: E402


def analyze(label, wav, cfg=None, language="en"):
    cfg = cfg or FixConfig()
    print("=" * 74)
    print(f"{label}")
    print("=" * 74)
    b = extract_boundaries(str(wav))
    print(f"  波形：{b.summary()}")

    engine = AsrEngine(model="small", device="cpu", compute_type="int8",
                       language=language)
    tr = engine.transcribe(str(wav), language=language)
    print(f"  转写：{tr.summary()}")

    cues, rep = fix_subtitles(tr.segments, b, cfg)
    print(f"  校正：{len(cues)} 条  |  {rep.summary()}")

    # --- 起点命中性
    anchors = filter_speech(b.speech, cfg.min_anchor_speech)
    onsets = [a for a, _ in anchors]
    offs = [e for _, e in anchors]
    hit = sum(1 for c in cues
              if min((abs(c.start - o) for o in onsets), default=9) <= 0.005)
    # --- 终点覆盖性
    early = 0
    for c in cues:
        reg = [(a, o) for a, o in anchors if a - 0.02 <= c.start <= o + 0.02]
        if reg and reg[0][1] - c.end > 0.05:
            early += 1
    # --- 结构
    probs = self_check(cues, cfg)
    # --- 文本完整性
    def words(ts):
        import re
        out = []
        for t in ts:
            out += re.findall(r"[A-Za-z0-9']+", t.lower())
        return out
    base_w = words([s.text for s in tr.segments])
    cur_w = words([c.text for c in cues])
    lost = sum((Counter(base_w) - Counter(cur_w)).values())
    # --- 文本长度与语音时长的偏离（错位信号）
    mism = 0
    for c in cues:
        reg = [(a, o) for a, o in anchors if a - 0.02 <= c.start <= o + 0.02]
        if not reg:
            continue
        dur = reg[0][1] - reg[0][0]
        n_chars = len(c.text)
        # 语音段超过 6 秒却只有极少文字（或反之），提示归属可疑
        if dur > 6.0 and n_chars < 40:
            mism += 1
        if dur < 1.2 and n_chars > 120:
            mism += 1

    print(f"  起点精确命中语音起点 : {hit}/{len(cues)}")
    print(f"  终点未覆盖语音结束   : {early} 条")
    print(f"  结构错误             : {len(probs)}")
    print(f"  文本丢失词次         : {lost}")
    print(f"  文本/语音不匹配可疑   : {mism} 条")
    durs = [c.duration_ms for c in cues]
    print(f"  字幕时长 中位/最短/最长: {statistics.median(durs):.0f} / "
          f"{min(durs)} / {max(durs)} ms")
    merged = sum(1 for c in cues if len(c.text) > 80)
    print(f"  超长条（>80字符，可能含多句）: {merged} 条")
    return {"label": label, "cues": len(cues), "hit": hit, "early": early,
            "problems": len(probs), "lost": lost, "mism": mism,
            "merged": merged, "rep": rep}


def main():
    orig = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test_output" / "test.wav"
    cont = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "test_output" / "audit_continuous.wav"
    if not orig.exists() or not cont.exists():
        print(f"[x] 素材缺失：{orig} / {cont}")
        sys.exit(1)

    print("\n" + "#" * 74)
    print("# 连续语流鲁棒性审计")
    print("#" * 74 + "\n")

    a = analyze("A. 原始素材（语句间有 2.7s 大停顿）", orig)
    print()
    b = analyze("B. 连续语流变体（停顿压缩到 0.34s）", cont)

    print("\n" + "#" * 74)
    print("# 对比结论")
    print("#" * 74)
    rows = [("字幕条数", "cues"), ("起点精确命中", "hit"),
            ("终点未覆盖", "early"), ("结构错误", "problems"),
            ("文本丢失词次", "lost"), ("文本/语音不匹配", "mism"),
            ("超长条(>80字符)", "merged")]
    print(f"  {'指标':<20}{'A 原始':>10}{'B 连续':>10}{'判定':>10}")
    for name, key in rows:
        va, vb = a[key], b[key]
        if key in ("cues", "hit"):
            verdict = "OK" if vb >= va * 0.85 else "退化"
        else:
            verdict = "OK" if vb <= max(va, 1) * 1.5 + 1 else "退化"
        print(f"  {name:<20}{va:>10}{vb:>10}{verdict:>10}")
    print()
    print(f"  原始素材开头几条：")
    print(f"  连续变体开头几条：")
    print("  （详见表下方的 text/时长对照，本脚本只做指标对比）")


if __name__ == "__main__":
    main()
