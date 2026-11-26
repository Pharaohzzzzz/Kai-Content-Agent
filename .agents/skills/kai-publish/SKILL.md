---
name: kai-publish
description: Prepare local Douyin and Xiaohongshu assets from a specified, already-rendered Kai A-line interview or B-line original. Use for platform copy, optional note cards, and publication review packaging.
---

# Kai 双平台发布准备

从项目根目录读取 `AGENTS.md` 和 `STATUS.md`，定位指定成品。运行 `python src/publish.py output/成品目录 --inspect`，读取其引用的场景或编辑方案、字幕和事实记录。保留制品身份、素材权利、AI 标识及草稿状态。依据已完成的成品撰写文案，技术预览、权利不明或有重大事实风险时只生成阻塞清单。

在 `workspace/` 保存 UTF-8 JSON 编辑稿。`source_manifest_sha256` 填 `--inspect` 返回的 `render.sha256`。`fact_review` 填 `status` 和 `note`，只在成品、字幕与现有出处一致时使用 `checked`。仍待确认用 `pending`，重大事实问题用 `major_risk`，说明具体原因。

`platforms` 下分别填写 `douyin` 和 `xiaohongshu` 的 `title`、`body`、`tags`（话题名称数组）和 `source_note`（实测、访谈或其他必要来源说明）。抖音优先写真实冲突和观看收获。小红书优先写可搜索的任务、方法和复用步骤。使用与内容相关的话题。保持数字、适用范围、引语出处及测试条件准确。

适合图文时填写 `cards` 数组，按阅读顺序给出 5–7 张独立卡片。每张包含 `kind`、`title`、`footer`（来源或自制说明），可加 `label`。复用 [P6 视觉数据格式](../kai-create/references/storyboard.md) 的 `title`、`table`、`chart` 或 `conclusion` 字段，提供 `cards_ai_generated` 布尔值。用已有数据重排证据、方法与结论。图文价值不足时填写 `cards_omitted_reason`。

```powershell
.\.venv\Scripts\python.exe -X utf8 src/publish.py 'output/成品目录' --editorial 'workspace/编辑稿.json' --output 'output/新发布包'
```

检查 `publish_manifest.json`、两平台文案与封面。图文逐张核对内容、字号、遮挡和来源。`awaiting_human_review` 表示本地打包完成，仍需人工终审与实机检查。`blocked` 只交付清单及阻塞原因。最终发布由用户在平台手动执行。
