# -*- coding: utf-8 -*-
"""
Whisper 时间戳偏差测量脚本（第四板块核心实验）

要解决的问题
------------
Aboboo 官方文档明确"字幕时间戳用作断点"。所以字幕对齐的唯一命门是
**Whisper 输出的时间戳准不准**。业界经验是 Whisper 的时间戳会系统性偏早
（词还没说出口，时间戳已经到了），但"偏早多少毫秒"必须实测，否则
第四板块的校正参数只能靠猜。

测量原理
--------
用 ffmpeg 的 silencedetect 从**音频波形**中提取真实的语音/静音边界
（这是独立于任何 ASR 模型的客观参照），再让 Whisper 转写同一段音频取词级
时间戳，然后统计：

  偏差 = Whisper 词起始时间 − 真实语音起点

对所有"Whisper 词的起始落在某个真实语音起点附近"的配对求统计量，
就能得到系统性偏差（中位数最有代表性，均值会被离群值拉偏）。

用法
----
    # 只用波形测出真实语音边界（不需要 GPU/模型，秒级完成）
    python scripts/measure_whisper_drift.py <音频或视频> --boundaries-only

    # 完整测量（需要 faster-whisper，首次会下载模型）
    python scripts/measure_whisper_drift.py <音频或视频> \
        --model large-v3 --device cuda --compute-type int8_float16 \
        [--limit-seconds 120] [--json 结果.json]

输出：偏差统计表 + 建议的补偿量，并可选写出 JSON 供报告引用。
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SIL_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


# ------------------------------------------------------------------ 波形参照

def extract_speech_segments(media, noise_db=-32.0, min_silence=0.25):
    """用 silencedetect 求真实语音段。

    返回 [(start_s, end_s), ...]，即静音之间的语音区间。
    开头若无静音，则视为从 0 开始；结尾若无静音，则延伸到音频末尾。
    """
    cmd = [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-nostats",
           "-i", str(media), "-af",
           f"silencedetect=noise={noise_db}dB:d={min_silence}",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{r.stderr[-800:]}")

    events = [(m.group(1), float(m.group(2)))
              for m in SIL_RE.finditer(r.stderr)]
    silences = []
    cur = None
    for kind, ts in events:
        if kind == "start":
            cur = ts
        elif kind == "end" and cur is not None:
            silences.append((cur, ts))
            cur = None
    if cur is not None:                      # 末尾未闭合的静音
        silences.append((cur, None))

    # 静音之间的空隙就是语音段
    total = probe_duration(media)
    speech, cursor = [], 0.0
    for s, e in silences:
        if s > cursor:
            speech.append((cursor, s))
        cursor = e if e is not None else total
    if total and cursor < total:
        speech.append((cursor, total))
    return [(a, b) for a, b in speech if b > a], silences, total


def probe_duration(media):
    fp = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [fp, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            capture_output=True, text=True, timeout=120)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


# ------------------------------------------------------------------ Whisper

def transcribe_words(media, model_name, device, compute_type, language,
                     limit_seconds=None, vad=True):
    """用 faster-whisper 取词级时间戳。参数刻意与生产环境保持一致。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[x] 未安装 faster-whisper。请先执行：")
        print("    venv\\Scripts\\python.exe -m pip install faster-whisper "
              "-i https://mirrors.aliyun.com/pypi/simple/")
        sys.exit(3)

    print(f"[*] 加载模型 {model_name} (device={device}, compute_type={compute_type}) ...")
    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    print(f"[*] 模型加载完成，用时 {time.time()-t0:.1f}s")

    t0 = time.time()
    segments, info = model.transcribe(
        str(media),
        language=language,
        word_timestamps=True,
        vad_filter=vad,
        condition_on_previous_text=False,     # 抑制"复读机"式幻觉循环
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    )
    print(f"[*] 音频时长 {info.duration:.1f}s，检测语言 {info.language} "
          f"(置信度 {info.language_probability:.2f})")

    words, segs = [], []
    for seg in segments:
        wl = list(seg.words or [])
        segs.append({
            "start": seg.start, "end": seg.end, "text": seg.text,
            "first_word": (wl[0].word if wl else None),
            "first_word_start": (wl[0].start if wl else None),
        })
        if limit_seconds is not None and seg.start > limit_seconds:
            break
        for w in wl:
            words.append({"start": w.start, "end": w.end,
                          "word": w.word, "prob": getattr(w, "probability", None)})
        print(f"\r  转写中… {seg.end:7.1f}s / {info.duration:.1f}s  "
              f"({seg.end*100/info.duration:5.1f}%)", end="", flush=True)
    print()
    print(f"[*] 转写完成，用时 {time.time()-t0:.1f}s，"
          f"共 {len(segs)} 段 / {len(words)} 词")
    return words, segs, info


