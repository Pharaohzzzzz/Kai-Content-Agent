# 原创场景数据格式 V1

`src/create.py` 读取 UTF-8 JSON。场景和句子按数组顺序播放。脚本、数字、标题、颜色配置和素材都可独立更换。

| 顶层字段 | 用法 |
| --- | --- |
| `schema_version` | `1` |
| `topic_id`, `title` | 选题唯一 ID 与封面标题 |
| `mode` | `release-candidate` 或 `method-demo-draft` |
| `ai_generated`, `ai_note` | 布尔标识。值为真时写明哪些内容涉及 AI |
| `voice` | `{"mode":"windows-tts","voice":"Microsoft Huihui Desktop","rate":1}`，或 `{"mode":"recording"}` |
| `evidence` | 故事板目录下的 Markdown 路径，写事实和实测摘要 |
| `sources` | 数组，条目含 `title`、`url`、`use`。无外部事实时可为空 |
| `materials` | 非空数组，条目含 `description`、`origin`、`rights`、`rights_note`、`ai_generated` |
| `scenes` | 条目含唯一 `id`、一个 `visual` 对象和非空 `narration` 数组 |
| `review_scene_ids` | 可选，1 到 3 个场景 ID，按指定顺序出视频帧 |

每个 `visual` 有 `kind`、`title`，可加 `label`、`footer`。同种场景可出现多次。长标题或表格应先运行 `--check`。

| 类型 | 字段 |
| --- | --- |
| `title` | 首个场景必用。必填 `headline`，可加 `detail` |
| `chart` | `bars` 含 1 到 3 个 `{"label":"条目","value":85.8}`，单位为百分比 |
| `table` | `columns` 为 2 到 4 个文字列名，`rows` 为 1 到 6 行文字数组。可给 `column_weights` 正整数数组和 `highlight_rows` 行号数组（从 1 起） |
| `conclusion` | `items` 含 1 到 3 条简短结论 |
| `image` / `video` | `asset` 声明已授权的本地图片或录屏。录屏从头播放，短素材定格末帧，声音使用独立口播 |

`narration` 每条填 `spoken`。可设 `subtitle` 以数字等书面写法匹配实际口播，默认使用 `spoken`。短句之间可设整数 `pause_ms`，默认 110 毫秒。录音模式每条另填 `recording`。可提交一句一个文件，或在同一文件上用 `clip_ms:[开始,结束]` 声明人工校对的范围。素材和录音记录均包含 `file`、`rights`、`rights_note`、`ai_generated`。相对路径从输入故事板所在目录解析。授权值为 `owned`、`licensed` 或 `permission-granted`。

场景片段随口播样本长度生成，补齐到视频帧边界。产物 `storyboard.json` 记录每场及每句样本或毫秒边界，`subtitles.ass` 提供中文字幕。精确到逐词的时间需另有实测依据。产物故事板是审核快照，重制用原始故事板与其相对路径素材。
