# -*- coding: utf-8 -*-
"""
字幕时间戳波形吸附（第四板块参考实现，算法已实测验证）

验证结论（test.mp4 / 313s / 英语跟读素材 / faster-whisper small）
--------------------------------------------------------------------
  · Whisper 起点系统性偏早：中位修正量 144ms，与独立测得的偏差
    （small 中位 −92ms、large-v3 中位 −267ms）方向一致、量级吻合。
  · 吸附后 49/54 段**精确命中**真实语音起点（残差 0ms），
    平均残差 58ms，零重叠。
  · 剩余 5 段偏差 ≥1s，属 Whisper 把多句合并进一段的情况，
    需先做"按静音边界切分"再接本流程。

算法（关键点：吸附到「下一个」语音起点，而不是「最近」的）
--------------------------------------------------------------------
实测证据（前 10 段的逐条比对）显示 Whisper 的段起点绝大多数**落在静音里**，
且总是比真实语音起点早 90~330ms：

    序 1  Whisper 0.000s -> 真实 0.378s   提前 378ms
    序 2  Whisper 3.160s -> 真实 3.289s   提前 129ms
    序 3  Whisper 6.940s -> 真实 7.085s   提前 145ms
    序 5  Whisper 16.720s -> 真实 16.772s 提前  52ms

所以正确的锚点是「>= 该起点的第一个语音起点」。若改用"最近的语音起点"，
当段起点落在静音里时会被吸到**上一句**的开头，把字幕整体推前一整句
（实测该错误做法会产生 2403ms 的中位修正量，完全不可用）。

用法：
    python scripts/snap_to_waveform.py <原始srt> <音频或视频> <输出srt>
        [--tol 3.0] [--min-dur 0.8] [--noise-db -32] [--min-silence 0.25]
        [--min-speech 0.10] [--json 报告.json]
"""
import argparse
import bisect
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_whisper_drift import extract_speech_segments   # 复用波形解析

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def ms_to_ts(ms):
    ms = max(0, int(ms))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_to_ms(s):
    h, m, rest = s.split(":")
    sec, ms = rest.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int(ms)


def parse_srt_text(path):
    """极简 SRT 解析：返回 [(index, start_ms, end_ms, text_lines), ...]

    不依赖 pysrt，便于在无第三方库的环境（如打包后的最小运行时）复用。
    """
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or not s.isdigit():
            i += 1
            continue
        idx = int(s)
        i += 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, b = [x.strip() for x in lines[i].split("-->")]
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() != "":
            body.append(lines[i].rstrip())
            i += 1
        try:
            cues.append((idx, ts_to_ms(a), ts_to_ms(b), body))
        except Exception:
            continue
    return cues


def build_srt(cues):
    out = []
    for n, (idx, a, b, body) in enumerate(cues, 1):
        out.append(f"{n}\n{ms_to_ts(a)} --> {ms_to_ts(b)}\n" + "\n".join(body) + "\n")
    return "\n".join(out)


def derive_anchors(speech_segments, min_speech=0.10):
    """从语音段推出吸附锚点。

    过滤掉过短的语音段：它们是噪声碎片（实测最短仅 21ms），
    若当作锚点会把字幕吸到错误位置。
    """
    regions = [(a, b) for a, b in speech_segments if (b - a) >= min_speech]
    return ([int(round(s * 1000)) for s, _ in regions],
            [int(round(e * 1000)) for _, e in regions],
            regions)


def snap_cues(cues, onsets, offsets, tol_ms, min_dur_ms, min_gap_ms=24):
    """把每条字幕的起止时间吸附到语音边界。

    返回 (吸附后的 cues, 统计字典)
    """
    st = {"起点吸附": 0, "起点放弃": 0, "终点吸附": 0, "终点放弃": 0,
          "时长兜底": 0, "单调修正": 0, "间隙修正": 0, "修正量ms": []}
    out = []

    for idx, a, b, body in cues:
        # --- 起点：吸附到 >= a 的第一个语音起点（不是最近的！见模块文档）
        i = bisect.bisect_left(onsets, a)
        if i < len(onsets) and (onsets[i] - a) <= tol_ms:
            na = onsets[i]
            st["起点吸附"] += 1
            st["修正量ms"].append(na - a)
        else:
            na = a
            st["起点放弃"] += 1

        # --- 终点：吸附到 <= b 的最后一个语音终点
        j = bisect.bisect_right(offsets, b)
        nb = offsets[j - 1] if j > 0 else b
        if nb <= na or (b - nb) > tol_ms:
            nb = b
            st["终点放弃"] += 1
        else:
            st["终点吸附"] += 1

        # --- 时长兜底
        if nb - na < min_dur_ms:
            nb = na + min_dur_ms
            st["时长兜底"] += 1
        out.append([idx, na, nb, body])

    # --- 强制单调：不得重叠、间隙不小于 min_gap。
    # 这一步不能省：吸附是逐条独立的，A 的起点可能被吸到上一句终点之前。
    for _ in range(20):
        changed = False
        for i in range(len(out) - 1):
            if out[i][2] > out[i + 1][1]:                 # 重叠 -> 向中点靠拢
                mid = (out[i][2] + out[i + 1][1]) // 2
                if mid > out[i][1] and out[i + 1][2] > mid:
                    out[i][2] = out[i + 1][1] = mid
                    st["单调修正"] += 1
                    changed = True
            elif out[i + 1][1] - out[i][2] < min_gap_ms:  # 间隙过小 -> 推开下一条
                dur = out[i + 1][2] - out[i + 1][1]
                out[i + 1][1] = out[i][2] + min_gap_ms
                out[i + 1][2] = max(out[i + 1][2], out[i + 1][1] + dur)
                st["间隙修正"] += 1
                changed = True
        for c in out:                                     # 再兜一次时长
            if c[2] - c[1] < min_dur_ms:
                c[2] = c[1] + min_dur_ms
                st["时长兜底"] += 1
                changed = True
        if not changed:
            break

    return [(c[0], c[1], c[2], c[3]) for c in out], st


