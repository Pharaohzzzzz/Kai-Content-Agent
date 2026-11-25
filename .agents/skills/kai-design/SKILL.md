---
name: kai-design
description: Apply Kai Visual System settings to an existing Kai Content Agent edit plan. Generate static video, subtitle and commentary previews, make the branded cover and its central 3:4 preview, and check the resulting layout.
---

# Kai 品牌视觉

从仓库根目录读取 `config/kai-brand.json`。默认黑金主题使用本机微软雅黑和 Arial。需要对照时，复制配置到任务目录，修改一个实际使用的参数，再用 `--brand` 和新输出目录生成静态预览。

```powershell
.\.venv\Scripts\python.exe src/render.py 'workspace/任务名' --version 1 --output 'output/新视觉预览' --preview-only
```

在预览目录打开 `frame-title.png`、`frame-subtitles.png`、`cover.png` 和 `cover-3x4.png`。采用结论、配音或长文技术预览时，同时检查 `frame-commentary.png`。结合 `timeline.json` 检查完整语句、双语分层和超行例外。结合 `render_manifest.json` 核对模板、配置 SHA-256、构图、封面时间和人工审核状态。

- 默认 `--crop fit` 保留整个源画面。只有明确选择裁切才使用 `--crop center-crop`，并检查源画面边缘的人脸、原字幕和信息。
- 可用 `--cover-time-ms` 选择源播放时点，`--cover-title-en` 增加英文副标题。音频素材使用标明无源画面的封面。
- 平台预留坐标用于本地预览。实际客户端遮挡需看客户端。模拟画面和 AI 标识须清晰。
- 长篇解读需要人工确认过的 `--conclusion` 或本地 `--voiceover` 加 `--voiceover-cues`。无可读短结论时确认草稿标记。使用相同参数去掉 `--preview-only` 即可交给 kai-render 生成正片，输出仍用新目录。
