# Kai Content Agent

Kai 的本地短视频生产工具。A 线处理已授权访谈素材并制作解读短视频。B 线面向原创 AI 科技、商业认知、个人成长和生产力、工具实测与技术项目。当前已实现 P1 本地素材处理与源语言转写。先读 [项目规则](AGENTS.md) 和 [当前状态](STATUS.md)。

## Windows 环境

在项目根目录使用 PowerShell。需要 Python 3.11、FFmpeg 和 FFprobe。现有项目已建立 `.venv`，首次重建环境时执行以下命令。

```powershell
py -V:Astral/CPython3.11.16 -m venv .venv
New-Item -ItemType Directory -Path '.\tmp' -Force | Out-Null
$env:TEMP = (Resolve-Path '.\tmp').Path
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path (Get-Location).Path '.cache/pip'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在已验证的 RTX 4070 环境使用 GPU 时，向同一项目环境安装可选依赖。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

运行时会优先使用 `models/` 内的完整模型缓存。缺少模型时会尝试访问公开模型仓库。此电脑的 `small` 和 `medium` 已缓存，权重已与 Systran 公布的 SHA-256 核对。处理和转写在本机完成。运行库和缓存位于项目，CLI 仅在自身进程中调整 DLL 搜索路径。 [small 权重](https://huggingface.co/Systran/faster-whisper-small/blob/main/model.bin) · [medium 权重](https://huggingface.co/Systran/faster-whisper-medium/blob/main/model.bin)

## 一条命令处理素材

以下命令使用本机验收样本。用自有或已明确授权的本地素材替换输入路径并填写来源。每次运行需指定 `workspace/` 下尚不存在的任务目录。示例使用新目录 `p1-gpu-next` 和 `p1-cpu-next`，验收产物保留在 `workspace/p1-gpu-medium/` 和 `workspace/p1-cpu-small/`。

这台 RTX 4070 推荐的已验证配置为 `medium / cuda / float16`。

```powershell
.\.venv\Scripts\python.exe src/preprocess.py 'inbox/p1-synthetic-speech.mp4' --output 'workspace/p1-gpu-next' --rights owned --source 'Local Microsoft Zira Desktop TTS from an original test script' --ai-label required --model medium --device cuda --language auto --word-timestamps
```

CPU 基线为 `small / cpu / int8`。

```powershell
.\.venv\Scripts\python.exe src/preprocess.py 'inbox/p1-synthetic-speech.mp4' --output 'workspace/p1-cpu-next' --rights owned --source 'Local Microsoft Zira Desktop TTS from an original test script' --ai-label required --model small --device cpu --language auto --word-timestamps
```

- `--rights owned|authorized` 必填。未确认授权的素材须先确认权利。可用 `--source` 写明来源或授权记录位置，省略时记录本地文件 URI。
- `--ai-label required|not-required|unknown` 记录标识需求，默认 `unknown`。
- `--audio-stream 2` 按 FFprobe 的绝对流编号选轨。省略时优先选择标为默认的音轨，其次选首个音轨。可用 `ffprobe -v error -show_streams -of json <本地文件>` 查看编号。
- `--language auto|zh|en` 支持自动检测、指定中文或英文。多语种模型按原语种转写。`--word-timestamps` 可选，开启后在 JSON 中保留原生词级时间戳。
- 任务成功后一次发布目录。目录已存在、输入无音轨或请求的设备不可用时，CLI 返回错误。GPU 请求失败时需显式选择 CPU 重新执行。

## 输出与时间轴

| 文件 | 内容 |
| --- | --- |
| `media.json` | 文件和流元数据、所选音轨、来源、授权、AI 标识需求和时间原点 |
| `audio.wav` | 16 kHz、单声道、16-bit PCM |
| `transcript.json` | 语言、模型、设备、运行耗时和整数毫秒时间轴，可带词级时间戳 |
| `transcript.srt` | 从 JSON 同一组段落生成的原文字幕 |

以 FFprobe 的容器起始 PTS 为源播放时间零点，未报告起始 PTS 时取 0。FFmpeg 保留输入 PTS，抽取时减去此原点并对迟开始的音轨补静音、对负向前导样本裁齐。因此 `audio.wav` 的第 0 毫秒等于源媒体播放第 0 毫秒。JSON 和 SRT 可直接使用该时间参考。识别段落边界由模型估计。 [FFmpeg 重采样说明](https://ffmpeg.org/ffmpeg-resampler.html)

`inbox/` 存放源素材，`workspace/` 存放任务结果，`output/` 留作最终视频成品。Git 忽略这些运行目录及 `.venv/`、`models/`、`.cache/` 和 `tmp/`。

核心实现与依赖配置汇总见 [P1 审核文本](P1-REVIEW.txt)。
