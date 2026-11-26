---
name: kai-create
description: Produce an original Chinese vertical video for Kai's B line from a specified P5 topic. Use to write narration, build a storyboard, prepare local Mandarin audio, render, and review a release candidate.
---

# Kai 原创视频生产

在项目根目录读取 `AGENTS.md` 和 `STATUS.md`。从指定研究任务的 `topics.json` 提取对应 B 线条目，核对 `production_state=ready` 和素材权利。只补读脚本实际引用的一手依据。按真实进度记录选题状态。

1. 写原创脚本，明确开场、证据、演示和结论。将文献结论、已完成的实测和编辑判断分别准确归因。实测先冻结提示、预期和评分规则，再收集独立的真实回复。保留完整原始记录。只有方法可演示时将 `mode` 设为 `method-demo-draft`，画面会显示草稿标识。
2. 新建 `workspace/任务名/`，将脚本、场景、来源和授权写入 `storyboard.json`。把简明事实与实测摘要保存为相对路径的 Markdown。场景和音频字段见 [场景数据格式](references/storyboard.md)。
3. 优先检查本机中文 Windows 语音。把 `narration` 切为完整短句，`spoken` 用实际发音写法，`subtitle` 可保留数字等书面形式。语音质量不足时，填入用户自有录音或每句在整段录音中的已校对时间。音频缺失或不可听时报告阻塞。
4. 校验后渲染到新目录。默认 Kai 黑金、1080×1920、30 FPS、NVENC H.264 和 AAC。合成配音要标记 AI 生成内容。

```powershell
.\.venv\Scripts\python.exe src/create.py 'workspace/任务名/storyboard.json' --check
.\.venv\Scripts\python.exe src/create.py 'workspace/任务名/storyboard.json' --output 'output/新成品目录'
ffmpeg -v error -xerror -nostdin -i 'output/新成品目录/video.mp4' -map 0:v:0 -map 0:a:0 -f null NUL
```

检查开头、图表、表格、结尾及三张关键帧。用实际音频核对字幕文本、短句边界和结尾有声。交付视频、封面、场景方案、ASS、渲染清单、事实与实测摘要。完成选题准备后可记录 `selected`，实际产生并验证发布候选后可记录 `produced`。人工审核状态保持待确认，发布由用户执行。
