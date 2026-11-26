---
name: kai-research
description: 为 Kai Content Agent 查找今日或指定时段值得制作的 AI、商业、创业或生产力选题。用于热点研究、事实核查、每日选题报告和 A/B 线研究交接。
---

在仓库根目录工作，读取 `config/kai-content.json` 的受众、内容偏好和时长。使用现有 Python 环境。

1. 检查本会话是否具备公开搜索或读取能力。以北京时间为准，取一个新的任务名，如 `p5-2026-11-26`。执行 `.\.venv\Scripts\python.exe -X utf8 src/research.py collect <任务名>`，默认近 72 小时，`--hours 168` 取近 7 天。
2. 如已有手工来源，用 `--manual <文件.json>` 一并导入。每条含 `title`、`source`、`url`、`published_at`（ISO 日期、带时区时间戳或 null）、`category`、`summary`、`first_hand`、`access`（确认读到正文用 `opened`，否则 `link_only`）和 `supplied_by`（`user` 或 `codex`）。联网不可用时使用 `--offline --manual` 并在结论注明。AIHOT 目前待书面授权，采集和导入均停用。
3. 读 `workspace/<任务名>/sources.json`，留意实际源状态、同一条目出现的来源、Hacker News 互动和发布时间。可另查公开来源并把证据直接加入草稿。来源数、热度与转述仅作发现线索。用 `src/research.py history` 查看是否已有同一事件。重要事实追至公告、原始论文、产品文档或完整访谈，给每条事实对应证据。有争议时找独立证据；同一机构的公告与论文不可算独立交叉验证。无法访问正文的链接不得用于确认事实，关键缺口应列为待核实。
4. 写 `workspace/<任务名>/research-draft.json`，UTF-8。推荐 3–5 条，高价值不足时少选或填空数组。按“发生了什么、对中文普通用户的价值、Kai 原创观点、表现形式和合法素材”判断，通过同一次研究综合排序。下列字段交给 Python 做确定性校验、去重、加权排序、准入判断和生成报告。
5. 依次运行 `src/research.py finalize <任务名>` 和 `src/research.py validate <任务名>`。发布本地报告路径并说明首选、生产线和素材状态。选题仅记为 `candidate`，后续真实进度可用 `history <topic_id> <状态> --note <进度依据>` 更新。被历史去重的选题只有在出现实质新增信息且能用证据解释时才重提。
6. 用户需要第二意见时运行 `src/research.py gemini-prompt <任务名>`，仅生成公开资料提示词供用户手动使用。收到建议后核实对应原始来源。

## 草稿字段

根对象为 `{"note":"本期编辑说明，零候选时必填","topics":[]}`。每条候选包含以下字段。

| 字段 | 取值 |
| --- | --- |
| `slug`, `title`, `theme` | ASCII 小写连字符 ID、中文推荐标题、用于跨日去重的具体事件主题 |
| `event_url`, `line` | 原始事件链接和 `A` 或 `B` |
| `evidence` | 每条含 `url`、`title`、`source`、`kind`（`primary/media/community/other`）、`access`（`opened/link_only`）和 `published_at`（未知为 null） |
| `claims` | 每条含 `statement`、`role`（`confirmed/party_statement/media_report/unverified/kai_analysis`）、`core` 布尔值、`urls`（引用 `evidence`）。至少一条为核心事实 |
| `audience_value`, `original_view`, `hook`, `recommendation` | 受众收益、独立观点、开场、推荐理由 |
| `scores`, `score_basis` | 七维 0–100 整数及理由 |
| `major_fact_gap`, `verification_notes` | 布尔值及待核实事项数组。存在关键缺口时注明 |
| `production` | 通用字段 `format`、`duration_seconds`（整数）、`difficulty`、`risk`，附下列路线字段 |
| `new_development` | 可选，解释本次实质新进展及新证据的价值 |

分数键为 `information_value` 25%、`audience_fit` 25%、`timeliness` 15%、`originality` 15%、`sharing_potential` 10%、`business_value` 5%、`production_feasibility` 5%。结合明确的受众、独立角度、证据和素材给分。事实和版权有独立准入条件。

- A 线 `production.interview` 包含 `interview_url`、`people`、`context`、`media_url`、`planned_segment`、`rights`、`rights_note`、`ai_label`、`has_legal_local_material`（布尔值）和 `local_path`（可空）。验收依赖已确认的用户授权或自有权益及实际本地文件。公网可看、但用户未确认授权时填 `unknown` 或 `pending`，不下载、不送入 P1。
- B 线 `production` 包含 `script_direction` 和 `materials` 数组。每项含 `kind`（`screen_recording/chart/original_text/visual`）、`description`、`essential`（布尔值）、`availability`（`ready/planned/missing`）、`rights`、`rights_note`、`ai_label` 和可选 `source_url`。必要素材需已可用且自有或有授权，或可用自有内容创制。不能作为必要素材的截图、视频或厂商图可从方案移除或记为可选。
- 两条线的 `rights` 可为 `owned/authorized/unknown/pending/denied`，`ai_label` 可为 `required/not-required/unknown`。Python 负责生成 `handoff`，仅输出研究交接数据。
