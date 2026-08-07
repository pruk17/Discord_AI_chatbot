"""All slash (/) commands, plus their pure-Python helpers.

Call register(client) once (from setup_hook) to attach every command to the
bot's command tree. To add a new command, define it inside register() with an
@tree.command(...) decorator — nothing else needs to change.
"""

import ast
import logging
import operator
import random

import discord
from discord import app_commands

from . import config, dcutils, memory, sounds

log = logging.getLogger(config.LOGGER_NAME)

# ---------------------------------------------------------------------------
# Pure-Python command logic (no AI, no Discord objects) — easy to unit test.
# ---------------------------------------------------------------------------
_CALC_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def safe_calc(expr: str) -> float:
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


def cmd_roll(arg: str) -> str:
    """roll -> d6, 20 -> d20, 2d6 -> two d6 summed."""
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


def cmd_pick(arg: str) -> str:
    options = [o.strip() for o in (arg.split(",") if "," in arg else arg.split())]
    options = [o for o in options if o]
    if len(options) < 2:
        return "ใส่ตัวเลือกอย่างน้อย 2 อันนะ เช่น `กิน นอน เที่ยว`"
    return f"🎯 ฉันเลือก: **{random.choice(options)}**"


def cmd_calc(arg: str) -> str:
    expr = arg.strip()
    if not expr:
        return "ใส่โจทย์ด้วยนะ เช่น `12*7+3`"
    try:
        result = safe_calc(expr)
    except Exception:
        return "คำนวณไม่ได้ ใช้ได้แค่ + - * / % ** และวงเล็บ เช่น `(5+3)*2`"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"🧮 {expr} = **{result}**"


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
# Registration: attach every command to the bot's command tree.
# ---------------------------------------------------------------------------
def register(client: discord.Client) -> None:
    tree = client.tree

    @tree.command(name="help", description="แสดงเมนูคำสั่งทั้งหมด")
    async def slash_help(interaction: discord.Interaction):
        await interaction.response.send_message(HELP_TEXT, ephemeral=True)

    @tree.command(name="roll", description="ทอยลูกเต๋า")
    @app_commands.describe(dice="เช่น 20, 2d6 หรือเว้นว่างเพื่อทอย d6")
    async def slash_roll(interaction: discord.Interaction, dice: str = ""):
        await interaction.response.send_message(cmd_roll(dice))

    @tree.command(name="pick", description="สุ่มเลือกจากรายการ")
    @app_commands.describe(options="ตัวเลือกคั่นด้วยเว้นวรรคหรือจุลภาค เช่น กิน นอน เที่ยว")
    async def slash_pick(interaction: discord.Interaction, options: str):
        await interaction.response.send_message(cmd_pick(options))

    @tree.command(name="calc", description="เครื่องคิดเลข")
    @app_commands.describe(expression="เช่น 12*7+3")
    async def slash_calc(interaction: discord.Interaction, expression: str):
        await interaction.response.send_message(cmd_calc(expression))

    @tree.command(name="remember", description="สั่งให้บอทจำข้อมูล (ทับของเก่า)")
    @app_commands.describe(key="หัวข้อ เช่น รหัสลับ", value="ค่า เช่น 2547")
    async def slash_remember(interaction: discord.Interaction, key: str, value: str):
        memory.set_fact(key.strip(), value.strip())
        await interaction.response.send_message(f"จำแล้ว ✅  {key.strip()} = {value.strip()}")

    @tree.command(name="forget", description="ลบข้อมูลที่บอทจำไว้")
    @app_commands.describe(key="หัวข้อที่จะลบ")
    async def slash_forget(interaction: discord.Interaction, key: str):
        memory.delete_fact(key.strip())
        await interaction.response.send_message(f"ลบแล้ว 🗑️  {key.strip()}")

    @tree.command(name="facts", description="ดูข้อมูลที่บอทจำไว้ทั้งหมด")
    async def slash_facts(interaction: discord.Interaction):
        facts = memory.all_facts()
        if not facts:
            await interaction.response.send_message("ฉันยังไม่ได้จำอะไรไว้เลย", ephemeral=True)
            return
        listing = "\n".join(f"• {k}: {v}" for k, v in facts)
        await dcutils.send_interaction(interaction, "**สิ่งที่ฉันจำไว้:**\n" + listing, ephemeral=True)

    @tree.command(name="roles", description="ดูรายชื่อบทบาททั้งหมด")
    async def slash_roles(interaction: discord.Interaction):
        if not config.ENABLE_MEMBERS or interaction.guild is None:
            await interaction.response.send_message(
                "ยังเข้าถึง role ไม่ได้ — ต้องตั้ง `ENABLE_MEMBERS=true` และเปิด SERVER MEMBERS INTENT",
                ephemeral=True,
            )
            return
        roles = [r.name for r in interaction.guild.roles if r.name != "@everyone"]
        await interaction.response.send_message("**Roles:** " + (", ".join(roles) or "(ไม่มี)"))

    @tree.command(name="members", description="ดูรายชื่อสมาชิก")
    async def slash_members(interaction: discord.Interaction):
        if not config.ENABLE_MEMBERS or interaction.guild is None:
            await interaction.response.send_message(
                "ยังเข้าถึงรายชื่อสมาชิกไม่ได้ — ต้องตั้ง `ENABLE_MEMBERS=true` และเปิด SERVER MEMBERS INTENT",
                ephemeral=True,
            )
            return
        names = [m.display_name for m in interaction.guild.members if not m.bot]
        await dcutils.send_interaction(interaction, "**Members:** " + (", ".join(names) or "(ไม่มี)"))

    @tree.command(name="whoami", description="เช็กบทบาทของตัวเอง")
    async def slash_whoami(interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("ใช้ในเซิร์ฟเวอร์เท่านั้นนะ", ephemeral=True)
            return
        my_roles = [r.name for r in interaction.user.roles if r.name != "@everyone"]
        await interaction.response.send_message(
            f"**{interaction.user.display_name}** มีบทบาท: "
            + (", ".join(my_roles) or "(ไม่มีบทบาทพิเศษ)"),
            ephemeral=True,
        )

    @tree.command(name="join", description="ให้บอทเข้าช่องเสียงที่คุณอยู่ แล้วเล่นเสียงทักทาย")
    async def slash_join(interaction: discord.Interaction):
        # Connecting to voice can take several seconds; defer so the interaction
        # token doesn't expire (Discord requires a response within 3s).
        await interaction.response.defer()
        voice_state = getattr(interaction.user, "voice", None)
        if interaction.guild is None or voice_state is None or voice_state.channel is None:
            await interaction.followup.send("เข้าช่องเสียงก่อนนะ แล้วค่อยเรียกฉัน 🔊", ephemeral=True)
            return
        channel = voice_state.channel
        existing = interaction.guild.voice_client
        # Already in the same channel? Do nothing (guards against /join spam).
        if existing is not None and existing.channel == channel:
            await interaction.followup.send("ฉันอยู่ในช่องนี้อยู่แล้วนะ 🔊", ephemeral=True)
            return
        try:
            if existing is not None:
                await existing.move_to(channel)
            else:
                await channel.connect()
        except discord.Forbidden:
            await interaction.followup.send(
                "ฉันไม่มีสิทธิ์เข้าช่องนี้ (ต้องมี Connect + Speak)", ephemeral=True
            )
            return
        except Exception:
            log.exception("Failed to join voice channel")
            await interaction.followup.send("เข้าช่องเสียงไม่สำเร็จ ลองใหม่นะ", ephemeral=True)
            return
        note = sounds.play_sound(interaction.guild.voice_client, config.JOIN_SOUND)
        await interaction.followup.send(f"เข้าช่อง **{channel.name}** แล้ว 🔊{note}")

    @tree.command(name="leave", description="ให้บอทออกจากช่องเสียง")
    async def slash_leave(interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if voice_client is None:
            await interaction.response.send_message("ฉันไม่ได้อยู่ในช่องเสียงนะ", ephemeral=True)
            return
        await voice_client.disconnect()
        await interaction.response.send_message("ออกจากช่องเสียงแล้ว 👋")

    @tree.command(name="kuru", description="เล่นเสียง kuru 🔊")
    async def slash_kuru(interaction: discord.Interaction):
        await interaction.response.defer()  # joining voice can take >3s
        if interaction.guild is None:
            await interaction.followup.send("ใช้ในเซิร์ฟเวอร์เท่านั้นนะ", ephemeral=True)
            return
        voice_client = interaction.guild.voice_client
        # Not in voice yet? Join the caller's channel first.
        if voice_client is None:
            voice_state = getattr(interaction.user, "voice", None)
            if voice_state is None or voice_state.channel is None:
                await interaction.followup.send("เข้าช่องเสียงก่อน หรือเรียก /join นะ", ephemeral=True)
                return
            try:
                voice_client = await voice_state.channel.connect()
            except discord.Forbidden:
                await interaction.followup.send(
                    "ฉันไม่มีสิทธิ์เข้าช่องนี้ (ต้องมี Connect + Speak)", ephemeral=True
                )
                return
            except Exception:
                log.exception("Failed to join for /kuru")
                await interaction.followup.send("เข้าช่องเสียงไม่สำเร็จ", ephemeral=True)
                return
        note = sounds.play_sound(voice_client, config.KURU_SOUND)
        await interaction.followup.send(f"kuru~ 🔊{note}")
