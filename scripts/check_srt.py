# -*- coding: utf-8 -*-
"""
SRT 字幕校验器（纯标准库，不依赖 pysrt / seconv）

第四板块的"字幕校正"必须有一个**客观、可复现、可进 CI** 的校验环节：
校正完之后用本脚本自检，而不是靠肉眼看。它同时是第五板块字幕专项测试的
可执行用例来源。

检查项（对应 Aboboo 对齐要求）：
  1. 编码      必须是 UTF-8；报告是否带 BOM（BOM 会在 Aboboo 首行渲染成乱码）
  2. 结构      序号连续递增、时间戳行格式严格合法、必须有空行分隔
  3. 时间戳    格式 hh:mm:ss,mmm；end > start；不出现负值
  4. 时长      段落时长是否过短（Aboboo 断点过密，无法复读）
  5. 重叠      相邻段落是否重叠（Aboboo 用时间戳做断点，重叠会错乱）
  6. 间隙      相邻段最小间隙（间隙为 0 会导致 Aboboo 合并相邻句）
  7. 文本      空文本、纯空白、纯标点、控制字符
  8. 行长      单行最大字符数、最多行数（文本模式下超长会溢出）
  9. 断词      英文单词是否被跨行拆断（会破坏 Aboboo 字幕取词/跟读）
 10. 覆盖      字幕末段与音视频总时长的偏差（需传 --media）

用法：
    python scripts/check_srt.py <srt路径> [<srt路径> ...]
    python scripts/check_srt.py <srt路径> --media <mp4路径>
    python scripts/check_srt.py <目录> --all          # 校验目录下所有 srt
    python scripts/check_srt.py <srt> --json          # 机器可读输出
    python scripts/check_srt.py <校正后.srt> --baseline <原始.srt>
        # 内容保真检查：确认校正过程没有让音频与文本错位（见 compare_baseline）

退出码：0 无问题；1 有 ERROR；2 仅有 WARN
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------- 阈值
# 默认值来自 Aboboo 对齐探针实验实测（见 docs/第四板块-探针实验结论.md）。
# 若存在 config/subtitle_params.json，则用它覆盖，使"硬参数结论"真正可执行，
# 而不是停留在文档里。
MAX_LINE_CHARS = 43        # seconv 默认 subtitleLineMaximumLength
MAX_LINES = 2              # seconv 默认 maxNumberOfLines
MIN_GAP_MS = 24            # seconv 默认 minimumMillisecondsBetweenLines
MIN_DUR_MS = 800           # 本模块下限（seconv 为 1000）
MAX_DUR_MS = 10000
CPS_WARN = 25              # seconv 默认 subtitleMaximumCharactersPerSeconds
PARAM_SOURCE = "内置默认值"


def load_params(path=None):
    """从 config/subtitle_params.json 读取硬参数，覆盖模块级阈值。

    参数文件缺失或字段损坏时保留内置默认值——校验器不能因为配置问题而失效。
    """
    global MAX_LINE_CHARS, MAX_LINES, MIN_GAP_MS, MIN_DUR_MS, MAX_DUR_MS
    global CPS_WARN, PARAM_SOURCE
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "subtitle_params.json"
    p = Path(path)
    if not p.exists():
        return PARAM_SOURCE
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        line = cfg.get("行长与行数", {})
        dur = cfg.get("时长与间隙", {})
        cps = cfg.get("阅读速度", {})
        if "单行最大字符数" in line:
            MAX_LINE_CHARS = int(line["单行最大字符数"])
        if "单条最多行数" in line:
            MAX_LINES = int(line["单条最多行数"])
        if "本模块下限ms" in dur:
            MIN_DUR_MS = int(dur["本模块下限ms"])
        if "本模块最长告警ms" in dur:
            MAX_DUR_MS = int(dur["本模块最长告警ms"])
        if "最小段间隙ms" in dur:
            MIN_GAP_MS = int(dur["最小段间隙ms"])
        if "每秒最大字符数" in cps:
            CPS_WARN = int(cps["每秒最大字符数"])
        PARAM_SOURCE = str(p)
    except Exception as e:
        PARAM_SOURCE = f"内置默认值（读取 {p} 失败：{type(e).__name__}）"
    return PARAM_SOURCE

TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
CUE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*$"
)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def ts_to_ms(s):
    m = TS_RE.match(s)
    if not m:
        return None
    h, mi, se, ms = (int(x) for x in m.groups())
    if mi > 59 or se > 59:
        return None
    return h * 3600000 + mi * 60000 + se * 1000 + ms


def ms_to_ts(ms):
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def media_duration_ms(media_path):
    """用 ffprobe 取媒体总时长（毫秒）。取不到返回 None。"""
    exe = "ffprobe"
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(round(float(r.stdout.strip()) * 1000))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 解析

def detect_encoding(raw):
    """返回 (编码名, 是否带BOM, 是否严格UTF-8)。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", True, True
    try:
        raw.decode("utf-8")
        return "utf-8", False, True
    except UnicodeDecodeError:
        for enc in ("gbk", "gb18030", "big5", "cp1252"):
            try:
                raw.decode(enc)
                return enc, False, False
            except UnicodeDecodeError:
                continue
    return "unknown", False, False


