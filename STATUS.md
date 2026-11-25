# Kai Content Agent 项目状态

**P0 — PASS**

**P1 — AWAITING_GPT_REVIEW**

GPT 已验收 P0 基线 `5a59e6d27486587e5eee7dfe5493b08658828579`。项目位于 Windows 真实桌面 `D:\Desktop\Kai-Content-Agent`，原始环境检查与初始化证据保留在 P0 提交。

## P1 验证环境

2026-11-25（Asia/Shanghai）。Windows 11 专业版 26H2，Intel Core i5-13400F、32 GiB 内存，RTX 4070 12,282 MiB，驱动 621.14。Python 3.11.16、FFmpeg 和 FFprobe 8.1.1。项目 `.venv` 中实际验证了 faster-whisper 1.2.1、CTranslate2 4.9.1、Hugging Face Hub 2.4.0、PyAV 18.1.1、ONNX Runtime 1.32.0。CUDA 增量依赖为 NVIDIA cuBLAS 12.9.1.4 和 CUDA Runtime 12.9.79。未另装 cuDNN、PyTorch 或完整 CUDA Toolkit，系统驱动未改动。

## P1 实际结果

- 一条 CLI 完成授权信息登记、媒体探测、选轨、对齐抽音、原语种识别与 JSON/SRT 导出。每次使用独立任务目录，源文件保持原样。
- 验收素材为本机 Microsoft Zira Desktop 根据原创英文稿合成的双音轨 MP4，容器时长 56.125 秒，所选语音轨起始 PTS 为 2.203 秒。抽取的语音 WAV 时长 54.355 秒，用相同的音频验证两个配置。
- CPU `small / int8` 转写 8.211 秒，模型加载 1.007 秒，生成 9 段。GPU `medium / float16` 转写 2.345 秒，模型加载 1.727 秒，生成 12 段。两次与 128 词原稿的规范化词序列一致。第一段均从 2.000 秒开始，末段分别止于 53.180 秒和 53.400 秒。两个配置的 WAV 字节相同，JSON 与 SRT 的编号、时间及内容一致。
- GPU 转写期间，对整卡显存的采样约为 2,608 MiB 基线、4,867 MiB 峰值，差值 2,259 MiB。模型加载、转写与导出的 GPU CLI 全程为 5.288 秒，模型下载不计入该时间。
- 正常路径、选轨、源文件哈希检查、同名任务拒绝且四个文件哈希不变、无音轨报错均通过。产物位于 `workspace/p1-cpu-small/` 和 `workspace/p1-gpu-medium/`。合成英文样本验证了工作流。中文、真实采访及复杂背景音的识别质量没有在这次样本中测量。
- Hugging Face 直连在本机超时，两套权重经 ModelScope 取得并与 Systran 官方 SHA-256 一致。缓存已可供本地重用。

等待 GPT 审核 P1。
