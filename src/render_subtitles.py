"""Lay out P1 speech and P2 copy on a relative ASS subtitle timeline."""

import re
import unicodedata
from pathlib import Path

from analysis_plan import require

FONT = "Microsoft YaHei"
WIDTH, HEIGHT, FPS = 1080, 1920, 30
TEXT_X = 505
SPEECH_LINES_PER_LANGUAGE = 3
STYLES = {
    "Brand": (32, "&H00B7E8A1", 1),
    "Title": (52, "&H00FFFFFF", 1),
    "Chinese": (49, "&H00FFFFFF", 0),
    "Original": (32, "&H00E4DFDA", 0),
    "CardLabel": (39, "&H00B7E8A1", 1),
    "CardText": (49, "&H00FFFFFF", 0),
}
TOKEN = re.compile(r"[!-~]+|[^\x20-\x7e]| ")
NO_LINE_START = "，。！？；：、）】》」』…"


def glyph_width(text: str) -> float:
    # Conservative em budget for the template font. Non-ASCII scripts get full ems.
    return sum(
        0 if unicodedata.combining(char) else
        0.36 if char == " " else
        1.05 if char in "MW@%#" else
        0.86 if char in "mw" or (char.isascii() and char.isupper()) else
        0.45 if char in "ilI.,'!:;|" else
        0.70 if char.isascii() else 1
        for char in text
    )


def wrap_lines(text: str, budget: float, balance: bool = True) -> list[str]:
    lines = []
    line = ""
    for token in TOKEN.findall(" ".join(text.split())):
        if glyph_width(token) > budget:
            # Keep text even when a single unbroken token is wider than the region.
            tokens = token
        else:
            tokens = (token,)
        for part in tokens:
            if not line and part == " ":
                continue
            if line and part not in NO_LINE_START and glyph_width(line + part) > budget:
                lines.append(line.rstrip())
                line = ""
            if part != " " or line:
                line += part
    if line.strip():
        lines.append(line.rstrip())
    if balance and len(lines) > 1 and glyph_width(lines[-1]) < budget * 0.45:
        narrower = wrap_lines(text, budget * 0.87, balance=False)
        if len(narrower) == len(lines) and glyph_width(narrower[-1]) > glyph_width(lines[-1]):
            return narrower
    return lines


def distribute(lines: list[str], count: int) -> list[list[str]]:
    # Keep the whole translation or original visible across the other language's pages.
    if len(lines) <= SPEECH_LINES_PER_LANGUAGE:
        return [lines] * count
    pages = []
    for index in range(count):
        first = index * len(lines) // count
        last = (index + 1) * len(lines) // count
        pages.append(lines[first:max(first + 1, last)])
    return pages


