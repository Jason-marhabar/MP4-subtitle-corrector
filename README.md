# MP4字幕矫正工具

给英语 MP4 视频自动生成**时间轴精准对齐**的 SRT 字幕，可直接导入 Aboboo 做听写/跟读练习。

- 输入：MP4 视频
- 输出：.srt 字幕文件 + 重新封装的 .mp4
- 核心保证：字幕时间戳对齐到真实语音边界，实测 98% 起点命中、零结构错误

---

## 给普通用户：直接下载 exe 用（推荐）

**不需要装 Python，不需要配环境，双击就能用。**

1. 打开 Releases 页面：https://github.com/Jason-marhabar/MP4-subtitle-corrector/releases/latest
2. 下载 `MP4-subtitle-tool-v1.0.zip`
3. 解压
4. 双击 `MP4字幕矫正工具.exe`
5. 选 MP4 → 选输出目录 → 点"开始"
6. 输出目录里得到 `.srt` 和 `_remux.mp4`

### 在 Aboboo 里使用

把 `.srt` 和 `.mp4` 放同一个文件夹、文件名相同（除扩展名），用 Aboboo 打开 mp4 即可自动加载字幕。

```
lesson.mp4
lesson.srt      ← 同名同目录，Aboboo 自动加载
```

### 注意

- 首次运行会自动下载 small 语音模型（约 464MB，走国内镜像，需联网几分钟）
- 需要 ffmpeg；如报错"找不到 ffmpeg"，把 `ffmpeg.exe` 放到 exe 同目录
- 支持 Windows 10/11 64位

---

## 项目分工（6人小组）

| 编号 | 职责 | 内容 |
|---|---|---|
| 1号 | GUI 界面 | PyQt6 窗口：文件选择、任务列表、进度条、日志 |
| 2号 | 业务调度 | 多线程/任务队列、串联各模块、全局异常处理 |
| 3号 | FFmpeg 音视频 | 提取音频、`-c copy` 重新封装 MP4（避免音画偏移） |
| 4号 | ASR + 字幕校正（核心） | faster-whisper 语音识别、波形吸附校正、字幕断句/去重叠 |
| 5号 | 测试 | Aboboo 兼容性验收、边界测试、bug 清单 |
| 6号 | 环境与打包 | Git 仓库、requirements.txt、PyInstaller 打包 exe |

---

## 给开发者：从源码运行

### 前置要求

- Python 3.11（[下载](https://www.python.org/downloads/)，安装勾选 Add to PATH）
- FFmpeg（[下载](https://www.gyan.dev/ffmpeg/builds/)，解压后把 bin 加入 PATH）

### 一键安装

```bat
install_env.bat
```

自动创建 venv、装依赖（阿里云镜像）、配国内模型镜像。

### 命令行处理视频

```bat
set_env.bat
venv\Scripts\python.exe -c "from model.pipeline import process; r=process('D:/videos/lesson.mp4','D:/out'); print(r.summary())"
```

### 启动 GUI

```bat
set_env.bat
venv\Scripts\python.exe app\main.py
```

### 打包 exe

```bat
打包成exe.bat
```

产出在 `dist\MP4字幕矫正工具\` 目录。

---

## 环境变量（国内网络必需）

`set_env.bat` 已配好，手动设置：

| 变量 | 值 | 作用 |
|---|---|---|
| `HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载镜像（必须） |
| `HF_HUB_DISABLE_XET` | `1` | 禁用 xet 后端（避免 401） |
| `HF_HOME` | `%CD%\.hf` | 模型缓存目录 |

---

## GPU 加速（可选）

实测 RTX 4060 Laptop：

| 模型 | CPU | GPU | 提速 |
|---|---|---|---|
| small | 42s | 7.8s | 5.4x |
| large-v3 | 不可用 | 59s | 379x |

```bat
venv\Scripts\python.exe -m pip install --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple nvidia-cublas-cu12 nvidia-cudnn-cu12
```

验证：`venv\Scripts\python.exe scripts\doctor.py --deep`

---

## 项目结构

```
my_app/
├── app/main.py            PyQt6 GUI 入口
├── model/                 业务逻辑层（4号）
│   ├── pipeline.py         统一入口 process()
│   ├── asr.py             faster-whisper 语音识别
│   ├── subtitle_fix.py     字幕校正流水线（核心）
│   ├── waveform.py         语音边界提取
│   ├── seconv_tools.py     Subtitle Edit 命令行
│   ├── gpu_env.py          CUDA 自动配置
│   └── ffmpeg_utils.py     FFmpeg 检查
├── controller/            调度层（2号）
│   ├── scheduler.py        批量任务调度
│   └── qt_worker.py        Qt 适配
├── config/                字幕参数
├── scripts/                诊断/测试脚本
├── build.py               PyInstaller 打包配置
├── requirements.txt
├── install_env.bat        一键装环境
└── 打包成exe.bat          一键打 exe
```

---

## 常用脚本

| 脚本 | 用途 |
|---|---|
| `scripts/doctor.py` | 环境诊断（依赖/ffmpeg/CUDA/模型） |
| `scripts/check_srt.py` | 校验 SRT 编码/结构/时间戳 |
| `scripts/test_pipeline.py` | 端到端测试 |

---

## 实测效果

人教版八年级 U1 英语课文视频（9分28秒）：

- 输出 206 条字幕
- 时间重叠：0
- 负时长/零时长：0
- 时间倒序：0
- Aboboo 断点对齐：人工听感通过

---

## 常见问题

**Q：exe 打不开/报错？**
A：确认解压后整个文件夹一起用（不能只拷 exe，旁边 `_internal` 文件夹是依赖）。

**Q：首次运行卡住？**
A：在下载 small 模型（464MB），走国内镜像，等几分钟。

**Q：报 ffmpeg 找不到？**
A：把 `ffmpeg.exe` 放到 exe 同目录。

**Q：字幕和声音对不齐？**
A：跑 `scripts\check_srt.py <srt> --media <mp4>` 查客观问题。

---

## 文档

- [第四板块-模块说明](docs/第四板块-模块说明.md)
- [第二板块-模块说明](docs/第二板块-模块说明.md)
- [GPU加速-配置与实测](docs/GPU加速-配置与实测.md)
