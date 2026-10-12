# -*- coding: utf-8 -*-
"""
第四板块校正模块的自测脚本（验证 check_srt.py 真的能抓到各类 bug）

为什么需要它
------------
字幕校正的核心风险不是"报错"，而是**静默地把音频和文本对错位**：
时间戳全部合法、格式完全正确、seconv lint 也通过，但听感上字幕和语音
对不上。这类 bug 只能靠"对比基线 + 顺序检查"发现。

本脚本故意构造 5 种缺陷文件，逐一断言 check_srt.py 必须报错。
这既是自测，也是第五板块"字幕专项测试"的可执行用例原型。

用法：
    python scripts/test_subtitle_checks.py            # 全部用例
    python scripts/test_subtitle_checks.py --keep     # 保留生成的用例文件
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_srt.py"
OUT = ROOT / "test_output" / "selftest"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = """1
00:00:01,000 --> 00:00:03,000
Hello world.

2
00:00:04,000 --> 00:00:06,000
This is a test.

3
00:00:07,000 --> 00:00:09,000
Goodbye world.
"""

SWAPPED = """1
00:00:01,000 --> 00:00:03,000
Hello world.

2
00:00:04,000 --> 00:00:06,000
Goodbye world.

3
00:00:07,000 --> 00:00:09,000
This is a test.
"""

OVERLAP = """1
00:00:01,000 --> 00:00:04,000
Hello world.

2
00:00:03,500 --> 00:00:06,000
This is a test.
"""

ZERO_DUR = """1
00:00:01,000 --> 00:00:01,000
Zero duration.

2
00:00:04,000 --> 00:00:06,000


3
00:00:07,000 --> 00:00:09,000
Goodbye world.
"""

TRUNCATED = """1
00:00:01,000 --> 00:00:03,000
Hello world.
"""


def write(name, text, encoding="utf-8", bom=False):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    with open(p, "w", encoding=encoding, newline="\n") as f:
        if bom:
            f.write("\ufeff")
        f.write(text)
    return p


def run_checker(args):
    """跑校验器并把输出以 UTF-8 读回。

    不能直接用 capture_output + text=True：Windows 上管道会按 ANSI 代码页
    （GBK）解码 UTF-8 输出，中文变成替换符，导致子串断言全部误判。
    改为让子进程把输出写进文件，再按 UTF-8 读取。
    """
    import os
    import tempfile
    OUT.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".txt", dir=str(OUT))
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            r = subprocess.run([sys.executable, str(CHECKER)] + [str(a) for a in args],
                               stdout=fh, stderr=subprocess.STDOUT, text=True)
        out = Path(tmp).read_text(encoding="utf-8", errors="replace")
        return r.returncode, out
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def expect(name, args, want_code, want_substrings):
    code, out = run_checker(args)
    ok_code = (code == want_code)
    missing = [s for s in want_substrings if s not in out]
    ok = ok_code and not missing
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"         期望退出码 {want_code}，实际 {code}")
        for s in missing:
            print(f"         输出中未找到: {s!r}")
        # 打印相关输出便于定位
        for line in out.splitlines():
            if "ERROR" in line or "WARN" in line or "结果" in line:
                print(f"         | {line}")
    return ok


def main():
    keep = "--keep" in sys.argv
    print("=" * 72)
    print("第四板块校正/校验自测")
    print("=" * 72)

    base = write("base.srt", BASE)
    swapped = write("bug_swapped.srt", SWAPPED)
    overlap = write("bug_overlap.srt", OVERLAP)
    zerodur = write("bug_zero_dur.srt", ZERO_DUR)
    truncated = write("bug_truncated.srt", TRUNCATED)
    z = write("bug_gbk.srt",
              "1\n00:00:01,000 --> 00:00:03,000\n中文字幕测试。\n",
              encoding="gbk")
    bom = write("bug_bom.srt", BASE, bom=True)

    results = []

    print("\n【结构检查】")
    results.append(expect("干净的基准文件应完全通过", [base], 0,
                          ["结果：0 个 ERROR", "全部检查通过"]))
    results.append(expect("重叠必须报 ERROR", [overlap], 1,
                          ["重叠"]))
    results.append(expect("零时长/空文本必须报 ERROR", [zerodur], 1,
                          ["结束时间不大于起始时间", "文本为空"]))
    # 注意：必须用含非 ASCII 字符的内容来测编码。
    # 纯 ASCII 文件的 GBK 与 UTF-8 字节完全相同，物理上无法区分，
    # 校验器（正确地）不会报错——这是设计缺陷最小的行为。
    results.append(expect("含中文的 GBK 文件必须报 ERROR", [z], 1,
                          ["编码不是 UTF-8"]))
    results.append(expect("BOM 必须给出 WARN", [bom], 2,
                          ["BOM"]))

    print("\n【内容保真检查（最危险的一类 bug）】")
    results.append(expect("自身对比应判定完全一致", [base, "--baseline", base], 0,
                          ["文本序列=完全一致"]))
    results.append(expect("段落顺序被调换必须报 ERROR（音频文本错位）",
                          [swapped, "--baseline", base], 1,
                          ["顺序相对基线发生变化"]))
    results.append(expect("文本被截断必须报 ERROR（相似度过低）",
                          [truncated, "--baseline", base], 1,
                          ["文本相似度"]))

    print("\n【组合：真实校正场景】")
    real_raw = ROOT / "test_output" / "probe" / "raw_small.srt"
    real_snapped = ROOT / "test_output" / "probe" / "snapped_small.srt"
    if real_raw.exists() and real_snapped.exists():
        results.append(expect("真实吸附输出应对基线保真",
                              [real_snapped, "--baseline", real_raw], 2,
                              ["文本序列=完全一致", "0 个 ERROR"]))
    else:
        print("  [SKIP] 真实素材不存在，跳过")

    if not keep:
        for f in OUT.glob("*.srt"):
            f.unlink()
        try:
            OUT.rmdir()
        except OSError:
            pass
        print(f"\n（已清理用例文件；加 --keep 可保留 {OUT}）")

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 72)
    print(f"自测结果：{passed}/{len(results)} 通过")
    print("=" * 72)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
