# MP4字幕矫正工具

导入 MP4 视频，自动输出**矫正后的 MP4 + 标准 SRT 字幕**，保证导入 Aboboo 后音字对齐。

## 目录结构

```
MP4字幕矫正工具/
├── gui/                # 1号：GUI 界面（PyQt6）
│   ├── main.py         #   程序入口
│   ├── view/           #   窗口与信号槽
│   └── controller/     #   调度器（当前为 Mock 模拟版）
├── scheduler/          # 2号：业务调度 & 多线程控制
│   ├── controller/     #   real_scheduler.py 真实调度器
│   ├── model/          #   config.py 全局配置 / exceptions.py 异常体系
│   └── tests/          #   单元测试（14 项）
├── ffmpeg_module/      # 3号：音视频 FFmpeg 模块
│   ├── model/          #   ffmpeg_processor.py 核心处理类
│   └── scripts/        #   test_ffmpeg.py 独立验证脚本
├── docs/               # 接口文档（对接说明书、接口对接文档）
├── assets/ffmpeg/      # 打包用 FFmpeg 资源（不入库，由脚本准备）
├── tests/              # 5号：测试脚本（测试计划/执行报告见 D:\yinpin）
├── demo/               # 真实功能 Demo（demo_main.py 入口 / demo_build.py 打包）
├── requirements.txt    # Python 依赖清单
├── setup.ps1           # 一键环境安装脚本
├── build.py            # PyInstaller 打包配置
└── build.ps1           # 一键打包脚本
```

## 环境要求

| 组件 | 说明 |
|---|---|
| Python | **3.11**（推荐 `D:\python3.11\python.exe` 或 `py -3.11`） |
| FFmpeg | 需安装并加入 PATH，或放到 `assets\ffmpeg\`（打包时嵌入） |
| Aboboo | 字幕对齐验收用（外部软件，手动安装） |
| Subtitle Edit | 字幕微调工具（4号 调用命令行版，手动安装） |

## 快速开始

```powershell
# 1. 一键安装环境（检查 Python/FFmpeg，创建 venv，安装依赖）
.\setup.ps1

# 2. 运行 GUI（开发模式，用 Mock 调度器）
.\venv\Scripts\python.exe .\gui\main.py

# 3. 跑 2号 单元测试
.\venv\Scripts\python.exe -m pytest .\scheduler\tests\

# 4. 跑 3号 FFmpeg 自测
.\venv\Scripts\python.exe .\ffmpeg_module\scripts\test_ffmpeg.py test.mp4 test_output

# 5. 打包 exe
.\build.ps1
```

## Demo 体验版（可直接双击，已打包）

不需要装环境，双击 exe 就能用（已接入 3号 FFmpeg，**真提取音频 + 真输出矫正 MP4**）。

**位置**：`dist_demo\MP4字幕矫正工具Demo\MP4字幕矫正工具Demo.exe`（桌面有快捷方式）

**使用步骤**：
1. 双击 exe 打开窗口
2. 点"选择 MP4 文件"导入视频
3. 点"选择目录"指定输出位置（**不选则输出到视频同目录**）
4. 点"开始"，看进度条和日志
5. 完成后输出目录出现：`xxx.wav`（16kHz 单声道音频）+ `xxx_矫正.mp4`（-c copy 防音画偏移）

**说明**：字幕 `.srt` 暂不生成——界面会提示"Whisper 识别待 4号 ASR 模块接入"。4号 交付后即可补齐。

**改代码后重新打包**（演示版）：
```powershell
D:\python3.11\python.exe demo\demo_build.py
```

**Demo 代码结构**：
- `demo/demo_main.py` —— Demo 入口（复用 1号 GUI）
- `demo/real_demo_scheduler.py` —— 真实调度（接 3号 FFmpeg）
- `demo/demo_build.py` —— Demo 专用打包脚本
- `demo/demo_smoke_test.py` —— 端到端自动验证（已 PASS）

## GitHub 协作

仓库：`https://github.com/Jason-marhabar/MP4-subtitle-corrector`（公开，master 分支）

**成员拉取最新代码**：
```powershell
git clone https://github.com/Jason-marhabar/MP4-subtitle-corrector.git
# 之后每次更新
git pull
```

**提交自己的修改**：
```powershell
git add .
git commit -m "说明改了什么"
git push
```

**注意**：
- 本机已配置 GitHub 代理（127.0.0.1:7890），直连不通时保持代理开启
- Python 统一用 `D:\python3.11\python.exe`（PATH 默认 python 是 3.14，会踩坑）
- 大文件（ffmpeg.exe/ffprobe.exe/模型）不入库，由 `assets\ffmpeg\` 脚本准备

## 分支管理规范

- `main`：正式可用版本，只有组长可合并
- `dev`：日常开发集成分支，各成员功能分支合并到此
- 功能分支：`dev-1号-gui`、`dev-2号-scheduler`、`dev-3号-ffmpeg`、`dev-4号-asr`、`dev-5号-test`、`dev-6号-build`
- 提交流程：功能分支开发 → 提交 → 合并到 `dev` → 联调通过后由组长合并 `main`
- **接口改动必须同步相关成员确认**（如 `extract_audio` 参数变更需 2号/3号 同时确认）

## Whisper 模型路径约定

- 模型默认存放目录：`models/`（不入库，体积大）
- 运行时查找顺序：环境变量 `WHISPER_MODEL_DIR` → `sys._MEIPASS/models`（打包后）→ `./models`
- 打包版通过 `--add-data models:models` 或运行时从外部目录加载

## 常见问题

- **命令找不到 python**：使用完整路径 `D:\python3.11\python.exe`
- **ffmpeg 找不到**：`where ffmpeg` 检查；或下载 ffmpeg 放 `assets\ffmpeg\`
- **pip 慢**：setup.ps1 已默认配置清华镜像
- **打包后 ffmpeg 缺失**：确认 build.py 中 `--add-binary` 指向 `assets\ffmpeg\`
