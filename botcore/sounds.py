"""Playing audio files in voice channels (requires FFmpeg + PyNaCl/davey)."""

import logging
import os

import discord

from . import config

log = logging.getLogger(config.LOGGER_NAME)


def play_sound(voice_client, path: str) -> str:
    """Play a sound file through the voice client. Returns a note if it couldn't."""
    if not path:
        return ""
    if not os.path.isfile(path):
        return f"\n(ยังไม่มีไฟล์เสียง `{path}` — ข้ามการเล่นเสียง)"
    try:
        if voice_client.is_playing():
            voice_client.stop()
        source = discord.FFmpegPCMAudio(path, executable=config.FFMPEG_PATH)
        voice_client.play(source)
    except FileNotFoundError:
        return "\n(เล่นเสียงไม่ได้ — ยังไม่ได้ลง FFmpeg)"
    except Exception:
        log.exception("Failed to play sound %s", path)
        return "\n(เล่นเสียงไม่ได้ ลองดู log)"
    return ""
