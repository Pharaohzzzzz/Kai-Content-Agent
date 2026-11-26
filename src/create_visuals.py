"""Compose data-driven original scenes on the Kai portrait canvas."""

from analysis_plan import require
from render_brand import HEIGHT, WIDTH
from render_subtitles import ass_color, block_height, events_for_block, fit_text, timestamp


def area(x, y, w, h):
    return dict(x=x, y=y, w=w, h=h)


def captions(brand, cues, offset_ms=0):
    safe = brand["platform_ui_reserve"]
    x = safe["left"] + 16
    right = WIDTH - safe["right"] - 16
    bottom = HEIGHT - safe["bottom"] - 32
    box = area(x, bottom - 130, right - x, 130)
    events = []
    for cue in cues:
        style = {**brand["styles"]["Chinese"], "width_factor": 1.0}
        block = fit_text(cue["text"], box, style, preferred_lines=2)
        require(len(block["lines"]) <= 2 and block_height(block) <= box["h"],
                f"Split this subtitle into shorter spoken cues: {cue['text']}")
        for line in events_for_block("Chinese", box, cue["start_ms"] - offset_ms,
                                     cue["end_ms"] - offset_ms, block):
            events.append(line.replace("Dialogue: 0,", "Dialogue: 2,", 1)
                          .replace(r"{\an7", r"{\an7\bord4\shad0", 1))
    return events


def asset_region(brand):
    safe = brand["platform_ui_reserve"]
    x = safe["left"] + 16
    return area(x, 490, WIDTH - safe["right"] - 10 - x, HEIGHT - safe["bottom"] - 760)


def scene_events(brand, visual, duration_ms, disclosure, *, cover=False, draft=False):
    safe, colors = brand["platform_ui_reserve"], brand["colors"]
    left = safe["left"] + 16
    right = WIDTH - safe["right"] - 10
    width = right - left
    end = timestamp(duration_ms)
    events = []

    def rect(x, y, w, h, color, alpha="00"):
        if w <= 0 or h <= 0:
            return
        fill = ass_color(color)[4:]
        tags = rf"{{\an7\pos({x},{y})\p1\bord0\shad0\1c&H{fill}&\1a&H{alpha}&}}"
        shape = f"m 0 0 l {w} 0 {w} {h} 0 {h}"
        events.append(f"Dialogue: 0,0:00:00.00,{end},Brand,,0,0,0,,{tags}{shape}\n")

    def text(value, x, y, w, style_name, size, lines=1, color=None):
        if not value:
            return
        style = {**brand["styles"][style_name], "size": size, "line_height": round(size * 1.22), "width_factor": 1.0}
        box = area(x, y, w, round(size * 1.22) * lines)
        block = fit_text(value, box, style, preferred_lines=lines)
        require(len(block["lines"]) <= lines, f"Shorten this scene text or split it across lines: {value}")
        for event in events_for_block(style_name, box, 0, duration_ms, block):
            if color:
                event = event.replace(r"{\an7", rf"{{\an7\1c&H{ass_color(colors[color])[4:]}&", 1)
            events.append(event.replace("Dialogue: 0,", "Dialogue: 1,", 1))

    def line(y, alpha="A0"):
        rect(left, y, width, 2, colors["accent"], alpha)

    kind = visual["kind"]
    top = max(safe["top"] + 16, 298 if cover else 156)
    text("KAI", left, top, width // 2, "Brand", 34)
    if draft:
        text("方法演示草稿", right - 273, top + 3, 273, "Brand", 26)
    if disclosure:
        text(disclosure, right - 273, top + (37 if draft else 5), 273, "Brand", 26, color="secondary")
    line(top + (80 if draft else 63), "45")
    text(visual.get("label"), left, top + 116, width, "CardLabel", 32)

    if kind == "title":
        text(visual["title"], left, top + 188, width, "CoverTitle", 100, 3)
        band_y = top + (555 if cover else 665)
        rect(left, band_y, width, 298, colors["accent"])
        text(visual["headline"], left + 34, band_y + 38, width - 68, "CardText", 66, 2, "background")
        text(visual.get("detail"), left + 36, band_y + 222, width - 72, "Brand", 32, 1, "background")
        text(visual.get("footer"), left, band_y + 353, width, "Brand", 31, 2, "secondary")
        return events

    text(visual["title"], left, top + 183, width, "Title", 64, 2)
    content_y = top + 350
    if kind == "chart":
        bars = visual["bars"]
        require(1 <= len(bars) <= 3, "A chart needs one to three bars.")
        spacing = 255 if len(bars) < 3 else 215
        for index, bar in enumerate(bars):
            value = bar["value"]
            require(type(value) in (int, float) and 0 <= value <= 100, "Chart values must be percentages from 0 to 100.")
            y = content_y + index * spacing
            text(bar["label"], left, y + 29, width - 247, "Chinese", 39, 2)
            text(f"{value:g}%", right - 236, y, 236, "Title", 63, color="accent")
            rect(left, y + 119, width, 38, colors["secondary"], "C0")
            rect(left, y + 119, round(width * value / 100), 38, colors["accent"], "42" if index == 0 else "00")
        foot_y = content_y + len(bars) * spacing + 15

    elif kind == "table":
        columns, rows = visual["columns"], visual["rows"]
        require(2 <= len(columns) <= 4 and 1 <= len(rows) <= 6
                and all(len(row) == len(columns) for row in rows), "Use 2–4 columns and 1–6 matching rows.")
        weights = visual.get("column_weights", [1] * len(columns))
        require(len(weights) == len(columns) and all(type(item) is int and item > 0 for item in weights),
                "Column weights must be positive integers, one per column.")
        widths = [width * weight // sum(weights) for weight in weights]
        row_height = min(119, 590 // (len(rows) + 1))
        highlight = set(visual.get("highlight_rows", []))
        require(all(type(index) is int and 1 <= index <= len(rows) for index in highlight),
                "Highlight rows use 1-based indexes.")
        for index, row in enumerate([columns, *rows]):
            y = content_y + index * row_height
            if index == 0 or index in highlight:
                rect(left, y, width, row_height, colors["accent"], "C8" if index == 0 else "DC")
            line(y + row_height - 1, "C0")
            x = left
            for cell, cell_width in zip(row, widths, strict=True):
                text(cell, x + 15, y + 19, cell_width - 27, "Chinese", 34 if index == 0 else 39,
                     1, "accent" if index == 0 or index in highlight else None)
                x += cell_width
        foot_y = content_y + (len(rows) + 1) * row_height + 45

    elif kind == "conclusion":
        line(content_y)
        items = visual["items"]
        require(1 <= len(items) <= 3, "A conclusion needs one to three takeaways.")
        for index, item in enumerate(items):
            y = content_y + 49 + index * 200
            rect(left, y + 5, 7, 112, colors["accent"])
            text(item, left + 33, y, width - 44, "CardText", 57, 2)
        foot_y = content_y + 49 + len(items) * 200 - 15

    elif kind in {"image", "video"}:
        region = asset_region(brand)
        foot_y = region["y"] + region["h"] + 20

    else:
        raise ValueError(f"Unknown visual kind: {kind}")

    text(visual.get("footer"), left, foot_y, width, "Brand", 30, 2, "secondary")
    return events
