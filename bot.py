"""
Discord AI chatbot backed by OpenRouter (one API for many models).

Talk to the bot by @mentioning it in a channel — it forwards the recent
conversation to a model via OpenRouter and replies. Utility features are
exposed as slash (/) commands.

Memory (SQLite, survives restarts):
  * Chat history — one SHARED rolling window across all channels (with author).
  * Facts        — long-term key/value store, always shown to the model and
    UPSERTed (new value overwrites old). Managed via /remember, /forget, or the
    model's own [[REMEMBER key = value]] / [[FORGET key]] tags.

The program must be RUNNING for the bot to answer.
"""

import ast
import collections
import datetime
import logging
import operator
import os
import random
import re
import sqlite3
import time

import discord
from discord import app_commands
from dotenv import load_dotenv
from openai import AsyncOpenAI, APIStatusError

# ---------------------------------------------------------------------------
# Configuration.
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]       # required
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]     # required

# Models tried left-to-right; fallback to the next on 429/errors.
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "google/gemma-4-31b-it:free,"
    "google/gemma-4-26b-a4b-it:free,"
    "openai/gpt-oss-20b:free,"
    "openrouter/free",
)
MODELS = [m.strip() for m in OPENROUTER_MODEL.split(",") if m.strip()]

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))
# Lower temperature = more consistent, stable style; higher = more creative and
# prone to drifting. 0.5–0.7 keeps the personality steady.
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "10"))
USER_COOLDOWN_SECONDS = float(os.getenv("USER_COOLDOWN_SECONDS", "5"))
DB_PATH = os.getenv("HISTORY_DB", "chat_history.db")
TZ_OFFSET_HOURS = float(os.getenv("TZ_OFFSET_HOURS", "7"))
LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=TZ_OFFSET_HOURS))
ENABLE_MEMBERS = os.getenv("ENABLE_MEMBERS", "false").lower() in ("1", "true", "yes")
# Optional server (guild) ID to register slash commands to INSTANTLY. Leave
# empty to register globally (can take up to ~1 hour to show up in Discord).
GUILD_ID = os.getenv("GUILD_ID", "").strip()

# Sound played automatically when the bot joins a voice channel via /join.
JOIN_SOUND = os.getenv("JOIN_SOUND", "sounds/join.mp3")
# Sound played by the /kuru command.
KURU_SOUND = os.getenv("KURU_SOUND", "sounds/kuru.mp3")
# Path to the ffmpeg executable (needed to play audio). "ffmpeg" = use PATH.
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a friendly, concise assistant living in a Discord server. "
    "Keep replies short and helpful. Answer in the same language the user writes in.",
)

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

