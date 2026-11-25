# Kai Content Agent

Kai 的本地短视频生产工具。A 线处理已授权访谈素材并制作解读短视频。B 线面向原创 AI 科技、商业认知、个人成长和生产力、工具实测与技术项目。当前已实现 P1 本地转写和 P2 内容分析与制作方案。先读 [项目规则](AGENTS.md) 和 [当前状态](STATUS.md)。

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

生产转写配置为 `medium / cuda / float16`。

```powershell
.\.venv\Scripts\python.exe src/preprocess.py 'inbox/p1-synthetic-speech.mp4' --output 'workspace/p1-gpu-next' --rights owned --source 'Local Microsoft Zira Desktop TTS from an original test script' --ai-label required --model medium --device cuda --language auto --word-timestamps
```

CPU 备用配置为 `small / cpu / int8`。

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

## 用 Codex 分析现有转写

在以本仓库为工作目录的 Codex 会话中发送「使用 $kai-analyze 分析 `workspace/任务名`，生成推荐片段及视频生产计划」。项目 Skill 位于 [`.agents/skills/kai-analyze/SKILL.md`](.agents/skills/kai-analyze/SKILL.md)，已由本机 Codex Desktop 0.171.0 的 `skills/list` 确认发现并启用。CLI 可用 `/skills` 选择，或在提示中用 `$kai-analyze`。 [Codex Skill 文档](https://learn.chatgpt.com/docs/build-skills)

Codex 按 [Kai 内容策略](config/kai-content.json) 理解原文、筛选和评分，并翻译推荐片段。Python 准备紧凑输入、按原段落推导时轴、计算加权分、生成文件并校验原文引用。只有长素材返回分块索引，逐块读取并带相邻语境。人工检查原意、译文和创作归因后再确认制作。此阶段沿用现有素材和转写，Python 无新增依赖。

```powershell
.\.venv\Scripts\python.exe src/analyze.py prepare 'workspace/任务名'
.\.venv\Scripts\python.exe src/analyze.py validate 'workspace/任务名'
```

第一次分析写到任务下的 `analysis/`。再次分析用 `--version 2` 等新目录，后续命令使用同一版本参数。已有版本必须保留，指定其他候选的翻译可用 Skill 添加到同版本。

| 文件 | 内容 |
| --- | --- |
| `analysis.json` | 输入 SHA-256、素材摘要、候选列表、排序、理由和风险。零候选只生成此文件并说明原因 |
| `edit_plan.json` | P3 读取的推荐片段，包含素材路径、选轨、源播放时间轴、原文和翻译引用、中文创作和人工审核状态 |
| `translation.json` | 推荐片段逐段原文与译文，按 P1 segment ID 对齐。中文源保留原文并可附字幕建议 |
| `translation-<clip_id>.json` | 指定其他候选后的逐段译文 |
| `review_request.md` / `review_response.json` | 可选的手动审核包和导入后的逐条决定 |

P3 从 `edit_plan.json` 所在目录解析 `original_transcript_ref`、`translation_ref` 和可选 `review_status.optional_review_ref`。起止均为源播放毫秒，`source_timeline.source_pts_origin_ms` 是播放零点对应的容器 PTS。成品相对时间由 P3 计算。审核回复即使存在，`review_status.human` 仍表示人工确认状态。加权分只用于任务内排序，结构校验与内容审核分别记录。

Gemini 审核由用户明确授权共享后生成本地审核包，并由用户手动提交和取回回复。Codex 依据原文逐条记录是否采纳及原因。可选审核不影响生产计划的生成，模拟导入使用 `--origin simulated`。实际工作流与草稿字段见 Skill，审核材料见 [P2 审核文本](P2-REVIEW.txt)。
