"""Text-to-speech via a VOICEVOX ENGINE server (Japanese character voices).

Flow: text -> VOICEVOX (/audio_query then /synthesis) -> wav bytes -> play
through the connected voice client with FFmpeg.
"""

import logging
import os
import re
import tempfile

import discord
import httpx

from . import config

log = logging.getLogger(config.LOGGER_NAME)

# Strip Discord mention/emoji/channel tags like <@123>, <:name:id>, <#456>.
_TAG_RE = re.compile(r"<[^>]+>")


async def _synthesize(text: str, speaker_id: int) -> bytes | None:
    """Call VOICEVOX and return WAV audio bytes (or None on failure)."""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            query = await http.post(
                f"{config.VOICEVOX_URL}/audio_query",
                params={"text": text, "speaker": speaker_id},
            )
            query.raise_for_status()
            audio = await http.post(
                f"{config.VOICEVOX_URL}/synthesis",
                params={"speaker": speaker_id},
                json=query.json(),
            )
            audio.raise_for_status()
            return audio.content
    except Exception:
        log.exception("VOICEVOX synthesis failed (is the engine running at %s?)",
                      config.VOICEVOX_URL)
        return None


async def speak(voice_client, text: str) -> None:
    """Synthesize `text` with VOICEVOX and play it through the voice client."""
    if not config.TTS_ENABLED or voice_client is None:
        return

    speaker_id = config.VOICEVOX_SPEAKER
    if "|" in text:
        parts = text.split("|", 1)
        if parts[0].strip().isdigit():
            speaker_id = int(parts[0].strip())
            text = parts[1]

    clean = _TAG_RE.sub("", text).strip()[: config.TTS_MAX_CHARS]
    if not clean:
        return
    audio = await _synthesize(clean, speaker_id)
    if audio is None:
        return

    # Write the WAV to a temp file and play it; delete the file afterwards.
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        if voice_client.is_playing():
            voice_client.stop()
        source = discord.FFmpegPCMAudio(path, executable=config.FFMPEG_PATH)
        voice_client.play(source, after=lambda err: _cleanup(path))
    except Exception:
        log.exception("Failed to play TTS audio")
        _cleanup(path)


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