REMEMBER_RE = re.compile(r"\[\[\s*REMEMBER\s+(.+?)\s*=\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)
FORGET_RE = re.compile(r"\[\[\s*FORGET\s+(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)

DISCORD_MESSAGE_LIMIT = 2000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("discord-ai-bot")

# ---------------------------------------------------------------------------
# Clients.
# ---------------------------------------------------------------------------
ai = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    max_retries=0,  # fail fast so we can fall back to the next model ourselves
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = ENABLE_MEMBERS  # read member list + roles (opt-in, read-only)


class ChatBot(discord.Client):
    """discord.Client plus an app-command tree for slash (/) commands."""

    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Register slash commands with Discord. With GUILD_ID set they appear in
        # that server instantly; without it, a global sync can take ~1 hour.
        try:
            if GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("Slash commands synced to guild %s", GUILD_ID)
            else:
                await self.tree.sync()
                log.info("Slash commands synced globally (may take up to ~1 hour)")
        except Exception:
            log.exception("Failed to sync slash commands")


client = ChatBot()

# ---------------------------------------------------------------------------
# Persistent memory (SQLite): chat history + facts.
# ---------------------------------------------------------------------------
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
    columns = [row[1] for row in con.execute("PRAGMA table_info(messages)").fetchall()]
    if "author" not in columns:
        con.execute("ALTER TABLE messages ADD COLUMN author TEXT")
    con.commit()
    con.close()


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
    author = entry.get("author")
    if author:
        return {"role": entry["role"], "content": f"{author}: {entry['content']}"}
    return {"role": entry["role"], "content": entry["content"]}


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

# Last time each user triggered the AI chat, for the per-user cooldown.
last_request_time: dict[int, float] = {}


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _clean_content(message: discord.Message) -> str:
    """Strip the bot's @mention out of the text so the model sees a clean prompt."""
    text = message.content
    for mention in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        text = text.replace(mention, "")
    return text.strip()


def _split_message(text: str) -> list[str]:
    """Split a long reply into <=2000 char chunks Discord will accept."""
    return [
        text[i : i + DISCORD_MESSAGE_LIMIT]
        for i in range(0, len(text), DISCORD_MESSAGE_LIMIT)
    ] or ["(empty)"]


async def _send_interaction(interaction: discord.Interaction, text: str, ephemeral: bool = False):
    """Reply to a slash command, splitting long text across follow-ups."""
    chunks = _split_message(text)
    await interaction.response.send_message(chunks[0], ephemeral=ephemeral)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=ephemeral)


def _server_roster(guild) -> str:
    """Roles + members (with mention tags and each member's roles) for the model."""
    if not ENABLE_MEMBERS or guild is None:
        return ""
    all_roles = [r for r in guild.roles if r.name != "@everyone"]
    role_txt = ", ".join(f"{r.name} (<@&{r.id}>)" for r in all_roles) or "(none)"
    members = [m for m in guild.members if not m.bot]
    shown = members[:50]
    lines = []
    for m in shown:
        member_roles = [r.name for r in m.roles if r.name != "@everyone"]
        lines.append(f"- {m.display_name} (<@{m.id}>): {', '.join(member_roles) or 'no roles'}")
    member_txt = "\n".join(lines) or "(none)"
    more = f"\n...and {len(members) - len(shown)} more" if len(members) > len(shown) else ""
    return (
        f"\n\nThis server is '{guild.name}'. To mention/ping someone, output their "
        f"exact tag (e.g. <@123>).\n"
        f"All roles: {role_txt}\n"
        f"Members and their roles:\n{member_txt}{more}"
    )


# Keywords that suggest the message needs the member/role roster. Only then do
# we attach it, to avoid wasting hundreds of tokens on unrelated questions.
_ROSTER_KEYWORDS = (
    "ใคร", "role", "บทบาท", "สมาชิก", "member", "admin", "แอดมิน", "mod",
    "mention", "เมนชั่น", "แท็ก", "tag", "เรียก", "ยศ", "who",
)


def _needs_roster(user_text: str) -> bool:
    """Heuristic: does this message likely need the member/role list?"""
    if "<@" in user_text:  # already contains a mention
        return True
    lowered = user_text.lower()
    return any(k in lowered for k in _ROSTER_KEYWORDS)


async def _ask_model(user_text: str, author: str, guild) -> str:
    """Send system prompt + facts + roster + history + message; fall back on errors."""
    now = datetime.datetime.now(LOCAL_TZ)
    time_note = (
        f"\n\nThe current date and time is {now:%Y-%m-%d %H:%M} ({now:%A}). "
        "This is the real current time — use it whenever asked about the date or time."
    )
    # Only attach the (potentially large) roster when the question seems to need it.
    roster = _server_roster(guild) if _needs_roster(user_text) else ""
    system_content = SYSTEM_PROMPT + MEMORY_NOTE + time_note + roster + _facts_block()
    messages = [{"role": "system", "content": system_content}]
    messages.extend(_format_for_model(e) for e in shared_history)
    messages.append({"role": "user", "content": f"{author}: {user_text}"})

    last_error: Exception | None = None
    for model in MODELS:
        try:
            response = await ai.chat.completions.create(
                model=model,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=messages,
            )
        except APIStatusError as exc:
            log.warning("Model %s unavailable (%s), trying next...", model, exc.status_code)
            last_error = exc
            continue

        raw = (response.choices[0].message.content or "").strip()
        reply = _apply_and_strip_facts(raw)
        if not reply:
            reply = "Got it! 👍"

        shared_history.append({"role": "user", "content": user_text, "author": author})
        shared_history.append({"role": "assistant", "content": reply, "author": None})
        _save_message("user", user_text, author)
        _save_message("assistant", reply, None)

        usage = response.usage
        if usage:
            log.info("Reply via %s: in=%s out=%s tokens",
                     model, usage.prompt_tokens, usage.completion_tokens)
        return reply

    raise last_error if last_error else RuntimeError("No models configured")


# ---------------------------------------------------------------------------
# Fun / utility commands — pure Python, no AI call.
# ---------------------------------------------------------------------------
_CALC_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_calc(expr: str) -> float:
    """Evaluate a basic arithmetic expression safely (no eval())."""
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
            left, right = _eval(node.left), _eval(node.right)
            if type(node.op) is ast.Pow and (abs(right) > 100 or abs(left) > 1_000_000):
                raise ValueError("number too large")
            return _CALC_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _CALC_OPS:
            return _CALC_OPS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    return _eval(ast.parse(expr, mode="eval").body)


def _cmd_roll(arg: str) -> str:
    """!roll -> d6, 20 -> d20, 2d6 -> two d6 summed."""
    arg = arg.strip().lower().replace(" ", "")
    try:
        if not arg:
            count, sides = 1, 6
        elif "d" in arg:
            left, _, right = arg.partition("d")
            count = int(left) if left else 1
            sides = int(right)
        else:
            count, sides = 1, int(arg)
    except ValueError:
        return "รูปแบบไม่ถูก ลอง: `20` หรือ `2d6`"
    if not (1 <= count <= 100) or not (2 <= sides <= 1000):
        return "จำนวนลูก 1-100, หน้า 2-1000 เท่านั้นนะ"
    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        return f"🎲 ทอย d{sides} ได้ **{rolls[0]}**"
    return f"🎲 ทอย {count}d{sides}: {' + '.join(map(str, rolls))} = **{sum(rolls)}**"


def _cmd_pick(arg: str) -> str:
    options = [o.strip() for o in (arg.split(",") if "," in arg else arg.split())]
    options = [o for o in options if o]
    if len(options) < 2:
        return "ใส่ตัวเลือกอย่างน้อย 2 อันนะ เช่น `กิน นอน เที่ยว`"
    return f"🎯 ฉันเลือก: **{random.choice(options)}**"


def _cmd_calc(arg: str) -> str:
    expr = arg.strip()
    if not expr:
        return "ใส่โจทย์ด้วยนะ เช่น `12*7+3`"
    try:
        result = _safe_calc(expr)
    except Exception:
        return "คำนวณไม่ได้ ใช้ได้แค่ + - * / % ** และวงเล็บ เช่น `(5+3)*2`"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"🧮 {expr} = **{result}**"


def _play_sound(voice_client, path: str) -> str:
    """Play a sound file through the voice client. Returns a note if it couldn't."""
    if not path:
        return ""
    if not os.path.isfile(path):
        return f"\n(ยังไม่มีไฟล์เสียง `{path}` — ข้ามการเล่นเสียง)"
    try:
        if voice_client.is_playing():
            voice_client.stop()
        source = discord.FFmpegPCMAudio(path, executable=FFMPEG_PATH)
        voice_client.play(source)
    except FileNotFoundError:
        return "\n(เล่นเสียงไม่ได้ — ยังไม่ได้ลง FFmpeg)"
    except Exception:
        log.exception("Failed to play sound %s", path)
        return "\n(เล่นเสียงไม่ได้ ลองดู log)"
    return ""


HELP_TEXT = (
    "**สิ่งที่ฉันทำได้:**\n"
    "• พิมพ์ @ฉัน ตามด้วยข้อความ — คุยกันได้เลย 💬\n"
    "• `/remember` — สั่งให้ฉันจำ (ทับของเก่า)\n"
    "• `/forget` — ลบสิ่งที่ฉันจำไว้\n"
    "• `/facts` — ดูว่าฉันจำอะไรไว้บ้าง\n"
    "• `/roll` — ทอยลูกเต๋า 🎲\n"
    "• `/pick` — สุ่มเลือกให้ 🎯\n"
    "• `/calc` — เครื่องคิดเลข 🧮\n"
    "• `/roles` / `/members` — ดูบทบาท/สมาชิก 👥\n"
    "• `/whoami` — เช็กบทบาทของตัวเอง\n"
    "• `/join` / `/leave` — เข้า/ออกช่องเสียง 🔊\n"
    "• `/kuru` — เล่นเสียง kuru 🔊\n"
    "• `/help` — แสดงเมนูนี้"
)


# ---------------------------------------------------------------------------
# Slash (/) commands.
# ---------------------------------------------------------------------------
@client.tree.command(name="help", description="แสดงเมนูคำสั่งทั้งหมด")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT, ephemeral=True)


