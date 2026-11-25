# Kai Content Agent 项目状态

**P0 — AWAITING_GPT_REVIEW**

环境检查日期为 2026-11-25（Asia/Shanghai）。基础文件和本地运行目录已建立。P1 处于待授权状态。

## 本机环境和验证记录

| 项目 | 实查结果 |
| --- | --- |
| 桌面 | Windows 返回 `C:\Users\Administrator\Desktop`，目录联接指向 `D:\Desktop` |
| 项目 | `D:\Desktop\Kai-Content-Agent`，D 盘检查时可用 544.7 GiB |
| 系统 | Windows 11 专业版 26H2 x64，构建号 26300.9974 |
| CPU 和内存 | Intel Core i5-13400F，10 核 16 线程，安装 32 GiB（CIM 报告系统可见 31.8 GiB） |
| GPU | NVIDIA GeForce RTX 4070，驱动 621.14，总显存 12,282 MiB，检查时空闲 9,159 MiB |
| 工具 | Python 默认 3.14.7 x64，另有可运行的 3.11.16 x64 和 `venv`。Git 2.55.0.windows.3，FFmpeg 和 FFprobe 8.1.1 full build 可用 |
| 媒体验证 | FFprobe 获取了合成 1080×1920、30 fps 视频元数据。FFmpeg 的 `h264_nvenc` 与 `libx264` 各编码 3 帧成功，48 kHz 测试音转换为 16 kHz 单声道 PCM 成功 |
| 推理环境 | 在两套全局 Python 中均未发现 `faster-whisper`、`ctranslate2`、PyTorch、ONNX Runtime、OpenCV、Pillow 或 PyAV。PATH 未发现 `nvcc`、CUDA 12 的 cuBLAS DLL 或 cuDNN 9 DLL |

P0 只验证工具和基础媒体操作。语音模型与 CUDA 推理尚未实跑。P0 未安装依赖、下载模型或实现 P1。

## 关键技术决定与建议

- P1 建议复用现有 FFmpeg/FFprobe，使用 Python 3.11 项目内 `.venv`。语音部分建议先只声明 `faster-whisper`，由其安装传递依赖，首次模型下载放到项目内的本地模型目录。Windows x64 有 Python 3.11 的 CTranslate2 wheel。[CTranslate2 发布文件](https://pypi.org/project/ctranslate2/#files)
- CPU INT8 与多语种 `small` 作为首次端到端验证基线。长访谈转写优先评估 RTX 4070，按实际选定的 Windows 推理版本配齐 CUDA 12 的 cuBLAS 和对应 cuDNN，再用同一段素材验证 `medium` 的准确率、时间戳、耗时与峰值显存。[faster-whisper 安装与运行说明](https://github.com/SYSTRAN/faster-whisper#requirements)
- 后续渲染可优先试用已通过编码冒烟的 H.264 NVENC，保留 `libx264` 作为可复现的软件编码选项。合成、字幕滤镜与最终画质应在渲染阶段验证。
- 工作流以本地 Python CLI 顺序调度 FFmpeg、FFprobe 和转写模块。任务间传递 JSON、WAV 和 SRT。Gemini 作为可选人工交接，生成结构化请求，由用户在免费网页版提交可共享内容，再将结果存回任务目录供本地校验导入。

## 待办

等待 GPT 审核。P1 获授权后，从单条本地授权媒体开始，完成探测、抽取 16 kHz 单声道 PCM WAV、带时间轴的原文转写及 SRT 导出。先用一段短授权样本验证输入保持原样、音轨选择、时间轴边界、JSON/SRT 一致性和重复运行保护。翻译、筛选、剪辑、渲染与发布属于后续阶段。
