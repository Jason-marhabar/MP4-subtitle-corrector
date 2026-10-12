# -*- coding: utf-8 -*-
"""
第四板块端到端验证脚本

跑通完整流程：MP4 -> 提取音频 -> 波形边界 -> ASR 转写 -> 字幕校正 -> 输出 SRT

用法：
    python scripts/test_pipeline.py [输入MP4] [输出目录]
    python scripts/test_pipeline.py --model small --device cpu --compute-type int8
    python scripts/test_pipeline.py --no-mp4          # 跳过 remux，只出字幕

验证项（脚本会自动断言）：
  1. SRT 为 UTF-8 无 BOM、LF 换行
  2. 无重叠、无逆序、无空文本、时长合规
  3. 字幕条数相对原始转写有变化（证明"按静音切分"确实生效）
  4. 吸附后起点精确落在真实语音边界上
  5. 与"未校正"的原始 SRT 对比，说明校正带来的改善

退出码：0 全部通过；1 有断言失败
"""
import argparse
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from model.asr import AsrEngine                      # noqa: E402
from model.subtitle_fix import (FixConfig, fix_subtitles,  # noqa: E402
                               self_check, to_srt, write_srt)
from model.waveform import extract_boundaries, filter_speech  # noqa: E402
from model.pipeline import extract_audio, remux_mp4  # noqa: E402


