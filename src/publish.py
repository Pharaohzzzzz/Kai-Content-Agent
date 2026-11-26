"""Package an existing Kai A/B deliverable for local publication review."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from analysis_plan import PROJECT, json_text, read_json, require, required_text
from create_audio import RIGHTS
from create_visuals import scene_events
from media import probe, run_media_tool
from render import new_output, sha256
from render_brand import DEFAULT_BRAND, WIDTH, ffmpeg_still, load_brand
from render_subtitles import write_events

CONFIG = PROJECT / "config" / "platforms.json"
OUTPUT = PROJECT / "output"
PENDING = "awaiting_human_review"


def file_record(path, base, expected=None):
    path = path.resolve()
    relative = path.relative_to(base)
    digest = sha256(path)
    require(expected is None or digest == expected, f"Source hash mismatch: {path}")
    return {"path": relative.as_posix(), "sha256": digest}


def inspect_source(source):
    require(OUTPUT in source.parents, "Choose an existing deliverable under output/.")
    manifest_path = source / "render_manifest.json"
    raw = manifest_path.read_bytes()
    render = json.loads(raw.decode("utf-8-sig"))
    origin = {"path": manifest_path.relative_to(PROJECT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
    blocks = []
    upstream_risks = render.get("content_risks", [])
    checks = list(render.get("verification_needed", []))

    if render.get("schema_version") == 1 and "storyboard" in render:
        lane, media = "B", render["media"]
        plan_path = source / "storyboard.json"
        hashes = render["artifacts_sha256"]
        plan_record = file_record(plan_path, PROJECT, hashes["storyboard.json"])
        plan = read_json(plan_path)
        identity = {key: required_text(render[key], key) for key in ("topic_id", "title")}
        require(all(plan[key] == value for key, value in identity.items()), "Storyboard identity differs from render.")
        evidence = (source / plan["evidence"]).resolve()
        require(evidence.parent == source and evidence.name in hashes, "Use the rendered evidence Markdown.")
        evidence_record = file_record(evidence, PROJECT, hashes[evidence.name])
        rights = plan.get("materials", [])
        if not rights or any(item.get("rights") not in RIGHTS for item in rights):
            blocks.append("成品素材权利未全部确认。")
        sources = plan["sources"]
        ai = render.get("ai_generated")
        if (type(ai) is not bool or plan.get("ai_generated") is not ai
                or (ai is False and (render.get("voice", {}).get("mode") == "windows-tts"
                                     or any(item.get("ai_generated") for item in rights)))):
            ai = None
        ai_note = render.get("ai_note")
        mode = render.get("mode")
        if mode != "release-candidate" or plan.get("mode") != mode:
            blocks.append("B 线成品为草稿，或渲染模式与场景计划不一致。")
        human = render.get("human_review")
    elif render.get("schema_version") in (1, 2) and "p2" in render:
        lane, media = "A", render["mp4"]
        hashes = render.get("artifacts_sha256", {})
        clip_id = required_text(render["recommended_clip_id"], "recommended_clip_id")
        edit = render["p2"]
        plan_path = Path(edit["plan_path"]).resolve()
        require(PROJECT in plan_path.parents, "Use the project-local P2 edit plan.")
        plan_record = file_record(plan_path, PROJECT, edit["sha256"]["edit_plan.json"])
        plan = read_json(plan_path)
        require(plan["recommended_clip_id"] == clip_id, "P2 clip ID differs from render.")
        identity = {"clip_id": clip_id, "topic_id": plan.get("topic_id"),
                    "title": required_text(plan["title_zh"], "title_zh")}
        evidence_record = None
        original = render["source"]
        rights = [{"description": "访谈源素材", "rights": original.get("rights_status"),
                   "origin": original.get("provenance"), "sha256": original.get("sha256")}]
        sources = [{"title": "访谈源素材", "use": required_text(original["provenance"], "source provenance")}]
        if original.get("rights_status") not in {"owned", "authorized"} or plan.get("rights_status") != original.get("rights_status"):
            blocks.append("访谈素材权利未确认，或与 P2 计划不一致。")
        ai_label = render.get("ai_label")
        ai = {"required": True, "not-required": False}.get(ai_label) if plan.get("ai_label") == ai_label else None
        ai_note = "沿用 A 线渲染清单中的 AI 标识要求。"
        mode = render.get("render_mode")
        if (mode != "full" or render.get("preview_only", False)
                or render["original_commentary"].get("status") != "review_candidate"):
            blocks.append("A 线成品为技术预览，或原创解读尚未达到候选状态。")
        human = render.get("review_status", {}).get("human")
    else:
        raise ValueError("Expected a Kai A- or B-line render_manifest.json.")

    if ai is None:
        blocks.append("AI 内容标识需求待核实。")
    if human not in ("pending", "approved"):
        blocks.append("上游人工审核状态需要确认。")
    return {
        "lane": lane, **identity, "render": origin, "render_mode": mode,
        "render_review_status": human, "plan": plan_record, "evidence": evidence_record,
        "media": media, "content_sources": sources, "material_rights": rights,
        "ai_disclosure": {"required": ai, "note": ai_note},
        "upstream_content_risks": upstream_risks, "upstream_review_items": checks,
        "blockers": blocks, "expected_hashes": hashes,
    }


def write_copy(path, platform, copy, source, cards_ai_required):
    title = required_text(copy["title"], f"{platform} title")
    body = required_text(copy["body"], f"{platform} body")
    note = required_text(copy["source_note"], f"{platform} source note")
    tags = copy["tags"]
    require(isinstance(tags, list) and tags, "Provide related topic tags.")
    tags = [required_text(tag, "tag").removeprefix("#") for tag in tags]
    require(all(tag and not any(ch.isspace() or ch == "#" for ch in tag) for tag in tags),
            "Tags must be single topic names.")
    sources = []
    for item in source["content_sources"]:
        label = item["title"]
        if item.get("url"):
            label = f"[{label}]({item['url']})"
        sources.append(f"- {label}。{item['use']}")
    ai = source["ai_disclosure"]
    reminder = ("视频发布时保留原有 AI 标识，并在实际发布界面主动声明含 AI 生成内容。"
                if ai["required"] else "成品清单记录视频无需 AI 标识。终审时核对最终素材和声明。")
    cards_reminder = ("发布图文时保留卡片上的 AI 标识，并在实际发布界面主动声明。"
                      if cards_ai_required else None)
    disclosure = "\n\n".join(filter(None, (ai["note"], reminder, cards_reminder)))
    text = "\n\n".join((
        "# 推荐标题", title, "## 正文", body, "## 相关标签",
        " ".join(f"#{tag}" for tag in tags), "## 来源说明", note,
        "\n".join(sources), "## AI 内容标识提醒（发布操作）", disclosure,
    ))
    path.write_text(text.strip() + "\n", encoding="utf-8")


def image_info(path):
    data = json.loads(run_media_tool([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path),
    ]))
    stream, = data["streams"]
    require(stream["codec_name"] == "png", f"Expected PNG: {path}")
    return [stream["width"], stream["height"]]


def cover_file(source, dest, size, original_size):
    if original_size == size:
        shutil.copyfile(source, dest / "cover.png")
        return "copy"
    width, height = size
    ffmpeg_still([
        "-i", str(source), "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
        "-frames:v", "1", "-threads:v", "1", "-update", "1", "cover.png",
    ], dest)
    require(image_info(dest / "cover.png") == size, "Cover output dimensions differ from the platform preset.")
    return "center_crop"


def card_files(entries, dest, parent, config, ai):
    width, height = config["size"]
    require(width == WIDTH, "The Kai card template uses a 1080 px wide canvas.")
    minimum, maximum = config["count"]
    require(isinstance(entries, list) and minimum <= len(entries) <= maximum,
            f"Supply {minimum}–{maximum} cards, or omit them with a reason.")
    brand, _ = load_brand(DEFAULT_BRAND)
    brand["platform_ui_reserve"] = config["safe_area"]
    cards = dest / "cards"
    cards.mkdir()
    records = []
    for index, visual in enumerate(entries, 1):
        require(visual["kind"] in {"title", "table", "chart", "conclusion"}, "Use a Kai title, table, chart or conclusion card.")
        required_text(visual["title"], "card title")
        required_text(visual["footer"], "card source or provenance")
        events = scene_events(brand, visual, 1000, "含 AI 生成内容" if ai else "", canvas_height=height)
        ass = cards / "card.ass"
        write_events(ass, brand, events, size=(width, height))
        file = cards / f"{index:02d}.png"
        ffmpeg_still([
            "-f", "lavfi", "-i", f"color=c=0x{brand['colors']['background'][1:]}:s={width}x{height}",
            "-vf", "ass=card.ass", "-frames:v", "1", "-threads:v", "1", "-update", "1", file.name,
        ], cards)
        ass.unlink()
        require(image_info(file) == [width, height], f"Unexpected card dimensions: {file}")
        records.append({**file_record(file, parent), "title": visual["title"], "source_note": visual["footer"]})
    return records


def pack(source_dir, source, editorial_path, output):
    start = perf_counter()
    config = read_json(CONFIG)
    editorial = read_json(editorial_path)
    editorial_record = file_record(editorial_path, PROJECT)
    require(editorial["source_manifest_sha256"] == source["render"]["sha256"],
            "Editorial brief must match this render manifest SHA-256.")
    fact = editorial["fact_review"]
    require(fact["status"] in {"checked", "pending", "major_risk"}, "Choose a supported fact review status.")
    required_text(fact["note"], "fact review note")
    blockers = source["blockers"]
    if fact["status"] != "checked":
        blockers.append("事实复核未通过发布准备门槛。")
    hashes = source.pop("expected_hashes")
    manifest = {
        "schema_version": 1, "status": "blocked" if blockers else PENDING,
        "source": source, "editorial": editorial_record, "fact_review": fact,
        "platform_config": file_record(CONFIG, PROJECT),
        "platform_parameter_basis": config["parameter_basis"],
        "disclosure_basis": config["disclosure_basis"], "platforms": {},
        "human_review": {"status": "pending", "items": [
            "终审标题、正文、图文、来源归因、事实边界及素材授权。",
            "实机检查封面裁切、字幕遮挡、画面可读性及人工听感。",
            "核对 AI 声明和发布页面实际要求，由用户手动发布。",
            *source["upstream_review_items"],
        ]},
    }
    with TemporaryDirectory(prefix=".kai-publish-", dir=output.parent) as temporary:
        stage = Path(temporary) / "package"
        stage.mkdir()
        if not blockers:
            video = source_dir / "video.mp4"
            cover = source_dir / "cover.png"
            for asset in (video, cover, source_dir / "subtitles.ass"):
                source[asset.name] = file_record(asset, PROJECT, hashes.get(asset.name))
            media, _, _ = probe(video, None)
            v, = [s for s in media["streams"] if s["codec_type"] == "video" and not s["disposition"]["attached_pic"]]
            a, = [s for s in media["streams"] if s["codec_type"] == "audio"]
            require(media["format"]["duration_ms"] > 0, "The video duration must be positive.")
            require(video.stat().st_size == source["media"]["size_bytes"], "Video size differs from render manifest.")
            require([v["width"], v["height"]] == [source["media"]["video"][key] for key in ("width", "height")],
                    "Video dimensions differ from render manifest.")
            run_media_tool(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(video),
                            "-map", f"0:{v['index']}", "-map", f"0:{a['index']}", "-f", "null", "-"])
            manifest["video_validation"] = {"mode": "full_decode_once", "media": media,
                                            "transfer": "byte_for_byte_copy", "reencoded": False}
            original_cover_size = image_info(cover)
            for name, preset in config["platforms"].items():
                vp = preset["video"]
                rw, rh = vp["ratio"]
                require(vp["container"] in media["format"]["format_name"].split(",")
                        and v["codec_name"] == vp["video_codec"] and a["codec_name"] == vp["audio_codec"]
                        and v["width"] * rh == v["height"] * rw,
                        f"MP4 does not fit the project preset for {preset['name']}.")
                dest = stage / name
                dest.mkdir()
                shutil.copyfile(video, dest / "video.mp4")
                cover_mode = cover_file(cover, dest, preset["cover"]["size"], original_cover_size)
                entries = editorial.get("cards") if "cards" in preset else None
                cards_ai_required = None
                if entries:
                    declared = editorial.get("cards_ai_generated")
                    require(type(declared) is bool, "Declare whether publication cards contain AI-generated material.")
                    cards_ai_required = source["ai_disclosure"]["required"] or declared
                write_copy(dest / "publish-copy.md", name, editorial["platforms"][name], source, cards_ai_required)
                files = {"video": file_record(dest / "video.mp4", stage, source["video.mp4"]["sha256"]),
                         "cover": file_record(dest / "cover.png", stage),
                         "copy": file_record(dest / "publish-copy.md", stage)}
                packaged = {"files": files, "cover_mode": cover_mode, "project_preset": preset}
                if "cards" in preset:
                    packaged["cards"] = ({"status": "packaged", "ai_disclosure_required": cards_ai_required, "files": card_files(
                        entries, dest, stage, preset["cards"], cards_ai_required,
                    )} if entries else {"status": "omitted", "reason": required_text(
                        editorial.get("cards_omitted_reason"), "cards_omitted_reason",
                    )})
                manifest["platforms"][name] = packaged
        manifest["packaging_seconds"] = round(perf_counter() - start, 3)
        (stage / "publish_manifest.json").write_text(json_text(manifest, indent=2) + "\n", encoding="utf-8")
        require(not output.exists(), f"Output already exists: {output}")
        stage.rename(output)
    print(f"{manifest['status']} | {output} | {manifest['packaging_seconds']:.3f}s")
    return 2 if blockers else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing output directory with render_manifest.json")
    parser.add_argument("--inspect", action="store_true", help="Read the existing manifest and plan without producing files")
    parser.add_argument("--editorial", type=Path, help="Local editorial brief JSON")
    parser.add_argument("--output", type=Path, help="New direct child of project output/")
    args = parser.parse_args()
    try:
        source = args.source.resolve()
        if args.inspect:
            require(args.editorial is None and args.output is None, "--inspect uses only the source directory.")
            print(json_text(inspect_source(source), indent=2))
            return 0
        require(args.editorial is not None and args.output is not None, "Provide --editorial and --output.")
        output = new_output(args.output)
        require(output.parent == OUTPUT, "Choose a new direct child of output/.")
        editorial = args.editorial.resolve()
        require(PROJECT in editorial.parents, "Keep the editorial brief inside the project.")
        return pack(source, inspect_source(source), editorial, output)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        print(f"Publish preparation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