def parse_srt(path):
    raw = Path(path).read_bytes()
    enc, bom, is_utf8 = detect_encoding(raw)
    text = raw.decode(enc, errors="replace") if enc != "unknown" else raw.decode("utf-8", "replace")
    if bom:
        text = text.lstrip("\ufeff")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues, errors, warns = [], [], []

    if not is_utf8:
        errors.append(f"编码不是 UTF-8（检测为 {enc}）——Aboboo 可能整片乱码")
    if bom:
        warns.append("文件带 UTF-8 BOM —— Aboboo 文本模式首行可能出现乱码字符")

    i, expect_index = 0, 1
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # 序号行
        if not line.isdigit():
            errors.append(f"第 {i+1} 行：期望序号但得到 {line[:40]!r}")
            i += 1
            continue
        idx = int(line)
        if idx != expect_index:
            warns.append(f"序号不连续：第 {i+1} 行是 {idx}，期望 {expect_index}")
        expect_index = idx + 1
        i += 1

        if i >= len(lines):
            errors.append(f"序 {idx}：缺少时间戳行（文件在此结束）")
            break
        m = CUE_RE.match(lines[i].strip())
        if not m:
            errors.append(f"序 {idx}：时间戳格式非法 -> {lines[i].strip()[:60]!r}")
            i += 1
            continue
        start_s, end_s = m.group(1), m.group(2)
        start, end = ts_to_ms(start_s), ts_to_ms(end_s)
        if start is None or end is None:
            errors.append(f"序 {idx}：时间戳数值越界（分/秒 > 59）")
        i += 1

        # 文本行：直到空行
        text_lines = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i].rstrip())
            i += 1

        cues.append({
            "index": idx, "start": start, "end": end,
            "start_s": start_s, "end_s": end_s, "lines": text_lines,
        })

    return {"path": str(path), "encoding": enc, "bom": bom, "is_utf8": is_utf8,
            "cues": cues, "errors": errors, "warnings": warns}


# ---------------------------------------------------------------- 检查

