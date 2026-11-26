"""Probe a local source and extract timestamp-aligned speech audio."""

import hashlib
import json
import subprocess
import wave
from decimal import Decimal
from pathlib import Path

SAMPLE_RATE = 16_000


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_media_tool(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{command[0]} is not available on PATH.") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or f"exit code {error.returncode}"
        raise RuntimeError(f"{command[0]} failed: {detail}") from error
    return result.stdout


def probe(source: Path, stream_index: int | None) -> tuple[dict, int, int]:
    entries = (
        "format=format_name,start_time,duration:"
        "stream=index,codec_type,codec_name,start_time,duration,width,height,sample_rate,channels:"
        "stream_tags=language,title:stream_disposition=default,attached_pic"
    )
    metadata = json.loads(run_media_tool([
        "ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(source),
    ]))
    streams = metadata["streams"]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]
    if not audio:
        raise ValueError("No audio stream found in the source media.")
    if stream_index is None:
        selected = next(
            (stream for stream in audio if stream.get("disposition", {}).get("default")),
            audio[0],
        )
    else:
        selected = next((stream for stream in audio if stream["index"] == stream_index), None)
        if selected is None:
            available = ", ".join(str(stream["index"]) for stream in audio)
            raise ValueError(f"Stream {stream_index} is not an audio stream. Audio indexes: {available}.")

    file_format = metadata["format"]
    source_start = file_format.get("start_time")
    origin_us = round(Decimal(source_start) * 1_000_000) if source_start not in (None, "N/A") else 0
    for item in [file_format, *streams]:
        for original, converted in (("start_time", "start_pts_ms"), ("duration", "duration_ms")):
            seconds = item.pop(original, None)
            item[converted] = round(Decimal(seconds) * 1000) if seconds not in (None, "N/A") else None
    details = {"format": file_format, "streams": streams, "selected_audio_index": selected["index"]}
    return details, selected["index"], origin_us


def extract_audio(source: Path, stream_index: int, origin_us: int, target: Path) -> int:
    # Keep source PTS, move playback zero to 0, and pad or trim to that zero.
    timeline_filter = (
        f"asetpts=PTS-({origin_us})/1000000/TB,"
        f"aresample={SAMPLE_RATE}:async=1:first_pts=0"
    )
    run_media_tool([
        "ffmpeg", "-v", "error", "-nostdin", "-n", "-copyts", "-i", str(source),
        "-map", f"0:{stream_index}", "-af", timeline_filter, "-ac", "1",
        "-c:a", "pcm_s16le", str(target),
    ])
    with wave.open(str(target), "rb") as audio:
        duration_ms = round(audio.getnframes() * 1000 / audio.getframerate())
    if duration_ms <= 0:
        raise ValueError("The selected audio stream has no decodable samples.")
    return duration_ms
