"""
Discord AI chatbot backed by OpenRouter (one API for many models).

The bot listens for messages in any server it has joined. When someone
@mentions the bot, it forwards the recent conversation to a model via
OpenRouter and posts the reply back into the same channel.

Memory (two layers, both in SQLite so they survive restarts):
  * Chat history  — one SHARED rolling window across all channels (last
    HISTORY_LIMIT messages), each tagged with WHO said it.
  * Facts         — a long-term key/value store. Facts are ALWAYS shown to
    the model (never scroll out of the window) and are UPSERTed, so a new
    value overwrites the old one (e.g. name A -> name B). The model manages
    facts itself via [[REMEMBER key = value]] / [[FORGET key]] tags, and you
    can inspect/edit them with the !facts and !forget commands.

High-level flow (compare to a ROS node):
    Discord gateway  --on_message-->  our handler  --> OpenRouter --> reply
    (the "topic")       (the "callback")               (the "service call")

The program must be RUNNING for the bot to answer. If this process stops,
the bot goes offline (but its memory is kept in the DB).
"""

import collections
import logging
import os
import re
import sqlite3
import time

import discord
from dotenv import load_dotenv
from openai import AsyncOpenAI, APIStatusError

# ---------------------------------------------------------------------------
# Configuration: load secrets/settings from the .env file into os.environ.
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]       # required
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]     # required

# One or more models, comma-separated. Tried in order: the first is the
# primary, the rest are fallbacks used only when the previous one is
# rate-limited (429) or errors.
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "google/gemma-4-31b-it:free,"
    "google/gemma-4-26b-a4b-it:free,"
    "openai/gpt-oss-20b:free,"
    "openrouter/free",
)
MODELS = [m.strip() for m in OPENROUTER_MODEL.split(",") if m.strip()]

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "10"))
USER_COOLDOWN_SECONDS = float(os.getenv("USER_COOLDOWN_SECONDS", "5"))
DB_PATH = os.getenv("HISTORY_DB", "chat_history.db")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a friendly, concise assistant living in a Discord server. "
    "Keep replies short and helpful. Answer in the same language the user writes in.",
)

# Instruction appended to the system prompt: how the "Name: message" labeling
# works and how to use the long-term fact memory.
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

# Regexes to pull memory tags out of the model's reply.
REMEMBER_RE = re.compile(r"\[\[\s*REMEMBER\s+(.+?)\s*=\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)
FORGET_RE = re.compile(r"\[\[\s*FORGET\s+(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)

# Discord hard-limits a single message to 2000 characters.
DISCORD_MESSAGE_LIMIT = 2000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("discord-ai-bot")

# ---------------------------------------------------------------------------
# Clients.
# ---------------------------------------------------------------------------
ai = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    # Fail fast on errors so we can quickly fall back to the next model in
    # our own MODELS list instead of the SDK retrying the same busy model.
    max_retries=0,
)

# We need the MESSAGE CONTENT intent to read what users type (also enable it
# in the Discord Developer Portal — see README).
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ---------------------------------------------------------------------------
# Persistent memory (SQLite): chat history + facts.
# ---------------------------------------------------------------------------
# Recent shared chat history kept in memory for speed; every turn is also
# mirrored into the DB. Each entry is {"role", "content", "author"}.
shared_history: collections.deque = collections.deque(maxlen=HISTORY_LIMIT)


def _init_db() -> None:
    """Create the messages + facts tables (and migrate old DBs)."""
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  role TEXT NOT NULL,"
        "  content TEXT NOT NULL,"
        "  author TEXT,"
        "  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS facts ("
        "  key TEXT PRIMARY KEY,"
        "  value TEXT NOT NULL,"
        "  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    # Migrate: if an older messages table predates the `author` column, add it.
    columns = [row[1] for row in con.execute("PRAGMA table_info(messages)").fetchall()]
    if "author" not in columns:
        con.execute("ALTER TABLE messages ADD COLUMN author TEXT")
    con.commit()
    con.close()


# --- chat history helpers --------------------------------------------------
def _save_message(role: str, content: str, author: str | None) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO messages (role, content, author) VALUES (?, ?, ?)",
        (role, content, author),
    )
    con.commit()
    con.close()


