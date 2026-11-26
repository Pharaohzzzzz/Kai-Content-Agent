# Kai Content Agent

Kai 的本地短视频生产工具。A 线处理已授权访谈素材并制作解读短视频。B 线面向原创 AI 科技、商业认知、个人成长和生产力、工具实测与技术项目。当前已实现 P1 本地转写、P2 内容分析与制作方案、P3 视频渲染、P4 品牌视觉、P5 日常选题研究和 P6 原创视频生产。先读 [项目规则](AGENTS.md) 和 [当前状态](STATUS.md)。

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

`inbox/` 存放源素材，`workspace/` 存放任务结果，`output/` 存放独立的视频成品目录。Git 忽略这些运行目录及 `.venv/`、`models/`、`.cache/` 和 `tmp/`。

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

P2 校验以 `edit_plan.json` 所在目录为基准，核对 `original_transcript_ref`、`translation_ref` 和可选 `review_status.optional_review_ref`。起止均为源播放毫秒，`source_timeline.source_pts_origin_ms` 是播放零点对应的容器 PTS。成品相对时间由 P3 计算。审核回复即使存在，`review_status.human` 仍表示人工确认状态。加权分只用于任务内排序，结构校验与内容审核分别记录。

Gemini 审核由用户明确授权共享后生成本地审核包，并由用户手动提交和取回回复。Codex 依据原文逐条记录是否采纳及原因。可选审核不影响生产计划的生成，模拟导入使用 `--origin simulated`。实际工作流与草稿字段见 Skill，审核材料见 [P2 审核文本](P2-REVIEW.txt)。

## 品牌视觉与渲染

在项目根目录运行 CLI，或在 Codex 使用 [`$kai-design`](.agents/skills/kai-design/SKILL.md) 和 [`$kai-render`](.agents/skills/kai-render/SKILL.md)。[品牌配置](config/kai-brand.json) 管理颜色、字体、行高和区域，默认使用 Windows 本机微软雅黑和 Arial、支持 libass 的 FFmpeg、`h264_nvenc` 编码。CPU 备用为 `--encoder libx264`。

```powershell
$conclusion = '同类工单总人工耗时连续几天没降，就暂停扩大试点。'
.\.venv\Scripts\python.exe src/render.py 'workspace/p3-real-chain' --output 'output/kai-preview' --preview-only --conclusion $conclusion
.\.venv\Scripts\python.exe src/render.py 'workspace/p3-real-chain' --output 'output/kai-next' --conclusion $conclusion
ffmpeg -v error -xerror -nostdin -i 'output/kai-next/video.mp4' -map 0:v:0 -map 0:a:0 -f null NUL
```

- `--version 1` 对应 `analysis/`，`--version 2` 对应 `analysis-v2/`。CLI 复用 P2 校验。素材不可访问、授权不足、无推荐片段或输出目录已存在时会报错。
- `--brand` 可读取自定义品牌 JSON。默认 `--crop fit` 按素材显示比例选择横屏或竖屏区域并保留完整画面。`--crop center-crop` 为显式中心裁切，需检查边缘内容。文字区域采用可配置的保守平台留边，客户端遮挡待实测。
- 成品为 1080×1920、30 fps、H.264/AAC MP4。渲染按源播放零点换算 PTS，使用 P1 指定的音轨。视频边界对齐到帧，原始音频精确裁到计划边界。
- 英文素材显示 P2 中文译文和 P1 原文，中文素材显示 P1 原文。以 P1 段落时间同时呈现完整双语，优先按标点和单词换行，两行放不下时保留全段并记录例外。时间轴提供阅读速度估算。
- 默认 `--mode full`。约 3–6 秒可读完的完整观点或显式 `--conclusion` 显示为「原创结论」。解读超出时长且未提供配音时标为「待完善草稿」。`--mode interview-preview` 为访谈预览，`--mode long-text-preview` 为静音长文技术预览。
- 提供本人或已授权的解读音频时，用 `--voiceover audio.wav --voiceover-cues cues.json`。UTF-8 JSON 格式如 `[{"start_ms":0,"end_ms":2000,"text":"第一条配音字幕。"}]`，时间相对于解读音频，区间需按顺序排列且不重叠。
- 封面默认取推荐片段约三分之一处的源帧。`--cover-time-ms` 接收源播放毫秒，`--cover-title-en` 接收英文副标题。用 `--preview-only` 生成静态版面、封面和辅助文件。

| 文件 | 内容 |
| --- | --- |
| `video.mp4` | 烧录字幕的成品或访谈预览 |
| `cover.png` / `cover-3x4.png` | 1080×1920 封面及中央 1080×1440 裁切 |
| `subtitles.ass` | 独立可查的字幕、标题和文字卡 |
| `timeline.json` | 成品字幕毫秒、计时依据和原创解读帧边界 |
| `render_manifest.json` | 素材和配置身份、构图、时间轴、编码、解读、封面来源和人工审核状态 |
| `frame-*.png` | 正片抽帧，或在静态模式下用同一布局生成的预览 |

