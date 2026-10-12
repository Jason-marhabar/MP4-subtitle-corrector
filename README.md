# 英语学习素材字幕生成工具

把 MP4 视频自动生成**与音频精确对齐**的 SRT 字幕，可直接导入 Aboboo 做复读/听写/跟读练习。

- 输入：一个 MP4 文件
- 输出：校正后的 SRT 字幕（+ 规范化封装的 MP4）
- 核心保证：字幕时间戳对齐到**真实语音边界**，实测 98% 精确命中、零结构错误

> **项目状态**：核心处理链路已完成并验证。GUI 界面与打包发布尚未开始，
> 目前通过命令行/脚本使用。详见文末「当前进度」。

---

## 快速开始

### 1. 一键安装环境

双击运行：

```
install_env.bat
```

它会自动完成：创建 venv → 安装依赖（阿里云镜像）→ 配置 HuggingFace 镜像 →
下载 seconv（Subtitle Edit 命令行）→ 环境自检。

**前置要求**：Python 3.10/3.11（[下载](https://www.python.org/downloads/)，
安装时勾选 "Add to PATH"）、FFmpeg（[下载](https://www.gyan.dev/ffmpeg/builds/)，
解压后把 `bin` 加入 PATH）。

### 2. 处理一个视频

```bat
set_env.bat
venv\Scripts\python.exe -c "from model.pipeline import process; r=process('D:/videos/lesson.mp4','D:/out'); print(r.summary())"
```

或者用调度层批量处理：

```bat
set_env.bat
venv\Scripts\python.exe -c "from controller.scheduler import run_batch; jobs=run_batch(['a.mp4','b.mp4'],'D:/out'); print([j.state.value for j in jobs])"
```

### 3. 在 Aboboo 里使用

Aboboo **自动加载视频同目录下的同名字幕**，所以：

```
D:/videos/lesson.mp4
D:/videos/lesson.srt      ← 同名同目录，Aboboo 打开视频即自动加载
```

代码里可用现成函数完成这个命名：

```python
from model.pipeline import srt_for_aboboo
srt_for_aboboo("D:/out/lesson.srt", "D:/videos/lesson.mp4")
```

若 Aboboo 已缓存旧字幕：视频窗口右键 → **字幕 → 字幕加载** → 选新文件。

---

## 环境变量（国内网络必需）

`set_env.bat` 已包含全部设置，也可手动：

| 变量 | 值 | 作用 |
|---|---|---|
| `HF_ENDPOINT` | `https://hf-mirror.com` | **必须**，否则模型无法下载 |
| `HF_HOME` | `%CD%\.hf` | 模型缓存放项目内，便于迁移 |
| `PIP_CACHE_DIR` | `%CD%\.pipcache` | **受限环境必须**，否则 pip 卡在 Building wheel |
| `TMP` / `TEMP` | `%CD%\.tmp` | 同上 |

---

## GPU 加速（强烈建议）

实测（RTX 4060 Laptop 8GB，313 秒英语音频）：

| 模型 | CPU | GPU | 提速 |
|---|---|---|---|
| `small` | 42.0s | **7.8s** | 5.4x |
| `large-v3` | **约 6.2 小时（不可用）** | **59.2s** | 379x |

**`large-v3` 只有靠 GPU 才可用。** 安装：

```bat
venv\Scripts\python.exe -m pip install --no-deps ^
  -i https://pypi.tuna.tsinghua.edu.cn/simple ^
  nvidia-cublas-cu12 nvidia-cudnn-cu12
```

> **三个坑（都实测踩过）**
> 1. 必须用**清华镜像** —— 阿里云拉这两个大 wheel（553MB+743MB）会卡死
> 2. 不能省 `--no-deps` —— 否则会拖入不需要的 `nvidia-cuda-nvrtc-cu12`
> 3. 系统装了 CUDA **13.x 也没用** —— ctranslate2 需要 CUDA **12.x**
>
> `model/gpu_env.py` 会自动把 DLL 目录接进 PATH（`os.add_dll_directory()`
> 对 ctranslate2 **无效**，它按 PATH 解析），无需手工配置。

验证 GPU：

```bat
venv\Scripts\python.exe scripts\doctor.py --deep
```

预期看到：`cublas64_12.dll：已找到`、`深检（真实推理）= True`

---

## 项目结构

```
my_app/
├── main.py                 程序入口【未开始】
├── model/                  业务逻辑层
│   ├── waveform.py         语音边界提取（ffmpeg silencedetect）
│   ├── asr.py              ASR 转写（faster-whisper，含进度/取消）
│   ├── subtitle_fix.py     字幕校正流水线【核心】
│   ├── seconv_tools.py     Subtitle Edit 命令行集成
│   ├── gpu_env.py          CUDA 运行库自动配置
│   ├── pipeline.py         统一入口 process()
│   ├── ffmpeg_processor.py 音视频处理（早期原型）
│   └── ffmpeg_utils.py     FFmpeg 可用性检查
├── controller/             业务调度层（事件控制）
│   ├── scheduler.py        调度核心（纯标准库，不依赖 Qt）
│   └── qt_worker.py        Qt 适配层（可选导入）
├── view/                   UI 窗口【未开始】
├── config/
│   └── subtitle_params.json  字幕硬参数（校验器读取）
├── scripts/                验证与诊断脚本（见下）
├── docs/                   文档
├── tools/                  seconv.exe（脚本自动下载，已 gitignore）
├── requirements.txt
├── install_env.bat         一键安装环境
└── set_env.bat             设置环境变量
```

---

## 常用脚本

| 脚本 | 用途 |
|---|---|
| `scripts/doctor.py` | **环境一键诊断**（依赖/ffmpeg/CUDA/DLL/模型），有问题直接打印修复命令 |
| `scripts/acceptance.py` | **一键验收**（环境+管线+质量+remux+调度） |
| `scripts/check_srt.py` | SRT 校正校验（编码/结构/内容保真），可进 CI |
| `scripts/test_pipeline.py` | 第四板块端到端验证（含自适应阈值秒级用例） |
| `scripts/test_scheduler.py` | 第二板块调度层测试（批量/取消/失败隔离） |
| `scripts/test_subtitle_checks.py` | 字幕校验器自测（9 个用例） |
| `scripts/bench_gpu.py` | GPU/CPU 性能对比 |
| `scripts/probe_aboboo.py` | 生成 Aboboo 兼容性探针素材 |
| `scripts/audit_continuous.py` | 连续语流鲁棒性审计 |
| `scripts/fetch_file.py` | 带断点续传的下载器（GitHub 直连不稳时用） |

例：

```bat
set_env.bat
venv\Scripts\python.exe scripts\doctor.py --deep
venv\Scripts\python.exe scripts\acceptance.py
```

---

## 关键技术点（改代码前请先读）

### Aboboo 对齐完全由 SRT 时间戳决定

Aboboo 官方文档明确"**字幕时间戳用作断点**"。实测 `-c copy` 封装与源
**逐帧比对 2702/2702 帧完全一致（最大偏差 0.000000ms）**，即：

- **MP4 不会影响对齐** —— 不要重编码，`-c copy` 即可
- **音频标准化对对齐零帮助** —— Aboboo 首次打开会自行采样、生成断点
- 全部对齐压力在字幕时间戳上

### 字幕校正流水线（顺序不可调换）

```
1. 剔除        空/纯空白/零时长/无有效字符
2. 按静音切分   Whisper 会把多句合并进一段，必须先切开
3. 波形吸附     起止对齐到真实语音边界
4. 约束        不重叠、间隙达标、时长不超上限
5. 行长约束     按词边界换行（不劈开英文单词，保护 Aboboo 取词）
6. seconv 规范化 Subtitle Edit 收尾 + 结果校验（不合格自动回滚）
```

**第 2 步必须在第 3 步之前**：切分会改变段边界，先吸附再切分会让新边界失去吸附效果。

### 核心参数（`config/subtitle_params.json`）

| 参数 | 值 | 依据 |
|---|---|---|
| 编码 | UTF-8 **无 BOM** | seconv 默认带 BOM，必须显式关闭 |
| 换行 | LF | seconv 强制 CRLF，需转回 |
| 单行字符上限 | 43 | seconv 默认 |
| 最多行数 | 2 | seconv 默认 |
| 最小段间隙 | 24ms（建议 150ms） | seconv 默认 |
| 静音阈值 | **自适应** | 固定值在连续语流下会退化 |

---

## 实测效果

以 313 秒英语跟读素材为基准：

| 指标 | 结果 |
|---|---|
| 起点精确命中真实语音起点 | **98%**（偏差中位 0ms） |
| 终点覆盖真实语音结束 | **101/101**（提前消失 0 条） |
| 结构错误（重叠/逆序/空文本/零时长） | **0** |
| 文本完整性 | 442 → 442 词，**零丢失** |
| 输出字幕条数 | 54 段原始转写 → 101 条 |

测试状态：字幕校验自测 **9/9**、调度层 **30/30** 通过。

---

## 当前进度

| 板块 | 状态 |
|---|---|
| 音视频 FFmpeg | 已完成（remux 逐帧验证零偏移） |
| **ASR 识别 + 字幕校正** | **已完成并验证** |
| 业务调度与多线程 | 已完成（纯标准库核心 + Qt 适配层） |
| GUI 界面（`view/`、`main.py`） | **未开始** |
| 打包发布（PyInstaller、README） | **未开始** |
| 正式测试用例与 bug 清单 | 未开始 |

**所以现在的产物是"能出正确字幕的 Python 模块"，还不是"能双击运行的桌面应用"。**

### 已知限制

| 项 | 说明 |
|---|---|
| 非英语内容 | 未验证（中文断句/行长规则不同，需另测） |
| 无词级时间戳的兜底分支 | 从未触发，逻辑未经验证 |
| Aboboo 对 BOM / 零间隙的容忍度 | 未实测（输出本就无 BOM、间隙 ≥24ms，规避了风险） |
| GPU 并发推理 | 不支持（ctranslate2 模型实例非线程安全），已用锁串行化 |
| 首次运行较慢 | 需下载模型（small 464MB / large-v3 3GB） |

---

## 常见问题

**Q：pip 安装卡在 "Building wheel"，怎么办？**
A：缓存目录权限问题。确认设置了 `PIP_CACHE_DIR`（`set_env.bat` 已包含）。

**Q：报 `Library cublas64_12.dll is not found`？**
A：缺 CUDA 12 运行库。按上文 GPU 章节安装；或改用 CPU
（`AsrEngine(device='cpu', compute_type='int8')`）。

**Q：模型下载失败？**
A：确认 `HF_ENDPOINT=https://hf-mirror.com`。

**Q：字幕与声音对不齐？**
A：先用 `scripts/check_srt.py <srt> --media <mp4>` 查客观问题。
若结构无错但听感偏移，请提供**具体序号**，这属于吸附参数问题。

**Q：环境莫名其妙坏了？**
A：先跑 `venv\Scripts\python.exe scripts\doctor.py`，它会指出问题并给出修复命令。

---

## 文档

- [第四板块-模块说明](docs/第四板块-模块说明.md) —— ASR+字幕校正的实现与缺陷记录
- [第四板块-探针实验结论](docs/第四板块-探针实验结论.md) —— 12 项硬参数的实测依据
- [第二板块-模块说明](docs/第二板块-模块说明.md) —— 调度层接口与接入建议
- [GPU加速-配置与实测](docs/GPU加速-配置与实测.md) —— CUDA 配置与踩坑
- [第四板块-组内汇报](docs/第四板块-组内汇报.md) —— 汇报用总结