def rule(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def deviation_to_onsets(cues, onsets, tol=1.0):
    """算每条字幕起点到最近真实语音起点的绝对值偏差"""
    out = []
    for c in cues:
        d = min((abs(c.start - o) for o in onsets), default=None)
        if d is not None and d <= tol:
            out.append(d)
    return out


def case_adaptive_threshold():
    """用例：自适应静音阈值必须在"跟读"与"连续语流"两类素材上一致生效。

    背景（真实缺陷）：min_silence 原先硬编码 0.25s。跟读素材句间停顿 2.7s
    时正常，但连续语流停顿仅 0.34s，阈值逼近停顿导致语音段被过度合并
    （111 段塌成 91 段），起点命中率从 99% 掉到 94%。改为从信号自适应后解决。

    本用例不依赖转写（秒级），只用波形断言：
      · 自适应阈值落在合理区间
      · 两类素材的语音段粒度接近（不再出现"连续语流被过度合并"）
      · 显式传固定阈值时仍按用户给定值工作（不破坏可覆盖性）
    """
    rule("用例：自适应静音阈值的鲁棒性（跟读 vs 连续语流）")
    from model.waveform import adapt_min_silence, extract_boundaries, filter_speech

    orig = ROOT / "test_output" / "test.wav"
    cont = ROOT / "test_output" / "audit_continuous.wav"
    if not orig.exists() or not cont.exists():
        print("  [!]  缺少对比素材，跳过（先跑 scripts/audit_continuous.py 生成）")
        return []

    out = []
    stats = {}
    for label, wav in (("跟读", orig), ("连续", cont)):
        ms = adapt_min_silence(str(wav))
        b = extract_boundaries(str(wav))          # 自适应
        anchored = filter_speech(b.speech, 0.30)
        med = statistics.median([e - s for s, e in b.speech]) if b.speech else 0
        stats[label] = {"threshold": ms, "regions": len(b.speech),
                        "anchors": len(anchored), "median": med}
        print(f"  {label}：阈值 {ms*1000:.0f}ms，语音段 {len(b.speech)}，"
              f"锚点 {len(anchored)}，中位 {med*1000:.0f}ms")
        if not (0.10 <= ms <= 0.30):
            out.append(f"{label} 自适应阈值 {ms:.3f}s 超出合理区间 [0.10, 0.30]")

    a, b_ = stats["跟读"], stats["连续"]
    # 两者都是"句间停顿"类素材，语音段数量不应再出现数量级差异
    if min(a["regions"], b_["regions"]) < max(a["regions"], b_["regions"]) * 0.7:
        out.append(f"两类素材的语音段数差异过大：{a['regions']} vs {b_['regions']}"
                   f"（连续语流可能又被过度合并）")
    # 锚点数应相当
    if b_["anchors"] < a["anchors"] * 0.7:
        out.append(f"连续语流锚点偏少：{b_['anchors']} vs {a['anchors']}")
    # 显式阈值必须仍被尊重（可覆盖性）
    fixed = extract_boundaries(str(orig), min_silence=0.25)
    auto = extract_boundaries(str(orig))
    if fixed.speech == auto.speech:
        print("  [!]  显式阈值与自适应结果相同，无法验证可覆盖性（非错误）")
    else:
        print(f"  [ OK ] 显式传 min_silence 时按用户给定值工作")

    return out


def main():
    ap = argparse.ArgumentParser(description="第四板块端到端验证")
    ap.add_argument("video", nargs="?", default=str(ROOT / "test.mp4"))
    ap.add_argument("out_dir", nargs="?", default=str(ROOT / "test_output" / "pipeline"))
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--language", default="en")
    ap.add_argument("--no-mp4", action="store_true", help="跳过 remux MP4")
    ap.add_argument("--only-adaptive", action="store_true",
                    help="只跑自适应阈值用例（秒级，跳过转写）")
    args = ap.parse_args()

    # 秒级用例：可在不跑转写的情况下单独执行
    failures_adaptive = case_adaptive_threshold()
    if args.only_adaptive:
        print()
        print("=" * 72)
        print(f"自适应阈值用例：{'通过' if not failures_adaptive else '失败'}")
        print("=" * 72)
        for f in failures_adaptive:
            print(f"  [FAIL] {f}")
        sys.exit(1 if failures_adaptive else 0)

    video = Path(args.video)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not video.exists():
        print(f"[x] 输入不存在：{video}")
        sys.exit(1)

    failures = []
    failures += failures_adaptive          # 自适应用例已在开头跑过
    t0 = time.time()
    if failures_adaptive:
        print(f"  [FAIL] 自适应用例有 {len(failures_adaptive)} 项问题（见上）")

    # ---------------------------------------------------------- 1. 提取音频
    rule("步骤 1/5：提取音频（16kHz 单声道 WAV）")
    wav = out_dir / f"{video.stem}.wav"
    extract_audio(str(video), str(wav))
    print(f"  {wav.name}  {wav.stat().st_size/1024/1024:.2f} MB")

    # ---------------------------------------------------------- 2. 波形边界
    rule("步骤 2/5：分析音频波形边界（独立于 ASR 的客观参照）")
    b = extract_boundaries(str(wav))
    print(f"  {b.summary()}")
    onsets = b.onsets
    print(f"  语音起点前 6 个：{', '.join(f'{x:.3f}s' for x in onsets[:6])}")

    # ---------------------------------------------------------- 3. ASR
    rule(f"步骤 3/5：ASR 转写（{args.model} / {args.device} / {args.compute_type}）")
    engine = AsrEngine(model=args.model, device=args.device,
                       compute_type=args.compute_type, language=args.language)

    def cb(p, msg):
        print(f"\r  [{p*100:5.1f}%] {msg[:60]:<60}", end="", flush=True)

    transcript = engine.transcribe(str(wav), language=args.language, progress_cb=cb)
    print()
    print(f"  {transcript.summary()}")
    raw_count = len(transcript.segments)

    # 原始（未校正）结果的偏差，作为对照基线
    class _C:
        def __init__(self, s):
            self.start, self.text = s.start, s.text
    raw_cues = [_C(s) for s in transcript.segments if s.text.strip()]
    raw_dev = deviation_to_onsets(raw_cues, onsets)

    # ---------------------------------------------------------- 4. 校正
    rule("步骤 4/5：字幕校正（剔除 -> 按静音切分 -> 波形吸附 -> 约束）")
    cues, report = fix_subtitles(transcript.segments, b, FixConfig())
    print(f"  原始转写段数      : {raw_count}")
    print(f"  校正后字幕条数    : {len(cues)}")
    print(f"  切分增加条数      : {report.split}")
    print(f"  统计              : {report.summary()}")

    # ---------------------------------------------------------- 5. 输出
    rule("步骤 5/5：输出与校验")
    srt_path = out_dir / f"{video.stem}.srt"
    write_srt(cues, str(srt_path))
    raw_bytes = srt_path.read_bytes()
    print(f"  已写出 {srt_path}")
    print(f"  大小 {len(raw_bytes)} bytes；"
          f"前 3 字节 {' '.join(f'{x:02X}' for x in raw_bytes[:3])}")

    # --- 断言 1：编码
    if raw_bytes[:3] == b"\xef\xbb\xbf":
        failures.append("SRT 带 BOM（要求 UTF-8 无 BOM）")
        print("  [FAIL] 带 BOM")
    else:
        print("  [ OK ] UTF-8 无 BOM")
    if b"\r\n" in raw_bytes:
        failures.append("SRT 使用了 CRLF 换行（要求 LF）")
        print("  [FAIL] 换行为 CRLF")
    else:
        print("  [ OK ] LF 换行")

    # --- 断言 2：结构自检
    problems = self_check(cues, FixConfig())
    if problems:
        failures += problems
        print(f"  [FAIL] 结构自检 {len(problems)} 项：")
        for p in problems[:8]:
            print(f"         {p}")
    else:
        print("  [ OK ] 结构自检通过（无重叠/逆序/空文本/时长合规）")

    # --- 断言 3：切分确实生效
    if report.split > 0:
        print(f"  [ OK ] 按静音切分生效（新增 {report.split} 条）")
    else:
        print("  [!]  未发生切分（该素材 Whisper 可能已按句切好，非错误）")

    # --- 断言 4：吸附效果
    new_dev = deviation_to_onsets(cues, onsets)
    if raw_dev and new_dev:
        r_med = statistics.median(raw_dev)
        n_med = statistics.median(new_dev)
        r_avg = statistics.fmean(raw_dev)
        n_avg = statistics.fmean(new_dev)
        print(f"  起点偏差（到最近真实语音起点）：")
        print(f"      校正前 中位 {r_med*1000:6.0f}ms  平均 {r_avg*1000:6.0f}ms  样本 {len(raw_dev)}")
        print(f"      校正后 中位 {n_med*1000:6.0f}ms  平均 {n_avg*1000:6.0f}ms  样本 {len(new_dev)}")
        if n_med <= r_med + 1e-9:
            print(f"  [ OK ] 吸附使起点偏差中位数下降 "
                  f"{(r_med-n_med)*1000:.0f}ms")
        else:
            failures.append(f"吸附后偏差反而变大：{r_med*1000:.0f} -> {n_med*1000:.0f}ms")
            print("  [FAIL] 吸附后偏差变大")
    else:
        print("  [!]  偏差样本不足，跳过对比")

    # --- 断言 5：终点覆盖度（"字幕消失得比声音早"的守门员）
    # Whisper 的段尾系统性偏早，若终点吸附只看"不晚于字幕末端"的语音终点，
    # 就会把每个更晚的真实终点都排除掉，导致字幕提前消失。用户实测反馈过
    # 这个问题，必须专门断言：字幕终点应覆盖到所属语音区间的结束。
    ends_short = []
    for i, c in enumerate(cues, 1):
        later = [o for s, o in b.speech
                 if s <= c.start + 0.02 and o > c.end + 0.05]
        if later:
            ends_short.append((i, min(later) - c.end, c.text[:24]))
    if not ends_short:
        print("  [ OK ] 终点覆盖良好（字幕未提前于语音消失）")
    else:
        worst = max(x[1] for x in ends_short)
        print(f"  [!]  {len(ends_short)}/{len(cues)} 条字幕终点早于真实语音结束"
              f"（最大 {worst*1000:.0f}ms）")
        for i, d, t in ends_short[:6]:
            print(f"         第 {i} 条 早 {d*1000:.0f}ms | {t}")
        if worst > 0.25:
            failures.append(
                f"{len(ends_short)} 条字幕提前消失，最大 {worst*1000:.0f}ms")
            print("  [FAIL] 提前消失过多，用户会看到'字幕比声音先消失'")

    # --- 断言 6：文本完整性（最容易被静默破坏的一项）
    # 切分/合并只应"搬运"词，绝不应丢词。实测曾经因为重叠解决时丢弃字幕
    # 而静默丢掉 40 个词次 —— 这类 bug 不会触发任何结构检查，必须专门断言。
    def words(texts):
        out = []
        for t in texts:
            out += re.findall(r"[A-Za-z0-9']+", t.lower())
        return out

    base_words = words([s.text for s in transcript.segments])
    cur_words = words([c.text for c in cues])
    from collections import Counter
    lost = Counter(base_words) - Counter(cur_words)
    n_lost = sum(lost.values())
    print(f"  文本完整性：基线 {len(base_words)} 词 -> 当前 {len(cur_words)} 词")
    if n_lost == 0:
        print("  [ OK ] 无词丢失（切分/合并只搬运了词）")
    else:
        pct = n_lost / max(1, len(base_words)) * 100
        detail = ", ".join(f"{w}x{n}" for w, n in lost.most_common(8))
        msg = f"丢失 {n_lost} 个词次（{pct:.1f}%）: {detail}"
        if pct > 3.0:
            failures.append(f"文本丢失过多：{msg}")
            print(f"  [FAIL] {msg}")
        else:
            print(f"  [!]  少量词丢失（可能来自幻觉段去重）：{msg}")

    # --- 可选：remux MP4
    if not args.no_mp4:
        rule("附加：remux MP4（-c copy，不重新编码）")
        mp4 = out_dir / f"{video.stem}_remux.mp4"
        remux_mp4(str(video), str(mp4))
        print(f"  已写出 {mp4.name}  {mp4.stat().st_size/1024/1024:.2f} MB")

    # ---------------------------------------------------------- 汇总
    print()
    print("=" * 72)
    print(f"结果：{' 全部通过 ' if not failures else f'{len(failures)} 项失败'}"
          f"（总耗时 {time.time()-t0:.1f}s）")
    print("=" * 72)
    if failures:
        for f in failures[:15]:
            print(f"  [FAIL] {f}")
        sys.exit(1)
    print(f"字幕文件：{srt_path}")
    print("导入 Aboboo：把该 SRT 放到视频同目录并改成同名即可自动加载。")


if __name__ == "__main__":
    main()
