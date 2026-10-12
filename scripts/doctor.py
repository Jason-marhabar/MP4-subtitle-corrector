# -*- coding: utf-8 -*-
"""
环境诊断工具（GPU / CUDA / 模型 / FFmpeg）

用途：一条命令查清"为什么 GPU 用不了""模型在哪里""ffmpeg 是否就绪"。
这是给团队排查环境问题的第一入口，避免每个人都重新踩一遍坑。

用法：
    python scripts/doctor.py              # 常规检查
    python scripts/doctor.py --deep       # 额外真跑一次 GPU 推理（慢但结论可靠）
    python scripts/doctor.py --fix-hint   # 只打印修复命令

退出码：0 无阻塞问题；1 有阻塞问题（如 GPU 不可用但配置要求 GPU）
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PY = sys.executable

FIX_COMMANDS = {
    "cuda_libs": (
        f'"{PY}" -m pip install --no-deps '
        f"-i https://pypi.tuna.tsinghua.edu.cn/simple "
        f"nvidia-cublas-cu12 nvidia-cudnn-cu12"
    ),
    "whisper": (
        f'"{PY}" -m pip install faster-whisper pysrt srt '
        f"-i https://mirrors.aliyun.com/pypi/simple/"
    ),
}


def rule(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def ok(msg):
    print(f"  [ OK ] {msg}")


def warn(msg):
    print(f"  [!]  {msg}")


def bad(msg):
    print(f"  [FAIL] {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true", help="真跑一次 GPU 推理验证")
    ap.add_argument("--fix-hint", action="store_true", help="只打印修复命令")
    args = ap.parse_args()

    problems = []

    if args.fix_hint:
        print("按需执行以下命令：\n")
        for k, v in FIX_COMMANDS.items():
            print(f"# {k}\n{v}\n")
        return

    print("=" * 74)
    print("环境诊断")
    print("=" * 74)

    # ---------------------------------------------------------- Python
    rule("1. Python 与依赖")
    print(f"  Python {sys.version.split()[0]}")
    print(f"  解释器 {PY}")
    venv = sys.prefix != sys.base_prefix
    (ok if venv else warn)(f"虚拟环境：{'是' if venv else '否（建议用 venv 隔离）'}")
    for mod, label in (("faster_whisper", "faster-whisper"),
                       ("ctranslate2", "ctranslate2"),
                       ("pysrt", "pysrt"),
                       ("srt", "srt")):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "")
            ok(f"{label} {ver}".strip())
        except ImportError:
            bad(f"{label} 未安装")
            problems.append(f"{label} 缺失 -> {FIX_COMMANDS['whisper']}")

    # ---------------------------------------------------------- FFmpeg
    rule("2. FFmpeg")
    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        if p:
            ok(f"{exe}: {p}")
        else:
            bad(f"{exe} 未找到（需加入 PATH）")
            problems.append(f"{exe} 未找到")

    # ---------------------------------------------------------- GPU
    rule("3. GPU 与 CUDA")
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        ok(f"ctranslate2 {ctranslate2.__version__}，可见 CUDA 设备 {n} 个")
        try:
            types = ctranslate2.get_supported_compute_types("cuda")
            ok(f"编译期支持的 CUDA 计算类型：{sorted(types)}")
            warn("注意：这只反映编译期支持，不代表真能推理（见下方深检）")
        except Exception as e:
            bad(f"无法枚举 CUDA 计算类型：{e}")
    except ImportError:
        bad("ctranslate2 未安装")
        problems.append("ctranslate2 缺失")

    try:
        from model.gpu_env import (ensure_cuda_dll_path, find_nvidia_bin_dirs,
                                   gpu_available)
        dirs = find_nvidia_bin_dirs()
        if dirs:
            ok(f"找到 {len(dirs)} 个 pip 分发的 CUDA 库目录")
            for d in dirs:
                print(f"         {d}")
            # 检查关键 DLL 是否齐全
            needed = {"cublas64_12.dll": False, "cudnn64_9.dll": False}
            for d in dirs:
                for f in Path(d).glob("*.dll"):
                    if f.name in needed:
                        needed[f.name] = True
            for name, found in needed.items():
                (ok if found else bad)(f"{name}：{'已找到' if found else '缺失'}")
                if not found:
                    problems.append(f"{name} 缺失 -> {FIX_COMMANDS['cuda_libs']}")
            # 注意：ensure_cuda_dll_path() 返回 bool（是否找到库目录），
            # 不是"新增目录列表"。早期误当列表用 len() 会抛 TypeError，
            # 结果把后面的深检整段跳过（诊断工具自己出错的典型）。
            readied = ensure_cuda_dll_path()
            if readied:
                ok("已把 CUDA 库目录前置到 PATH（ctranslate2 按 PATH 解析 DLL）")
            else:
                bad("未能准备好 CUDA 库路径")
                problems.append("CUDA 库路径准备失败")
        else:
            warn("未找到 pip 分发的 CUDA 库（nvidia-cublas-cu12 / nvidia-cudnn-cu12）")
            problems.append(f"CUDA 库未安装 -> {FIX_COMMANDS['cuda_libs']}")

        shallow = gpu_available(deep=False)
        (ok if shallow else warn)(f"浅检 gpu_available() = {shallow}")
        if args.deep:
            print("  正在做深检（真跑一次推理，较慢）…")
            deep = gpu_available(deep=True)
            (ok if deep else bad)(f"深检（真实推理）gpu_available(deep=True) = {deep}")
            if not deep:
                problems.append("GPU 深检失败：CUDA 库虽在但推理不可用")
        else:
            warn("未做深检（加 --deep 可真实验证；首次会下载 tiny 模型）")
    except Exception as e:
        bad(f"GPU 检查异常：{type(e).__name__}: {e}")

    # 系统 CUDA 版本（提示常见的"版本不匹配"坑）
    nvcc = shutil.which("nvcc")
    if nvcc:
        try:
            import subprocess
            r = subprocess.run([nvcc, "--version"], capture_output=True,
                               text=True, timeout=30, errors="replace")
            line = [l for l in r.stdout.splitlines() if "release" in l]
            if line:
                warn(f"系统 CUDA Toolkit：{line[0].strip()}")
                warn("ctranslate2 需要 CUDA **12.x**；若系统是 13.x 等其它主版本，"
                     "它不会被使用，需另装 pip 版运行库")
        except Exception:
            pass

    # ---------------------------------------------------------- 模型
    rule("4. Whisper 模型缓存")
    hf = os.environ.get("HF_HOME")
    if hf:
        ok(f"HF_HOME = {hf}")
    else:
        warn("未设置 HF_HOME（模型会落到用户目录的默认缓存）")
    cache = Path(hf) / "hub" if hf else Path.home() / ".cache" / "huggingface" / "hub"
    if cache.exists():
        models = sorted(d.name for d in cache.glob("models--*"))
        if models:
            ok(f"已缓存 {len(models)} 个模型：")
            for m in models:
                size = sum(f.stat().st_size for f in (cache / m).rglob("*")
                           if f.is_file())
                print(f"         {m}  ({size/1024/1024:.0f} MB)")
        else:
            warn("缓存目录存在但无模型（首次使用会自动下载）")
    else:
        warn(f"模型缓存目录不存在：{cache}")
    if not os.environ.get("HF_ENDPOINT"):
        warn("未设置 HF_ENDPOINT；国内直连 huggingface.co 通常不可用，"
             "建议设 HF_ENDPOINT=https://hf-mirror.com")

    # ---------------------------------------------------------- 结论
    rule("结论")
    if problems:
        print(f"  发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"    · {p}")
        print("\n  修复命令：")
        for k, v in FIX_COMMANDS.items():
            print(f"    # {k}\n    {v}")
        sys.exit(1)
    print("  未发现阻塞性问题。")
    if not args.deep:
        print("  提示：加 --deep 可真实验证 GPU 推理（首次会下载 tiny 模型）。")


if __name__ == "__main__":
    main()
