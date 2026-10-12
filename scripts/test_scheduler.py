# -*- coding: utf-8 -*-
"""
第二板块（业务调度）验证脚本

测试策略：**用假任务注入，而不是跑真实转写**。
调度层要验证的是并发、排队、取消、失败隔离、进度聚合这些**逻辑正确性**，
这些与"转写是否准确"无关。用假任务可以在几秒内精确构造边界场景
（失败、卡住、取消、慢任务），而真实转写一次要 40 秒且难以制造这些情况。

用法：
    python scripts/test_scheduler.py            # 全部用例
    python scripts/test_scheduler.py --real     # 额外跑一次真实素材的端到端（慢）

退出码：0 全部通过；1 有失败
"""
import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from controller.scheduler import (BatchConfig, BatchScheduler, Job,  # noqa: E402
                                 JobState, validate_batch_inputs)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def rule(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


# ---------------------------------------------------------------- 假任务注入

class FakePipeline:
    """替换 model.pipeline.process 的假实现。

    通过文件名约定行为，便于精确构造场景：
      · 名字含 "fail"    -> 抛异常（测试失败隔离）
      · 名字含 "hang"    -> 长时间占用（测试取消传播）
      · 其余             -> 按 progress_cb 上报 0→1，产出同名 .srt
    """

    def __init__(self, duration=0.30, engine_loader=None):
        self.duration = duration
        self.calls = []
        self.asserted = []
        self.lock = threading.Lock()
        self._engine_loader = engine_loader

    def __call__(self, video, out_dir, engine=None, fix_config=None, language="en",
                 progress_cb=None, cancel_flag=None, make_mp4=True,
                 keep_audio=False, srt_name=None):
        name = Path(video).name
        with self.lock:
            self.calls.append(name)
            # 断言：调度层必须把引擎传进来（证明模型只加载一次、被复用）
            self.asserted.append(engine is not None)

        if "fail" in name:
            time.sleep(0.05)
            raise RuntimeError("模拟的处理失败")

        steps = 5
        for i in range(steps):
            if cancel_flag and cancel_flag():
                from model.asr import AsrCancelled
                raise AsrCancelled("被取消")
            time.sleep(self.duration / steps)
            if progress_cb:
                progress_cb((i + 1) / steps, f"阶段{i+1}")

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        srt = out / (srt_name or f"{Path(video).stem}.srt")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nfake\n",
                       encoding="utf-8")

        # 造一个假的 ProcessResult（字段与真实的保持一致）
        class R:
            pass
        r = R()
        r.video = video
        r.srt_path = str(srt)
        r.mp4_path = str(out / f"{Path(video).stem}_remux.mp4") if make_mp4 else None
        r.audio_path = None
        r.cue_count = 7
        r.duration = 10.0
        r.elapsed = self.duration
        r.report = None
        r.problems = []
        return r


class FakeEngine:
    """替换 model.asr.AsrEngine，记录加载次数。"""

    load_count = 0

    def __init__(self, **kw):
        self.kw = kw

    def load(self, progress_cb=None):
        FakeEngine.load_count += 1


class FakeFixConfig:
    def __init__(self, **kw):
        self.kw = kw


def install_fakes(pipeline: FakePipeline):
    """把假实现注入到 model.pipeline 与 model.asr（调度层是引用导入的）。"""
    import model.asr as asr_mod
    import model.pipeline as pipe_mod
    import model.subtitle_fix as fix_mod
    FakeEngine.load_count = 0
    pipe_mod.process = pipeline
    asr_mod.AsrEngine = FakeEngine
    return pipe_mod, asr_mod, fix_mod


# ---------------------------------------------------------------- 用例

def make_files(tmp: Path, names):
    tmp.mkdir(parents=True, exist_ok=True)
    files = []
    for n in names:
        p = tmp / n
        p.write_bytes(b"\x00" * 64)        # 仅用于存在性判断
        files.append(str(p))
    return files


def case_input_validation(tmp: Path):
    rule("用例 1：导入前输入校验")
    good = make_files(tmp / "in", ["a.mp4", "b.mp4"])
    empty = tmp / "in" / "empty.mp4"
    empty.write_bytes(b"")
    bad_ext = tmp / "in" / "note.txt"
    bad_ext.write_text("x", encoding="utf-8")

    problems = validate_batch_inputs(
        good + [str(empty), str(bad_ext), good[0], str(tmp / "missing.mp4")])
    joined = " | ".join(problems)
    check("能识别空文件", "空" in joined, joined[:60])
    check("能识别非视频扩展名", "不是支持的视频格式" in joined)
    check("能识别重复导入", "重复导入" in joined)
    check("能识别文件不存在", "不存在" in joined)


