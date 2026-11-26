---
name: kai-content-agent
description: Kai Content Agent 的日常主入口。用 daily 做当日研究与至多一条视频，用 research 仅研究，用 produce 按选题或合法本地素材制作，用 review 导入后台数据与复盘，也可按任务 ID 恢复。
---

# Kai Content Agent

在本仓库的 Codex 会话中使用。读 `AGENTS.md`、`STATUS.md` 和 [日常使用](../../../README.md#日常使用)。下列 Skill 是当前会话按需读取并执行的流程说明。Codex 直接运行它们列出的本地 CLI。以北京时间安排研究，使用现有模型、Python 和 FFmpeg。

## 模式

| 请求示例 | 实际路由 |
| --- | --- |
| `$kai-content-agent daily` | 当日研究 → 从已核查候选中选一条 → 对应 A/B 线 → 双平台打包 → 待发布登记 |
| `$kai-content-agent research` | 当日研究 → 简报及候选推荐 |
| `$kai-content-agent produce p5-2026-11-26:measure-ai-handoff-work` | 复用该完整选题 ID 的研究交接 → 对应 A/B 线 → 双平台打包 → 待发布登记 |
| `$kai-content-agent produce inbox/interview.mp4，素材自有，来源为本人录制，AI 标识待定` | 核对来源、权利和 AI 标识 → A 线本地生产 → 双平台打包 → 待发布登记 |
| `$kai-content-agent review workspace/metrics.csv` | 导入用户提供的后台数据 → 真实台账复盘与策略建议。无文件时使用当前台账 |
| `$kai-content-agent 恢复 <任务ID>` | 核对已有任务与最近有效检查点并继续其原模式 |

## 任务和检查点

用 `.\.venv\Scripts\python.exe -X utf8` 运行 `src/task.py` 及业务 CLI。新任务先执行 `src/task.py init <任务ID> <模式>`。`produce` 按输入加 `--topic <完整ID>` 或 `--media <路径> --rights owned|authorized --source <来源> --ai-label required|not-required|unknown`。测试素材显式加 `--synthetic-test`。任务 ID 用小写 ASCII、数字和连字符。

先找用户指定或身份匹配的已有任务，执行 `src/task.py resume <任务ID>`。`verified` 给出已通过指纹检查的阶段，`next_stage` 为空即交付已有结果。阶段产物已落盘但检查点尚未写入时，先核对内容、状态和输入关联，再以 `--reused` 记录，避免重跑。若返回 `invalid_file`，从首个失效阶段继续。保留旧产物，重做时用新输出目录。原始输入变化时按报错恢复原输入或新建任务。可通过 `workspace/kai-tasks/<任务ID>/task.json` 找到输出与过去的阻塞记录。

每个成功阶段执行 `src/task.py checkpoint <任务ID> <阶段> <主产物>`。主产物见下文。需要绑定附加证据、录音或审查记录时用 `--file <路径>`，参数可重复。以实测 `--seconds`、`--manual-minutes`、`--cost-cny` 和 `--note` 记录本阶段耗时、人工与新增成本，未知值留空。`--seconds` 缺省使用阶段间墙钟时间，包含等待。缺事实、素材、实测、权利、可用配音或发生失败时执行 `src/task.py hold <任务ID> "具体原因及恢复条件"`。

## 执行

1. **研究。** 按 [kai-research](../kai-research/SKILL.md) 采集、核事实、成稿、排序。研究名可用本任务 ID 或其短后缀。`collect` 记录 `sources.json`，`research` 在 `finalize` 和 `validate` 后记录 `topics.json`。`research` 模式交付简报及候选即结束。`daily` 优先选择排序最高且尚未制作、事实与素材均满足并已完成必要实测的候选。任何必要证据缺失就寻找下一位合格候选，全部缺失则暂停。本次只绑定一条，执行 `checkpoint <任务ID> select <同一topics.json> --topic <完整ID>`。
2. **A 线。** 从研究交接或用户处取权利明确的源媒体。采用现有 `src/preprocess.py` 写入新 P1 目录，沿用已配环境、`medium / cuda / float16` 和 `--word-timestamps`，并填写来源和 AI 标识。合成验收素材也在 P1 用 `--synthetic-test`。复用旧 P1 时核对素材身份、选轨、时间轴和来源，历史上无导入哈希如实记为 `legacy_unrecorded`。`ingest` 记录 `media.json`。按 [kai-analyze](../kai-analyze/SKILL.md) 分析及校验指定版本，`analysis` 记录 `edit_plan.json`。没有可用候选或原创解读仍待完善时暂停。依 [kai-design](../kai-design/SKILL.md) 检查画面、字幕、原创解读和封面，按需做静态预览。按 [kai-render](../kai-render/SKILL.md) 生产完整审核候选，`render` 记录 `render_manifest.json`。复用时要求 P1 和 P2 哈希、源媒体、剪辑和渲染状态相符。
3. **B 线。** 读 [kai-create](../kai-create/SKILL.md)，用已核实的 P5 交接写原创口播、场景和事实记录。实测必须有原始记录，演示场景的虚构数字需在口播和画面标明。已完成且身份匹配的故事板、源证据和成品可直接检查并收录。`storyboard` 记录校验通过的候选 `storyboard.json`，`render` 记录原版成品 `render_manifest.json`。检查代表画面、字幕与实际配音、事实和标识。若紧接 P7 打包，由 P7 执行一次完整解码，否则按渲染 Skill 做一次。
4. **发布准备。** 按 [kai-publish](../kai-publish/SKILL.md) 查验成品和写双平台文案，有独立图文价值才制作小红书卡片。若 A 线来自 P5 选题，在 P7 编辑稿带 `topic_id`，CLI 会比对媒体、权利和 AI 标识。`package` 记录 `publish_manifest.json`。遇 `blocked` 保存原因并暂停。真实 P5 选题开始生产时从 `candidate` 标记 `selected`，交付经验证的发布候选后从 `selected` 标记 `produced`，保留已经达到或超过该状态的记录。用 `src/review.py register <清单> --category <内容分类>` 创建待发布记录，将其 JSON 输出保存为任务收据，`register` 记录该收据。登记多条时工时、成本留在任务账本，需写运营记录时按平台与形式分别录入。合成 A 线只用 `src/publish.py ... --simulate --output workspace/p8-simulation/<新目录>`，再用 `src/review.py --simulate register`，文案和台账均标测试。
5. **复盘。** 按 [kai-review](../kai-review/SKILL.md) 用 `src/review.py import` 导入用户提供的 CSV 或 JSONL，用 `report --name <任务ID>` 生成真实报告。`review` 记录 `review.json`，有导入时用 `--file` 附项目内数据源。报告用实际可比样本提策略，缺样本时保留空数据结论。

四种模式都只在本地处理和准备。用户人工终审、检查客户端、手动发布，并明确确认真实作品信息之后才可按 README 单独运行 `src/review.py confirm`。Gemini 为可选人工交接，沿用研究与分析 Skill 的本地审核包。
