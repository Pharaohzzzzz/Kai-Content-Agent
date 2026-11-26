"""Place complete P2 translations and commentary on the relative ASS timeline."""

import re
import unicodedata
from pathlib import Path

from analysis_plan import require
from render_brand import FPS, HEIGHT, WIDTH

TOKEN = re.compile(r"[!-~]+|[^\x20-\x7e]| ")
NO_LINE_START = "，。！？；：、）】》」』…"
NATURAL_STOP = "，。！？；：、,.!?;:"


def glyph_width(text: str) -> float:
    # Conservative em estimates for the local typefaces. Keep words intact.
    return sum(
        0 if unicodedata.combining(char) else
        0.34 if char == " " else
        1.03 if char in "MW@%#" else
        0.82 if char in "mw" or (char.isascii() and char.isupper()) else
        0.41 if char in "ilI.,'!:;|" else
        0.63 if char.isascii() else 1
        for char in text
    )


def wrap_lines(text: str, budget: float, balance: bool = True) -> list[str]:
    tokens = TOKEN.findall(" ".join(text.split()))
    lines = []
    while tokens:
        end = 0
        units = 0.0
        stop = None
        for index, token in enumerate(tokens):
            width = glyph_width(token)
            if index and token not in NO_LINE_START and units + width > budget:
                break
            units += width
            end = index + 1
            if units >= budget * 0.75 and token[-1] in NATURAL_STOP:
                stop = end
        if end < len(tokens) and stop is not None:
            end = stop
        lines.append("".join(tokens[:end]).rstrip())
        tokens = tokens[end:]
        if tokens and tokens[0] == " ":
            tokens.pop(0)
    lines = [line for line in lines if line]
    if balance and len(lines) > 1 and glyph_width(lines[-1]) < budget * 0.42:
        tighter = wrap_lines(text, budget * 0.88, balance=False)
        if len(tighter) == len(lines) and glyph_width(tighter[-1]) > glyph_width(lines[-1]):
            return tighter
    return lines


