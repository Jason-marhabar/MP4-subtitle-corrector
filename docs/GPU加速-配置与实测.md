# GPU 加速配置与实测结论

> 实测环境：NVIDIA GeForce RTX 4060 Laptop（8188 MiB，驱动 592.82，计算能力 8.9）
> 音频：`test.mp4` 提取的 313 秒英语语音
> 结论：**GPU 已跑通，`device="auto"` 会自动启用。**

## 一、为什么 GPU 是必需项（而非锦上添花）

| 模型 | CPU | GPU | 提速 |
|---|---|---|---|
| `small` | 42.0s（RTF 7.5x） | **7.8s（RTF 40.3x）** | 5.4x |
| `large-v3` | **22462s ≈ 6.2 小时（不可用）** | **59.2s（RTF 5.3x）** | 379x |

**关键结论：`large-v3` 只有靠 GPU 才可用。** 用 GPU 换更大的模型，通常比
"CPU + small" 更准 —— 实测 large-v3/GPU 的识别段落数（96）多于 small/CPU（100 但更碎）。

**量化的取舍**：GPU 上默认用 `int8_float16`，比 CPU 的 `int8` 略激进。
实测同素材 GPU 与 CPU 产出差异很小（100 条/439 词 vs 101 条/442 词，
差的词是 "the"/"yes"/"colors" 这类），但要求极致精度时可用
`compute_type="float16"`（显存换精度）。

## 二、三个实测踩过的坑

### 坑 1：系统装了 CUDA 13.0 也没用

本机 `nvcc` 显示 **CUDA 13.0**，但 ctranslate2 的 Windows wheel 是按
**CUDA 12.x** 编译的（官方文档明确要求 CUDA 12.x + cuDNN）。
主版本不匹配 → 13.x 的库**完全不会被使用**。

需要的是：
- `cublas64_12.dll` / `cublasLt64_12.dll`（cuBLAS 12）
- `cudnn64_9.dll` 等（cuDNN 9；官方文档另一处写 cuDNN 8，但 ctranslate2 4.8
  实际查找的是 `cudnn64_9.dll`）

### 坑 2：用 pip 分发版最省事，但镜像有讲究

```bat
venv\Scripts\python.exe -m pip install --no-deps ^
  -i https://pypi.tuna.tsinghua.edu.cn/simple ^
  nvidia-cublas-cu12 nvidia-cudnn-cu12
```

- **必须用清华镜像**：实测阿里云镜像拉这两个大 wheel（553MB + 743MB）
  会**卡死在下载阶段**（缓存 20 秒零增长）。
- **不能省 `--no-deps`**：cublas 会拖入 `nvidia-cuda-nvrtc-cu12` 等，
  而我们只需要这两个包本身。
- 装完 DLL 位于 `venv/Lib/site-packages/nvidia/<pkg>/bin`。

### 坑 3（最关键）：`os.add_dll_directory()` 无效

ctranslate2 是 C++ 扩展，内部用 `LoadLibrary("cublas64_12.dll")` 按 **PATH** 解析，
**不走 Python 的 DLL 目录机制**。实测对比：

| 做法 | 结果 |
|---|---|
| 只调 `os.add_dll_directory(...)` | ❌ 仍报 `Library cublas64_12.dll is not found or cannot be loaded` |
| 把目录**前置到 PATH** | ✅ 成功 |

本项目的 `model/gpu_env.py` 会自动完成 PATH 配置，**无需手工设置**。

## 三、另一个隐蔽陷阱：构造成功 ≠ CUDA 可用

`WhisperModel(device='cuda', ...)` 的构造是**惰性**的：

```
>>> WhisperModel('small', device='cuda', compute_type='int8_float16')
（构造成功，不报错）
>>> model.transcribe(wav)      # 第一次推理才抛错
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

我在排查时就被这一点误导过：看到"GPU 加载成功"以为修好了。
**所以诊断必须真跑一次推理**，`gpu_env.gpu_available(deep=True)` 就是做这件事。

同理，`ctranslate2.get_supported_compute_types('cuda')` 在缺 cublas 时
**仍返回完整集合** —— 它只反映编译期支持，不做实际加载。不能用它判断可用性。

## 四、并发推理：不要做

ctranslate2 的模型实例**不支持多线程并发推理**。实测两个线程共用一个
GPU 模型实例时结果不可靠（耗时统计异常），且**没有任何吞吐收益** ——
GPU 上并发只是争抢同一份显存与算力。

因此 `BatchScheduler` 用 `_infer_lock` 把**真正推理**这一段串行化：
即使 `workers>1`，队列与界面仍是并发的，只有推理排队执行。
等待时任务状态会显示"等待上一条转写完成"。

## 五、自检与排查

```bat
:: 一条命令查清环境（推荐团队第一入口）
venv\Scripts\python.exe scripts\doctor.py

:: 额外真跑一次 GPU 推理（结论可靠，首次会下载 tiny 模型 75MB）
venv\Scripts\python.exe scripts\doctor.py --deep

:: 性能对比
venv\Scripts\python.exe scripts\bench_gpu.py --models small large-v3
```

`doctor.py` 会检查：Python 与依赖、ffmpeg/ffprobe、CUDA 设备与计算类型、
`cublas64_12.dll` / `cudnn64_9.dll` 是否齐全、模型缓存、环境变量，
并在发现问题时**直接打印修复命令**。

## 六、环境变量（国内网络必需）

```bat
set HF_ENDPOINT=https://hf-mirror.com     :: 否则模型无法下载
set HF_HOME=%CD%\.hf                      :: 模型缓存放项目内，便于打包/迁移
set PIP_CACHE_DIR=%CD%\.pipcache          :: 受限环境下必需，否则 pip 卡在 Building wheel
set TMP=%CD%\.tmp
set TEMP=%CD%\.tmp
```

## 七、模型缓存占用

| 模型 | 体积 |
|---|---|
| `tiny` | 75 MB |
| `small` | 464 MB |
| `large-v3` | 2948 MB |

`large-v3` 约 3GB，已写入 `.gitignore`（`.hf/`），**切勿提交**。

## 八、已知限制

| 项 | 说明 |
|---|---|
| **并发推理不可用** | 已用锁串行化；若将来要并发，需为每个工作线程各建一个模型实例，显存会成倍占用（8GB 上 large-v3 只能 1 个） |
| **8GB 显存的上限** | `large-v3` + `int8_float16` 实测可跑；若同时开多个实例或改用 `float16` 可能爆显存 |
| **首次运行较慢** | 首次会下载模型（tiny 75MB / small 464MB / large-v3 3GB），之后走缓存 |
| **偶发首次下载失败** | 实测首次拉 tiny 模型时因网络超时导致 GPU 深检误报 False，重试即成功 |