@client.tree.command(name="roll", description="ทอยลูกเต๋า")
@app_commands.describe(dice="เช่น 20, 2d6 หรือเว้นว่างเพื่อทอย d6")
async def slash_roll(interaction: discord.Interaction, dice: str = ""):
    await interaction.response.send_message(_cmd_roll(dice))


@client.tree.command(name="pick", description="สุ่มเลือกจากรายการ")
@app_commands.describe(options="ตัวเลือกคั่นด้วยเว้นวรรคหรือจุลภาค เช่น กิน นอน เที่ยว")
async def slash_pick(interaction: discord.Interaction, options: str):
    await interaction.response.send_message(_cmd_pick(options))


@client.tree.command(name="calc", description="เครื่องคิดเลข")
@app_commands.describe(expression="เช่น 12*7+3")
async def slash_calc(interaction: discord.Interaction, expression: str):
    await interaction.response.send_message(_cmd_calc(expression))


@client.tree.command(name="remember", description="สั่งให้บอทจำข้อมูล (ทับของเก่า)")
@app_commands.describe(key="หัวข้อ เช่น รหัสลับ", value="ค่า เช่น 2547")
async def slash_remember(interaction: discord.Interaction, key: str, value: str):
    _set_fact(key.strip(), value.strip())
    await interaction.response.send_message(f"จำแล้ว ✅  {key.strip()} = {value.strip()}")


