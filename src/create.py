"""Render a versioned original storyboard and local narration to a review candidate."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from analysis_plan import json_text, read_json, require, required_text
from create_audio import RIGHTS, local_asset, prepare_audio
from create_visuals import asset_region, captions, scene_events
from media import run_media_tool
from render import ENCODERS, inspect_video, new_output, sha256
from render_brand import DEFAULT_BRAND, FPS, color_input, ffmpeg_still, load_brand
from render_commentary import SAMPLE_RATE
from render_subtitles import write_events

HUMAN_REVIEW = "pending"


def disclosure(plan):
    return "含 AI 生成内容" if plan["ai_generated"] else ""


def load_storyboard(path, brand):
    plan = read_json(path)
    require(type(plan["schema_version"]) is int and plan["schema_version"] == 1, "Expected storyboard schema version 1.")
    for label in ("topic_id", "title", "evidence"):
        required_text(plan[label], label)
    review_note = (path.parent / plan["evidence"]).resolve()
    require(review_note.is_file() and review_note.suffix.lower() == ".md", "Use a local Markdown evidence record.")
    require(plan["mode"] in {"release-candidate", "method-demo-draft"}, "Choose a supported production mode.")
    require(type(plan["ai_generated"]) is bool, "Declare the AI content status.")
    if plan["ai_generated"]:
        required_text(plan["ai_note"], "AI content note")
    voice_mode = plan["voice"]["mode"]
    require(voice_mode in {"windows-tts", "recording"}, "Provide Windows TTS or original recordings.")
    require(voice_mode != "windows-tts" or plan["ai_generated"], "Mark synthesized narration as AI content.")

    require(isinstance(plan["sources"], list), "Sources must be a list of source records.")
    for source in plan["sources"]:
        for key in ("title", "url", "use"):
            required_text(source[key], f"source {key}")
    require(isinstance(plan["materials"], list) and plan["materials"], "Record the origin and rights of the visual and audio materials.")
    for item in plan["materials"]:
        for key in ("description", "origin", "rights_note"):
            required_text(item[key], f"material {key}")
        require(item["rights"] in RIGHTS, f"Unconfirmed material rights: {item['description']}")
        require(type(item["ai_generated"]) is bool and (not item["ai_generated"] or plan["ai_generated"]),
                f"Check the AI declaration for {item['description']}.")

    scenes = plan["scenes"]
    require(isinstance(scenes, list) and scenes and scenes[0]["visual"]["kind"] == "title", "A storyboard starts with a title scene.")
    ids = [required_text(scene["id"], "scene id") for scene in scenes]
    require(len(set(ids)) == len(ids), "Scene IDs must be unique.")
    frame_ids = plan.get("review_scene_ids", ids[:3])
    require(isinstance(frame_ids, list) and len(frame_ids) == len(set(frame_ids))
            and 1 <= len(frame_ids) <= 3 and set(frame_ids) <= set(ids), "Choose up to three distinct review scene IDs.")
    assets = {}
    for scene in scenes:
        narration = scene["narration"]
        require(isinstance(narration, list) and narration, f"Scene {scene['id']} needs audible narration.")
        for cue in narration:
            spoken = required_text(cue["spoken"], "spoken phrase")
            caption = required_text(cue.get("subtitle", spoken), "subtitle")
            captions(brand, [dict(text=caption, start_ms=0, end_ms=1000)])
            if voice_mode == "recording":
                local_asset(path.parent, cue["recording"], "narration recording")
                require(not cue["recording"]["ai_generated"] or plan["ai_generated"], "Recording AI declaration is missing.")
        visual = scene["visual"]
        required_text(visual["title"], "visual title")
        if visual["kind"] in {"image", "video"}:
            assets[scene["id"]] = local_asset(path.parent, visual["asset"], "scene asset")
            require(not visual["asset"]["ai_generated"] or plan["ai_generated"], "Visual asset AI declaration is missing.")
        scene_events(brand, visual, 1000, disclosure(plan), draft=plan["mode"] == "method-demo-draft")
    cover = {**scenes[0]["visual"], "title": plan["title"]}
    scene_events(brand, cover, 1000, disclosure(plan), cover=True, draft=plan["mode"] == "method-demo-draft")
    return plan, review_note, assets, frame_ids


def draw_scene(directory, brand, visual, asset, frame_count, media_name, cover=False, encoder="h264_nvenc"):
    inputs = ["-f", "lavfi", "-i", color_input(brand)]
    graph = "[0:v]format=yuv420p"
    if asset:
        region = asset_region(brand)
        w, h = region["w"], region["h"]
        inputs += ["-i", str(asset)]
        graph += (
            f"[base];[1:v:0]setpts=PTS-STARTPTS,fps={FPS},"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1"
            f"[asset];[base][asset]overlay="
            f"x={region['x']}+({w}-w)/2:y={region['y']}+({h}-h)/2:eof_action=repeat:repeatlast=1"
        )
    graph += ",ass=scene.ass[v]"
    args = [*inputs, "-filter_complex_threads", "1", "-filter_complex", graph, "-map", "[v]"]
    if cover:
        ffmpeg_still([*args, "-frames:v", "1", "-threads:v", "1", "-update", "1", media_name], directory)
        return
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-nostdin", "-n", "-nostats", "-xerror",
        *args, "-frames:v", str(frame_count), "-an", "-c:v", encoder, *ENCODERS[encoder],
        "-bf", "0", "-pix_fmt", "yuv420p", "-video_track_timescale", str(FPS * 512), media_name,
    ], cwd=directory, capture_output=True, text=True, encoding="utf-8", errors="replace")
    require(result.returncode == 0, f"Scene render failed: {visual['title']}\n" + "\n".join(result.stderr.splitlines()[-18:]))
    require("failed to find any fallback" not in result.stderr.lower(), "A scene glyph is missing in libass.")
    require("fontselect:" in result.stderr, "libass did not select a scene font.")


def render(args):
    started = perf_counter()
    source = args.storyboard.resolve()
    brand, brand_sha = load_brand(args.brand.resolve())
    plan, review_note, assets, frame_ids = load_storyboard(source, brand)
    if args.check:
        print(f"VALID original storyboard: {plan['title']} | {', '.join(scene['id'] for scene in plan['scenes'])}")
        return
    require(args.output is not None, "Rendering needs a new --output under the project output directory.")
    output = new_output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".create-", dir=output.parent) as temp:
        scratch = Path(temp)
        staged = scratch / "ready"
        staged.mkdir()
        voice_start = perf_counter()
        timeline, voice = prepare_audio(plan, source.parent, scratch)
        voice_seconds = perf_counter() - voice_start
        encode_start = perf_counter()
        scene_files, all_captions = [], []
        label = disclosure(plan)
        draft = plan["mode"] == "method-demo-draft"
        for index, (scene, span) in enumerate(zip(plan["scenes"], timeline, strict=True)):
            duration = span["end_ms"] - span["start_ms"]
            frames = span["end_frame"] - span["start_frame"]
            events = scene_events(brand, scene["visual"], duration, label, draft=draft)
            events += captions(brand, span["cues"], span["start_ms"])
            all_captions.extend(captions(brand, span["cues"]))
            write_events(scratch / "scene.ass", brand, events)
            name = f"scene-{index:03}.mp4"
            draw_scene(scratch, brand, scene["visual"], assets.get(scene["id"]), frames, name, encoder=args.encoder)
            scene_files.append(f"file '{name}'\nduration {frames / FPS:.9f}\n")

        cuts = scratch / "scenes.ffconcat"
        cuts.write_text("ffconcat version 1.0\n" + "".join(scene_files), encoding="ascii")
        run_media_tool([
            "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-n", "-f", "concat", "-safe", "1",
            "-i", str(cuts), "-i", str(scratch / "narration.wav"), "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-map_metadata", "-1", "-movflags", "+faststart", str(staged / "video.mp4"),
        ])
        encode_seconds = perf_counter() - encode_start
        media_info = inspect_video(staged / "video.mp4", timeline[-1]["end_frame"], 1)
        write_events(staged / "subtitles.ass", brand, all_captions)

        intro = {**plan["scenes"][0]["visual"], "title": plan["title"]}
        write_events(scratch / "scene.ass", brand, scene_events(brand, intro, 1000, label, cover=True, draft=draft))
        draw_scene(scratch, brand, intro, None, 1, "cover.png", cover=True)
        shutil.move(scratch / "cover.png", staged / "cover.png")
        spans = {scene["id"]: span for scene, span in zip(plan["scenes"], timeline, strict=True)}
        frame_records = []
        for index, scene_id in enumerate(frame_ids, 1):
            cue = max(spans[scene_id]["cues"], key=lambda item: item["end_ms"] - item["start_ms"])
            at_ms = (cue["start_ms"] + cue["end_ms"]) // 2
            filename = f"frame-{index:02}.png"
            ffmpeg_still([
                "-ss", f"{at_ms / 1000:.3f}", "-i", str(staged / "video.mp4"), "-map", "0:v:0",
                "-frames:v", "1", "-threads:v", "1", "-update", "1", filename,
            ], staged)
            frame_records.append(dict(file=filename, scene_id=scene_id, at_ms=at_ms))

        evidence = "facts-and-test.md"
        shutil.copyfile(review_note, staged / evidence)
        snapshot = {
            **plan, "evidence": evidence, "source_storyboard": str(source), "human_review": HUMAN_REVIEW,
            "scenes": [{**scene, "timeline": span} for scene, span in zip(plan["scenes"], timeline, strict=True)],
        }
        (staged / "storyboard.json").write_text(json_text(snapshot), encoding="utf-8")
        manifest = dict(
            schema_version=1, title=plan["title"], topic_id=plan["topic_id"], mode=plan["mode"],
            human_review=HUMAN_REVIEW, ai_generated=plan["ai_generated"], ai_note=plan.get("ai_note"),
            storyboard=dict(path=str(source), sha256=sha256(source)),
            brand=dict(path=str(args.brand.resolve()), sha256=brand_sha, version=brand["version"]),
            source_assets={key: dict(path=str(value), sha256=sha256(value)) for key, value in assets.items()},
            voice=voice, encoder=args.encoder, media=media_info,
            timings=dict(narration_seconds=round(voice_seconds, 3), encoding_seconds=round(encode_seconds, 3),
                         production_seconds=round(perf_counter() - started, 3)),
            review_frames=frame_records,
            artifacts_sha256={file.name: sha256(file) for file in sorted(staged.iterdir())},
        )
        (staged / "render_manifest.json").write_text(json_text(manifest), encoding="utf-8")
        staged.rename(output)
    print(f"KAI ORIGINAL | {output} | {media_info['duration_seconds']:.3f}s | human review pending")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storyboard", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--brand", type=Path, default=DEFAULT_BRAND)
    parser.add_argument("--encoder", choices=ENCODERS, default="h264_nvenc")
    parser.add_argument("--check", action="store_true", help="Read and validate independent scene data without rendering.")
    try:
        render(parser.parse_args())
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
