---
name: kai-review
description: Use when importing manually collected Kai creator metrics, recording user-confirmed publications, or preparing a weekly Douyin or Xiaohongshu operations review from local data.
---

# Kai 运营复盘

在仓库根目录读取 `AGENTS.md`、`STATUS.md` 和 [P8 使用说明](../../../README.md#p8-运营数据反馈)。复制 [指标 CSV 模板](assets/metrics-template.csv) 后填后台已知值，或使用同字段的本地 JSONL。

使用 `python -X utf8 src/review.py list` 找到发布记录。新发布包先用 `register` 关联。只有用户明确确认已经实际发布，才用 `confirm --confirmed-published` 以及真实标题、时间和作品 ID 或链接登记。导入数据的请求本身不构成发布确认。按记录上的平台和形式录入，沿用后台指标名称和口径。空白表示缺失，只有后台确认为零时填 0。涉及付费流量或不同统计范围时填写可区分的 `metric_scope`。无批准的后台数据文件时，按现有本地记录复盘并注明缺口。

```powershell
python -X utf8 src/review.py import 'workspace/最新运营数据.csv'
python -X utf8 src/review.py report
```

报告默认选择北京时间本周发布的作品。读返回目录的 `review.json`，从 `comparisons[].works` 使用同平台、同形式、同指标口径、同观察窗口的单条快照，并注明实际经过小时和有效样本数。可查看 `observations` 中每条作品的最新一笔累计数据，保留单条事实的实际窗口。没有有效样本时交付空数据报告。

有指标时，按证据解释涨粉效率、收藏和分享、视频节奏、人工与成本、商业转化及下次应验证的内容方向。完播率或平均观看偏低仅是查阅留存曲线和正片的线索。区分咨询和实收，明确可算净收益的范围。写明缺失、样本不足和分析限制。勿根据小样本推断因果。选题建议交 GPT 或用户审核，保留原评分权重。

要把编辑判断写入 Markdown 和 JSON，在本地建立 UTF-8 编辑稿，复制报告的 `data_mode` 和 `basis_sha256`，在 `notes` 数组中逐项填 `section`、`text` 和 `publication_ids`（依据作品 ID 数组）。以同一发布日期范围及 `--as-of` 重新运行 `report --editorial <编辑稿.json>`，交付两个报告文件。模拟演练使用全局 `--simulate`，并明确标注模拟数据。
