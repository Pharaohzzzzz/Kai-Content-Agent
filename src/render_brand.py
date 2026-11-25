"""Load the Kai visual system and compose source pictures on its canvas."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from analysis_plan import PROJECT, require
from media import run_media_tool

WIDTH, HEIGHT, FPS = 1080, 1920, 30
COVER_CROP_HEIGHT = WIDTH * 4 // 3
COVER_CROP_Y = (HEIGHT - COVER_CROP_HEIGHT) // 2
DEFAULT_BRAND = PROJECT / "config/kai-brand.json"
STYLES = ("Brand", "Title", "Chinese", "Original", "CardLabel", "CardText", "CoverTitle", "CoverEnglish")


def load_brand(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    brand = json.loads(raw.decode("utf-8-sig"))
    require(all(isinstance(brand[key], str) and brand[key].strip() for key in ("version", "template")),
            "Brand version and template must be named.")
    for key in ("background", "accent", "primary", "secondary"):
        require(bool(re.fullmatch(r"#[0-9a-fA-F]{6}", brand["colors"][key])), f"Invalid brand color: {key}")
    for name in STYLES:
        style = brand["styles"][name]
        require(bool(re.fullmatch(r"[^,{}\r\n]+", style["font"])) and bool(style["font"].strip())
                and type(style["bold"]) is bool and style["color"] in brand["colors"]
                and type(style["size"]) is int and 0 < style["size"] <= 200
                and type(style["width_factor"]) in (int, float) and 0.5 <= style["width_factor"] <= 2
                and type(style["line_height"]) is int and style["size"] <= style["line_height"] <= 250,
                f"Invalid brand style: {name}")

    def check_box(box: dict, label: str, bounds: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = bounds
        x, y, w, h = (box[key] for key in ("x", "y", "w", "h"))
        require(all(type(value) is int for value in (x, y, w, h))
                and w > 0 and h > 0 and left <= x and top <= y and x + w <= right and y + h <= bottom,
                f"Brand region is outside its bounds: {label}")

    reserve = brand["platform_ui_reserve"]
    require(all(type(reserve[key]) is int and reserve[key] >= 0 for key in ("left", "top", "right", "bottom")),
            "Invalid platform UI reserve.")
    safe = reserve["left"], reserve["top"], WIDTH - reserve["right"], HEIGHT - reserve["bottom"]
    whole = 0, 0, WIDTH, HEIGHT
    center = 0, COVER_CROP_Y, WIDTH, COVER_CROP_Y + COVER_CROP_HEIGHT
    for key in ("brand", "title"):
        check_box(brand[key], key, safe)
    for layout in ("landscape", "portrait"):
        picture, subs = (brand[layout][key] for key in ("video", "subtitles"))
        check_box(picture, f"{layout}.video", whole)
        check_box(subs, f"{layout}.subtitles", safe)
        require(box_bottom(brand["title"]) <= picture["y"] and box_bottom(picture) <= subs["y"],
                f"Title, video and subtitle regions overlap: {layout}")
        require(all(picture[key] % 2 == 0 for key in ("x", "y", "w", "h")),
                f"Video region coordinates and dimensions must be even: {layout}")
    for key in ("label", "text"):
        check_box(brand["commentary"][key], f"commentary.{key}", safe)
    for key in ("brand", "title", "english", "video"):
        check_box(brand["cover"][key], f"cover.{key}", center)
    rule = brand["rule"]
    require(all(type(rule[key]) is int and 0 <= rule[key] <= 240 for key in ("length", "thickness", "gap"))
            and rule["length"] > 0 and rule["thickness"] > 0,
            "Invalid brand rule.")
    require(type(brand["subtitle_language_gap"]) is int and 0 <= brand["subtitle_language_gap"] <= 60,
            "Invalid subtitle language gap.")
    for boxes in (
        (brand["brand"], brand["title"]),
        tuple(brand["commentary"][key] for key in ("label", "text")),
        tuple(brand["cover"][key] for key in ("brand", "title", "english", "video")),
    ):
        require(all(box_bottom(first) <= second["y"] for first, second in zip(boxes, boxes[1:])),
                "Brand layout contains vertically overlapping regions.")
    for boxes in (brand, brand["cover"]):
        require(box_bottom(boxes["brand"]) + rule["gap"] + rule["thickness"] <= boxes["title"]["y"]
                and rule["length"] <= boxes["brand"]["w"], "Brand rule exceeds available space.")
    return brand, hashlib.sha256(raw).hexdigest()


def box_bottom(box: dict) -> int:
    return box["y"] + box["h"]


def source_layout(source: Path, video_index: int | None) -> str:
    if video_index is None:
        return "landscape"
    stream = json.loads(run_media_tool([
        "ffprobe", "-v", "error", "-select_streams", str(video_index),
        "-show_entries", "stream=width,height,sample_aspect_ratio:stream_side_data=rotation",
        "-of", "json", str(source),
    ]))["streams"][0]
    width, height = stream["width"], stream["height"]
    sar = stream.get("sample_aspect_ratio", "1:1")
    if ":" in sar:
        numerator, denominator = map(int, sar.split(":"))
        if numerator > 0 and denominator > 0:
            width, height = width * numerator, height * denominator
    rotation = next((data["rotation"] for data in stream.get("side_data_list", []) if "rotation" in data), 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return "portrait" if height > width else "landscape"


def color_input(brand: dict) -> str:
    return f"color=c=0x{brand['colors']['background'][1:]}:s={WIDTH}x{HEIGHT}:r={FPS}"


def compose(
    canvas: str, picture: str | None, region: dict, crop: str, brand: dict,
    ass: str, *, cover: bool = False, at_ms: int = 0, clip_frames: int | None = None,
) -> str:
    box = brand["cover"]["brand"] if cover else brand["brand"]
    rule = brand["rule"]
    canvas += (
        f",drawbox=x={box['x']}:y={box_bottom(box) + rule['gap']}:"
        f"w={rule['length']}:h={rule['thickness']}:color=0x{brand['colors']['accent'][1:]}:t=fill"
    )
    if picture is not None:
        w, h = region["w"], region["h"]
        aspect = "increase" if crop == "center-crop" else "decrease"
        picture += f",scale={w}:{h}:force_original_aspect_ratio={aspect}:force_divisible_by=2:reset_sar=1"
        if crop == "center-crop":
            picture += f",crop={w}:{h}"
        enable = f":enable='lt(n,{clip_frames})'" if clip_frames is not None else ""
        canvas = (
            f"{picture}[picture];{canvas}[canvas];[canvas][picture]"
            f"overlay=x={region['x']}+({w}-w)/2:y={region['y']}+({h}-h)/2:"
            f"eof_action=pass:repeatlast=0{enable}"
        )
    if at_ms:
        canvas += f",settb=AVTB,setpts=PTS+{at_ms * 1000}"
    return f"{canvas},ass={ass}[v]"


def ffmpeg_still(command: list[str], directory: Path) -> None:
    result = subprocess.run(
        ["ffmpeg", "-v", "warning", "-nostdin", "-n", "-xerror", *command],
        cwd=directory, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    require(result.returncode == 0 and "failed to find any fallback" not in result.stderr.lower(),
            f"FFmpeg still generation failed: {result.stderr.strip()}")
    require((directory / command[-1]).is_file(), f"No frame produced: {command[-1]}")


def extract_picture(source: Path, index: int, source_ms: int, directory: Path, name: str) -> Path:
    # Input seeking is relative to the source playback origin, unlike the copyts render graph.
    ffmpeg_still([
        "-ss", f"{source_ms / 1000:.3f}", "-i", str(source), "-map", f"0:{index}",
        "-frames:v", "1", "-threads:v", "1", "-update", "1", name,
    ], directory)
    return directory / name


def render_still(
    output: str, directory: Path, brand: dict, region: dict,
    picture: Path | None, crop: str, ass: str, *, at_ms: int = 0, cover: bool = False,
) -> None:
    command = ["-f", "lavfi", "-i", color_input(brand)]
    if picture is not None:
        command += ["-i", str(picture)]
    graph = compose(
        "[0:v]format=yuv420p", "[1:v]setpts=PTS-STARTPTS" if picture is not None else None,
        region, crop, brand, ass, cover=cover, at_ms=at_ms,
    )
    ffmpeg_still([
        *command, "-filter_complex", graph, "-map", "[v]", "-frames:v", "1",
        "-threads:v", "1", "-update", "1", output,
    ], directory)