def check(data, media_ms=None):
    errors = list(data["errors"])
    warns = list(data["warnings"])
    cues = data["cues"]

    if not cues:
        errors.append("没有任何字幕段落")
        return errors, warns, {}

    for c in cues:
        i = c["index"]
        s, e = c["start"], c["end"]
        text = "\n".join(c["lines"])
        flat = text.strip()

        # --- 时间戳合法性
        if s is None or e is None:
            continue
        if s < 0:
            errors.append(f"序 {i}：起始时间为负 {c['start_s']}")
        if e <= s:
            errors.append(f"序 {i}：结束时间不大于起始时间（{c['start_s']} -> {c['end_s']}）")
            continue
        dur = e - s
        if dur < MIN_DUR_MS:
            warns.append(f"序 {i}：时长仅 {dur}ms（< {MIN_DUR_MS}ms），Aboboo 断点过密")
        if dur > MAX_DUR_MS:
            warns.append(f"序 {i}：时长 {dur}ms（> {MAX_DUR_MS}ms），过长需切分")

        # --- 文本无效
        if flat == "":
            errors.append(f"序 {i}：文本为空")
            continue
        if not flat.strip(" \t\u3000"):
            errors.append(f"序 {i}：文本只有空白字符")
            continue
        if CTRL_RE.search(text):
            errors.append(f"序 {i}：文本含控制字符")
        if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", flat):
            warns.append(f"序 {i}：文本不含任何文字或数字 -> {flat[:30]!r}")

        # --- 行长 / 行数
        for ln in c["lines"]:
            if len(ln) > MAX_LINE_CHARS:
                warns.append(f"序 {i}：单行 {len(ln)} 字符（> {MAX_LINE_CHARS}）")
        if len(c["lines"]) > MAX_LINES:
            warns.append(f"序 {i}：{len(c['lines'])} 行（> {MAX_LINES} 行）")

        # --- 断词：上一行末尾是字母、下一行开头是字母，且中间无连字符
        for a, b in zip(c["lines"], c["lines"][1:]):
            if re.search(r"[A-Za-z]$", a.rstrip()) and re.match(r"^[A-Za-z]", b.lstrip()):
                warns.append(f"序 {i}：英文单词疑似被跨行拆断 -> ...{a.strip()[-12:]!r} / {b.strip()[:12]!r}...")

        # --- 阅读速度
        cps = len(flat) / (dur / 1000)
        if cps > CPS_WARN:
            warns.append(f"序 {i}：{cps:.1f} 字符/秒（> {CPS_WARN}），阅读过快")

    # --- 相邻段：重叠与间隙
    overlaps, small_gaps, zero_gaps = [], [], []
    for p, n in zip(cues, cues[1:]):
        if p["end"] is None or n["start"] is None:
            continue
        gap = n["start"] - p["end"]
        if gap < 0:
            overlaps.append((p["index"], n["index"], -gap))
        elif gap == 0:
            zero_gaps.append((p["index"], n["index"]))
        elif gap < MIN_GAP_MS:
            small_gaps.append((p["index"], n["index"], gap))

    for a, b, ov in overlaps:
        errors.append(f"序 {a} 与 {b} 重叠 {ov}ms —— Aboboo 断点会错乱")
    for a, b in zero_gaps:
        warns.append(f"序 {a} 与 {b} 间隙为 0 —— 可能被 Aboboo 合并为一句")
    for a, b, g in small_gaps:
        warns.append(f"序 {a} 与 {b} 间隙仅 {g}ms（< {MIN_GAP_MS}ms）")

    # --- 时间戳单调性
    for p, n in zip(cues, cues[1:]):
        if p["start"] is not None and n["start"] is not None and n["start"] < p["start"]:
            errors.append(f"序 {n['index']} 起始时间早于序 {p['index']}（时间轴非单调）")

    # --- 与媒体总时长比对
    stats = {
        "段落数": len(cues),
        "首段起始": cues[0]["start_s"],
        "末段结束": cues[-1]["end_s"],
        "重叠数": len(overlaps),
        "零间隙数": len(zero_gaps),
        "小间隙数": len(small_gaps),
    }
    if media_ms is not None:
        last = cues[-1]["end"]
        if last is not None:
            stats["媒体总时长"] = ms_to_ts(media_ms)
            stats["末段与媒体差值ms"] = media_ms - last
            if last > media_ms + 1000:
                errors.append(f"末段结束 {cues[-1]['end_s']} 超出媒体时长 {ms_to_ts(media_ms)}")
            if media_ms - last > 60000:
                warns.append(f"末段比媒体早结束 {(media_ms-last)/1000:.0f}s，疑似截断")

    return errors, warns, stats


def norm_text(lines):
    """规范化字幕文本用于比对：折叠空白、去首尾。"""
    return " ".join(" ".join(lines).split())


