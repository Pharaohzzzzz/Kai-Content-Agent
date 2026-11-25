"""Choose a reviewable original-commentary presentation and prepare local audio."""

import math
import wave
from pathlib import Path

from analysis_plan import read_json, require
from media import run_media_tool
from render_brand import FPS
from render_subtitles import commentary_pages, frame_ms, glyph_width

SAMPLE_RATE = 48_000


def prepare_commentary(args, text: str, first: int, brand: dict, temp: Path, channels: int) -> tuple[dict, list, Path | None]:
    if args.mode in ("interview-preview", "long-text-preview"):
        require(not (args.conclusion or args.voiceover or args.voiceover_cues),
                "Preview modes use the P2 commentary as-is or omit it. Remove the commentary overrides.")
        if args.mode == "interview-preview":
            return {"presentation": "interview_preview", "status": "technical_preview"}, [], None
        return (
            {"presentation": "long_text_preview", "status": "technical_preview"},
            commentary_pages(text, first, brand),
            None,
        )
    if args.voiceover or args.voiceover_cues:
        require(args.voiceover and args.voiceover_cues and not args.conclusion,
                "Supply both --voiceover and --voiceover-cues, or use --conclusion alone.")
        path = temp / "voiceover.wav"
        run_media_tool([
            "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-n", "-i", str(args.voiceover.resolve()),
            "-map", "0:a:0", "-ar", str(SAMPLE_RATE), "-ac", str(channels), "-c:a", "pcm_s16le", str(path),
        ])
        with wave.open(str(path)) as voiced:
            samples = voiced.getnframes()
        require(samples > 0, "Voiceover has no samples.")
        duration_ms = samples * 1000 // SAMPLE_RATE
        end_frame = first + (samples * FPS + SAMPLE_RATE - 1) // SAMPLE_RATE
        cues = read_json(args.voiceover_cues) if args.voiceover_cues.is_file() else None
        require(isinstance(cues, list) and bool(cues), "Voiceover cues must be a nonempty JSON array.")
        pages = []
        previous = 0
        for cue in cues:
            start, end, caption = (cue[key] for key in ("start_ms", "end_ms", "text"))
            require(type(start) is int and type(end) is int and previous <= start < end <= duration_ms
                    and isinstance(caption, str) and bool(caption.strip()),
                    "Voiceover cues must have text and ordered, non-overlapping millisecond intervals within the audio.")
            frame_start, frame_end = (first + (value * FPS + 999) // 1000 for value in (start, end))
            require(frame_start < frame_end, "Voiceover cue is shorter than one display frame.")
            pages.append({
                "start_frame": frame_start, "end_frame": frame_end, "text": caption.strip(),
                "voiceover_start_ms": start, "voiceover_end_ms": end,
            })
            previous = end
        return (
            {"presentation": "voiceover", "status": "review_candidate", "duration_ms": duration_ms,
             "caption_basis": "user_timed_cues", "end_frame": end_frame},
            pages,
            path,
        )
    conclusion = " ".join((args.conclusion if args.conclusion is not None else text).split())
    require(bool(conclusion), "Conclusion cannot be empty.")
    # Give the reader an initial beat, then budget about 5.5 Chinese ems per second.
    units = glyph_width(conclusion)
    frames = max(3 * FPS, math.ceil((0.45 + units / 5.5) * FPS))
    if frames > 6 * FPS:
        return ({
            "presentation": "draft_pending_commentary", "status": "draft",
            "reason": "original_commentary_exceeds_short_reading_budget",
        }, [], None)
    return ({
        "presentation": "short_conclusion", "status": "review_candidate",
        "text_basis": "provided_conclusion" if args.conclusion is not None else "complete_p2_commentary",
        "reading_units": round(units, 2), "display_ms": frame_ms(frames),
    }, [{"start_frame": first, "end_frame": first + frames, "text": conclusion}], None)
