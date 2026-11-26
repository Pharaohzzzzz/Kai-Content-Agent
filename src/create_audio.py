"""Prepare exact phrase and scene timing from Windows TTS or original recordings."""

from array import array
from pathlib import Path
import subprocess
import sys
import wave

from analysis_plan import json_text, require, required_text
from media import run_media_tool
from render import sha256
from render_brand import FPS
from render_commentary import SAMPLE_RATE

SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
WAV_FORMAT = (1, 2, SAMPLE_RATE)
DEFAULT_PAUSE_MS = 110
RIGHTS = {"owned", "licensed", "permission-granted"}


def local_asset(base, item, label):
    path = (base / required_text(item["file"], label)).resolve()
    require(path.is_file(), f"{label} is missing: {path}")
    require(item["rights"] in RIGHTS, f"Record confirmed rights for {label}: {path}")
    required_text(item["rights_note"], f"{label} rights note")
    require(type(item["ai_generated"]) is bool, f"Declare AI status for {label}.")
    return path


def speech_wav(source, target, clip):
    trim = []
    if clip is not None:
        start, end = clip
        require(type(start) is int and type(end) is int and 0 <= start < end, "Invalid recording clip_ms.")
        trim = ["-af", f"atrim=start={start / 1000:.3f}:end={end / 1000:.3f},asetpts=PTS-STARTPTS"]
    run_media_tool([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-n", "-i", str(source),
        "-map", "0:a:0", *trim, "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(target),
    ])
    if clip is not None:
        with wave.open(str(target), "rb") as audio:
            require(abs(audio.getnframes() - (end - start) * SAMPLE_RATE // 1000) <= 960,
                    f"Recording ends before the specified clip: {source}")


def voiced_pcm(path):
    with wave.open(str(path), "rb") as audio:
        actual_format = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
        require(actual_format == WAV_FORMAT, f"Unexpected narration WAV format: {path}")
        pcm = audio.readframes(audio.getnframes())
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    window = SAMPLE_RATE // 100
    active = []
    for index in range(0, len(samples), window):
        block = samples[index:index + window]
        if sum(sample * sample for sample in block) >= 110 ** 2 * len(block):
            active.append(index)
    require(len(active) >= 8, f"No usable speech in {path}. Supply an audible recording or another local voice.")
    # Keep quiet consonants just outside the measured energy span. Internal pauses stay intact.
    start = max(0, active[0] - 4 * window)
    end = min(len(samples), active[-1] + 5 * window)
    return pcm[2 * start:2 * end]


def prepare_audio(plan, base, directory):
    config = plan["voice"]
    mode = config["mode"]
    parts = [cue for scene in plan["scenes"] for cue in scene["narration"]]
    voice = {"mode": mode}
    if mode == "windows-tts":
        voice_name = required_text(config["voice"], "Windows voice")
        rate = config.get("rate", 0)
        require(type(rate) is int and -10 <= rate <= 10, "Windows speech rate must be an integer from -10 to 10.")
        request = directory / "tts.json"
        request.write_text(json_text({
            "voice": voice_name, "rate": rate, "parts": [part["spoken"] for part in parts],
        }), encoding="utf-8")
        script = Path(__file__).with_name("create_voice.ps1").resolve()
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script), str(request), str(directory),
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        require(result.returncode == 0, f"Chinese TTS failed. Use a working Windows voice or import original recordings.\n{result.stderr}")
        voice.update(engine="Windows System.Speech", voice=voice_name, rate=rate)

    def ms(sample):
        return round(sample * 1000 / SAMPLE_RATE)

    cursor = 0
    part_index = 0
    input_hashes = {}
    timeline = []
    with wave.open(str(directory / "narration.wav"), "wb") as joined:
        joined.setnchannels(1)
        joined.setsampwidth(2)
        joined.setframerate(SAMPLE_RATE)
        for scene in plan["scenes"]:
            scene_start = cursor
            cues = []
            for cue in scene["narration"]:
                if mode == "windows-tts":
                    wav = directory / f"tts-{part_index:04}.wav"
                else:
                    source = local_asset(base, cue["recording"], "recording")
                    if source not in input_hashes:
                        input_hashes[source] = sha256(source)
                    wav = directory / "converted-voice.wav"
                    speech_wav(source, wav, cue["recording"].get("clip_ms"))
                pcm = voiced_pcm(wav)
                count = len(pcm) // 2
                require(count >= SAMPLE_RATE // 5, f"Speech cue is too short: {cue['spoken']}")
                joined.writeframesraw(pcm)
                end = cursor + count
                cues.append(dict(text=cue.get("subtitle", cue["spoken"]), start_ms=ms(cursor), end_ms=ms(end),
                                 start_sample=cursor, end_sample=end))
                pause = cue.get("pause_ms", DEFAULT_PAUSE_MS)
                require(type(pause) is int and 0 <= pause <= 600, "Cue pause_ms must be an integer between 0 and 600.")
                silence = pause * SAMPLE_RATE // 1000
                joined.writeframesraw(b"\0\0" * silence)
                cursor = end + silence
                if mode == "recording":
                    wav.unlink()
                part_index += 1
            frame_padding = -cursor % SAMPLES_PER_FRAME
            joined.writeframesraw(b"\0\0" * frame_padding)
            cursor += frame_padding
            timeline.append(dict(
                start_frame=scene_start // SAMPLES_PER_FRAME, end_frame=cursor // SAMPLES_PER_FRAME,
                start_ms=ms(scene_start), end_ms=ms(cursor), cues=cues,
            ))
    voice["recording_sha256"] = {str(path): checksum for path, checksum in input_hashes.items()}
    voice["timing"] = "Energy crop with 40 ms guard on each phrase, then measured PCM samples; no word-level timing."
    return timeline, voice
