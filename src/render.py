"""Render one validated Kai production plan to a new project output directory."""

import argparse
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from analysis_plan import PROJECT, check_same, json_text, load_source, read_json, require, validate
from analysis_review import validate_review
from media import probe, run_media_tool
from render_brand import (
    COVER_CROP_HEIGHT, COVER_CROP_Y, DEFAULT_BRAND, FPS, HEIGHT, WIDTH, color_input, compose,
    extract_picture, ffmpeg_still, load_brand, render_still, source_layout,
)
from render_commentary import SAMPLE_RATE, prepare_commentary
from render_subtitles import frame_ms, speech_cues, write_ass, write_cover_ass

ENCODERS = {
    "h264_nvenc": ("-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"),
    "libx264": ("-preset", "medium", "-crf", "19"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def new_output(path: Path) -> Path:
    require(not path.exists() and not path.is_symlink(), f"Render output already exists: {path}")
    output = path.resolve()
    require(PROJECT / "output" in output.parents, f"Choose a new directory under {PROJECT / 'output'}.")
    return output


def video_filter(
    video_index: int | None, cut_us: int, clip_frames: int, total_frames: int, brand: dict, layout: str, crop: str,
) -> str:
    canvas = f"[1:v]trim=end_frame={total_frames},format=yuv420p"
    # Round a source frame forward to the first 30 fps tick where its PTS has elapsed.
    # Trimming after resampling retains a held frame already visible at the cut.
    picture = (
        f"[0:{video_index}]settb=AVTB,setpts=PTS-({cut_us}),fps={FPS}:round=up,"
        f"trim=start=0,trim=end={clip_frames / FPS:.9f}"
    ) if video_index is not None else None
    return compose(
        canvas, picture, brand[layout]["video"], crop, brand, "subtitles.ass", clip_frames=clip_frames,
    )


def encode(
    staged: Path, source: Path, cut_us: int, end_us: int, clip_frames: int, total_frames: int,
    video_index: int | None, audio_index: int, channels: int, encoder: str, brand: dict, layout: str,
    crop: str, voice: Path | None,
) -> tuple[float, list[str]]:
    samples = total_frames * (SAMPLE_RATE // FPS)
    # Trim decoded source audio at both edges. Fill a late start against the shared
    # cut zero, then extend with silence for the video frame grid and commentary.
    interview_samples = clip_frames * (SAMPLE_RATE // FPS) if voice else samples
    audio = (
        f"[0:{audio_index}]atrim=start={cut_us}us:end={end_us}us,"
        f"asettb=AVTB,asetpts=PTS-({cut_us}),"
        f"aresample={SAMPLE_RATE}:async=1:first_pts=0:min_hard_comp=0.020,"
        f"apad=whole_len={interview_samples},atrim=end_sample={interview_samples}"
    )
    audio += (
        f",aformat=channel_layouts={'stereo' if channels == 2 else 'mono'}[speech];"
        f"[speech][2:a]concat=n=2:v=0:a=1,apad=whole_len={samples},atrim=end_sample={samples}[a]"
    ) if voice else "[a]"
    graph = video_filter(video_index, cut_us, clip_frames, total_frames, brand, layout, crop) + ";" + audio
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-n", "-nostats", "-xerror", "-copyts",
        "-i", str(source), "-f", "lavfi", "-i", color_input(brand),
        *(["-i", str(voice)] if voice else []),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-map_metadata", "-1", "-map_chapters", "-1", "-c:v", encoder, *ENCODERS[encoder],
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", str(channels),
        "-b:a", "192k" if channels == 2 else "128k", "-movflags", "+faststart", "video.mp4",
    ]
    started = perf_counter()
    result = subprocess.run(command, cwd=staged, capture_output=True, text=True, encoding="utf-8", errors="replace")
    render_seconds = perf_counter() - started
    if result.returncode != 0:
        detail = "\n".join(result.stderr.splitlines()[-24:])
        raise RuntimeError(f"FFmpeg render failed ({encoder}):\n{detail}")
    log = result.stderr.splitlines()
    require(not any("failed to find any fallback" in line.lower() for line in log),
            "FFmpeg libass could not find a glyph. Check the subtitle font installation.")
    fonts = sorted({line.split("fontselect:", 1)[1].strip() for line in log if "fontselect:" in line})
    require(bool(fonts), "FFmpeg libass did not report a selected subtitle font.")
    return round(render_seconds, 3), fonts


def inspect_video(path: Path, total_frames: int, channels: int) -> dict:
    metadata = json.loads(run_media_tool([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))
    video, audio = metadata["streams"]
    require(
        video["codec_name"] == "h264" and (video["width"], video["height"]) == (WIDTH, HEIGHT)
        and video["pix_fmt"] == "yuv420p" and video["avg_frame_rate"] == f"{FPS}/1"
        and int(video["nb_frames"]) == total_frames and Decimal(video["start_time"]) == 0
        and audio["codec_name"] == "aac" and int(audio["sample_rate"]) == SAMPLE_RATE
        and audio["channels"] == channels and Decimal(audio["start_time"]) == 0,
        "Encoded media does not match the planned video and audio format.",
    )
    expected = Decimal(total_frames) / FPS
    require(
        all(abs(Decimal(stream["duration"]) - expected) <= Decimal("0.002") for stream in (video, audio)),
        "Encoded stream durations do not match the frame and sample timeline.",
    )
    return {
        "duration_seconds": float(metadata["format"]["duration"]),
        "size_bytes": path.stat().st_size,
        "video": {key: video.get(key) for key in (
            "codec_name", "profile", "width", "height", "pix_fmt", "avg_frame_rate", "bit_rate",
        )},
        "audio": {key: audio.get(key) for key in ("codec_name", "profile", "sample_rate", "channels", "bit_rate")},
    }


def create_visuals(
    staged: Path, temp: Path, args: argparse.Namespace, plan: dict, brand: dict, layout: str,
    source: Path, video_index: int | None, cover_ms: int | None, label: str, cues: list[dict], pages: list[dict],
) -> dict:
    cover_ass = staged / "cover-layout.ass"
    write_cover_ass(cover_ass, plan, brand, args.cover_title_en, label, video_index is not None)
    cover_picture = (
        extract_picture(source, video_index, cover_ms, temp, "cover-source.png")
        if video_index is not None else None
    )
    render_still("cover.png", staged, brand, brand["cover"]["video"], cover_picture, "fit", cover_ass.name, cover=True)
    cover_ass.unlink()
    ffmpeg_still([
        "-i", "cover.png", "-vf", f"crop={WIDTH}:{COVER_CROP_HEIGHT}:0:{COVER_CROP_Y}",
        "-frames:v", "1", "-threads:v", "1", "-update", "1", "cover-3x4.png",
    ], staged)

    cue = max(cues[1:] or cues, key=lambda item: len(item["zh_lines"]) + len(item["original_lines"]))
    times = {"frame-title.png": 0, "frame-subtitles.png": (cue["start_ms"] + cue["end_ms"]) // 2}
    if pages:
        first = pages[0]
        times["frame-commentary.png"] = frame_ms((first["start_frame"] + first["end_frame"] - 1) // 2)
    for name, time in times.items():
        if args.preview_only:
            picture = None
            if video_index is not None and name != "frame-commentary.png":
                picture = extract_picture(source, video_index, plan["source_start_ms"] + time, temp, name)
            render_still(
                name, staged, brand, brand[layout]["video"], picture, args.crop, "subtitles.ass", at_ms=time,
            )
        else:
            ffmpeg_still([
                "-ss", f"{time / 1000:.3f}", "-i", "video.mp4", "-map", "0:v:0",
                "-frames:v", "1", "-threads:v", "1", "-update", "1", name,
            ], staged)
    return times


def render(args: argparse.Namespace) -> None:
    started = perf_counter()
    output = new_output(args.output)
    brand, brand_hash = load_brand(args.brand)
    task = args.task.resolve()
    require(PROJECT / "workspace" in task.parents, "Choose a P1/P2 task under project workspace.")
    analysis = task / ("analysis" if args.version == 1 else f"analysis-v{args.version}")
    source = load_source(task)
    artifacts = validate(source, analysis, read_json(PROJECT / "config/kai-content.json"))
    has_plan = "edit_plan.json" in artifacts
    validate_review(analysis, has_plan)
    if not has_plan:
        reason = artifacts["analysis.json"]["no_candidate_reason"]
        raise ValueError(f"No recommended clip. {reason}")
    plan = artifacts["edit_plan.json"]
    artifact_hashes = {}
    for name, artifact in artifacts.items():
        payload = (analysis / name).read_bytes()
        check_same(json.loads(payload.decode("utf-8-sig")), artifact, name)
        artifact_hashes[name] = hashlib.sha256(payload).hexdigest()
    transcript_payload = (task / "transcript.json").read_bytes()
    require(hashlib.sha256(transcript_payload).hexdigest() == source["input_sha256"]["transcript.json"],
            "P1 transcript changed after validation.")
    transcript = json.loads(transcript_payload.decode("utf-8-sig"))

    source_path = Path(plan["source_media"]).resolve(strict=True)
    require(source_path.is_file(), f"Original media must be a local file: {source_path}")
    source_stat = source_path.stat()
    source_hash = sha256(source_path)
    metadata, audio_index, origin_us = probe(source_path, plan["selected_audio_index"])
    require(round(origin_us / 1000) == plan["source_timeline"]["source_pts_origin_ms"],
            "The current source playback origin differs from the P1 timeline.")
    video_index = next((
        stream["index"] for stream in metadata["streams"]
        if stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
    ), None)
    audio_stream = next(stream for stream in metadata["streams"] if stream["index"] == audio_index)
    channels = min(2, audio_stream["channels"])
    start, end = plan["source_start_ms"], plan["source_end_ms"]
    source_duration = metadata["format"]["duration_ms"]
    require(source_duration is None or end <= source_duration,
            "The current source ends before the recommended clip.")
    cut_us, end_us = origin_us + 1000 * start, origin_us + 1000 * end
    clip_frames = ((end - start) * FPS + 999) // 1000
    layout = source_layout(source_path, video_index)
    cues = speech_cues(plan, artifacts["translation.json"], transcript, brand, brand[layout]["subtitles"])
    cover_ms = args.cover_time_ms
    if cover_ms is not None:
        require(video_index is not None and 0 <= cover_ms and (source_duration is None or cover_ms < source_duration),
                "--cover-time-ms must identify a playback time within a visual source.")
    elif video_index is not None:
        cover_ms = start + (end - start) // 3
    source_label = "合成测试" if "synthetic test" in plan["source"].lower() else ""

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".p4-", dir=output.parent) as directory:
        temp = Path(directory)
        staged = temp / "render"
        staged.mkdir()
        voice_inputs = {}
        if args.voiceover and args.voiceover_cues:
            voice_inputs = {
                key: {"path": str(path.resolve()), "sha256": sha256(path)}
                for key, path in (("audio", args.voiceover), ("cues", args.voiceover_cues))
            }
        commentary, pages, voice = prepare_commentary(
            args, plan["original_commentary_zh"], clip_frames, brand, temp, channels,
        )
        if voice_inputs:
            require(all(sha256(Path(item["path"])) == item["sha256"] for item in voice_inputs.values()),
                    "Voiceover inputs changed during preparation.")
            commentary["inputs"] = voice_inputs
        total_frames = commentary.pop("end_frame", pages[-1]["end_frame"] if pages else clip_frames)
        write_ass(
            staged / "subtitles.ass", plan, cues, pages, brand, layout, clip_frames, total_frames,
            video_index is not None, commentary["presentation"], source_label,
        )
        video = seconds = None
        fonts = []
        if not args.preview_only:
            seconds, fonts = encode(
                staged, source_path, cut_us, end_us, clip_frames, total_frames, video_index,
                audio_index, channels, args.encoder, brand, layout, args.crop, voice,
            )
            video = inspect_video(staged / "video.mp4", total_frames, channels)
        keyframes = create_visuals(
            staged, temp, args, plan, brand, layout, source_path, video_index, cover_ms, source_label, cues, pages,
        )
        current_stat = source_path.stat()
        require(
            (source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns)
            == (current_stat.st_ino, current_stat.st_size, current_stat.st_mtime_ns),
            "The source file changed during production. Render again from a stable source.",
        )
        timeline = {
            "fps": FPS,
            "interview_frames": clip_frames,
            "total_frames": total_frames,
            "speech_cues": cues,
            "commentary_pages": pages,
        }
        manifest = {
            "schema_version": 2,
            "source": {
                "path": str(source_path), "sha256": source_hash, "sha256_scope": "current_render_input",
                "provenance": plan["source"], "rights_status": plan["rights_status"],
                "language": plan["source_language"], "video_stream_index": video_index,
                "selected_audio_stream_index": audio_index, "original_audio": audio_stream,
                "playback_origin_pts_us": origin_us,
            },
            "p2": {
                "analysis_version": args.version, "plan_path": str(analysis / "edit_plan.json"),
                "sha256": artifact_hashes, "p1_input_sha256": source["input_sha256"],
            },
            "recommended_clip_id": plan["recommended_clip_id"],
            "source_playback_ms": {"start": start, "end": end},
            "source_clip_pts_us": {"start": cut_us, "end": end_us},
            "output_timeline": {
                "source_content_ms": {"start": 0, "end": end - start},
                "interview_video_ms": {"start": 0, "end": frame_ms(clip_frames)},
                "total_video_ms": {"start": 0, "end": frame_ms(total_frames)},
            },
            "mp4": {
                **video, "encoder": args.encoder, "encoder_options": ENCODERS[args.encoder],
                "audio_target_bitrate": "192k" if channels == 2 else "128k",
            } if video is not None else None,
            "subtitle_fonts": fonts,
            "ai_label": plan["ai_label"],
            "render_mode": args.mode,
            "preview_only": args.preview_only,
            "visual_source": "source_video" if video_index is not None else "labeled_audio_only_card",
            "brand": {"version": brand["version"], "template": brand["template"], "sha256": brand_hash},
            "composition": {
                "layout": layout, "mode": args.crop if video_index is not None else "audio_only",
                "video_region": brand[layout]["video"], "platform_ui_reserve": brand["platform_ui_reserve"],
            },
            "subtitles": {"pagination_basis": "one_complete_segment",
                          "segments_over_two_lines": [
                              cue["segment_id"] for cue in cues
                              if "kept_complete_segment_over_two_lines" in cue["reading"]["review_flags"]
                          ]},
            "original_commentary": {**commentary, "pages": len(pages)},
            "cover": {
                "source": "source_video_frame" if video_index is not None else "labeled_audio_only_card",
                "source_stream_index": video_index, "requested_source_playback_ms": cover_ms,
                "selection": ("explicit_time" if args.cover_time_ms is not None else "one_third_into_clip")
                if video_index is not None else None,
                "composition": "fit", "english_title": args.cover_title_en or None,
                "central_crop": {"path": "cover-3x4.png", "x": 0, "y": COVER_CROP_Y,
                                 "width": WIDTH, "height": COVER_CROP_HEIGHT},
            },
            "review_frames_requested_ms": keyframes,
            "render_seconds": seconds,
            "production_seconds": round(perf_counter() - started, 3),
            "content_risks": plan["content_risks"],
            "verification_needed": plan["verification_needed"],
            "review_status": {"human": "pending"},
        }
        for name, value in (("timeline.json", timeline), ("render_manifest.json", manifest)):
            (staged / name).write_text(json_text(value, indent=2) + "\n", encoding="utf-8", newline="\n")
        # On the supported Windows runtime rename is atomic and refuses an existing target.
        new_output(output)
        staged.rename(output)
    result = f"{video['duration_seconds']:.3f}s | {args.encoder} | render {seconds:.3f}s" if video else "static previews"
    print(f"{output} | {result} | {commentary['status']}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path, help="P1/P2 task directory under workspace")
    parser.add_argument("--version", type=int, default=1, metavar="N", help="P2 analysis version")
    parser.add_argument("--output", type=Path, required=True, help="New render directory under output")
    parser.add_argument("--mode", choices=("full", "interview-preview", "long-text-preview"), default="full")
    parser.add_argument("--encoder", choices=ENCODERS, default="h264_nvenc")
    parser.add_argument("--brand", type=Path, default=DEFAULT_BRAND, help="Kai visual configuration JSON")
    parser.add_argument("--crop", choices=("fit", "center-crop"), default="fit", help="Source video composition")
    parser.add_argument("--conclusion", help="Reviewed short original conclusion in Chinese")
    parser.add_argument("--voiceover", type=Path, help="Local original-commentary audio")
    parser.add_argument("--voiceover-cues", type=Path, help="Voiceover-relative subtitle JSON")
    parser.add_argument("--cover-time-ms", type=int, help="Cover frame time in source playback milliseconds")
    parser.add_argument("--cover-title-en", default="", help="Optional English cover title")
    parser.add_argument("--preview-only", action="store_true", help="Make stills and sidecars without an MP4")
    args = parser.parse_args()
    if args.version < 1:
        parser.error("--version must be positive")
    try:
        render(args)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"Kai render error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
