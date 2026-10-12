# -*- coding: utf-8 -*-
"""
一键验收脚本

用途：不必手工逐个跑测试，一条命令完成全部功能验收并给出结论。

验收内容：
  阶段 1  环境（依赖 / ffmpeg / GPU / 模型缓存）
  阶段 2  第四板块：端到端转写 + 字幕校正（真实素材）
  阶段 3  产出质量：编码、结构、对齐精度、文本完整性
  阶段 4  第三板块：remux 是否真的不产生时间轴偏移（逐帧比对）
  阶段 5  第二板块：调度层（批量、取消、失败隔离）
  阶段 6  人工验收提示（Aboboo 导入步骤）

用法：
    python scripts/acceptance.py                # 全流程（约 1~2 分钟，GPU）
    python scripts/acceptance.py --cpu          # 强制 CPU（慢，约 2 分钟）
    python scripts/acceptance.py --skip-sched   # 跳过调度层用例
    python scripts/acceptance.py --quick        # 跳过转写，只查环境与已有产出

退出码：0 全部通过；1 有失败项
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 让国内网络可用（不覆盖用户已设的值）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(ROOT / ".hf")

CHECKS = []          # (阶段, 项目, 是否通过, 说明)


def stage(n, title):
    print("\n" + "=" * 76)
    print(f"阶段 {n}：{title}")
    print("=" * 76)


def check(item, ok, detail=""):
    CHECKS.append((item, bool(ok), detail))
    tag = "[通过]" if ok else "[失败]"
    print(f"  {tag} {item}" + (f"    {detail}" if detail else ""))


def info(msg):
    print(f"         {msg}")


# ---------------------------------------------------------------- 阶段 1
def stage1_env():
    from model.gpu_env import (ensure_cuda_dll_path, find_nvidia_bin_dirs,
                               gpu_available)
    import ctranslate2

    stage(1, "环境")

    check("Python 版本", sys.version_info[:2] >= (3, 9),
          f"{sys.version.split()[0]}")
    for mod, label in (("faster_whisper", "faster-whisper"),
                       ("ctranslate2", "ctranslate2"),
                       ("pysrt", "pysrt")):
        try:
            __import__(mod)
            check(f"依赖 {label}", True)
        except ImportError:
            check(f"依赖 {label}", False, "未安装")

    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        check(f"依赖 {exe}", bool(p), p or "未找到")

    dirs = find_nvidia_bin_dirs()
    check("CUDA 运行库（pip 分发）", bool(dirs), f"{len(dirs)} 个目录")
    if dirs:
        ensure_cuda_dll_path()
        needed = {"cublas64_12.dll": False, "cudnn64_9.dll": False}
        for d in dirs:
            for f in Path(d).glob("*.dll"):
                if f.name in needed:
                    needed[f.name] = True
        for k, v in needed.items():
            check(f"  {k}", v, "已找到" if v else "缺失")

    check("CUDA 设备可见", ctranslate2.get_cuda_device_count() > 0,
          f"{ctranslate2.get_cuda_device_count()} 个")
    deep = gpu_available(deep=True)
    check("GPU 可真实推理（深检）", deep,
          "是" if deep else "否（将回退 CPU）")

    cache = Path(os.environ["HF_HOME"]) / "hub"
    models = sorted(d.name.replace("models--Systran--faster-whisper-", "")
                    for d in cache.glob("models--*")) if cache.exists() else []
    check("Whisper 模型缓存", bool(models), ", ".join(models) or "无（首次会下载）")


# ---------------------------------------------------------------- 阶段 2
def stage2_pipeline(use_cpu, quick):
    from model.asr import AsrEngine
    from model.pipeline import extract_audio, process, remux_mp4
    from model.subtitle_fix import FixConfig, fix_subtitles, self_check
    from model.waveform import extract_boundaries, filter_speech

    video = ROOT / "test.mp4"
    out_dir = ROOT / "test_output" / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)

    stage(2, "第四板块：转写 + 字幕校正（真实素材）")
    if not video.exists():
        check("测试素材 test.mp4", False, "不存在")
        return None, None, None
    check("测试素材 test.mp4", True, f"{video.stat().st_size/1024/1024:.1f} MB")

    if quick:
        info("--quick：跳过转写，仅使用已有产出")
        srt = ROOT / "test_output" / "pipeline" / "test.srt"
        if srt.exists():
            return srt, None, None
        check("已有产出", False, "不存在，请去掉 --quick")
        return None, None, None

    from model.gpu_env import gpu_available
    device = "cpu" if use_cpu else ("cuda" if gpu_available() else "cpu")
    ctype = "int8" if device == "cpu" else "int8_float16"
    info(f"计算设备：{device} / {ctype}")

    # 提取音频
    wav = out_dir / "test.wav"
    if not wav.exists():
        extract_audio(str(video), str(wav))
    check("音频提取（16kHz 单声道）", wav.exists(),
          f"{wav.stat().st_size/1024/1024:.1f} MB")

    # 波形边界
    b = extract_boundaries(str(wav))
    check("波形边界提取", len(b.speech) > 0, b.summary())

    # 转写
    engine = AsrEngine(model="small", device=device, compute_type=ctype,
                       language="en")
    t0 = time.time()
    tr = engine.transcribe(str(wav), language="en")
    elapsed = time.time() - t0
    rtf = tr.duration / elapsed if elapsed else 0
    check("ASR 转写", len(tr.segments) > 0,
          f"{len(tr.segments)} 段，{elapsed:.1f}s（RTF {rtf:.1f}x，{device}）")

    # 校正
    cues, rep = fix_subtitles(tr.segments, b, FixConfig())
    check("字幕校正", len(cues) > 0, rep.summary())

    srt = out_dir / "test.srt"
    from model.subtitle_fix import write_srt
    write_srt(cues, str(srt))
    check("写出 SRT", srt.exists(), f"{srt.stat().st_size} bytes")

    return srt, cues, b


# ---------------------------------------------------------------- 阶段 3
def stage3_quality(srt, cues, b):
    stage(3, "产出质量")
    if srt is None or not srt.exists():
        check("产出存在", False, "无产出，跳过质量检查")
        return

    raw = srt.read_bytes()
    check("编码为 UTF-8 无 BOM", raw[:3] != b"\xef\xbb\xbf",
          " ".join(f"{x:02X}" for x in raw[:3]))
    check("换行为 LF（无 CRLF）", b"\r\n" not in raw)

    # 结构（用独立校验器复核，不只信自检）
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_srt.py"),
                        str(srt), "--media", str(ROOT / "test.mp4")],
                       capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    n_err = re.search(r"结果：(\d+) 个 ERROR", out)
    n_err = int(n_err.group(1)) if n_err else -1
    overlap = re.search(r"重叠数=(\d+)", out)
    check("独立校验器无 ERROR", n_err == 0,
          f"{n_err} 个 ERROR" if n_err >= 0 else "无法解析")
    check("无字幕重叠", overlap and overlap.group(1) == "0",
          f"重叠数={overlap.group(1) if overlap else '?'}")

    if cues and b:
        from model.subtitle_fix import self_check, FixConfig
        probs = self_check(cues, FixConfig())
        check("结构自检通过", not probs, f"{len(probs)} 项问题")

        anchors = filter_speech_safe(b)
        onsets = [a for a, _ in anchors]
        hit = sum(1 for c in cues
                  if min((abs(c.start - o) for o in onsets), default=9) <= 0.005)
        pct = hit / len(cues) * 100 if cues else 0
        check("起点对齐精度 ≥90%", pct >= 90,
              f"{hit}/{len(cues)} = {pct:.0f}% 精确命中真实语音起点")

        offs = {a: o for a, o in anchors}
        early = 0
        for c in cues:
            reg = [(a, o) for a, o in anchors if a - 0.02 <= c.start <= o + 0.02]
            if reg and reg[0][1] - c.end > 0.25:
                early += 1
        check("字幕未提前于语音消失", early == 0, f"{early} 条提前结束")


def filter_speech_safe(b):
    from model.waveform import filter_speech
    return filter_speech(b.speech, 0.30)


# ---------------------------------------------------------------- 阶段 4
def stage4_remux():
    stage(4, "第三板块：remux 是否引入时间轴偏移（逐帧比对）")
    video = ROOT / "test.mp4"
    out = ROOT / "test_output" / "acceptance" / "test_remux.mp4"
    if not video.exists():
        check("remux 素材", False, "test.mp4 不存在")
        return
    from model.pipeline import remux_mp4
    if not out.exists():
        remux_mp4(str(video), str(out))
    check("remux 输出", out.exists(), f"{out.stat().st_size/1024/1024:.1f} MB")

    fp = shutil.which("ffprobe")
    if not fp:
        check("ffprobe 可用", False)
        return

    def pts(path):
        r = subprocess.run(
            [fp, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, errors="replace")
        return [float(x) for x in r.stdout.split() if x.strip()]

    a, b2 = pts(video), pts(out)
    n = min(len(a), len(b2))
    if n == 0:
        check("帧时间戳比对", False, "未取到帧")
        return
    maxdiff = max(abs(x - y) for x, y in zip(a[:n], b2[:n]))
    check("remux 逐帧时间轴一致", maxdiff == 0,
          f"比对 {n} 帧，最大偏差 {maxdiff*1000:.6f} ms")


# ---------------------------------------------------------------- 阶段 5
def stage5_scheduler():
    stage(5, "第二板块：调度层（批量 / 取消 / 失败隔离）")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "test_scheduler.py")],
                       capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"结果：(\d+)/(\d+) 通过", out)
    if not m:
        check("调度层测试", False, "无法解析输出")
        return
    passed, total = int(m.group(1)), int(m.group(2))
    check("调度层测试全部通过", passed == total, f"{passed}/{total}")


# ---------------------------------------------------------------- 阶段 6
def stage6_manual(srt):
    stage(6, "人工验收（Aboboo 导入）")
    if srt is None or not srt.exists():
        info("无产出，跳过")
        return
    # 按 Aboboo 的自动加载约定，把 SRT 复制到视频同目录并同名
    dst = ROOT / "test.srt"
    shutil.copyfile(srt, dst)
    print(f"  已生成可导入的字幕：{dst}")
    print()
    print("  请按以下步骤人工确认（这是唯一无法自动化的环节）：")
    print("    1. 打开 Aboboo")
    print(f"    2. 课件 -> 打开  {ROOT / 'test.mp4'}")
    print("    3. Aboboo 会自动加载同名的 test.srt")
    print("    4. 逐句核对：字幕是否在开口时出现、念完时消失")
    print()
    print("  若 Aboboo 缓存了旧字幕：视频窗口右键 -> 字幕 -> 字幕加载，重选该文件")


# ---------------------------------------------------------------- 汇总
def summary():
    print("\n" + "=" * 76)
    print("验收汇总")
    print("=" * 76)
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    for item, ok, detail in CHECKS:
        if not ok:
            print(f"  [失败] {item}    {detail}")
    print()
    if passed == total:
        print(f"  结果：全部通过（{passed}/{total}）")
        print()
        print("  自动部分验收完成。请继续完成阶段 6 的人工确认（Aboboo 导入）。")
        print("  未完成的功能模块（非本脚本范围）：")
        print("    · 第一板块 GUI 界面（view/ 未创建）")
        print("    · 程序入口 main.py")
        print("    · 第六板块打包（PyInstaller 配置、一键安装脚本、README）")
    else:
        print(f"  结果：{passed}/{total} 通过，{total-passed} 项失败（见上）")
    return 0 if passed == total else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpu", action="store_true", help="强制用 CPU 转写")
    ap.add_argument("--quick", action="store_true", help="跳过转写，只查环境与已有产出")
    ap.add_argument("--skip-sched", action="store_true", help="跳过调度层测试")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 76)
    print("一键验收")
    print("=" * 76)
    print(f"  项目目录：{ROOT}")

    stage1_env()
    srt, cues, b = stage2_pipeline(args.cpu, args.quick)
    stage3_quality(srt, cues, b)
    if not args.quick:
        stage4_remux()
    if not args.skip_sched:
        stage5_scheduler()
    stage6_manual(srt)

    code = summary()
    print(f"\n  总耗时 {time.time()-t0:.1f}s")
    sys.exit(code)


if __name__ == "__main__":
    main()
