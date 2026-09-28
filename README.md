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
