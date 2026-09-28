# -*- coding: utf-8 -*-
"""
5号 测试工程师 · FFmpeg 模块测试执行脚本
针对 3号 交付的 FFmpegProcessor（extract_audio / copy_mp4_stream）执行 TC-FF 用例。
运行：D:\python3.11\python.exe 5号_FFmpeg模块测试执行.py
"""
import os
import sys
import traceback

sys.path.insert(0, r"D:\yinpin\3号_FFmpeg模块\my_app")

from model.ffmpeg_processor import FFmpegProcessor

MAT = r"D:\yinpin\5号_测试素材"
OUT = r"D:\yinpin\5号_测试输出"
processor = FFmpegProcessor()

results = []


def record(case_id, name, passed, detail, note=""):
    results.append({
        "case": case_id, "name": name,
        "passed": passed, "detail": detail, "note": note,
    })
    tag = "PASS" if passed else "FAIL"
    print(f"[{tag}] {case_id} {name}")
    print(f"        {detail}")
    if note:
        print(f"        备注: {note}")


def try_call(case_id, name, fn, expect_success):
    """执行接口调用并判断结果"""
    try:
        r = fn()
        if expect_success:
            record(case_id, name, True, f"成功，返回: {r}")
        else:
            record(case_id, name, False, f"预期报错但成功了，返回: {r}",
                   "模块未对异常输入报错，建议增加输入校验")
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        if expect_success:
            record(case_id, name, False, f"预期成功但报错：{msg}")
        else:
            record(case_id, name, True, f"正确抛出异常：{msg}")


os.makedirs(OUT, exist_ok=True)
print("=" * 60)
print("FFmpeg 模块测试执行（5号）")
print("=" * 60)

# ---------- TC-FF-001 正常提取 ----------
print("\n[TC-FF-001] 正常提取音频")
src = os.path.join(MAT, "中文测试 视频(1).mp4")  # 用中文路径的素材兼顾002
out_wav = os.path.join(OUT, "tc001", "out.wav")


def ff001():
    return processor.extract_audio(src, out_wav)


try_call("TC-FF-001", "正常提取音频(16k单声道)", ff001, True)

# 用 ffprobe 验证规格（独立验证）
import subprocess
if os.path.exists(out_wav):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,sample_rate,channels",
         "-of", "default=noprint_wrappers=1", out_wav],
        capture_output=True, text=True
    )
    spec_ok = ("pcm_s16le" in probe.stdout and "16000" in probe.stdout
               and "channels=1" in probe.stdout)
    record("TC-FF-001b", "WAV规格独立验证(ffprobe)", spec_ok,
           probe.stdout.strip().replace("\n", " | ") or probe.stderr.strip(),
           "验证编码/采样率/声道是否符合 Whisper 需求")
    # 更新001结果：规格通过才真通过
    results[0]["passed"] = results[0]["passed"] and spec_ok

# ---------- TC-FF-002 中文路径 ----------
print("\n[TC-FF-002] 中文路径 + 中文文件名")
out_wav2 = os.path.join(OUT, "tc002", "中文输出.wav")


def ff002():
    return processor.extract_audio(
        os.path.join(MAT, "中文测试 视频(1).mp4"), out_wav2)


try_call("TC-FF-002", "中文路径/文件名", ff002, True)

# ---------- TC-FF-003 无音频视频 ----------
print("\n[TC-FF-003] 无音频轨视频")
out_wav3 = os.path.join(OUT, "tc003", "noaudio.wav")


def ff003():
    return processor.extract_audio(os.path.join(MAT, "无音频视频.mp4"), out_wav3)


try_call("TC-FF-003", "无音频视频应友好报错", ff003, False)

# ---------- TC-FF-004 损坏文件 ----------
print("\n[TC-FF-004] 损坏文件")
out_wav4 = os.path.join(OUT, "tc004", "corrupt.wav")


def ff004():
    return processor.extract_audio(os.path.join(MAT, "损坏文件.mp4"), out_wav4)


try_call("TC-FF-004", "损坏文件应报错不崩溃", ff004, False)

# ---------- TC-FF-005 非视频文件(txt) ----------
print("\n[TC-FF-005] 假视频文件(.txt)")
out_wav5 = os.path.join(OUT, "tc005", "fake.wav")


def ff005():
    return processor.extract_audio(os.path.join(MAT, "假文件.txt"), out_wav5)


try_call("TC-FF-005", "非视频文件应报错", ff005, False)

# ---------- TC-FF-005b 真实视频但mkv扩展名 ----------
print("\n[TC-FF-005b] mkv 扩展名(内容是视频)")
out_wav5b = os.path.join(OUT, "tc005b", "mkv.wav")


def ff005b():
    return processor.extract_audio(os.path.join(MAT, "真实视频.mkv"), out_wav5b)


try_call("TC-FF-005b", "mkv真实视频(观察兼容性)", ff005b, True)

# ---------- TC-FF-006 路径不存在 ----------
print("\n[TC-FF-006] 输入路径不存在")
out_wav6 = os.path.join(OUT, "tc006", "none.wav")


def ff006():
    return processor.extract_audio(r"D:\不存在的目录\不存在的文件.mp4", out_wav6)


try_call("TC-FF-006", "不存在的输入路径应报错", ff006, False)

# ---------- TC-FF-007 流拷贝封装 ----------
print("\n[TC-FF-007] MP4流拷贝封装")
out_mp4 = os.path.join(OUT, "tc007", "output.mp4")


def ff007():
    return processor.copy_mp4_stream(
        os.path.join(MAT, "中文测试 视频(1).mp4"), out_mp4)


try_call("TC-FF-007", "流拷贝封装输出MP4", ff007, True)

# 验证输出 MP4 有音视频流
if os.path.exists(out_mp4):
    probe2 = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name",
         "-of", "default=noprint_wrappers=1", out_mp4],
        capture_output=True, text=True
    )
    has_v = "codec_type=video" in probe2.stdout
    has_a = "codec_type=audio" in probe2.stdout
    record("TC-FF-007b", "输出MP4含音视频流", has_v and has_a,
           probe2.stdout.strip().replace("\n", " | "),
           "验证封装后画面和声音都在")

# ---------- TC-FF-008 输出目录不存在 ----------
print("\n[TC-FF-008] 输出目录不存在(应自动创建)")
out_wav8 = os.path.join(OUT, "tc008", "深", "层", "目录", "auto.wav")


def ff008():
    return processor.extract_audio(
        os.path.join(MAT, "中文测试 视频(1).mp4"), out_wav8)


try_call("TC-FF-008", "自动创建深层输出目录", ff008, True)

# ---------- TC-FF-009 特殊字符文件名 ----------
print("\n[TC-FF-009] 空格/括号/& 特殊字符")
out_wav9 = os.path.join(OUT, "tc009", "特 殊&输出.wav")


def ff009():
    return processor.extract_audio(
        os.path.join(MAT, "特 殊&字符(文件).mp4"), out_wav9)


try_call("TC-FF-009", "特殊字符文件名", ff009, True)

# ---------- 汇总 ----------
print("\n" + "=" * 60)
passed = sum(1 for r in results if r["passed"])
total = len(results)
print(f"汇总：{passed} 通过 / {total} 总计")
print("=" * 60)
for r in results:
    print(f"  {'✅' if r['passed'] else '❌'} {r['case']} {r['name']}")