def compare_baseline(cur, base, min_ratio=0.85):
    """校正后字幕相对基线（原始 Whisper 输出）的内容保真检查。

    这是本模块最危险的一类 bug 的守门员：**音频与文本错位**。
    时间戳全部合法、格式完全正确，但某一段的文本在重排/切分/剔除时被
    挪到了别的句子上——听感上表现为"字幕和语音对不上"，而所有结构检查
    都是通过的。必查项：

      · 段落数量变化（增/减）——需人工确认是否预期（切分会增、剔除会减）
      · 文本内容被修改
      · 段落顺序被调换（典型的"音频文本错位"信号）
      · 文本丢失（原始段落在新文件中找不到对应）
      · 与音频时长严重不匹配

    返回 (errors, warnings, info)
    """
    errors, warns = [], []
    cur_text = [norm_text(c["lines"]) for c in cur["cues"]]
    base_text = [norm_text(c["lines"]) for c in base["cues"]]

    info = {"基线段数": len(base_text), "当前段数": len(cur_text),
            "条数变化": len(cur_text) - len(base_text)}

    if not base_text:
        warns.append("基线字幕为空，跳过内容保真检查")
        return errors, warns, info

    # --- 1. 完全相同是最理想情况（吸附只动时间戳时应当如此）
    if cur_text == base_text:
        info["文本序列"] = "完全一致"
        return errors, warns, info

    # --- 2. 顺序是否被打乱（最危险：音频与文本错位）
    common_cur = [t for t in cur_text if t in set(base_text)]
    common_base = [t for t in base_text if t in set(cur_text)]
    if common_cur and common_base and common_cur != common_base:
        errors.append(
            "段落顺序相对基线发生变化 —— 这是音频与文本错位的典型信号，"
            "Aboboo 中会表现为字幕与语音对不上，必须人工核验"
        )
        info["文本序列"] = "顺序改变（危险）"

    # --- 3. 丢失的段落
    missing = [t for t in base_text if t not in set(cur_text)]
    if missing:
        warns.append(f"有 {len(missing)} 条基线文本在当前文件中找不到对应"
                     f"（剔除无效段时会正常发生，但需确认是预期行为）")
        for t in missing[:5]:
            warns.append(f"    丢失: {t[:50]!r}")
    info["丢失条数"] = len(missing)

    # --- 4. 新增的段落
    added = [t for t in cur_text if t not in set(base_text)]
    if added:
        warns.append(f"有 {len(added)} 条文本在基线中不存在"
                     f"（切分长句时会正常发生）")
        for t in added[:5]:
            warns.append(f"    新增: {t[:50]!r}")
    info["新增条数"] = len(added)

    # --- 5. 字符级相似度（切分场景下文本应高度相似）
    from difflib import SequenceMatcher
    a = norm_text([" ".join(base_text)])
    b = norm_text([" ".join(cur_text)])
    ratio = SequenceMatcher(None, a, b).ratio()
    info["文本相似度"] = round(ratio, 4)
    if ratio < min_ratio:
        errors.append(
            f"与基线的整体文本相似度仅 {ratio:.1%}（阈值 {min_ratio:.0%}）—— "
            f"文本可能被大规模改动或截断"
        )

    # --- 6. 总时长对照：字幕总覆盖时长不应异常变化
    base_span = (base["cues"][-1]["end"] or 0) - (base["cues"][0]["start"] or 0)
    cur_span = (cur["cues"][-1]["end"] or 0) - (cur["cues"][0]["start"] or 0)
    if base_span > 0:
        info["总跨度变化ms"] = cur_span - base_span
        if abs(cur_span - base_span) > max(5000, base_span * 0.1):
            warns.append(
                f"字幕总跨度相对基线变化 {cur_span-base_span:+d}ms"
                f"（基线 {base_span}ms）—— 吸附不应大幅改变总跨度，请检查"
            )

    return errors, warns, info


