"""Run local Whisper inference and serialize its source-aligned timeline."""

import os
from pathlib import Path
from sysconfig import get_path
from time import perf_counter


def configure_runtime(project: Path) -> None:
    cache = project / ".cache" / "huggingface"
    scratch = project / "tmp"
    scratch.mkdir(exist_ok=True)
    os.environ.update({
        "HF_HOME": str(cache),
        "HF_HUB_CACHE": str(project / "models"),
        "HF_XET_CACHE": str(cache / "xet"),
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "TEMP": str(scratch),
        "TMP": str(scratch),
        "TMPDIR": str(scratch),
    })


def transcribe(
    audio: Path,
    duration_ms: int,
    timeline: dict,
    source: Path,
    project: Path,
    model_name: str,
    device: str,
    language: str,
    word_timestamps: bool,
) -> dict:
    configure_runtime(project)
    if os.name == "nt" and device == "cuda":
        vendor = Path(get_path("purelib")) / "nvidia"
        bins = [vendor / name / "bin" for name in ("cuda_runtime", "cublas", "cudnn")]
        # CTranslate2's native loader searches the process PATH.
        os.environ["PATH"] = os.pathsep.join([
            *(str(path) for path in bins if path.is_dir()), os.environ.get("PATH", ""),
        ])

    from ctranslate2 import get_supported_compute_types
    from faster_whisper import WhisperModel, available_models, download_model
    from huggingface_hub.errors import LocalEntryNotFoundError

    if model_name not in available_models():
        raise ValueError(f"Unknown model {model_name!r}. Choose from: {', '.join(available_models())}.")

    compute_type = "float16" if device == "cuda" else "int8"
    if compute_type not in get_supported_compute_types(device):
        raise RuntimeError(f"{device} does not support {compute_type} on this machine.")

    cache_dir = str(project / "models")
    try:
        cached = Path(download_model(model_name, cache_dir=cache_dir, local_files_only=True))
    except LocalEntryNotFoundError:
        cached = None
    if cached is None or not has_model_files(cached):
        print(f"Downloading {model_name} to {cache_dir} ...", flush=True)
        try:
            cached = Path(download_model(model_name, cache_dir=cache_dir))
        except LocalEntryNotFoundError as error:
            raise RuntimeError(f"Could not obtain {model_name} from the model hub: {error}") from error
        if not has_model_files(cached):
            raise RuntimeError(f"Incomplete model snapshot: {cached}")

    print(f"Transcribing with {model_name}, {device}, {compute_type} ...", flush=True)
    load_start = perf_counter()
    model = WhisperModel(str(cached), device=device, compute_type=compute_type)
    if language == "zh" and not model.model.is_multilingual:
        raise ValueError(f"Model {model_name!r} is English-only. Select a multilingual model for zh.")
    segmentation_start = perf_counter()
    recognized, info = model.transcribe(
        str(audio), task="transcribe", language=None if language == "auto" else language,
        word_timestamps=word_timestamps, vad_filter=True,
    )
    segments = []
    for item in recognized:
        text = " ".join(item.text.split())
        start = bound_ms(item.start, duration_ms)
        end = bound_ms(item.end, duration_ms)
        if not text or end <= start:
            continue
        segment = {"id": len(segments) + 1, "start_ms": start, "end_ms": end, "text": text}
        if item.words is not None:
            segment["words"] = [
                {
                    "start_ms": bound_ms(word.start, duration_ms),
                    "end_ms": bound_ms(word.end, duration_ms),
                    "text": word.word,
                }
                for word in item.words
            ]
        segments.append(segment)

    return {
        "schema_version": 1,
        "source_media": str(source),
        "language": info.language,
        "language_probability": info.language_probability,
        "model": model_name,
        "model_revision": cached.name,
        "device": device,
        "compute_type": compute_type,
        "duration_ms": duration_ms,
        "timeline": timeline,
        "model_load_seconds": round(segmentation_start - load_start, 3),
        "transcription_seconds": round(perf_counter() - segmentation_start, 3),
        "segments": segments,
    }


def has_model_files(path: Path) -> bool:
    required = ("model.bin", "config.json", "tokenizer.json")
    return all((path / name).is_file() for name in required) and any(
        (path / name).is_file() for name in ("vocabulary.txt", "vocabulary.json")
    )


def bound_ms(seconds: float, duration_ms: int) -> int:
    return min(duration_ms, max(0, round(seconds * 1000)))


def srt_timestamp(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def write_srt(path: Path, segments: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for segment in segments:
            start = srt_timestamp(segment["start_ms"])
            end = srt_timestamp(segment["end_ms"])
            file.write(f"{segment['id']}\n{start} --> {end}\n{segment['text']}\n\n")