def _load_recent(limit: int) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT role, content, author FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [{"role": r, "content": c, "author": a} for r, c, a in reversed(rows)]


def _format_for_model(entry: dict) -> dict:
    """Convert a stored entry to an OpenAI message, prefixing the speaker name."""
    author = entry.get("author")
    if author:
        return {"role": entry["role"], "content": f"{author}: {entry['content']}"}
    return {"role": entry["role"], "content": entry["content"]}


# --- fact helpers ----------------------------------------------------------
def _set_fact(key: str, value: str) -> None:
    """Insert or overwrite a fact (upsert on key)."""
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (key, value),
    )
    con.commit()
    con.close()


def _delete_fact(key: str) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM facts WHERE key = ?", (key,))
    con.commit()
    con.close()


def _all_facts() -> list[tuple[str, str]]:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT key, value FROM facts ORDER BY key").fetchall()
    con.close()
    return rows


def _facts_block() -> str:
    """A text block of all facts, injected into the system prompt every call."""
    facts = _all_facts()
    if not facts:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in facts)
    return "\n\nKnown facts you remember (authoritative, always up to date):\n" + lines


def _apply_and_strip_facts(reply: str) -> str:
    """Apply any [[REMEMBER]]/[[FORGET]] tags to the DB, then remove them."""
    for key, value in REMEMBER_RE.findall(reply):
        _set_fact(key.strip(), value.strip())
        log.info("Remembered fact: %s = %s", key.strip(), value.strip())
    for key in FORGET_RE.findall(reply):
        _delete_fact(key.strip())
        log.info("Forgot fact: %s", key.strip())
    reply = REMEMBER_RE.sub("", reply)
    reply = FORGET_RE.sub("", reply)
    return reply.strip()


# Prepare the DB and warm the in-memory history from it at startup.
_init_db()
for _msg in _load_recent(HISTORY_LIMIT):
    shared_history.append(_msg)

# Last time each user triggered the bot, for the per-user cooldown.
last_request_time: dict[int, float] = {}


def _clean_content(message: discord.Message) -> str:
    """Strip the bot's @mention out of the text so the model sees a clean prompt."""
    text = message.content
    for mention in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        text = text.replace(mention, "")
    return text.strip()


async def _ask_model(user_text: str, author: str) -> str:
    """Send system prompt + facts + shared history + new message to OpenRouter.

    Tries each model in MODELS in order, falling through on 429/errors.
    """
    system_content = SYSTEM_PROMPT + MEMORY_NOTE + _facts_block()
    messages = [{"role": "system", "content": system_content}]
    messages.extend(_format_for_model(e) for e in shared_history)
    messages.append({"role": "user", "content": f"{author}: {user_text}"})

    last_error: Exception | None = None
    for model in MODELS:
        try:
            response = await ai.chat.completions.create(
                model=model,
                max_tokens=MAX_TOKENS,
                messages=messages,
            )
        except APIStatusError as exc:
            log.warning("Model %s unavailable (%s), trying next...", model, exc.status_code)
            last_error = exc
            continue

        raw = (response.choices[0].message.content or "").strip()
        # Apply memory tags to the DB and remove them from the visible reply.
        reply = _apply_and_strip_facts(raw)
        if not reply:
            reply = "Got it! 👍"  # model only emitted memory tags

        # Update rolling history (visible reply only) + persist to the DB.
        shared_history.append({"role": "user", "content": user_text, "author": author})
        shared_history.append({"role": "assistant", "content": reply, "author": None})
        _save_message("user", user_text, author)
        _save_message("assistant", reply, None)

        usage = response.usage
        if usage:
            log.info(
                "Reply via %s: in=%s out=%s tokens",
                model, usage.prompt_tokens, usage.completion_tokens,
            )
        return reply

    raise last_error if last_error else RuntimeError("No models configured")


def _split_message(text: str) -> list[str]:
    """Split a long reply into <=2000 char chunks Discord will accept."""
    return [
        text[i : i + DISCORD_MESSAGE_LIMIT]
        for i in range(0, len(text), DISCORD_MESSAGE_LIMIT)
    ] or ["(empty)"]


