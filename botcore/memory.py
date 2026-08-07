"""Persistent memory: SQLite chat history + long-term facts + server roster.

Two layers:
  * shared_history — one rolling window (last HISTORY_LIMIT messages) shared
    across all channels, mirrored to SQLite so it survives restarts.
  * facts          — a key/value store, always shown to the model and UPSERTed
    (a new value overwrites the old one).
"""

import collections
import logging
import sqlite3

from . import config

log = logging.getLogger(config.LOGGER_NAME)

# Recent shared history kept in memory for speed; also mirrored to the DB.
# Each entry is {"role": ..., "content": ..., "author": ...}.
shared_history: collections.deque = collections.deque(maxlen=config.HISTORY_LIMIT)


# --- schema -----------------------------------------------------------------
def init_db() -> None:
    """Create the messages + facts tables (and migrate old DBs)."""
    con = sqlite3.connect(config.DB_PATH)
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


# --- chat history -----------------------------------------------------------
def save_message(role: str, content: str, author: str | None) -> None:
    con = sqlite3.connect(config.DB_PATH)
    con.execute(
        "INSERT INTO messages (role, content, author) VALUES (?, ?, ?)",
        (role, content, author),
    )
    con.commit()
    con.close()


def load_recent(limit: int) -> list[dict]:
    con = sqlite3.connect(config.DB_PATH)
    rows = con.execute(
        "SELECT role, content, author FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [{"role": r, "content": c, "author": a} for r, c, a in reversed(rows)]


def load_history() -> None:
    """Warm the in-memory history from the DB at startup."""
    for msg in load_recent(config.HISTORY_LIMIT):
        shared_history.append(msg)


def remember_turn(user_text: str, author: str, reply: str) -> None:
    """Record one user+assistant exchange in memory and the DB."""
    shared_history.append({"role": "user", "content": user_text, "author": author})
    shared_history.append({"role": "assistant", "content": reply, "author": None})
    save_message("user", user_text, author)
    save_message("assistant", reply, None)


def format_for_model(entry: dict) -> dict:
    """Convert a stored entry to an OpenAI message, prefixing the speaker name."""
    author = entry.get("author")
    if author:
        return {"role": entry["role"], "content": f"{author}: {entry['content']}"}
    return {"role": entry["role"], "content": entry["content"]}


# --- facts ------------------------------------------------------------------
def set_fact(key: str, value: str) -> None:
    """Insert or overwrite a fact (upsert on key)."""
    con = sqlite3.connect(config.DB_PATH)
    con.execute(
        "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (key, value),
    )
    con.commit()
    con.close()


def delete_fact(key: str) -> None:
    con = sqlite3.connect(config.DB_PATH)
    con.execute("DELETE FROM facts WHERE key = ?", (key,))
    con.commit()
    con.close()


def all_facts() -> list[tuple[str, str]]:
    con = sqlite3.connect(config.DB_PATH)
    rows = con.execute("SELECT key, value FROM facts ORDER BY key").fetchall()
    con.close()
    return rows


def facts_block() -> str:
    """A text block of all facts, injected into the system prompt every call."""
    facts = all_facts()
    if not facts:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in facts)
    return "\n\nKnown facts you remember (authoritative, always up to date):\n" + lines


def apply_and_strip_facts(reply: str) -> str:
    """Apply any [[REMEMBER]]/[[FORGET]] tags to the DB, then remove them."""
    for key, value in config.REMEMBER_RE.findall(reply):
        set_fact(key.strip(), value.strip())
        log.info("Remembered fact: %s = %s", key.strip(), value.strip())
    for key in config.FORGET_RE.findall(reply):
        delete_fact(key.strip())
        log.info("Forgot fact: %s", key.strip())
    reply = config.REMEMBER_RE.sub("", reply)
    reply = config.FORGET_RE.sub("", reply)
    return reply.strip()


# --- server roster (members + roles) ---------------------------------------
# Keywords that suggest a message needs the roster; only then do we attach it,
# to avoid wasting hundreds of tokens on unrelated questions.
_ROSTER_KEYWORDS = (
    "ใคร", "role", "บทบาท", "สมาชิก", "member", "admin", "แอดมิน", "mod",
    "mention", "เมนชั่น", "แท็ก", "tag", "เรียก", "ยศ", "who",
)


def needs_roster(user_text: str) -> bool:
    """Heuristic: does this message likely need the member/role list?"""
    if "<@" in user_text:  # already contains a mention
        return True
    lowered = user_text.lower()
    return any(k in lowered for k in _ROSTER_KEYWORDS)


def server_roster(guild) -> str:
    """Roles + members (with mention tags and each member's roles) for the model."""
    if not config.ENABLE_MEMBERS or guild is None:
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
