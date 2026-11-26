"""Prepare P1 data and derive P2 artifacts from an authored analysis draft."""

import hashlib
import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
SOURCE_FILES = ("media.json", "transcript.json")
CLIP_TEXT_FIELDS = ("core_point", "title_zh", "hook_zh", "reason", "context")
CLIP_RISK_FIELDS = ("content_risks", "verification_needed")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def json_text(value, **options) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **options)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def required_text(value, field: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{field} must contain text.")
    return value.strip()


def load_source(task: Path) -> dict:
    payloads = {name: (task / name).read_bytes() for name in SOURCE_FILES}
    media, transcript = (json.loads(payloads[name].decode("utf-8-sig")) for name in SOURCE_FILES)
    require(media["rights_status"] in ("owned", "authorized"), "Source rights must be owned or authorized.")
    require(media["schema_version"] == transcript["schema_version"] == 1, "Expected P1 input schema version 1.")
    timeline = transcript["timeline"]
    require(
        timeline == media["timeline"] and timeline["reference"] == "source_playback"
        and timeline["audio_zero_ms"] == 0 and type(timeline["source_pts_origin_ms"]) is int
        and transcript["source_media"] == media["source_media"]
        and transcript["duration_ms"] == media["audio_duration_ms"],
        "P1 files disagree about the source or playback timeline.",
    )
    source_path = required_text(media["source_media"], "source_media")
    source_name = required_text(media["source"], "source")
    source_hash = media.get("source_sha256")
    require("source_sha256" not in media or (isinstance(source_hash, str) and re.fullmatch(r"[0-9a-f]{64}", source_hash)),
            "Invalid source SHA-256 in P1 media.json.")
    require(media["ai_label"] in ("required", "not-required", "unknown"), "Invalid P1 AI label.")
    audio_index = media["selected_audio_index"]
    require(type(audio_index) is int and audio_index >= 0, "Invalid selected audio stream index.")
    p1_segments = transcript["segments"]
    require(isinstance(p1_segments, list) and bool(p1_segments), "No valid speech segments in transcript.json.")
    audio_duration = transcript["duration_ms"]
    duration_ms = media["format"]["duration_ms"]
    if duration_ms is None:
        duration_ms = audio_duration
    require(type(audio_duration) is int and audio_duration > 0, "Invalid P1 audio duration.")
    require(type(duration_ms) is int and duration_ms > 0, "Invalid source duration.")
    segments = []
    previous_start = previous_end = -1
    for expected_id, row in enumerate(p1_segments, start=1):
        segment_id, start, end, text = (row[key] for key in ("id", "start_ms", "end_ms", "text"))
        require(
            type(segment_id) is int and segment_id == expected_id
            and type(start) is int and type(end) is int
            and 0 <= start < end <= audio_duration and start >= previous_start and end >= previous_end,
            f"Invalid P1 segment ID or time bounds at position {expected_id}.",
        )
        required_text(text, f"P1 segment {segment_id}")
        segments.append({"id": segment_id, "start_ms": start, "end_ms": end, "text": text})
        previous_start, previous_end = start, end
    return {
        "source_media": source_path,
        "source_sha256": source_hash,
        "synthetic_test": media.get("synthetic_test", False) or "synthetic test" in source_name.lower(),
        "source": source_name,
        "rights_status": media["rights_status"],
        "ai_label": media["ai_label"],
        "selected_audio_index": audio_index,
        "source_timeline": {**timeline, "unit": "ms"},
        "source_duration_ms": duration_ms,
        "source_language": required_text(transcript["language"], "language"),
        "input_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
        "segments": segments,
    }


def prepare(source: dict, chunk_chars: int, chunk_number: int | None) -> dict:
    require(chunk_chars > 0, "--chunk-chars must be positive.")
    rows = source["segments"]
    spans = []
    start = used = 0
    for index, row in enumerate(rows):
        size = len(json_text(row, separators=(",", ":"))) + 1
        if index > start and used + size > chunk_chars:
            spans.append((start, index))
            start, used = index, 0
        used += size
    spans.append((start, len(rows)))
    result = {key: value for key, value in source.items() if key != "segments"}
    result["chunks"] = [
        {"number": number, "first_id": rows[first]["id"], "last_id": rows[stop - 1]["id"]}
        for number, (first, stop) in enumerate(spans, start=1)
    ]
    if chunk_number is None and len(spans) > 1:
        return result
    number = chunk_number if chunk_number is not None else 1
    require(1 <= number <= len(spans), f"Chunk must be between 1 and {len(spans)}.")
    first, stop = spans[number - 1]
    context_start = max(0, first - 2)
    result.update(
        chunk=number,
        context_before=rows[context_start:first],
        segments=rows[first:stop],
        context_after=rows[stop:stop + 2],
    )
    return result


def rank_candidates(source: dict, draft: dict, strategy: dict) -> list[dict]:
    require(draft["input_sha256"] == source["input_sha256"], "P1 inputs changed or draft hashes are missing.")
    candidates = draft["candidates"]
    require(
        isinstance(candidates, list) and len(candidates) <= strategy["max_candidates"],
        f"Expected 0–{strategy['max_candidates']} candidates.",
    )
    weights = strategy["scoring_weights_percent"]
    seen = set()
    ranked = []
    rows = source["segments"]
    for candidate in candidates:
        clip_id = candidate["clip_id"]
        require(isinstance(clip_id, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,39}", clip_id) is not None,
                "Invalid clip_id. Use lowercase letters, digits and hyphens, starting with a letter.")
        require(clip_id not in seen, f"Duplicate clip_id {clip_id}.")
        seen.add(clip_id)
        ids = candidate["segment_ids"]
        require(isinstance(ids, list) and bool(ids) and all(type(i) is int for i in ids),
                f"{clip_id}: expected a nonempty list of integer segment IDs.")
        first, last = ids[0], ids[-1]
        require(1 <= first <= last <= len(rows) and ids == list(range(first, last + 1)),
                f"{clip_id}: segment IDs must exist and be in uninterrupted source order.")
        start, end = rows[first - 1]["start_ms"], rows[last - 1]["end_ms"]
        require(start < end <= source["source_duration_ms"], f"{clip_id}: outside the source duration.")
        require(candidate["context_complete"] is True and candidate["serious_fact_risk"] is False,
                f"{clip_id}: incomplete context or serious fact risk fails admission.")
        scores = candidate["scores"]
        require(
            isinstance(scores, dict) and scores.keys() == weights.keys()
            and all(type(score) is int and 0 <= score <= 100 for score in scores.values()),
            f"{clip_id}: supply the five integer scores (0–100).",
        )
        risks = {}
        for field in CLIP_RISK_FIELDS:
            items = candidate[field]
            require(isinstance(items, list), f"{clip_id}.{field} must be an array.")
            risks[field] = [required_text(item, f"{clip_id}.{field}") for item in items]
        ranked.append({
            "clip_id": clip_id, "segment_ids": ids, "source_start_ms": start, "source_end_ms": end,
            **{field: required_text(candidate[field], f"{clip_id}.{field}") for field in CLIP_TEXT_FIELDS},
            **risks, "context_complete": True, "serious_fact_risk": False,
            "scores": scores, "weighted_score": sum(scores[key] * weight for key, weight in weights.items()) / 100,
        })
    return sorted(ranked, key=lambda clip: (-clip["weighted_score"], clip["source_start_ms"], clip["clip_id"]))


def make_translation(source: dict, clip: dict, entries: list) -> dict:
    ids = clip["segment_ids"]
    require(isinstance(entries, list) and len(entries) == len(ids),
            "Translation must have one entry per selected segment.")
    chinese_source = source["source_language"].split("-")[0].lower() == "zh"
    segments = []
    for segment_id, entry in zip(ids, entries, strict=True):
        require(type(entry["segment_id"]) is int and entry["segment_id"] == segment_id,
                f"Expected translation entry for segment {segment_id} in source order.")
        original = source["segments"][segment_id - 1]["text"]
        segment = {"segment_id": segment_id, "original_text": original}
        field = "subtitle_suggestion_zh" if chinese_source else "translation_zh"
        if not chinese_source or field in entry:
            segment[field] = required_text(entry[field], f"segment {segment_id}.{field}")
        if "uncertainty" in entry:
            segment["uncertainty"] = required_text(entry["uncertainty"], f"segment {segment_id}.uncertainty")
        segments.append(segment)
    return {
        "schema_version": SCHEMA_VERSION,
        "clip_id": clip["clip_id"],
        "source_language": source["source_language"],
        "mode": "original" if chinese_source else "zh_cn",
        "segments": segments,
    }


def build_outputs(source: dict, draft: dict, strategy: dict) -> dict[str, dict]:
    ranked = rank_candidates(source, draft, strategy)
    winner = ranked[0] if ranked else None
    production = draft.get("production")
    analysis = {
        "schema_version": SCHEMA_VERSION,
        "input_sha256": source["input_sha256"],
        "source_language": source["source_language"],
        "source_duration_ms": source["source_duration_ms"],
        "source_summary": required_text(draft["source_summary"], "source_summary"),
        "scoring_weights_percent": strategy["scoring_weights_percent"],
        "candidates": ranked,
        "recommended_clip_id": winner["clip_id"] if winner else None,
    }
    if winner is None:
        require(production is None, "Production must be omitted when there are no candidates.")
        analysis["no_candidate_reason"] = required_text(draft["no_candidate_reason"], "no_candidate_reason")
        return {"analysis.json": analysis}

    require(isinstance(production, dict) and production["clip_id"] == winner["clip_id"],
            f"Production must use the highest-ranked candidate {winner['clip_id']}.")
    translation = make_translation(source, winner, production["translation_segments"])
    analysis["recommendation_reason"] = required_text(draft["recommendation_reason"], "recommendation_reason")
    plan = {
        "schema_version": SCHEMA_VERSION,
        **{key: source[key] for key in (
            "source_media", "source", "rights_status", "ai_label", "selected_audio_index",
            "source_timeline", "source_duration_ms", "source_language",
        )},
        **{key: winner[key] for key in (
            "segment_ids", "source_start_ms", "source_end_ms", "title_zh", "hook_zh", *CLIP_RISK_FIELDS,
        )},
        "recommended_clip_id": winner["clip_id"],
        "original_transcript_ref": "../transcript.json",
        "translation_ref": "translation.json",
        "core_summary_zh": required_text(production["core_summary_zh"], "core_summary_zh"),
        "original_commentary_zh": required_text(production["original_commentary_zh"], "original_commentary_zh"),
        "review_status": {
            "human": "pending",
            "optional_review_ref": "review_response.json",
        },
    }
    if "closing_point_zh" in production:
        plan["closing_point_zh"] = required_text(production["closing_point_zh"], "closing_point_zh")
    return {"analysis.json": analysis, "edit_plan.json": plan, "translation.json": translation}


def check_same(actual: dict, expected: dict, name: str) -> None:
    require(json_text(actual, sort_keys=True) == json_text(expected, sort_keys=True),
            f"{name} does not match the P1 inputs or the derived P2 plan.")


def validate(source: dict, output: Path, strategy: dict) -> dict[str, dict]:
    analysis = read_json(output / "analysis.json")
    draft = dict(analysis)
    if analysis["candidates"]:
        plan = read_json(output / "edit_plan.json")
        translation = read_json(output / "translation.json")
        draft["production"] = {
            **plan, "clip_id": plan["recommended_clip_id"], "translation_segments": translation["segments"],
        }
    expected = build_outputs(source, draft, strategy)
    for name in ("analysis.json", "edit_plan.json", "translation.json"):
        path = output / name
        if name in expected:
            check_same(read_json(path), expected[name], name)
        else:
            require(not path.exists(), f"{name} must be absent with zero candidates.")
    clips = {clip["clip_id"]: clip for clip in expected["analysis.json"]["candidates"]}
    for path in output.glob("translation-*.json"):
        clip_id = path.stem.removeprefix("translation-")
        require(clip_id in clips and clip_id != analysis["recommended_clip_id"],
                f"{path.name} does not match an additional candidate.")
        extra = read_json(path)
        check_same(extra, make_translation(source, clips[clip_id], extra["segments"]), path.name)
    return expected