@client.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", client.user, client.user.id)
    log.info("Models (tried in order): %s", ", ".join(MODELS))
    log.info("Loaded %s past messages, %s facts from %s",
             len(shared_history), len(_all_facts()), DB_PATH)
    log.info("Bot is online. Mention it in a channel to chat.")


@client.event
async def on_message(message: discord.Message):
    # 1. Never reply to ourselves (or other bots) — avoids infinite loops.
    if message.author.bot:
        return

    # 2. Only respond when the bot is @mentioned.
    if client.user not in message.mentions:
        return

    user_text = _clean_content(message)
    if not user_text:
        await message.reply("Hi! Ask me something 🙂", mention_author=False)
        return

    # 3. Per-user cooldown: cheap protection against spam and runaway usage.
    now = time.monotonic()
    previous = last_request_time.get(message.author.id, 0.0)
    if now - previous < USER_COOLDOWN_SECONDS:
        wait = USER_COOLDOWN_SECONDS - (now - previous)
        await message.reply(
            f"Please wait {wait:.1f}s before asking again.", mention_author=False
        )
        return
    last_request_time[message.author.id] = now

    # 4. Manual commands (handled here — no API call, so they never hit 429).
    lowered = user_text.lower()

    # !help — list what the bot can do.
    if lowered in ("!help", "!commands"):
        help_text = (
            "**สิ่งที่ฉันทำได้:**\n"
            "• พิมพ์ @ฉัน ตามด้วยข้อความ — คุยกันได้เลย 💬\n"
            "• `!remember หัวข้อ = ค่า` — สั่งให้ฉันจำ (มีแล้วทับของเก่า)\n"
            "• `!forget หัวข้อ` — ลบสิ่งที่ฉันจำไว้\n"
            "• `!facts` — ดูว่าฉันจำอะไรไว้บ้าง\n"
            "• `!help` — แสดงเมนูนี้"
        )
        await message.reply(help_text, mention_author=False)
        return

    # !facts / !fact — show everything the bot has remembered.
    if lowered in ("!facts", "!fact"):
        facts = _all_facts()
        if not facts:
            await message.reply("ฉันยังไม่ได้จำอะไรไว้เลย ลองสั่ง `!remember` ดูสิ", mention_author=False)
        else:
            listing = "\n".join(f"• {k}: {v}" for k, v in facts)
            for chunk in _split_message("**สิ่งที่ฉันจำไว้:**\n" + listing):
                await message.reply(chunk, mention_author=False)
        return

    # !remember key = value — manually save/overwrite a fact (reliable, no AI).
    if lowered.startswith("!remember "):
        payload = user_text[len("!remember "):]
        if "=" not in payload:
            await message.reply(
                "ใช้แบบนี้นะ: `!remember หัวข้อ = ค่า`\nเช่น `!remember รหัสลับ = 2547`",
                mention_author=False,
            )
        else:
            key, value = payload.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and value:
                _set_fact(key, value)
                await message.reply(f"จำแล้ว ✅  {key} = {value}", mention_author=False)
            else:
                await message.reply("หัวข้อหรือค่าว่างเปล่า ลองใหม่นะ", mention_author=False)
        return

    # !forget key — delete a fact.
    if lowered.startswith("!forget "):
        key = user_text[len("!forget "):].strip()
        _delete_fact(key)
        await message.reply(f"ลบแล้ว 🗑️  {key}", mention_author=False)
        return

    author_name = message.author.display_name  # server nickname if set

    # 5. Call the model. "typing" shows the animated indicator while we wait.
    try:
        async with message.channel.typing():
            reply = await _ask_model(user_text, author_name)
    except APIStatusError as exc:
        log.exception("OpenRouter API error")
        await message.reply(
            f"Sorry, the AI service returned an error ({exc.status_code}).",
            mention_author=False,
        )
        return
    except Exception:
        log.exception("Unexpected error while handling a message")
        await message.reply(
            "Sorry, something went wrong while contacting the AI.",
            mention_author=False,
        )
        return

    # 6. Send the reply back, split across multiple messages if needed.
    for chunk in _split_message(reply):
        await message.reply(chunk, mention_author=False)


def main():
    client.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