# ------------------------------------------------------------------ 偏差统计

def compute_drift(words, segs, speech_segments, tol=0.4):
    """测量系统性偏差：把"每段首词"与其最近的"真实语音起点"配对。

    为什么必须这样配对（而不是让所有词都去就近匹配）：
      - 只有**句首词**才应该恰好落在语音起点上。句子中间的词本身就位于语音
        区间内部，如果也拿去和语音起点匹配，就会被强行吸附到最近的语音起点上，
        产生大量假配对，把统计量彻底污染（实测中位数会被拉偏上百毫秒）。
      - 另外每个真实语音起点只接受一个最佳配对（贪心去重），避免多个词争抢
        同一个起点。

    tol：配对容差（秒）。超出容差说明该段首词与任何语音起点都对不上
        （可能是 VAD 切分与波形切分不一致），这类样本计入 unmatched 并单独报告。
    """
    onsets = [s for s, _ in speech_segments]

    cands = []
    for si, seg in enumerate(segs):
        # 找出该段首词：优先用 segments 里第一个词时间；没有词级数据则退回段起点
        if seg.get("first_word_start") is not None:
            ws, label = seg["first_word_start"], seg["first_word"]
        else:
            ws, label = seg["start"], seg.get("text", "")[:20]
        if not onsets:
            break
        nearest = min(onsets, key=lambda o: abs(o - ws))
        cands.append({"seg": si, "word": label, "whisper_start": ws,
                      "true_onset": nearest, "drift": ws - nearest})

    # 贪心去重：按 |drift| 升序，每个真实起点只保留最匹配的那一段
    cands.sort(key=lambda c: abs(c["drift"]))
    used, pairs, unmatched = set(), [], []
    for c in cands:
        if abs(c["drift"]) > tol:
            unmatched.append(c)
            continue
        if c["true_onset"] in used:
            unmatched.append(c)
            continue
        used.add(c["true_onset"])
        pairs.append(c)
    return pairs, unmatched


def summarize(pairs):
    if not pairs:
        return {}
    d = [p["drift"] for p in pairs]
    return {
        "样本数": len(d),
        "中位数ms": round(statistics.median(d) * 1000, 1),
        "均值ms": round(statistics.fmean(d) * 1000, 1),
        "标准差ms": round(statistics.pstdev(d) * 1000, 1) if len(d) > 1 else 0.0,
        "最小ms": round(min(d) * 1000, 1),
        "最大ms": round(max(d) * 1000, 1),
        "偏早比例": round(sum(1 for x in d if x < 0) / len(d) * 100, 1),
        "25分位ms": round(statistics.quantiles(d, n=4)[0] * 1000, 1) if len(d) > 3 else None,
        "75分位ms": round(statistics.quantiles(d, n=4)[2] * 1000, 1) if len(d) > 3 else None,
    }


def ms_to_ts(ms):
    ms = max(0, int(ms))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segs, path):
    """把 Whisper 段级结果写成标准 SRT（UTF-8 无 BOM，LF 换行）。

    刻意不带 BOM：seconv 默认输出带 BOM，而 Aboboo 是否会把它渲染成乱码
    尚未实测，出于保守统一用无 BOM。
    """
    blocks = []
    for n, seg in enumerate(segs, 1):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"{n}\n{ms_to_ts(seg['start']*1000)} --> "
                      f"{ms_to_ts(seg['end']*1000)}\n{text}\n")
    Path(path).write_text("\n".join(blocks), encoding="utf-8", newline="\n")
    return len(blocks)