原始媒体 SHA-256 从本次实际渲染输入计算。人工审核时播放视频并检查原意、画面、字幕、原创观点及 AI 标识。P4 运行证据见 [P4 审核文本](P4-REVIEW.txt)。

## 每日选题与素材研究

在以仓库为工作目录的 Codex 会话中发送「使用 $kai-research，寻找今天最值得 Kai 账号制作的选题」。Codex 复用账号定位，追溯一手证据、判断中文受众价值并给出原创切入点。Python 用标准库采集公开订阅和社区元数据，并完成去重、加权排序、制作准入和历史记录。草稿字段见 [kai-research Skill](.agents/skills/kai-research/SKILL.md)。

```powershell
.\.venv\Scripts\python.exe -X utf8 src/research.py collect kai-next --hours 72
# Codex 核查来源后写入 workspace/kai-next/research-draft.json
.\.venv\Scripts\python.exe -X utf8 src/research.py finalize kai-next
.\.venv\Scripts\python.exe -X utf8 src/research.py validate kai-next
.\.venv\Scripts\python.exe -X utf8 src/research.py history
```

- 任务目录名须唯一。默认查看近 72 小时，近 7 天用 `--hours 168`。发布日仅有日期时保留日期精度。日期未知的条目可以留作线索，社区提交时间只代表社区记录。
- 可用 `--manual <链接清单.json>` 补充用户或 Codex 提供的公开链接和短摘要，纯手工研究加 `--offline`。正文是否读到、发生超时及未接通入口会分别记录。首次试点的公开来源与权限见 [信息源注册表](config/research-sources.json)。
- AIHOT 将作为未来优先的热点聚合入口，当前为 `pending` 且禁用采集及聚合数据导入。授权后按新版 REST 文档确认精选约 20 条、热点榜前十和个别事件详情的访问条件。 [API 文档](https://aihot.news/agent?tab=api) · [条款](https://aihot.news/terms)
- 历史记录写入 `workspace/research-history.jsonl`。真实进度发生后用 `history <topic_id> <状态> --note <依据>` 更新。可选 `gemini-prompt <任务名>` 在本地生成提示词，用户可以手动提交，反馈仍须核查原始来源。

| 文件 | 内容 |
| --- | --- |
| `sources.json` | 来源状态、采集方式、实际窗口、短摘要、社区信号和去重记录 |
| `research-draft.json` | Codex 的证据核查、选题理由、评分依据及制作路线草稿 |
| `topics.json` | 计算后的分数和排序、候选状态、证据、素材权利及 A/B 研究交接 |
| `daily-brief.md` | 中文运营简报及推荐首选 |

去重与有限候选名额优先保留通过制作准入的选题，同组内按原评分排序。待核实或待补素材的选题可保留为备选。A 线须已确认权利且本地文件可用。B 线可用原创图表、文字或其他合法素材准备制作。选题记录由候选开始。P5 实际验收见 [P5 审核文本](P5-REVIEW.txt)。

## B 线原创中文视频

在仓库的 Codex 会话中发送「使用 $kai-create 制作指定 P5 选题的原创中文视频」，并提供研究任务和选题 ID。Skill 读取研究交接、准备脚本与事实记录、调用本地渲染器并检查成片。已编好的独立场景方案可直接送入命令行。 [kai-create Skill](.agents/skills/kai-create/SKILL.md) · [场景数据格式](.agents/skills/kai-create/references/storyboard.md)

```powershell
.\.venv\Scripts\python.exe -X utf8 src/create.py 'workspace/p6-ai-reliability/storyboard.json' --check
.\.venv\Scripts\python.exe -X utf8 src/create.py 'workspace/p6-ai-reliability/storyboard.json' --output 'output/新的成品目录'
```

场景按顺序使用标题、百分比图表、表格、结论或合法本地图片和录屏。沿用 Kai 黑金品牌配置和平台保守留边，默认输出 1080×1920、30 FPS、NVENC H.264 和 AAC。可用 `--encoder libx264` 切换 CPU 备用编码。

配音可选已安装的 Windows 普通话语音。也可以提交自有录音，每句话一个文件，或指定每句在整段录音里的 `clip_ms`。字幕沿实际短句音频的样本边界放置。缺少可用配音时渲染报错。演示草稿和 AI 内容按故事板状态显示标识。

交付 `video.mp4`、`cover.png`、带时间轴的 `storyboard.json`、`subtitles.ass`、`render_manifest.json`、`facts-and-test.md` 和复查关键帧。成片须由人工审核并手动发布。首条成片位于 `output/p6-ai-reliability/`。 [P6 审核文本](P6-REVIEW.txt)