def case_batch_ordering(tmp: Path):
    rule("用例 2：批量排队 + 进度单调 + 模型只加载一次")
    pipe = FakePipeline(duration=0.30)
    install_fakes(pipe)

    files = make_files(tmp / "batch", [f"v{i}.mp4" for i in range(4)])
    cfg = BatchConfig(out_dir=str(tmp / "out"), workers=2)
    sched = BatchScheduler(cfg)

    job_events = []
    prog_values = []
    logs = []
    sched.on_job_update = lambda j: job_events.append((j.name, j.state))
    sched.on_progress = lambda p: prog_values.append(p.overall)
    sched.on_log = lambda lvl, m: logs.append((lvl, m))
    sched.attach_logging()

    sched.add_files(files)
    jobs = sched.run(block=True)
    sched.shutdown()

    check("全部 4 个任务完成",
          all(j.state == JobState.DONE for j in jobs),
          f"状态={[j.state.value for j in jobs]}")
    check("都产出了 SRT", all(j.srt_path and Path(j.srt_path).exists() for j in jobs))
    check("字幕条数被正确回传", all(j.cue_count == 7 for j in jobs))

    mono = all(b >= a - 1e-9 for a, b in zip(prog_values, prog_values[1:]))
    check("总进度单调不回退", mono,
          f"{len(prog_values)} 次上报，末值={prog_values[-1]:.2f}")
    check("最终总进度为 1.0", abs(prog_values[-1] - 1.0) < 1e-6)

    check("模型只加载一次（被复用）", FakeEngine.load_count == 1,
          f"load_count={FakeEngine.load_count}")
    check("每个任务都拿到了引擎实例",
          all(pipe.asserted), f"{sum(pipe.asserted)}/{len(pipe.asserted)}")
    check("日志已转发到前端回调", len(logs) > 0, f"{len(logs)} 条")


def case_failure_isolation(tmp: Path):
    rule("用例 3：单个任务失败不影响整个队列")
    pipe = FakePipeline(duration=0.10)
    install_fakes(pipe)

    files = make_files(tmp / "fail", ["ok1.mp4", "fail1.mp4", "ok2.mp4"])
    cfg = BatchConfig(out_dir=str(tmp / "out2"), workers=1)
    sched = BatchScheduler(cfg)
    sched.add_files(files)
    jobs = sched.run(block=True)
    sched.shutdown()

    states = {j.name: j.state for j in jobs}
    check("失败任务被标记为 FAILED",
          states.get("fail1.mp4") == JobState.FAILED, str(states))
    check("失败任务记录了错误文本",
          any(j.error and "模拟的处理失败" in j.error for j in jobs))
    check("其余任务仍然成功",
          states.get("ok1.mp4") == JobState.DONE
          and states.get("ok2.mp4") == JobState.DONE, str(states))
    check("失败没有中断队列（3 个都被处理）", len(pipe.calls) == 3,
          f"实际处理 {len(pipe.calls)} 个")


def case_cancel(tmp: Path):
    rule("用例 4：取消传播（长任务在运行中被中止）")
    pipe = FakePipeline(duration=3.0)      # 足够长，方便中途取消
    install_fakes(pipe)

    files = make_files(tmp / "cancel", ["long1.mp4", "long2.mp4"])
    cfg = BatchConfig(out_dir=str(tmp / "out3"), workers=1)
    sched = BatchScheduler(cfg)
    sched.add_files(files)

    # 启动后 0.4 秒取消全部
    timer = threading.Timer(0.4, sched.cancel_all)
    timer.start()
    t0 = time.time()
    jobs = sched.run(block=True)
    elapsed = time.time() - t0
    sched.shutdown()
    timer.cancel()

    cancelled = [j for j in jobs if j.state == JobState.CANCELLED]
    check("任务被标记为 CANCELLED", len(cancelled) >= 1,
          f"状态={[j.state.value for j in jobs]}")
    check("取消在远早于自然完成的时间内生效", elapsed < 6.0,
          f"耗时 {elapsed:.1f}s（若不取消需 ~6s）")
    check("已取消的任务不产出 SRT",
          all(not (j.srt_path and Path(j.srt_path).exists())
              for j in cancelled))


def case_cancel_waiting(tmp: Path):
    rule("用例 5：取消排队中的任务（尚未开始）")
    pipe = FakePipeline(duration=0.5)
    install_fakes(pipe)

    files = make_files(tmp / "q", ["q1.mp4", "q2.mp4", "q3.mp4"])
    cfg = BatchConfig(out_dir=str(tmp / "out4"), workers=1)
    sched = BatchScheduler(cfg)
    jobs = sched.add_files(files)

    # 只取消还没轮到的第 3 个
    sched.cancel_job(jobs[2].job_id)
    check("排队中的任务立即变为 CANCELLED",
          jobs[2].state == JobState.CANCELLED, jobs[2].state.value)

    done = sched.run(block=True)
    sched.shutdown()
    check("被取消的排队任务没有被执行", "q3.mp4" not in pipe.calls,
          f"实际处理={pipe.calls}")
    check("其余任务照常完成",
          sum(1 for j in done if j.state == JobState.DONE) == 2)