@client.tree.command(name="forget", description="ลบข้อมูลที่บอทจำไว้")
@app_commands.describe(key="หัวข้อที่จะลบ")
async def slash_forget(interaction: discord.Interaction, key: str):
    _delete_fact(key.strip())
    await interaction.response.send_message(f"ลบแล้ว 🗑️  {key.strip()}")


@client.tree.command(name="facts", description="ดูข้อมูลที่บอทจำไว้ทั้งหมด")
async def slash_facts(interaction: discord.Interaction):
    facts = _all_facts()
    if not facts:
        await interaction.response.send_message("ฉันยังไม่ได้จำอะไรไว้เลย", ephemeral=True)
        return
    listing = "\n".join(f"• {k}: {v}" for k, v in facts)
    await _send_interaction(interaction, "**สิ่งที่ฉันจำไว้:**\n" + listing, ephemeral=True)


@client.tree.command(name="roles", description="ดูรายชื่อบทบาททั้งหมด")
async def slash_roles(interaction: discord.Interaction):
    if not ENABLE_MEMBERS or interaction.guild is None:
        await interaction.response.send_message(
            "ยังเข้าถึง role ไม่ได้ — ต้องตั้ง `ENABLE_MEMBERS=true` และเปิด SERVER MEMBERS INTENT",
            ephemeral=True,
        )
        return
    roles = [r.name for r in interaction.guild.roles if r.name != "@everyone"]
    await interaction.response.send_message("**Roles:** " + (", ".join(roles) or "(ไม่มี)"))


@client.tree.command(name="members", description="ดูรายชื่อสมาชิก")
async def slash_members(interaction: discord.Interaction):
    if not ENABLE_MEMBERS or interaction.guild is None:
        await interaction.response.send_message(
            "ยังเข้าถึงรายชื่อสมาชิกไม่ได้ — ต้องตั้ง `ENABLE_MEMBERS=true` และเปิด SERVER MEMBERS INTENT",
            ephemeral=True,
        )
        return
    names = [m.display_name for m in interaction.guild.members if not m.bot]
    await _send_interaction(interaction, "**Members:** " + (", ".join(names) or "(ไม่มี)"))


