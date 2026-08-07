"""Central configuration: loads .env and exposes all settings + constants.

This is the only place that reads environment variables, so every other
module imports its settings from here.
"""

import datetime
import os
import re

from dotenv import load_dotenv

load_dotenv()

# --- Required secrets (no default → the bot refuses to start if missing) ----
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

# --- Models (comma-separated; tried left-to-right, fallback on 429/errors) --
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "google/gemma-4-31b-it:free,"
    "google/gemma-4-26b-a4b-it:free,"
    "openai/gpt-oss-20b:free,"
    "openrouter/free",
)
MODELS = [m.strip() for m in OPENROUTER_MODEL.split(",") if m.strip()]

# --- Behaviour --------------------------------------------------------------
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "10"))
USER_COOLDOWN_SECONDS = float(os.getenv("USER_COOLDOWN_SECONDS", "5"))
DB_PATH = os.getenv("HISTORY_DB", "chat_history.db")
TZ_OFFSET_HOURS = float(os.getenv("TZ_OFFSET_HOURS", "7"))
LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=TZ_OFFSET_HOURS))
ENABLE_MEMBERS = os.getenv("ENABLE_MEMBERS", "false").lower() in ("1", "true", "yes")
GUILD_ID = os.getenv("GUILD_ID", "").strip()

# --- Voice ------------------------------------------------------------------
JOIN_SOUND = os.getenv("JOIN_SOUND", "sounds/join.mp3")
KURU_SOUND = os.getenv("KURU_SOUND", "sounds/kuru.mp3")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# --- VOICEVOX (Japanese text-to-speech) -------------------------------------
# URL of the VOICEVOX ENGINE (run it via Docker). If the bot runs on the same
# machine as the engine, localhost is correct.
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://localhost:50021")
# Speaker/style id. Find it with: GET /speakers (e.g. 3 = ずんだもん ノーマル).
VOICEVOX_SPEAKER = int(os.getenv("VOICEVOX_SPEAKER", "3"))
# When True, the bot speaks its reply aloud if it's in a voice channel.
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in ("1", "true", "yes")
# Only synthesize the first N characters (keeps CPU synthesis fast).
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "200"))

# --- Prompt / notes ---------------------------------------------------------
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are Officer (also called Herta), a chill, friendly character in a Discord server. "
    "Reply in the SAME language the user writes in (Thai to Thai, Japanese to Japanese, etc.). "
    "Keep replies to 1-2 short sentences. Be lightly playful, never rude.",
)

# Added to the system prompt ONLY when the bot is in a voice channel: it asks
# the model to also provide a Japanese spoken version (read aloud by VOICEVOX)
# while the visible text stays in the user's language.
SAY_NOTE = (
    "\n\nYou are currently in a voice channel. You MUST add a Japanese spoken version at the end of your reply. "
    "You MUST include an Emotion ID. "
    "Available IDs: 20(Normal), 66(Sexy), 77(Crying), 78(Angry), 79(Happy), 80(Relaxed).\n"
    "Format MUST be EXACTLY: [[SAY: <ID>|<Japanese_Text>]]\n"
    "[[SAY: <ID>|<Japanese_Text>]] — it is read aloud by a Japanese voice, so make it sound natural "
    "Example 1: [[SAY: 78|もう、バカ！]]\n"
    "Example 2: [[SAY: 20|嬉しいわ！]]\n"
    "DO NOT output just [[SAY: text]]. The ID number and the '|' separator are MANDATORY."
    "The chosen Emotion ID MUST strictly match the mood and tone of your text reply. (e.g., if you act angry, you MUST use 78)."
    "DO NOT put emojis, english words, or symbols inside [[SAY: ...]] block."
)

# Appended to the system prompt: explains the "Name:" labeling + fact tags.
MEMORY_NOTE = (
    "\n\nEach message in the history is prefixed with the speaker's Discord name "
    "(for example 'Alice: hello'). Remember who said what, and decide for yourself "
    "whether earlier information is relevant to the current question."
    "\n\nYou also have a long-term fact memory that is always visible to you below. "
    "To save a durable fact, output a tag like [[REMEMBER key = value]] "
    "(e.g. [[REMEMBER Alice's name = Bob]]). Reusing the same key overwrites the old "
    "value. To delete a fact, output [[FORGET key]]. Only remember durable, useful "
    "facts (names, preferences, important details) — not small talk. These tags are "
    "hidden from the user, so also reply normally in your message."
    "\n\nIf a message is unclear, ask a short, friendly clarifying question instead of "
    "only saying you don't understand."
)

# Tags the model may emit to manage its fact memory.
REMEMBER_RE = re.compile(r"\[\[\s*REMEMBER\s+(.+?)\s*=\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)
FORGET_RE = re.compile(r"\[\[\s*FORGET\s+(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)
# The Japanese spoken version for TTS (kept out of the visible text).
SAY_RE = re.compile(r"\[\[\s*SAY:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)

# Discord hard-limits a single message to 2000 characters.
DISCORD_MESSAGE_LIMIT = 2000

# Shared logger name used across all modules.
LOGGER_NAME = "discord-ai-bot"
