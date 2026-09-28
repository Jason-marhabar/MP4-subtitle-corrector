# MP4 字幕矫正工具

用户提供 MP4 文件，导入系统，输出矫正后的 MP4 和 SRT 字幕文件，确保导入 Aboboo 能音字对齐。

## 目录结构

```
my_app/
├── main.py               # 程序入口
├── view/                 # 所有窗口 UI（任务1）
│   ├── signals.py        # GUI 与业务层的信号/槽接口契约（关键，勿随意改动）
│   └── main_window.py    # 主窗口
├── controller/           # 事件控制层 / 业务调度层（任务2）
│   └── mock_scheduler.py # 演示用假调度器（联调时替换为真实调度器）
├── model/                # 数据、业务逻辑（任务3/4：FFmpeg、ASR、字幕）
├── assets/               # 图标、图片资源
├── db/                   # SQLite 数据库文件（当前未用到）
├── requirements.txt      # 依赖清单
└── README.md
```

## 环境搭建

1. 安装 Python 3.11（本项目已统一锁定 3.11.16，推荐 3.10~3.11，PyInstaller 兼容性更好；本机有 uv 时可用 `uv python install 3.11`）
2. 创建虚拟环境并激活：
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. 安装依赖：
   ```powershell
   pip install -r requirements.txt
   ```

## 运行

```powershell
python main.py
```

当前接入的是演示用假调度器（`controller/mock_scheduler.py`），选择几个 MP4 文件即可看到完整界面效果。联调时把 `main.py` 里的 `MockScheduler` 替换为任务2的真实调度器即可。

## GUI 与业务层接口约定

见 `view/signals.py`，这是任务1（GUI）和任务2（调度）对接的核心协议：

- **GuiSignals**（GUI → 业务层）：`start_requested` / `pause_requested` / `resume_requested` / `stop_requested` / `clear_requested`
- **WorkerSignals**（业务层 → GUI）：`log` / `task_progress` / `task_finished` / `task_error` / `all_finished`

双方必须严格按该文件的信号名和参数类型开发，改动需双方同步确认。
