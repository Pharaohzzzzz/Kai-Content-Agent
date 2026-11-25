"""Process one authorized local media file into an independent workspace task."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from media import extract_audio, probe
from transcribe import transcribe, write_srt

PROJECT = Path(__file__).resolve().parents[1]


def process(args: argparse.Namespace) -> None:
    source = args.input.resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"Input must be a local media file: {source}")

    requested_output = args.output
    if requested_output.exists() or requested_output.is_symlink():
        raise FileExistsError(f"Task output already exists: {requested_output}")
    output = requested_output.resolve()
    workspace = PROJECT / "workspace"
    if workspace not in output.parents:
        raise ValueError(f"Choose a new task directory under {workspace}.")

    metadata, audio_index, origin_us = probe(source, args.audio_stream)
    timeline = {
        "reference": "source_playback",
        "source_pts_origin_ms": round(origin_us / 1000),
        "audio_zero_ms": 0,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Write into a sibling directory. Windows rename publishes it without overwriting
    # a task directory created by another process after the initial existence check.
    with TemporaryDirectory(prefix=".p1-", dir=output.parent) as scratch:
        staged = Path(scratch) / "task"
        staged.mkdir()
        audio = staged / "audio.wav"
        duration_ms = extract_audio(source, audio_index, origin_us, audio)
        write_json(staged / "media.json", {
            "schema_version": 1,
            "source_media": str(source),
            "source": args.source or source.as_uri(),
            "rights_status": args.rights,
            "ai_label": args.ai_label,
            **metadata,
            "audio_duration_ms": duration_ms,
            "timeline": timeline,
        })
        transcript = transcribe(
            audio, duration_ms, timeline, source, PROJECT, args.model, args.device,
            args.language, args.word_timestamps,
        )
        write_json(staged / "transcript.json", transcript)
        write_srt(staged / "transcript.srt", transcript["segments"])
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Task output already exists: {output}")
        staged.rename(output)

    print(
        f"{output} | {len(transcript['segments'])} segments | "
        f"{transcript['language']} | inference {transcript['transcription_seconds']:.3f}s"
    )


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Local audio or video file")
    parser.add_argument("--output", type=Path, required=True, help="New task directory under workspace")
    parser.add_argument("--rights", required=True, choices=("owned", "authorized"), help="Confirmed source rights")
    parser.add_argument("--source", help="Provenance, such as a source URL or recording note")
    parser.add_argument("--ai-label", choices=("required", "not-required", "unknown"), default="unknown")
    parser.add_argument("--audio-stream", type=int, help="Absolute FFprobe stream index")
    parser.add_argument("--model", default="small", help="faster-whisper model name")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
    parser.add_argument("--word-timestamps", action="store_true")
    args = parser.parse_args()
    try:
        process(args)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
