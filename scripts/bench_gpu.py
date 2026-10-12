# -*- coding: utf-8 -*-
"""
GPU / CPU 性能与可用性实测

回答三个问题：
  1. GPU 到底能不能用（需要正确的 DLL 解析方式）
  2. GPU 比 CPU 快多少（同模型同音频，测 RTF）
  3. 8GB 显存能不能跑 large-v3（本项目最想用的模型）

关键结论（已实测）：Windows 上必须把 nvidia 包的 bin 目录加进 **PATH**，
``os.add_dll_directory()`` 对 ctranslate2 无效（它是 C++ 扩展，内部用
LoadLibrary 按 PATH 解析 cublas64_12.dll）。

用法：
    python scripts/bench_gpu.py                     # small: cpu vs cuda
    python scripts/bench_gpu.py --models small large-v3
    python scripts/bench_gpu.py --device cuda --compute-type int8_float16
"""
import argparse
import os
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

from model.gpu_env import (describe, ensure_cuda_dll_path,  # noqa: E402
                           gpu_available)


def bench(model_name, device, compute_type, wav, repeat=1):
    from faster_whisper import WhisperModel
    t0 = time.time()
    try:
        m = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {str(e)[:160]}"}
    load = time.time() - t0

    times, segs_n, audio = [], 0, 0.0
    for _ in range(repeat):
        try:
            t1 = time.time()
            segs, info = m.transcribe(str(wav), language="en",
                                      word_timestamps=False, vad_filter=True)
            n = 0
            for s in segs:
                n += 1
            times.append(time.time() - t1)
            segs_n = n
            audio = info.duration
        except Exception as e:
            msg = str(e)
            hint = ""
            if "cublas" in msg or "cudnn" in msg:
                hint = "（CUDA 运行库未就绪：需把 nvidia 包的 bin 目录加入 PATH）"
            return {"ok": False, "err": f"{type(e).__name__}: {msg[:140]}{hint}"}

    best = min(times)
    return {"ok": True, "load": load, "time": best, "segs": segs_n,
            "audio": audio, "rtf": audio / best if best else 0,
            "all_times": times}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", nargs="?",
                    default=str(ROOT / "test_output" / "test.wav"))
    ap.add_argument("--models", nargs="+", default=["small"])
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    print("=" * 76)
    print("GPU / CPU 实测")
    print("=" * 76)
    print(describe())
    ensure_cuda_dll_path()
    print(f"  CUDA 可用: {gpu_available()}")
    print()

    wav = Path(args.wav)
    if not wav.exists():
        print(f"[x] 音频不存在：{wav}")
        sys.exit(1)

    rows = []
    for model in args.models:
        print("-" * 76)
        print(f"模型：{model}")
        for device, ct in (("cpu", "int8"), ("cuda", "int8_float16")):
            r = bench(model, device, ct, wav, args.repeat)
            if r["ok"]:
                print(f"  {device:>5}/{ct:<14} 加载 {r['load']:5.1f}s  "
                      f"转写 {r['time']:6.1f}s  RTF {r['rtf']:5.1f}x  "
                      f"{r['segs']} 段")
                rows.append((model, device, r["time"], r["rtf"]))
            else:
                print(f"  {device:>5}/{ct:<14} 失败 -> {r['err']}")

    if rows:
        print()
        print("=" * 76)
        print("汇总")
        print("=" * 76)
        by_model = {}
        for model, device, t, rtf in rows:
            by_model.setdefault(model, {})[device] = (t, rtf)
        for model, d in by_model.items():
            if "cpu" in d and "cuda" in d:
                speedup = d["cpu"][0] / d["cuda"][0]
                print(f"  {model:<12} CPU {d['cpu'][0]:6.1f}s -> "
                      f"GPU {d['cuda'][0]:6.1f}s   提速 {speedup:.1f}x")
            else:
                for dev, (t, rtf) in d.items():
                    print(f"  {model:<12} {dev} {t:.1f}s (RTF {rtf:.1f}x)")


if __name__ == "__main__":
    main()