def main():
    ap = argparse.ArgumentParser(description="测量 Whisper 时间戳系统性偏差")
    ap.add_argument("media", help="音频或视频路径")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute-type", default="int8_float16")
    ap.add_argument("--language", default="en")
    ap.add_argument("--noise-db", type=float, default=-32.0)
    ap.add_argument("--min-silence", type=float, default=0.25)
    ap.add_argument("--tol", type=float, default=0.4,
                    help="配对容差（秒），只统计段首词与语音起点距离在此范围内的配对")
    ap.add_argument("--limit-seconds", type=float, default=None)
    ap.add_argument("--no-vad", action="store_true")
    ap.add_argument("--boundaries-only", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--srt-out", default=None,
                    help="同时把原始 Whisper 结果写成 SRT，供 snap_to_waveform.py 吸附验证")
    args = ap.parse_args()

    if not os.path.exists(args.media):
        print(f"[x] 文件不存在：{args.media}")
        sys.exit(1)

    print("=" * 72)
    print("步骤 1：从音频波形提取真实语音边界（独立于 ASR 的客观参照）")
    print("=" * 72)
    speech, silences, total = extract_speech_segments(
        args.media, args.noise_db, args.min_silence)
    print(f"音频总时长：{total:.3f}s")
    print(f"静音段：{len(silences)} 个；语音段：{len(speech)} 个")
    if speech:
        durs = [b - a for a, b in speech]
        print(f"语音段时长：中位 {statistics.median(durs)*1000:.0f}ms，"
              f"最短 {min(durs)*1000:.0f}ms，最长 {max(durs)*1000:.0f}ms")
    if silences:
        sd = [(e - s) for s, e in silences if e is not None]
        if sd:
            print(f"静音段时长：中位 {statistics.median(sd)*1000:.0f}ms，"
                  f"最长 {max(sd)*1000:.0f}ms")
    print("前 8 个语音起点：" +
          ", ".join(f"{s:.3f}s" for s, _ in speech[:8]))

    result = {"media": args.media, "duration": total,
              "speech_count": len(speech), "silence_count": len(silences)}

    if args.boundaries_only:
        print("\n（--boundaries-only：跳过 ASR 测量）")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"已写出 {args.json}")
        return

    print()
    print("=" * 72)
    print("步骤 2：Whisper 词级转写")
    print("=" * 72)
    words, segs, info = transcribe_words(
        args.media, args.model, args.device, args.compute_type,
        args.language, args.limit_seconds, vad=not args.no_vad)

    print()
    print("=" * 72)
    print("步骤 3：偏差统计（每段首词起始 − 最近真实语音起点）")
    print("=" * 72)
    pairs, unmatched = compute_drift(words, segs, speech, args.tol)
    stats = summarize(pairs)
    if pairs:
        for k, v in stats.items():
            print(f"  {k:12} {v}")
        print(f"  {'未配对段数':12} {len(unmatched)}")
        med = stats["中位数ms"]
        print()
        print(f"  配对容差 ±{args.tol*1000:.0f}ms；"
              f"共 {len(segs)} 段，成功配对 {len(pairs)} 段")
        if med < -50:
            print(f"  >>> 结论：Whisper 时间戳系统性偏早 {abs(med):.0f}ms（中位数）")
            print(f"      建议在字幕输出前对全部时间戳补偿 +{abs(med):.0f}ms")
        elif med > 50:
            print(f"  >>> 结论：Whisper 时间戳系统性偏晚 {med:.0f}ms（中位数）")
            print(f"      建议对全部时间戳补偿 -{med:.0f}ms")
        else:
            print(f"  >>> 结论：未发现明显系统性偏差（中位数 {med:.0f}ms，在 ±50ms 内）")
        if stats["标准差ms"] > 150:
            print(f"  >>> 警告：偏差标准差 {stats['标准差ms']:.0f}ms 偏大，"
                  f"说明逐句偏差不一致，")
            print(f"      单纯做全局补偿无法解决，必须靠 VAD 边界吸附（见下）")
    else:
        print("  [!] 没有配对样本，无法统计。可放宽 --tol 或检查音频是否有语音。")

    result["words"] = len(words)
    result["segments"] = len(segs)
    result["drift"] = stats
    result["paired"] = len(pairs)
    result["unmatched"] = len(unmatched)
    result["sample_pairs"] = pairs[:15]
    if args.srt_out:
        n = write_srt(segs, args.srt_out)
        print(f"\n原始 Whisper SRT 已写出：{args.srt_out}（{n} 条）")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n已写出详细结果：{args.json}")


if __name__ == "__main__":
    main()
