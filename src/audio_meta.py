"""
Audio metadata extraction for the ConsultBae audio collection app.

Extracts duration, sample rate, bitrate, loudness and a rough noise estimate
from any audio file the browser or a user can hand us.

Why soundfile and not pydub
---------------------------
pydub imports the stdlib `audioop` module, which was REMOVED in Python 3.13
(PEP 594). On 3.13+ pydub fails at import time. soundfile (libsndfile) has no
such dependency, and reading raw samples ourselves makes the loudness maths
explicit rather than hidden behind a library call.

Why ffmpeg is still needed
--------------------------
libsndfile cannot decode webm/opus, which is exactly what the browser's
MediaRecorder produces. So anything soundfile refuses to open gets transcoded
to a temporary WAV first. ffprobe is also the only reliable source of the
container bitrate.
"""

import json
import math
import os
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf


# Allow an explicit override so the app still runs when ffmpeg is installed
# but not on PATH - a situation that is common on Windows.
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

# Windowing for the noise estimate.
WINDOW_MS = 100
CLEAN_DB = 25.0      # dynamic range above this looks like clear speech
FAIR_DB = 10.0       # below this the signal barely rises above its own floor


# --------------------------------------------------------------- helpers
def _dbfs(samples):
    """
    Root-mean-square level in dBFS (decibels relative to full scale).

    Samples are floats in [-1.0, 1.0], so RMS is at most 1.0 and the result is
    at most 0 dB. Real recordings sit around -15 to -30 dB. Digital silence has
    an RMS of exactly 0, and log10(0) is undefined, so that case returns None
    rather than -inf: -inf poisons every average it touches downstream.
    """
    if samples.size == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 0:
        return None
    return 20.0 * math.log10(rms)


def _probe(path):
    """Return ffprobe's parsed JSON for a file, or None if ffprobe is absent."""
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def to_wav(src, dest):
    """
    Transcode any audio file to 16-bit PCM WAV using ffmpeg.

    The app calls this on every upload before storing it, because browser
    recordings arrive as webm/opus, which libsndfile cannot read and whose
    reported bitrate is unreliable.

    Returns True on success.
    """
    cmd = [FFMPEG, "-y", "-i", str(src), "-acodec", "pcm_s16le", str(dest)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and os.path.exists(dest)


def _read_samples(path):
    """
    Load a file as mono float samples.

    Falls back to an ffmpeg transcode when libsndfile refuses the format.
    Returns (samples, sample_rate) or (None, None).
    """
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return data.mean(axis=1), sr          # mix any channel count to mono
    except Exception:
        pass

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        if not to_wav(path, tmp.name):
            return None, None
        data, sr = sf.read(tmp.name, dtype="float32", always_2d=True)
        return data.mean(axis=1), sr
    except Exception:
        return None, None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _noise_estimate(samples, sr):
    """
    Rough recording-quality label based on dynamic range.

    Slice the audio into 100 ms windows and measure each window's level. In a
    clean recording the loud windows (speech) sit far above the quiet ones
    (room tone), so the gap between the 90th and 10th percentile is wide. In a
    noisy recording the background fills the gaps and the spread collapses.

    This is a proxy for SNR, not a real SNR measurement: it cannot tell loud
    background noise apart from genuinely quiet speech. It is good enough to
    flag unusable submissions for review, which is all it is used for.

    Returns (label, dynamic_range_db).
    """
    win = int(sr * WINDOW_MS / 1000)
    if win <= 0 or samples.size < win * 2:
        return None, None

    levels = []
    for start in range(0, samples.size - win + 1, win):
        level = _dbfs(samples[start:start + win])
        if level is not None and math.isfinite(level):
            levels.append(level)

    if len(levels) < 3:
        return None, None

    spread = float(np.percentile(levels, 90) - np.percentile(levels, 10))
    if spread >= CLEAN_DB:
        label = "clean"
    elif spread >= FAIR_DB:
        label = "fair"
    else:
        label = "noisy"
    return label, round(spread, 2)


# --------------------------------------------------------------- public API
def extract(path):
    """
    Extract audio properties from a file.

    Every field is independently optional. A file we cannot decode still
    returns a dict with None values and an "error" key, so the caller can
    store a submission row instead of failing the upload - losing a worker's
    recording is worse than storing it with missing metadata.

    Keys: duration_sec, sample_rate_khz, bitrate_kbps, loudness_db,
          noise_estimate, dynamic_range_db, channels, format, error
    """
    result = {
        "duration_sec": None,
        "sample_rate_khz": None,
        "bitrate_kbps": None,
        "loudness_db": None,
        "noise_estimate": None,
        "dynamic_range_db": None,
        "channels": None,
        "format": None,
        "error": None,
    }

    if not os.path.exists(path):
        result["error"] = "file not found"
        return result

    # --- container metadata (bitrate, and a duration fallback) -----------
    info = _probe(path)
    if info:
        fmt = info.get("format", {}) or {}
        result["format"] = fmt.get("format_name")

        bitrate = fmt.get("bit_rate")
        if bitrate is None:
            # Some containers only report bitrate per stream, not per file.
            for stream in info.get("streams", []) or []:
                if stream.get("codec_type") == "audio" and stream.get("bit_rate"):
                    bitrate = stream["bit_rate"]
                    break
        if bitrate is not None:
            try:
                result["bitrate_kbps"] = round(int(bitrate) / 1000, 2)
            except (TypeError, ValueError):
                pass

        for stream in info.get("streams", []) or []:
            if stream.get("codec_type") == "audio":
                result["channels"] = stream.get("channels")
                break

        try:
            result["duration_sec"] = round(float(fmt["duration"]), 3)
        except (KeyError, TypeError, ValueError):
            pass

    # --- sample-level metadata ------------------------------------------
    samples, sr = _read_samples(path)
    if samples is None:
        if result["duration_sec"] is None:
            result["error"] = "could not decode audio (is ffmpeg installed?)"
        return result

    result["sample_rate_khz"] = round(sr / 1000, 3)
    # Prefer the sample count over ffprobe's duration: it is exact.
    result["duration_sec"] = round(samples.size / sr, 3)

    loudness = _dbfs(samples)
    result["loudness_db"] = round(loudness, 2) if loudness is not None else None

    label, spread = _noise_estimate(samples, sr)
    result["noise_estimate"] = label
    result["dynamic_range_db"] = spread

    # Uncompressed WAV has no stored bitrate, so derive it.
    if result["bitrate_kbps"] is None and result["duration_sec"]:
        try:
            size_bits = os.path.getsize(path) * 8
            result["bitrate_kbps"] = round(size_bits / result["duration_sec"] / 1000, 2)
        except OSError:
            pass

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python src/audio_meta.py <audiofile>")
        sys.exit(1)
    print(json.dumps(extract(sys.argv[1]), indent=2))