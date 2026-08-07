"""
Discord AI chatbot backed by OpenRouter — entry point.

This file is intentionally thin: it builds the Discord client, wires up the
events (@mention AI chat) and the slash commands, and runs. All the real logic
lives in the `botcore` package:

    botcore.config   — settings from .env
    botcore.memory   — SQLite chat history + facts + server roster
    botcore.ai       — OpenRouter client + ask_model()
    botcore.sounds   — playing audio in voice channels
    botcore.dcutils  — Discord message helpers
    botcore.slash    — all the slash (/) commands

Talk to the bot by @mentioning it in a channel; use / for utility commands.
"""

import logging
import time

import discord
from discord import app_commands
from openai import APIStatusError

from botcore import ai, config, dcutils, memory, slash, tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(config.LOGGER_NAME)

# ---------------------------------------------------------------------------
# Discord client (with a slash-command tree).
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = config.ENABLE_MEMBERS  # read member list + roles (opt-in, read-only)


class ChatBot(discord.Client):
    """discord.Client plus an app-command tree for slash (/) commands."""

    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Register slash commands, then sync them with Discord. With GUILD_ID
        # set they appear instantly; without it, a global sync can take ~1 hour.
        slash.register(self)
        try:
            if config.GUILD_ID:
                guild = discord.Object(id=int(config.GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("Slash commands synced to guild %s", config.GUILD_ID)
            else:
                await self.tree.sync()
                log.info("Slash commands synced globally (may take up to ~1 hour)")
        except Exception:
            log.exception("Failed to sync slash commands")


client = ChatBot()

# Prepare persistent memory before the bot connects.
memory.init_db()
memory.load_history()

# Last time each user triggered the AI chat, for the per-user cooldown.
last_request_time: dict[int, float] = {}


def _clean_content(message: discord.Message) -> str:
    """Strip the bot's @mention out of the text so the model sees a clean prompt."""
    text = message.content
    for mention in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        text = text.replace(mention, "")
    return text.strip()


# ---------------------------------------------------------------------------
# Events.
# ---------------------------------------------------------------------------
@client.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", client.user, client.user.id)
    log.info("Models (tried in order): %s", ", ".join(config.MODELS))
    log.info("Loaded %s past messages, %s facts from %s",
             len(memory.shared_history), len(memory.all_facts()), config.DB_PATH)
    log.info("Bot is online. Mention it to chat, or use / commands.")


@client.event
async def on_message(message: discord.Message):
    """Handle the @mention AI chat. Slash commands are handled by the tree."""
    if message.author.bot:
        return
    if client.user not in message.mentions:
        return

    user_text = _clean_content(message)
    if not user_text:
        await message.reply("Hi! Ask me something 🙂", mention_author=False)
        return

    # Per-user cooldown: cheap protection against spam and runaway usage.
    now = time.monotonic()
    previous = last_request_time.get(message.author.id, 0.0)
    if now - previous < config.USER_COOLDOWN_SECONDS:
        wait = config.USER_COOLDOWN_SECONDS - (now - previous)
        await message.reply(f"Please wait {wait:.1f}s before asking again.", mention_author=False)
        return
    last_request_time[message.author.id] = now

    author_name = message.author.display_name  # server nickname if set

    try:
        async with message.channel.typing():
            reply = await ai.ask_model(user_text, author_name, message.guild)
    except APIStatusError as exc:
        log.exception("OpenRouter API error")
        if exc.status_code == 429:
            if "free-models-per-day" in str(exc):
                total_min = int(round(config.TZ_OFFSET_HOURS * 60)) % (24 * 60)
                rh, rm = divmod(total_min, 60)
                await message.reply(
                    f"โควตาฟรีวันนี้หมดแล้ว 😴 เดี๋ยวรีเซ็ตราวๆ {rh:02d}:{rm:02d} น. นะ~\n"
                    "ระหว่างนี้ใช้ `/roll` `/calc` `/whoami` และคำสั่งอื่นที่ไม่ใช้ AI ได้เลย",
                    mention_author=False,
                )
            else:
                await message.reply(
                    "ตอนนี้โมเดลไม่ว่าง (คิวเต็ม) รอสักครู่แล้วลองใหม่นะ 🙏",
                    mention_author=False,
                )
            return
        await message.reply(
            f"ขอโทษนะ ระบบ AI มีปัญหา (error {exc.status_code}) ลองใหม่อีกทีนะ",
            mention_author=False,
        )
        return
    except Exception:
        log.exception("Unexpected error while handling a message")
        await message.reply(
            "ขอโทษนะ มีบางอย่างผิดพลาดตอนติดต่อ AI ลองใหม่อีกทีนะ", mention_author=False
        )
        return

    for chunk in dcutils.split_message(reply):
        await message.reply(chunk, mention_author=False)

    # If the bot is in a voice channel in this server, speak the reply (VOICEVOX).
    if message.guild is not None and message.guild.voice_client is not None:
        await tts.speak(message.guild.voice_client, reply)


def main():
    client.run(config.DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
