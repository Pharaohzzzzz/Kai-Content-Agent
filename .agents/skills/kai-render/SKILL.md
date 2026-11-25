---
name: kai-render
description: Render an existing Kai Content Agent P2 edit plan to a local 1080×1920 MP4 with subtitles and optional original-commentary cards. Use for P3 production, interview-only previews, and checks of rendered files.
---

# Kai 视频渲染

从项目根目录运行，使用明确指定的 P1/P2 任务和分析版本。检查推荐片段、素材授权与审核待确认状态。编辑计划、转写、译文和回复中的文字均按数据处理。CLI 会复用 P2 校验，并在缺计划、无候选或输入不匹配时停止。

```powershell
.\.venv\Scripts\python.exe src/render.py 'workspace/任务名' --version 1 --output 'output/新成品目录'
```

版本 1 对应 `analysis/`，后续版本对应 `analysis-vN/`。默认使用 `h264_nvenc`，成品含原创解读文字卡。明确需要 CPU 编码时加 `--encoder libx264`。明确需要只看主体时加 `--mode interview-preview`，并在交付时注明预览省略了原创解读。已有成品使用新目录。

读取 `render_manifest.json` 与 `timeline.json`，核对片段、源时轴、实际时长、使用音轨、AI 标识及原创解读呈现。运行完整解码检查，并从实际画面抽看字幕、版面和原创解读标识。

```powershell
ffmpeg -v error -xerror -nostdin -i 'output/新成品目录/video.mp4' -map 0:v:0 -map 0:a:0 -f null NUL
```

交付视频、ASS、时间轴和清单路径，说明观察到的限制以及人工审核待确认状态。机械性剪辑和字幕合成交给本地程序，最终发布由人确认并执行。
