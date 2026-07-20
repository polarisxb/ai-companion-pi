"""M14 pluggable text-to-speech for Feishu voice bubbles.

The command backend runs a configurable engine (default documented for the
local Piper install the legacy voice scripts already use), then converts to
opus 16k mono with ffmpeg and measures the duration with ffprobe — the exact
shape Feishu voice messages require. The fake backend keeps tests and dry
runs hermetic. Synthesized audio lives in caller-provided temp directories
and is never retained.
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TTS_TIMEOUT_SECONDS = 120
OPUS_SAMPLE_RATE = "16000"


class TTSError(RuntimeError):
    """Raised when speech synthesis or conversion fails."""


@dataclass(frozen=True)
class SynthesizedVoice:
    opus_path: Path
    duration_ms: int


class FakeTTSBackend:
    """Deterministic synthesis stand-in: writes stub bytes, estimates duration."""

    name = "fake"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def synthesize_opus(self, text: str, output_dir: Path) -> SynthesizedVoice:
        self.calls += 1
        if self.fail:
            raise TTSError("fake tts failure requested by test")
        cleaned = str(text or "").strip()
        if not cleaned:
            raise TTSError("cannot synthesize empty text")
        opus_path = Path(output_dir) / "voice.opus"
        digest = hashlib.sha256(cleaned.encode("utf-8")).digest()
        opus_path.write_bytes(b"OggS-fake-opus:" + digest[:16])
        return SynthesizedVoice(opus_path=opus_path, duration_ms=max(600, len(cleaned) * 80))


class CommandTTSBackend:
    """Engine-agnostic synthesis through a configurable command template.

    The template receives ``{output}`` (target wav path) and optionally
    ``{text}``; when ``{text}`` is absent the text is piped on stdin, which is
    how Piper works:

        piper --model /path/zh_CN-huayan-medium.onnx --output_file {output}
    """

    name = "command"

    def __init__(
        self,
        command_template: str,
        *,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        timeout_seconds: int = DEFAULT_TTS_TIMEOUT_SECONDS,
        runner=None,
    ):
        if not str(command_template or "").strip():
            raise TTSError("tts command template must not be empty")
        if "{output}" not in command_template:
            raise TTSError("tts command template must contain the {output} placeholder")
        self.command_template = command_template
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.timeout_seconds = timeout_seconds
        self._runner = runner or self._default_runner

    def synthesize_opus(self, text: str, output_dir: Path) -> SynthesizedVoice:
        cleaned = str(text or "").strip()
        if not cleaned:
            raise TTSError("cannot synthesize empty text")
        output_dir = Path(output_dir)
        wav_path = output_dir / "voice.wav"
        opus_path = output_dir / "voice.opus"

        command = []
        text_inlined = False
        for token in shlex.split(self.command_template):
            if "{output}" in token:
                command.append(token.replace("{output}", str(wav_path)))
            elif "{text}" in token:
                command.append(token.replace("{text}", cleaned))
                text_inlined = True
            else:
                command.append(token)
        self._runner(command, input_text=None if text_inlined else cleaned)
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise TTSError("tts engine produced no audio output")

        self._runner([
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(wav_path),
            "-acodec",
            "libopus",
            "-ac",
            "1",
            "-ar",
            OPUS_SAMPLE_RATE,
            str(opus_path),
        ], input_text=None)
        if not opus_path.exists() or opus_path.stat().st_size == 0:
            raise TTSError("ffmpeg produced no opus output")

        duration_output = self._runner([
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(opus_path),
        ], input_text=None)
        try:
            duration_ms = max(1, int(float(str(duration_output).strip()) * 1000))
        except (TypeError, ValueError) as exc:
            raise TTSError(f"could not parse audio duration: {duration_output!r}") from exc
        return SynthesizedVoice(opus_path=opus_path, duration_ms=duration_ms)

    def _default_runner(self, command: list[str], *, input_text: str | None) -> str:
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise TTSError(f"tts tool not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSError(f"tts step timed out: {command[0]}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:200]
            raise TTSError(f"tts step failed ({command[0]}): {detail}")
        return completed.stdout


def create_tts_backend(config) -> CommandTTSBackend | None:
    """Build the configured backend, or ``None`` when TTS is not configured."""

    command_template = getattr(config, "tts_command", None)
    if not command_template:
        return None
    return CommandTTSBackend(command_template)