def fit_text(text: str, box: dict, style: dict, preferred_lines: int | None = None) -> dict:
    size = style["size"]
    width = box["w"] / style["width_factor"]
    lines = wrap_lines(text, width / size)
    if preferred_lines is not None and len(lines) > preferred_lines:
        for trial in range(size - 1, (size * 9 + 9) // 10 - 1, -1):
            wrapped = wrap_lines(text, width / trial)
            if len(wrapped) <= preferred_lines:
                lines, size = wrapped, trial
                break
    line_height = round(style["line_height"] * size / style["size"])
    return {"lines": lines, "size": size, "line_height": line_height, "width_factor": style["width_factor"]}


def shrink(block: dict, factor: float) -> dict:
    return {
        **block,
        "size": max(1, int(block["size"] * factor)),
        "line_height": max(1, int(block["line_height"] * factor)),
    }


def block_height(block: dict) -> int:
    return len(block["lines"]) * block["line_height"]


def speech_cues(plan: dict, translation: dict, transcript: dict, brand: dict, box: dict) -> list[dict]:
    origin = plan["source_start_ms"]
    chinese_source = translation["mode"] == "original"
    entries = translation["segments"]
    rows = [transcript["segments"][entry["segment_id"] - 1] for entry in entries]
    cues = []
    previous_end = origin
    gap = brand["subtitle_language_gap"]
    for index, (entry, row) in enumerate(zip(entries, rows, strict=True)):
        start = max(row["start_ms"], previous_end)
        end = row["end_ms"]
        if index + 1 < len(rows) and rows[index + 1]["start_ms"] < end:
            end = (end + rows[index + 1]["start_ms"]) // 2
        previous_end = end
        zh = entry["original_text"] if chinese_source else entry["translation_zh"]
        original = "" if chinese_source else entry["original_text"]
        primary = fit_text(zh, box, brand["styles"]["Chinese"], 2)
        secondary = fit_text(original, box, brand["styles"]["Original"], 2)
        available = box["h"] - (gap if original else 0)
        height = block_height(primary) + block_height(secondary)
        if height > available:
            primary = shrink(primary, available / height)
            secondary = shrink(secondary, available / height)
        start_ms, end_ms = start - origin, end - origin
        require((end_ms + 5) // 10 > (start_ms + 5) // 10,
                f"P1 segment {row['id']} has insufficient time for an ASS cue.")
        seconds = (end - start) / 1000
        zh_rate = round(glyph_width(zh) / seconds, 2)
        original_wpm = round(len(original.split()) * 60 / seconds, 1) if original else None
        flags = []
        if max(len(primary["lines"]), len(secondary["lines"])) > 2:
            flags.append("kept_complete_segment_over_two_lines")
        if zh_rate > 8 or (original_wpm is not None and original_wpm > 220):
            flags.append("fast_reading")
        if height > available:
            flags.append("reduced_size_to_fit_region")
        cues.append({
            "segment_id": row["id"], "start_ms": start_ms, "end_ms": end_ms,
            "timing_basis": "p1_segment", "overlap_adjusted": start != row["start_ms"] or end != row["end_ms"],
            "translation_alignment": "original" if chinese_source else "p2_segment",
            "pagination_basis": "one_complete_segment",
            "zh_lines": primary["lines"], "original_lines": secondary["lines"],
            "zh_style": primary, "original_style": secondary,
            "reading": {"zh_units_per_second": zh_rate, "original_words_per_minute": original_wpm,
                        "review_flags": flags},
        })
    return cues


def commentary_pages(text: str, start_frame: int, brand: dict) -> list[dict]:
    box, style = brand["commentary"]["text"], brand["styles"]["CardText"]
    max_lines = max(1, box["h"] // style["line_height"])
    lines = fit_text(text, box, style)["lines"]
    pages = []
    for first in range(0, len(lines), max_lines):
        page = lines[first:first + max_lines]
        frames = max(3 * FPS, round(glyph_width("".join(page)) * FPS / 5.5))
        pages.append({"start_frame": start_frame, "end_frame": start_frame + frames, "lines": page})
        start_frame += frames
    return pages


def timestamp(milliseconds: int) -> str:
    centiseconds = (milliseconds + 5) // 10
    hours, remaining = divmod(centiseconds, 360_000)
    minutes, remaining = divmod(remaining, 6_000)
    seconds, centiseconds = divmod(remaining, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def frame_ms(frame: int) -> int:
    return round(frame * 1000 / FPS)


def ass_color(color: str) -> str:
    return "&H00" + color[5:7] + color[3:5] + color[1:3]


def events_for_block(
    name: str, box: dict, start: int, end: int, block: dict,
) -> list[str]:
    events = []
    for index, line in enumerate(block["lines"]):
        # Keep long compound words/URLs on one line and constrain them to the box.
        measured = glyph_width(line) * block["size"] * block["width_factor"]
        scale = max(1, min(100, int(100 * box["w"] / max(1, measured))))
        escaped = line.replace("\\", "\\\u2060").replace("{", r"\{").replace("}", r"\}")
        y = box["y"] + index * block["line_height"]
        placement = rf"{{\an7\pos({box['x']},{y})\fs{block['size']}\fscx{scale}}}"
        events.append(f"Dialogue: 0,{timestamp(start)},{timestamp(end)},{name},,0,0,0,,{placement}{escaped}\n")
    return events


def text_events(
    brand: dict, name: str, box: dict, start: int, end: int, text: str, preferred_lines: int | None = None,
) -> list[str]:
    block = fit_text(text, box, brand["styles"][name], preferred_lines)
    height = block_height(block)
    if height > box["h"]:
        block = shrink(block, box["h"] / height)
    require(block_height(block) <= box["h"], f"{name} text cannot fit the configured region.")
    return events_for_block(name, box, start, end, block)


def write_events(path: Path, brand: dict, events: list[str], *, size=(WIDTH, HEIGHT)) -> None:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {size[0]}
PlayResY: {size[1]}
ScaledBorderAndShadow: yes
WrapStyle: 2
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    styles = []
    for name, style in brand["styles"].items():
        color = ass_color(brand["colors"][style["color"]])
        background = ass_color(brand["colors"]["background"])
        styles.append(
            f"Style: {name},{style['font']},{style['size']},{color},{color},{background},{background},"
            f"{-int(style['bold'])},0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        )
    event_header = "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    path.write_text(header + "".join(styles) + event_header + "".join(events), encoding="utf-8", newline="\n")


def write_ass(
    path: Path, plan: dict, cues: list[dict], pages: list[dict], brand: dict, layout: str,
    interview_frames: int, total_frames: int, has_video: bool, presentation: str, source_label: str,
) -> None:
    interview_end = frame_ms(interview_frames)
    brand_text = "Kai"
    labels = {
        "interview_preview": "访谈预览",
        "long_text_preview": "长文技术预览",
        "draft_pending_commentary": "待完善草稿",
    }
    if presentation in labels:
        brand_text += f"  /  {labels[presentation]}"
    if source_label:
        brand_text += f"  /  {source_label}"
    if plan["ai_label"] == "required":
        brand_text += "  /  含 AI 生成内容"
    events = text_events(brand, "Brand", brand["brand"], 0, frame_ms(total_frames), brand_text)
    events += text_events(brand, "Title", brand["title"], 0, interview_end, plan["title_zh"], 2)
    if not has_video:
        box = brand[layout]["video"]
        label = {**box, "x": brand["title"]["x"], "y": box["y"] + box["h"] // 2}
        events += text_events(brand, "CardLabel", label, 0, interview_end, "音频素材 · 无源画面")
    box = brand[layout]["subtitles"]
    for cue in cues:
        start, end = cue["start_ms"], cue["end_ms"]
        primary = cue["zh_style"]
        events += events_for_block("Chinese", box, start, end, primary)
        if cue["original_lines"]:
            lower = {**box, "y": box["y"] + block_height(primary) + brand["subtitle_language_gap"]}
            events += events_for_block("Original", lower, start, end, cue["original_style"])
    if pages:
        label = "原创结论" if presentation == "short_conclusion" else "原创解读"
        label_box, text_box = (brand["commentary"][key] for key in ("label", "text"))
        end = frame_ms(total_frames)
        events += text_events(brand, "CardLabel", label_box, interview_end, end, label)
        for page in pages:
            start, end = frame_ms(page["start_frame"]), frame_ms(page["end_frame"])
            if "lines" in page:
                style = brand["styles"]["CardText"]
                block = {"lines": page["lines"], "size": style["size"], "line_height": style["line_height"],
                         "width_factor": style["width_factor"]}
                events += events_for_block("CardText", text_box, start, end, block)
            else:
                events += text_events(brand, "CardText", text_box, start, end, page["text"])
    write_events(path, brand, events)


def write_cover_ass(path: Path, plan: dict, brand: dict, english: str, source_label: str, has_video: bool) -> None:
    cover = brand["cover"]
    label = "  /  ".join(part for part in ("Kai", source_label, "含 AI 生成内容" if plan["ai_label"] == "required" else "") if part)
    events = text_events(brand, "Brand", cover["brand"], 0, 1000, label)
    events += text_events(brand, "CoverTitle", cover["title"], 0, 1000, plan["title_zh"], 2)
    if english:
        events += text_events(brand, "CoverEnglish", cover["english"], 0, 1000, english, 2)
    if not has_video:
        box = {**cover["video"], "x": cover["title"]["x"], "y": cover["video"]["y"] + cover["video"]["h"] // 2}
        events += text_events(brand, "CardLabel", box, 0, 1000, "音频素材 · 无源画面")
    write_events(path, brand, events)