def speech_cues(plan: dict, translation: dict, transcript: dict) -> list[dict]:
    origin = plan["source_start_ms"]
    chinese_source = translation["mode"] == "original"
    entries = translation["segments"]
    rows = [transcript["segments"][entry["segment_id"] - 1] for entry in entries]
    cues = []
    previous_end = origin
    for index, (entry, row) in enumerate(zip(entries, rows, strict=True)):
        start = max(row["start_ms"], previous_end)
        end = row["end_ms"]
        if index + 1 < len(rows) and rows[index + 1]["start_ms"] < end:
            end = (end + rows[index + 1]["start_ms"]) // 2
        previous_end = end
        chinese = wrap_lines(entry["original_text"] if chinese_source else entry["translation_zh"], 17)
        original = [] if chinese_source else wrap_lines(entry["original_text"], 26)
        count = (max(len(chinese), len(original)) + SPEECH_LINES_PER_LANGUAGE - 1) // SPEECH_LINES_PER_LANGUAGE
        chinese_pages = distribute(chinese, count)
        original_pages = distribute(original, count)
        basis = "p1_segment"
        alignment = "original" if chinese_source else "p2_segment"
        if not chinese_source and len(chinese) > SPEECH_LINES_PER_LANGUAGE:
            alignment = "estimated_within_segment"
        boundaries = [start, end]
        if count > 1:
            # P1 word times are usable only when page breaks preserve whole original words.
            pages = chinese_pages if chinese_source else original_pages
            words = row.get("words", [])
            original_text = "".join(entry["original_text"].split())
            joined = "".join("".join(word["text"].split()) for word in words)
            prefix_to_time = {}
            length = 0
            for word in words:
                prefix_to_time[length] = word["start_ms"]
                length += len("".join(word["text"].split()))
            page_lengths = [len("".join("".join(page).split())) for page in pages]
            word_bounds = [
                prefix_to_time.get(sum(page_lengths[:number]))
                for number in range(1, count)
            ]
            if joined == original_text and sum(page_lengths) == len(joined) and all(
                time is not None and start < time < end for time in word_bounds
            ) and word_bounds == sorted(set(word_bounds)):
                boundaries = [start, *word_bounds, end]
                basis = "p1_word_boundaries"
            else:
                boundaries = [start + (end - start) * number // count for number in range(count + 1)]
                basis = "estimated_equal_subdivision"
        for page in range(count):
            start_ms, end_ms = boundaries[page] - origin, boundaries[page + 1] - origin
            require(
                (end_ms + 5) // 10 > (start_ms + 5) // 10,
                f"P1 segment {row['id']} has insufficient time for a readable ASS cue.",
            )
            cues.append({
                "segment_id": row["id"], "start_ms": start_ms, "end_ms": end_ms,
                "timing_basis": basis,
                "overlap_adjusted": start != row["start_ms"] or end != row["end_ms"],
                "translation_alignment": alignment,
                "zh_lines": chinese_pages[page], "original_lines": original_pages[page],
            })
    return cues


def commentary_pages(text: str, start_frame: int) -> list[dict]:
    pages = []
    pending = ""
    for sentence in re.findall(r"[^。！？!?]+[。！？!?]*|[。！？!?]+", " ".join(text.split())):
        candidate = wrap_lines(pending + sentence, 17)
        if len(candidate) <= 5:
            pending += sentence
            continue
        if pending:
            pages.append(wrap_lines(pending, 17))
            pending = ""
        lines = wrap_lines(sentence, 17)
        if len(lines) <= 5:
            pending = sentence
        else:
            pages.extend(lines[index:index + 5] for index in range(0, len(lines), 5))
    if pending:
        pages.append(wrap_lines(pending, 17))
    result = []
    for lines in pages:
        units = glyph_width("".join(lines))
        frames = max(3 * FPS, round(units * FPS / 5.5))
        result.append({"start_frame": start_frame, "end_frame": start_frame + frames, "lines": lines})
        start_frame += frames
    return result


def timestamp(milliseconds: int) -> str:
    centiseconds = (milliseconds + 5) // 10
    hours, remaining = divmod(centiseconds, 360_000)
    minutes, remaining = divmod(remaining, 6_000)
    seconds, centiseconds = divmod(remaining, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def frame_ms(frame: int) -> int:
    return round(frame * 1000 / FPS)


def ass_event(style: str, y: int, start: int, end: int, lines: list[str]) -> str:
    # Escape libass control characters before inserting intentional line breaks.
    text = r"\N".join(
        line.replace("\\", "\\\u2060").replace("{", r"\{").replace("}", r"\}") for line in lines
    )
    placement = rf"{{\an8\pos({TEXT_X},{y})}}"
    return f"Dialogue: 0,{timestamp(start)},{timestamp(end)},{style},,0,0,0,,{placement}{text}\n"


def write_ass(
    path: Path, plan: dict, cues: list[dict], pages: list[dict],
    interview_frames: int, total_frames: int, has_video: bool, preview: bool,
) -> None:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
ScaledBorderAndShadow: yes
WrapStyle: 2
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    styles = "".join(
        f"Style: {name},{FONT},{size},{color},{color},&H00101620,&H00101620,"
        f"{-bold},0,0,0,100,100,0,0,1,2,0,8,0,0,0,1\n"
        for name, (size, color, bold) in STYLES.items()
    )
    interview_end = frame_ms(interview_frames)
    brand = "Kai"
    if preview:
        brand += "  /  访谈预览"
    if plan["ai_label"] == "required":
        brand += "  /  含 AI 生成内容"
    events = [ass_event("Brand", 175, 0, frame_ms(total_frames), [brand])]
    title = wrap_lines(plan["title_zh"], 16)
    if len(title) > 3:
        title = [*title[:2], title[2].rstrip() + "…"]
    events.append(ass_event("Title", 232, 0, interview_end, title))
    if not has_video:
        events.append(ass_event("CardLabel", 775, 0, interview_end, ["音频素材 · 无源画面"]))
    for cue in cues:
        events.append(ass_event("Chinese", 1235, cue["start_ms"], cue["end_ms"], cue["zh_lines"]))
        if cue["original_lines"]:
            events.append(ass_event("Original", 1418, cue["start_ms"], cue["end_ms"], cue["original_lines"]))
    for number, page in enumerate(pages, start=1):
        start, end = frame_ms(page["start_frame"]), frame_ms(page["end_frame"])
        events.append(ass_event("CardLabel", 490, start, end, [f"原创解读  {number}/{len(pages)}"]))
        events.append(ass_event("CardText", 620, start, end, page["lines"]))
    event_header = "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    path.write_text(header + styles + event_header + "".join(events), encoding="utf-8", newline="\n")