def case_skip_and_clear(tmp: Path):
    rule("用例 6：非法输入跳过 + 清除已完成")
    pipe = FakePipeline(duration=0.05)
    install_fakes(pipe)

    good = make_files(tmp / "skip", ["s1.mp4"])
    cfg = BatchConfig(out_dir=str(tmp / "out5"), workers=1)
    sched = BatchScheduler(cfg)
    jobs = sched.add_files(good + [str(tmp / "skip" / "nope.mp4")])

    check("不存在的文件被标记 SKIPPED",
          any(j.state == JobState.SKIPPED for j in jobs))
    check("SKIPPED 的任务不会进入处理",
          "nope.mp4" not in pipe.calls)

    sched.run(block=True)
    removed = sched.clear_finished()
    check("清除已完成后列表为空", len(sched.jobs) == 0,
          f"移除了 {removed} 条")
    sched.shutdown()


def case_duplicate_output_guard(tmp: Path):
    rule("用例 7：输出已存在时不覆盖（保护用户成果）")
    pipe = FakePipeline(duration=0.05)
    install_fakes(pipe)

    files = make_files(tmp / "dup", ["d1.mp4"])
    out = tmp / "out6"
    out.mkdir(parents=True, exist_ok=True)
    (out / "d1.srt").write_text("existing", encoding="utf-8")

    cfg = BatchConfig(out_dir=str(out), workers=1, overwrite=False)
    sched = BatchScheduler(cfg)
    sched.add_files(files)
    jobs = sched.run(block=True)
    sched.shutdown()

    check("已存在输出时任务被跳过",
          jobs[0].state == JobState.SKIPPED, jobs[0].state.value)
    check("原有文件未被覆盖",
          (out / "d1.srt").read_text(encoding="utf-8") == "existing")


def case_config_roundtrip(tmp: Path):
    rule("用例 8：配置持久化与损坏回退")
    p = tmp / "cfg.json"
    cfg = BatchConfig(out_dir="D:/x", workers=3, model="medium",
                      language="zh", make_mp4=False)
    cfg.to_json(str(p))
    back = BatchConfig.from_json(str(p))
    check("配置能往返序列化",
          back.out_dir == "D:/x" and back.workers == 3
          and back.model == "medium" and back.language == "zh"
          and back.make_mp4 is False,
          f"workers={back.workers} model={back.model}")

    bad = tmp / "bad.json"
    bad.write_text("{ 这不是合法 json", encoding="utf-8")
    fb = BatchConfig.from_json(str(bad))
    check("配置损坏时回退默认值而不抛异常", fb.workers == 1 and fb.model == "small")

    unknown = tmp / "unknown.json"
    unknown.write_text('{"workers": 4, "不存在的字段": 1}', encoding="utf-8")
    u = BatchConfig.from_json(str(unknown))
    check("忽略未知字段", u.workers == 4)


def restore_real_modules():
    """恢复被 install_fakes() 替换掉的真实实现。

    若不恢复，后面的"真实端到端"用例仍会跑到假任务上 —— 实测就出现过
    cue_count=7、耗时 0.1s 这种假结果被当成真实结果（误报）。
    """
    import importlib
    import model.asr as asr_mod
    import model.pipeline as pipe_mod
    importlib.reload(pipe_mod)
    importlib.reload(asr_mod)


def case_real(tmp: Path):
    rule("用例 9：真实素材端到端（模型注入 + 真实转写）")
    restore_real_modules()
    test_mp4 = ROOT / "test.mp4"
    if not test_mp4.exists():
        check("跳过（test.mp4 不存在）", True)
        return
    cfg = BatchConfig(out_dir=str(tmp / "real"), workers=1,
                      model="small", device="cpu", compute_type="int8",
                      make_mp4=False)
    sched = BatchScheduler(cfg)
    logs = []
    prog = []
    sched.on_log = lambda l, m: logs.append(f"[{l}] {m}")
    sched.on_progress = lambda p: prog.append(p.overall)
    sched.attach_logging()
    sched.add_files([str(test_mp4)])
    jobs = sched.run(block=True)
    sched.shutdown()

    j = jobs[0]
    check("真实任务完成", j.state == JobState.DONE,
          f"state={j.state.value} err={j.error}")
    if j.state == JobState.DONE:
        check("产出了可用 SRT", j.srt_path and Path(j.srt_path).exists())
        check("字幕条数合理（>50）", j.cue_count > 50, f"cue_count={j.cue_count}")
        check("进度收敛到 1.0", abs(prog[-1] - 1.0) < 1e-6)
        print(f"        输出：{j.srt_path}")
        print(f"        耗时：{j.elapsed:.1f}s，字幕 {j.cue_count} 条")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="额外跑真实素材端到端")
    args = ap.parse_args()

    tmp = ROOT / "test_output" / "scheduler"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("第二板块（业务调度）验证")
    print("=" * 72)

    case_input_validation(tmp)
    case_batch_ordering(tmp)
    case_failure_isolation(tmp)
    case_cancel(tmp)
    case_cancel_waiting(tmp)
    case_skip_and_clear(tmp)
    case_duplicate_output_guard(tmp)
    case_config_roundtrip(tmp)
    if args.real:
        case_real(tmp)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 72)
    print(f"结果：{passed}/{total} 通过")
    print("=" * 72)
    if passed != total:
        for n, ok, d in RESULTS:
            if not ok:
                print(f"  [FAIL] {n}  {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