@client.tree.command(name="whoami", description="เช็กบทบาทของตัวเอง")
async def slash_whoami(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("ใช้ในเซิร์ฟเวอร์เท่านั้นนะ", ephemeral=True)
        return
    my_roles = [r.name for r in interaction.user.roles if r.name != "@everyone"]
    await interaction.response.send_message(
        f"**{interaction.user.display_name}** มีบทบาท: " + (", ".join(my_roles) or "(ไม่มีบทบาทพิเศษ)"),
        ephemeral=True,
    )


@client.tree.command(name="join", description="ให้บอทเข้าช่องเสียงที่คุณอยู่ แล้วเล่นเสียงทักทาย")
async def slash_join(interaction: discord.Interaction):
    # The caller must be in a voice channel; the bot joins the same one.
    voice_state = getattr(interaction.user, "voice", None)
    if interaction.guild is None or voice_state is None or voice_state.channel is None:
        await interaction.response.send_message(
            "เข้าช่องเสียงก่อนนะ แล้วค่อยเรียกฉัน 🔊", ephemeral=True
        )
        return
    channel = voice_state.channel
    existing = interaction.guild.voice_client
    # Already in the same channel? Do nothing (guards against /join spam).
    if existing is not None and existing.channel == channel:
        await interaction.response.send_message(
            "ฉันอยู่ในช่องนี้อยู่แล้วนะ 🔊", ephemeral=True
        )
        return
    try:
        if existing is not None:
            await existing.move_to(channel)
        else:
            await channel.connect()
    except discord.Forbidden:
        await interaction.response.send_message(
            "ฉันไม่มีสิทธิ์เข้าช่องนี้ (ต้องมี Connect + Speak)", ephemeral=True
        )
        return
    except Exception:
        log.exception("Failed to join voice channel")
        await interaction.response.send_message("เข้าช่องเสียงไม่สำเร็จ ลองใหม่นะ", ephemeral=True)
        return
    note = _play_sound(interaction.guild.voice_client, JOIN_SOUND)
    await interaction.response.send_message(f"เข้าช่อง **{channel.name}** แล้ว 🔊{note}")


@client.tree.command(name="leave", description="ให้บอทออกจากช่องเสียง")
async def slash_leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client is None:
        await interaction.response.send_message("ฉันไม่ได้อยู่ในช่องเสียงนะ", ephemeral=True)
        return
    await voice_client.disconnect()
    await interaction.response.send_message("ออกจากช่องเสียงแล้ว 👋")


@client.tree.command(name="kuru", description="เล่นเสียง kuru 🔊")
async def slash_kuru(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("ใช้ในเซิร์ฟเวอร์เท่านั้นนะ", ephemeral=True)
        return
    voice_client = interaction.guild.voice_client
    # Not in voice yet? Join the caller's channel first.
    if voice_client is None:
        voice_state = getattr(interaction.user, "voice", None)
        if voice_state is None or voice_state.channel is None:
            await interaction.response.send_message(
                "เข้าช่องเสียงก่อน หรือเรียก /join นะ", ephemeral=True
            )
            return
        try:
            voice_client = await voice_state.channel.connect()
        except discord.Forbidden:
            await interaction.response.send_message(
                "ฉันไม่มีสิทธิ์เข้าช่องนี้ (ต้องมี Connect + Speak)", ephemeral=True
            )
            return
        except Exception:
            log.exception("Failed to join for /kuru")
            await interaction.response.send_message("เข้าช่องเสียงไม่สำเร็จ", ephemeral=True)
            return
    note = _play_sound(voice_client, KURU_SOUND)
    await interaction.response.send_message(f"kuru~ 🔊{note}")


# ---------------------------------------------------------------------------
# Events.
# ---------------------------------------------------------------------------
@client.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", client.user, client.user.id)
    log.info("Models (tried in order): %s", ", ".join(MODELS))
    log.info("Loaded %s past messages, %s facts from %s",
             len(shared_history), len(_all_facts()), DB_PATH)
    log.info("Bot is online. Mention it to chat, or use / commands.")


@client.event
async def on_message(message: discord.Message):
    """Handle the @mention AI chat. Slash commands are handled separately."""
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
    if now - previous < USER_COOLDOWN_SECONDS:
        wait = USER_COOLDOWN_SECONDS - (now - previous)
        await message.reply(f"Please wait {wait:.1f}s before asking again.", mention_author=False)
        return
    last_request_time[message.author.id] = now

    author_name = message.author.display_name  # server nickname if set

    try:
        async with message.channel.typing():
            reply = await _ask_model(user_text, author_name, message.guild)
    except APIStatusError as exc:
        log.exception("OpenRouter API error")
        if exc.status_code == 429:
            if "free-models-per-day" in str(exc):
                # Daily free quota exhausted — tell them when it resets (00:00 UTC).
                total_min = int(round(TZ_OFFSET_HOURS * 60)) % (24 * 60)
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

    for chunk in _split_message(reply):
        await message.reply(chunk, mention_author=False)


def main():
    client.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
