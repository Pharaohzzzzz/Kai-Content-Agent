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
from render_subtitles import FPS, HEIGHT, WIDTH, commentary_pages, frame_ms, speech_cues, write_ass

SAMPLE_RATE = 48000
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


def video_filter(video_index: int | None, cut_us: int, clip_frames: int, total_frames: int) -> str:
    canvas = f"[1:v]trim=end_frame={total_frames},format=yuv420p"
    if video_index is None:
        return f"{canvas},ass=subtitles.ass[v]"
    # Round a source frame forward to the first 30 fps tick where its PTS has elapsed.
    # Trimming after resampling retains a held frame already visible at the cut.
    picture = (
        f"[0:{video_index}]settb=AVTB,setpts=PTS-({cut_us}),fps={FPS}:round=up,"
        f"trim=start=0,trim=end={clip_frames / FPS:.9f},"
        "scale=900:800:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1[picture]"
    )
    return (
        f"{picture};{canvas}[canvas];"
        "[canvas][picture]overlay=x=(1010-w)/2:y=420+(800-h)/2:"
        f"eof_action=pass:repeatlast=0:enable='lt(n,{clip_frames})',ass=subtitles.ass[v]"
    )


def encode(
    staged: Path, source: Path, cut_us: int, end_us: int, clip_frames: int, total_frames: int,
    video_index: int | None, audio_index: int, channels: int, encoder: str,
) -> tuple[float, list[str]]:
    samples = total_frames * (SAMPLE_RATE // FPS)
    # Trim decoded source audio at both edges. Fill a late start against the shared
    # cut zero, then extend with silence for the video frame grid and commentary.
    audio = (
        f"[0:{audio_index}]atrim=start={cut_us}us:end={end_us}us,"
        f"asettb=AVTB,asetpts=PTS-({cut_us}),"
        f"aresample={SAMPLE_RATE}:async=1:first_pts=0:min_hard_comp=0.020,"
        f"apad=whole_len={samples},atrim=end_sample={samples}[a]"
    )
    graph = video_filter(video_index, cut_us, clip_frames, total_frames) + ";" + audio
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-n", "-nostats", "-xerror", "-copyts",
        "-i", str(source), "-f", "lavfi", "-i", f"color=c=0x10141C:s={WIDTH}x{HEIGHT}:r={FPS}",
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


def render(args: argparse.Namespace) -> None:
    started = perf_counter()
    output = new_output(args.output)
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
    preview = args.mode == "interview-preview"
    pages = [] if preview else commentary_pages(plan["original_commentary_zh"], clip_frames)
    total_frames = pages[-1]["end_frame"] if pages else clip_frames
    cues = speech_cues(plan, artifacts["translation.json"], transcript)

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".p3-", dir=output.parent) as temp:
        staged = Path(temp) / "render"
        staged.mkdir()
        write_ass(staged / "subtitles.ass", plan, cues, pages, clip_frames, total_frames, video_index is not None, preview)
        seconds, fonts = encode(
            staged, source_path, cut_us, end_us, clip_frames, total_frames, video_index,
            audio_index, channels, args.encoder,
        )
        current_stat = source_path.stat()
        require(
            (source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns)
            == (current_stat.st_ino, current_stat.st_size, current_stat.st_mtime_ns),
            "The source file changed during production. Render again from a stable source.",
        )
        video = inspect_video(staged / "video.mp4", total_frames, channels)
        timeline = {
            "fps": FPS,
            "interview_frames": clip_frames,
            "total_frames": total_frames,
            "speech_cues": cues,
            "commentary_pages": pages,
        }
        manifest = {
            "schema_version": 1,
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
            },
            "subtitle_fonts": fonts,
            "ai_label": plan["ai_label"],
            "render_mode": args.mode,
            "visual_source": "source_video" if video_index is not None else "labeled_audio_only_card",
            "original_commentary": {
                "presentation": "labeled_text_cards" if pages else "omitted_interview_preview",
                "pages": len(pages),
            },
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
    print(f"{output} | {video['duration_seconds']:.3f}s | {args.encoder} | render {seconds:.3f}s")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path, help="P1/P2 task directory under workspace")
    parser.add_argument("--version", type=int, default=1, metavar="N", help="P2 analysis version")
    parser.add_argument("--output", type=Path, required=True, help="New render directory under output")
    parser.add_argument("--mode", choices=("full", "interview-preview"), default="full")
    parser.add_argument("--encoder", choices=ENCODERS, default="h264_nvenc")
    args = parser.parse_args()
    if args.version < 1:
        parser.error("--version must be positive")
    try:
        render(args)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"P3 error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