def print_report(data, errors, warns, stats):
    p = data["path"]
    print("=" * 70)
    print(f"文件：{p}")
    print(f"编码：{data['encoding']}  BOM：{'有' if data['bom'] else '无'}  "
          f"严格UTF-8：{'是' if data['is_utf8'] else '否'}")
    if stats:
        print("统计：" + "  ".join(f"{k}={v}" for k, v in stats.items()))
    print(f"结果：{len(errors)} 个 ERROR，{len(warns)} 个 WARN")
    for e in errors[:40]:
        print(f"  [ERROR] {e}")
    if len(errors) > 40:
        print(f"  ... 另有 {len(errors)-40} 个 ERROR")
    for w in warns[:20]:
        print(f"  [WARN ] {w}")
    if len(warns) > 20:
        print(f"  ... 另有 {len(warns)-20} 个 WARN")
    if not errors and not warns:
        print("  [ OK  ] 全部检查通过")


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    all_mode = "--all" in argv
    argv = [a for a in argv if a not in ("--json", "--all")]

    media = None
    if "--media" in argv:
        k = argv.index("--media")
        media = argv[k + 1]
        del argv[k:k + 2]

    baseline = None
    if "--baseline" in argv:
        k = argv.index("--baseline")
        baseline = argv[k + 1]
        del argv[k:k + 2]

    min_ratio = 0.85
    if "--min-ratio" in argv:
        k = argv.index("--min-ratio")
        min_ratio = float(argv[k + 1])
        del argv[k:k + 2]

    if not argv:
        print(__doc__)
        sys.exit(1)

    src = load_params()

    targets = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            if not all_mode:
                print(f"[!] {a} 是目录，需加 --all")
                sys.exit(1)
            targets += sorted(p.glob("*.srt"))
        else:
            targets.append(p)

    if not targets:
        print("没有找到任何 srt 文件")
        sys.exit(1)

    media_ms = media_duration_ms(media) if media else None
    if media and media_ms is None:
        print(f"[!] 无法用 ffprobe 读取媒体时长：{media}")

    base_data = None
    if baseline:
        if not Path(baseline).exists():
            print(f"[!] 基线文件不存在：{baseline}")
            sys.exit(1)
        base_data = parse_srt(baseline)

    results, any_error, any_warn = [], False, False
    for t in targets:
        if not t.exists():
            print(f"[!] 文件不存在：{t}")
            any_error = True
            continue
        data = parse_srt(t)
        errors, warns, stats = check(data, media_ms)

        # 内容保真检查：相对基线（原始 Whisper 输出）比对
        binfo = None
        if base_data is not None:
            b_err, b_warn, binfo = compare_baseline(data, base_data, min_ratio)
            errors = errors + b_err
            warns = warns + b_warn

        results.append({"file": str(t), "encoding": data["encoding"], "bom": data["bom"],
                        "errors": errors, "warnings": warns, "stats": stats,
                        "baseline": binfo})
        if not as_json:
            print_report(data, errors, warns, stats)
            if binfo is not None:
                print("  内容保真（对比基线）：" +
                      "  ".join(f"{k}={v}" for k, v in binfo.items()))
        any_error = any_error or bool(errors)
        any_warn = any_warn or bool(warns)

    # 退出码分层：只要有一个文件有 ERROR 就是 1；仅 WARN 才是 2；全干净是 0。
    # 不能对单个文件的等级取 max 后累加，否则一个无关文件的 WARN 会把
    # 另一个文件的 ERROR 掩盖成 2。
    worst = 1 if any_error else (2 if any_warn else 0)

    if as_json:
        print(json.dumps({"results": results, "exitCode": worst},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print(f"阈值来源：{src}")
        print(f"  单行≤{MAX_LINE_CHARS}字符  最多{MAX_LINES}行  段间隙≥{MIN_GAP_MS}ms  "
              f"时长{MIN_DUR_MS}~{MAX_DUR_MS}ms  ≤{CPS_WARN}字符/秒")
        print(f"共 {len(results)} 个文件；退出码 {worst}（0=干净 1=有ERROR 2=仅WARN）")
    sys.exit(worst)


if __name__ == "__main__":
    main()
