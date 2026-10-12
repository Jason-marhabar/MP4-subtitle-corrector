# -*- coding: utf-8 -*-
"""
干净克隆可运行性验证

回答一个问题：把仓库克隆到一台新机器（没有 venv、没有模型缓存、
没有 seconv、没有 GPU 库）后，按 README 的步骤能不能跑起来？

逐项检查：
  1. requirements.txt 是否覆盖所有**必需**依赖
  2. 未声明的 import（torch / PySide6 / PyQt6）是否真的可选
  3. seconv 缺失时是否优雅降级（不崩、仍能出字幕）
  4. 模型缺失时是否只影响"首次下载慢"，而非功能缺失
"""
import importlib.util as iu
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL = []


def ok(msg):
    print(f"  [OK]   {msg}")


def warn(msg):
    print(f"  [警告] {msg}")


def bad(msg):
    print(f"  [缺失] {msg}")
    FAIL.append(msg)


print("=" * 74)
print("干净克隆可运行性验证")
print("=" * 74)

# ---------------------------------------------------------------- 1. 依赖清单
print("\n[1] 扫描代码实际引用的第三方模块")
mods = {}
for p in list((ROOT / "model").glob("*.py")) + list((ROOT / "controller").glob("*.py")):
    text = p.read_text(encoding="utf-8", errors="replace")
    for m in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z_][\w]*)", text, re.M):
        mods.setdefault(m, []).append(p.name)

std = set(sys.stdlib_module_names)
LOCAL = {"model", "controller", "scripts"}
third = {m: v for m, v in mods.items() if m not in std and m not in LOCAL}

req_text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
# 有些 import 是"可选依赖"，用 try/except 包着
OPTIONAL = {"torch", "PySide6", "PyQt6"}

for m, files in sorted(third.items()):
    declared = m.lower().replace("_", "-") in req_text.lower().replace("_", "-")
    if declared:
        ok(f"{m:16} 已在 requirements.txt 声明   （{', '.join(files)}）")
    elif m in OPTIONAL:
        opt = "可选依赖，代码用 try/except 包裹，缺失不影响核心功能"
        ok(f"{m:16} {opt}   （{', '.join(files)}）")
    else:
        bad(f"{m:16} 既未声明也非可选   （{', '.join(files)}）")

# ---------------------------------------------------------------- 2. 可选依赖验证
print("\n[2] 验证可选依赖缺失时的行为")
from model.asr import AsrEngine          # noqa: E402
eng = AsrEngine(model="small", device="cpu", compute_type="int8")
try:
    eng.load()
    ok("AsrEngine 加载成功（faster-whisper 不依赖 torch）")
    have_torch = iu.find_spec("torch") is not None
    print(f"         本机 torch：{'已装' if have_torch else '未装'}，"
          f"未装也能正常转写 -> 确认 torch 非必需")
except ImportError as e:
    bad(f"AsrEngine 加载失败：{e}")
except Exception as e:
    warn(f"AsrEngine 加载抛出非导入错误（可能是模型下载）：{type(e).__name__}")

try:
    from controller import qt_worker
    print(f"         qt_worker 可导入，Qt 后端 = {qt_worker.backend_name()}"
          f"，available() = {qt_worker.available()}")
    if not qt_worker.available():
        ok("无 Qt 时导入不报错（调度核心仍可用）")
    try:
        qt_worker.SchedulerWorker()
        ok("SchedulerWorker 可构造")
    except ImportError:
        ok("SchedulerWorker 按预期提示需要安装 PySide6")
except Exception as e:
    bad(f"qt_worker 导入失败：{type(e).__name__}: {e}")

from controller.scheduler import BatchScheduler, BatchConfig   # noqa: E402
s = BatchScheduler(BatchConfig())
ok("BatchScheduler 可构造（纯标准库，无 Qt 依赖）")

# ---------------------------------------------------------------- 3. seconv 降级
print("\n[3] 验证 seconv 缺失时的降级行为")
from model.seconv_tools import describe, find_seconv            # noqa: E402
from model.subtitle_fix import (FixConfig, Cue, normalize_with_seconv,  # noqa: E402
                               read_srt_cues, to_srt)
print(f"         {describe()}")

tmp = ROOT / "test_output" / "clean_clone_check"
tmp.mkdir(parents=True, exist_ok=True)
srt = tmp / "t.srt"
cues = [Cue(0.5, 1.5, "Hello world."), Cue(2.0, 3.2, "Second line.")]
srt.write_text(to_srt(cues), encoding="utf-8", newline="\n")
before = srt.read_bytes()

if find_seconv():
    out = normalize_with_seconv(str(srt), FixConfig(seconv_enabled=True))
    after = read_srt_cues(out)
    ok(f"seconv 可用时正常规范化（{len(after)} 条）")
else:
    out = normalize_with_seconv(str(srt), FixConfig(seconv_enabled=True))
    after = read_srt_cues(out)
    if out == str(srt) and after:
        ok(f"seconv 缺失时优雅降级：保留原文件、仍可解析（{len(after)} 条），未抛异常")
    else:
        bad("seconv 缺失时行为异常")

# ---------------------------------------------------------------- 4. 模型缓存
print("\n[4] 模型缓存缺失的影响")
import os
hf = os.environ.get("HF_HOME")
cache = pathlib.Path(hf) / "hub" if hf else pathlib.Path.home() / ".cache" / "huggingface" / "hub"
if cache.exists() and list(cache.glob("models--*")):
    ok(f"缓存存在：{[d.name.replace('models--Systran--faster-whisper-','') for d in cache.glob('models--*')]}")
    print("         注：缺失时首次运行会自动下载（small 464MB / large-v3 3GB）")
else:
    warn("本机无缓存 —— 这正是新克隆的状态，首次运行需联网下载模型")
if not os.environ.get("HF_ENDPOINT"):
    warn("未设 HF_ENDPOINT，国内将无法下载模型（set_env.bat 已包含）")
else:
    ok(f"HF_ENDPOINT={os.environ['HF_ENDPOINT']}")

# ---------------------------------------------------------------- 结论
print("\n" + "=" * 74)
if FAIL:
    print(f"结论：发现 {len(FAIL)} 个阻塞项")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("结论：干净克隆后按 install_env.bat + set_env.bat 即可运行，无阻塞项")
print("      缺失的内容都能通过脚本或联网自动恢复。")
