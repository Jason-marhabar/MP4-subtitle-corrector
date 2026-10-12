# -*- coding: utf-8 -*-
"""
CUDA 运行库自动配置（解决 GPU 加速"库找不到"问题）

问题背景（实测）
----------------
ctranslate2 的 Windows wheel 是按 **CUDA 12.x** 编译的，需要：
  · ``cublas64_12.dll`` / ``cublasLt64_12.dll``  (cuBLAS 12)
  · ``cudnn64_9.dll`` 等                          (cuDNN 9，语音识别有卷积层，必需)

但在本机实测到的坑有三个：

1. **CUDA Toolkit 13.0 不管用**。系统装了 CUDA 13.0（``nvcc`` 可见），
   但 ctranslate2 要的是 12.x，主版本不匹配。装 12.x Toolkit 要几 GB。

2. **pip 分发版是更轻的选择**：``nvidia-cublas-cu12``（553MB wheel）
   + ``nvidia-cudnn-cu12``（743MB wheel），装完 DLL 落在
   ``venv/Lib/site-packages/nvidia/<pkg>/bin``。

3. **关键坑：``os.add_dll_directory()`` 无效**。
   ctranslate2 是 C++ 扩展，内部用 ``LoadLibrary("cublas64_12.dll")``
   按 **PATH** 解析，不走 Python 的 DLL 目录机制。实测：
   · 只调 ``os.add_dll_directory``      -> 仍报 "cublas64_12.dll is not found"
   · 把目录加进 ``PATH``                 -> 成功
   所以本模块**把目录前置到 PATH**。

另外注意：``WhisperModel(device='cuda')`` 构造是**惰性**的，构造成功不代表
CUDA 可用 —— 真正的报错要到 ``transcribe()`` 第一次推理时才出现。诊断时
必须真跑一次推理，不能只看构造是否抛错。

用法
----
    from model.gpu_env import ensure_cuda_dll_path, gpu_available, describe
    ensure_cuda_dll_path()          # 在创建 AsrEngine 之前调用即可
    if gpu_available():
        ...                         # 可以放心用 device="cuda"
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

_APPLIED = False
_FOUND_DIRS: List[str] = []


def find_nvidia_bin_dirs() -> List[str]:
    """定位 pip 分发的 CUDA 库目录（nvidia/*/bin）。"""
    dirs: List[str] = []
    roots = []

    # 1) 当前虚拟环境 / 解释器
    prefixes = {sys.prefix, sys.base_prefix}
    for pre in prefixes:
        if pre:
            roots.append(Path(pre) / "Lib" / "site-packages" / "nvidia")
    # 2) 用户级 site-packages（有些环境装在这里）
    try:
        import site
        for sp in site.getsitepackages() + [site.getusersitepackages()]:
            roots.append(Path(sp) / "nvidia")
    except Exception:
        pass

    seen = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for p in root.rglob("bin"):
                if p.is_dir():
                    s = str(p)
                    if s not in seen:
                        seen.add(s)
                        dirs.append(s)
        except OSError:
            continue
    return dirs


def apply_path(dirs: Optional[List[str]] = None) -> List[str]:
    """把 CUDA 库目录**前置**到 PATH，返回实际加入的目录列表。

    为什么是 PATH 而不是 os.add_dll_directory：见模块文档第 3 点。
    """
    global _APPLIED, _FOUND_DIRS
    dirs = dirs if dirs is not None else find_nvidia_bin_dirs()
    if not dirs:
        return []
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    lowered = {p.lower() for p in parts}
    added = []
    for d in dirs:
        if d.lower() not in lowered:
            added.append(d)
    if added:
        os.environ["PATH"] = os.pathsep.join(added + parts)
    _FOUND_DIRS = dirs
    _APPLIED = True
    # 兼容：也调一次 add_dll_directory，对纯 Python 加载的场景有帮助，
    # 但对 ctranslate2 无效，所以不能只靠它。
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError, FileNotFoundError):
            pass
    return added


def ensure_cuda_dll_path() -> bool:
    """幂等地准备好 CUDA 库路径。返回是否找到任何库目录。"""
    if not _APPLIED:
        added = apply_path()
        if added:
            log.info("已加入 CUDA 库目录到 PATH：%s", "; ".join(added))
    return bool(_FOUND_DIRS)


def gpu_available(deep: bool = False) -> bool:
    """CUDA 是否可用于推理。

    deep=False（默认）
        只检查计算类型是否可枚举。**注意这不代表真能推理** —— 本机实测
        在缺 cublas 时 ``get_supported_compute_types('cuda')`` 仍返回完整集合，
        因为它只反映编译期支持，不做实际加载。
    deep=True
        真做一次最小推理来验证。慢（要加载模型），但结论可靠。
        诊断脚本应该用 deep=True。
    """
    ensure_cuda_dll_path()
    try:
        import ctranslate2
    except ImportError:
        return False

    if not deep:
        try:
            return bool(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:
            return False

    # 深检：真跑一次推理
    try:
        from faster_whisper import WhisperModel
        import numpy as np
        m = WhisperModel("tiny", device="cuda", compute_type="int8_float16")
        audio = np.zeros(16000, dtype=np.float32)      # 1 秒静音
        list(m.transcribe(audio, language="en")[0])
        return True
    except Exception as e:
        log.debug("GPU 深检失败：%s", e)
        return False


def cuda_error_hint(exc: BaseException) -> Optional[str]:
    """把 CUDA 相关异常转成可操作的建议；非 CUDA 异常返回 None。"""
    msg = str(exc)
    low = msg.lower()
    if "cublas" in low or "cudnn" in low or "cuda" in low:
        return (
            "GPU 加速不可用（缺少 CUDA 12 运行库）。三种解决方式：\n"
            "  1) 用 pip 装（推荐，约 1.3GB，无需管理员权限）：\n"
            "     venv\\Scripts\\python.exe -m pip install --no-deps \\\n"
            "       -i https://pypi.tuna.tsinghua.edu.cn/simple \\\n"
            "       nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
            "     装完后 model.gpu_env.ensure_cuda_dll_path() 会自动接好 PATH\n"
            "  2) 安装 CUDA 12.x Toolkit + cuDNN 9（体积大）\n"
            "  3) 改用 CPU：AsrEngine(device='cpu', compute_type='int8')\n"
            "     （实测 small 模型 RTF≈6x，313s 音频约 52s）\n"
            "注意：系统里的 CUDA 13.0 对 ctranslate2 无效，必须是 12.x。"
        )
    return None


def describe() -> str:
    """一行环境摘要，便于日志与 bug 报告。"""
    lines = []
    try:
        import ctranslate2
        lines.append(f"ctranslate2 {ctranslate2.__version__}")
    except ImportError:
        lines.append("ctranslate2 未安装")
    try:
        import torch  # noqa: F401
        lines.append("torch 已装")
    except ImportError:
        pass
    dirs = _FOUND_DIRS or find_nvidia_bin_dirs()
    if dirs:
        lines.append(f"CUDA 库目录 {len(dirs)} 个")
    else:
        lines.append("未找到 pip 分发的 CUDA 库")
    try:
        import ctranslate2
        lines.append(f"CUDA 设备数 {ctranslate2.get_cuda_device_count()}")
    except Exception:
        pass
    return "  " + " | ".join(lines)