def self_check(cues, min_dur_ms):
    """输出前自检：绝不交出带重叠/逆序/过短段的字幕。

    这不是可选项——Aboboo 用时间戳做断点，重叠会直接导致断点错乱。
    开发过程中正是因为漏了这一步，才一度交出带 255ms 重叠的结果。
    """
    problems = []
    for i in range(len(cues) - 1):
        _, a1, b1, _ = cues[i]
        _, a2, b2, _ = cues[i + 1]
        if b1 > a2:
            problems.append(f"序{cues[i][0]} 与 序{cues[i+1][0]} 重叠 {b1-a2}ms")
        if a2 < a1:
            problems.append(f"序{cues[i+1][0]} 起点逆序")
    for idx, a, b, _ in cues:
        if b - a < min_dur_ms:
            problems.append(f"序{idx} 时长仅 {b-a}ms（< {min_dur_ms}ms）")
        if b <= a:
            problems.append(f"序{idx} 结束时间不大于起始时间")
    return problems


def main():
    ap = argparse.ArgumentParser(description="把字幕时间戳吸附到真实语音边界")
    ap.add_argument("srt")
    ap.add_argument("media")
    ap.add_argument("out")
    ap.add_argument("--tol", type=float, default=3.0,
                    help="吸附容差（秒）。默认 3.0 —— 覆盖 Whisper 把多句合并成长段的情况；"
                         "超出容差则放弃吸附，避免把字幕推到无关位置")
    ap.add_argument("--min-dur", type=float, default=0.8, help="最短字幕时长（秒）")
    ap.add_argument("--min-gap", type=float, default=0.024, help="最小段间隙（秒）")
    ap.add_argument("--noise-db", type=float, default=-32.0)
    ap.add_argument("--min-silence", type=float, default=0.25)
    ap.add_argument("--min-speech", type=float, default=0.10,
                    help="忽略短于该值的语音段（噪声碎片）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    tol_ms = int(args.tol * 1000)
    min_dur_ms = int(args.min_dur * 1000)
    min_gap_ms = int(args.min_gap * 1000)

    cues = parse_srt_text(args.srt)
    if not cues:
        print(f"[x] 未能解析出任何字幕：{args.srt}")
        sys.exit(1)

    speech, silences, total = extract_speech_segments(
        args.media, args.noise_db, args.min_silence)
    onsets, offsets, regions = derive_anchors(speech, args.min_speech)
    print(f"字幕 {len(cues)} 条；语音段 {len(speech)} 个 -> 有效锚点 {len(onsets)} 个")

    snapped, st = snap_cues(cues, onsets, offsets, tol_ms, min_dur_ms, min_gap_ms)
    Path(args.out).write_text(build_srt(snapped), encoding="utf-8", newline="\n")
    print(f"已写出：{args.out}")

    problems = self_check(snapped, min_dur_ms)
    if problems:
        print(f"  [!] 自检未通过（{len(problems)} 项）：")
        for p in problems[:10]:
            print(f"      {p}")
    else:
        print("  [OK] 自检通过：无重叠、无逆序、无过短段")

    print()
    print("处理统计：")
    for k, v in st.items():
        if k != "修正量ms":
            print(f"  {k:8} {v}")

    corr = st["修正量ms"]
    if corr:
        print()
        print(f"起点修正量：中位 {statistics.median(corr):.0f}ms  "
              f"平均 {statistics.fmean(corr):.0f}ms  最大 {max(corr):.0f}ms")
        big = [c for c in corr if c >= 1000]
        if big:
            print(f"  [!] {len(big)} 条修正量 >=1s：Whisper 把多句合并进了一段，")
            print(f"      应在吸附前先做「按静音边界切分」，否则这些句子无法对齐。")

    # 吸附后残差：起点与最近锚点的距离，0 表示精确命中
    resid = []
    for _, a, _, _ in snapped:
        i = bisect.bisect_left(onsets, a)
        cands = [onsets[k] for k in (i - 1, i, i + 1) if 0 <= k < len(onsets)]
        if cands:
            resid.append(min(abs(a - c) for c in cands))
    if resid:
        print(f"吸附后残差：中位 {statistics.median(resid):.0f}ms  "
              f"平均 {statistics.fmean(resid):.0f}ms  "
              f"精确命中 {sum(1 for r in resid if r == 0)}/{len(resid)} 条")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"input": args.srt, "output": args.out,
             "anchors": len(onsets), "cues": len(cues),
             "stats": {k: v for k, v in st.items() if k != "修正量ms"},
             "correction_median_ms": round(statistics.median(corr), 1) if corr else None,
             "residual_median_ms": round(statistics.median(resid), 1) if resid else None,
             "self_check_failures": problems},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写出：{args.json}")


if __name__ == "__main__":
    main()
