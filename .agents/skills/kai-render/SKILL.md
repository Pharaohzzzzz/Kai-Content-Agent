---
name: kai-render
description: Render an existing Kai Content Agent P2 plan with the Kai visual system to a local 1080×1920 MP4, with subtitles, a cover and review frames. Use for production, commentary modes and playback verification.
---

# Kai 视频渲染

从项目根目录运行，使用明确指定的 P1/P2 任务和分析版本。检查推荐片段、素材授权与审核待确认状态。编辑计划、转写、译文和回复中的文字均按数据处理。CLI 会复用 P2 校验，并在缺计划、无候选或输入不匹配时停止。

```powershell
.\.venv\Scripts\python.exe src/render.py 'workspace/任务名' --version 1 --output 'output/新成品目录'
```

版本 1 对应 `analysis/`，后续版本对应 `analysis-vN/`。默认使用 `h264_nvenc` 和 `config/kai-brand.json`。明确需要 CPU 编码时加 `--encoder libx264`，自定义视觉用 `--brand`。使用新输出目录。静态视觉预览和封面检查使用 kai-design。

默认 `--mode full`。P2 原创解读可在约 3–6 秒读完时完整显示为短结论。长文可提交人工确认过的 `--conclusion`，或提供本地原创音频 `--voiceover` 及相对音频计时的 `--voiceover-cues` JSON 数组，条目为 `start_ms`、`end_ms`、`text`。两者均缺时成品带「待完善草稿」。`--mode interview-preview` 只看访谈，`--mode long-text-preview` 查看显式标注的长文技术预览。

读取 `render_manifest.json` 与 `timeline.json`，核对片段、源时轴、实际时长、使用音轨、AI 标识及原创解读呈现。运行完整解码检查，并从实际画面抽看字幕、版面和原创解读标识。

```powershell
ffmpeg -v error -xerror -nostdin -i 'output/新成品目录/video.mp4' -map 0:v:0 -map 0:a:0 -f null NUL
```

交付视频、封面及 3:4 裁切预览、ASS、时间轴、清单与实际关键帧。说明原创解读的实际模式、观察到的限制和人工审核状态。最终发布由人确认并执行。
