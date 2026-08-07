"""
SQLite chat persistence for Lantern.

DB lives next to mcp.json when possible (%APPDATA%/Lantern on Windows,
~/.config/Lantern elsewhere), falling back to ./chats.db beside this file.

Isolation rule: chats never share message history. Projects only share
explicit instructions the user typed — never sibling conversations.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager


def _default_db_path() -> str:
    if env := os.getenv("CHATS_DB"):
        return env
    appdata = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    cfg_dir = os.path.join(appdata, "Lantern")
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        return os.path.join(cfg_dir, "chats.db")
    except OSError:
        return os.path.join(os.path.dirname(__file__), "chats.db")


DB_PATH = _default_db_path()
ABOUT_TOKEN_CAP = 300
PROJECT_INSTR_TOKEN_CAP = 500
WORDS_PER_TOKEN = 0.75


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              instructions TEXT,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chats (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              project_id TEXT REFERENCES projects(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              sources_json TEXT,
              created_at INTEGER NOT NULL,
              route TEXT,
              stopped INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat
              ON messages(chat_id, created_at);
            CREATE TABLE IF NOT EXISTS facts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              key TEXT NOT NULL UNIQUE,
              value TEXT NOT NULL,
              source_chat_id TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_profile (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              about TEXT NOT NULL DEFAULT '',
              about_enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT OR IGNORE INTO user_profile (id, about, about_enabled)
              VALUES (1, '', 1);
            """
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)").fetchall()}
        if "project_id" not in cols:
            conn.execute(
                "ALTER TABLE chats ADD COLUMN project_id TEXT "
                "REFERENCES projects(id) ON DELETE SET NULL"
            )
        cols_m = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "route" not in cols_m:
            conn.execute("ALTER TABLE messages ADD COLUMN route TEXT")
        if "stopped" not in cols_m:
            conn.execute("ALTER TABLE messages ADD COLUMN stopped INTEGER NOT NULL DEFAULT 0")


def est_tokens(text: str) -> int:
    return int(len((text or "").split()) / WORDS_PER_TOKEN)


def clip_tokens(text: str, limit: int) -> str:
    words = (text or "").split()
    max_w = int(limit * WORDS_PER_TOKEN)
    if len(words) <= max_w:
        return text or ""
    # Reserve room for the ellipsis marker in the token estimate.
    cut = max(0, max_w - 1)
    return " ".join(words[:cut]) + " …"


def derive_title(text: str) -> str:
    text = " ".join((text or "").strip().split())
    if not text:
        return "New chat"
    if len(text) <= 60:
        return text
    cut = text[:60]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def create_chat(title: str | None = None, project_id: str | None = None) -> str:
    init_db()
    chat_id = uuid.uuid4().hex
    now = int(time.time())
    title = (title or "New chat").strip() or "New chat"
    with _db() as conn:
        if project_id:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                project_id = None
        conn.execute(
            "INSERT INTO chats (id, title, created_at, updated_at, project_id) VALUES (?,?,?,?,?)",
            (chat_id, title, now, now, project_id),
        )
    return chat_id


def add_message(
    chat_id: str,
    role: str,
    content: str,
    sources: list | None = None,
    route: str | None = None,
    stopped: bool = False,
) -> int:
    init_db()
    now = int(time.time())
    sources_json = json.dumps(sources) if sources is not None else None
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (chat_id, role, content, sources_json, created_at, route, stopped) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, role, content, sources_json, now, route, 1 if stopped else 0),
        )
        return int(cur.lastrowid)


def delete_last_assistant(chat_id: str) -> bool:
    """Remove the most recent assistant message (for Retry after stop)."""
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND role = 'assistant' "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
        return True


def truncate_after_user(chat_id: str, user_created_at: int) -> None:
    """Edit-question: drop the user turn and everything after it."""
    init_db()
    with _db() as conn:
        conn.execute(
            "DELETE FROM messages WHERE chat_id = ? AND created_at >= ?",
            (chat_id, user_created_at),
        )


def list_chats(limit: int = 200) -> list[dict]:
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at, project_id FROM chats ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "updated_at": r["updated_at"],
            "project_id": r["project_id"],
        }
        for r in rows
    ]


def get_messages(chat_id: str) -> list[dict]:
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content, sources_json, created_at, route, stopped FROM messages "
            "WHERE chat_id = ? ORDER BY created_at ASC, id ASC",
            (chat_id,),
        ).fetchall()
    out = []
    for r in rows:
        sources = json.loads(r["sources_json"]) if r["sources_json"] else None
        out.append(
            {
                "role": r["role"],
                "content": r["content"],
                "sources": sources,
                "created_at": r["created_at"],
                "route": r["route"],
                "stopped": bool(r["stopped"]),
            }
        )
    return out


def get_chat(chat_id: str) -> dict | None:
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at, project_id FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "project_id": row["project_id"],
        "messages": get_messages(chat_id),
    }


def rename_chat(chat_id: str, title: str) -> None:
    init_db()
    title = title.strip() or "New chat"
    with _db() as conn:
        conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
            (title, int(time.time()), chat_id),
        )


def set_chat_project(chat_id: str, project_id: str | None) -> None:
    init_db()
    with _db() as conn:
        if project_id:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                raise ValueError("project not found")
        conn.execute(
            "UPDATE chats SET project_id = ?, updated_at = ? WHERE id = ?",
            (project_id, int(time.time()), chat_id),
        )


def delete_chat(chat_id: str) -> None:
    """Hard-delete the chat and its messages (FK CASCADE also covers messages)."""
    init_db()
    with _db() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


def touch(chat_id: str) -> None:
    init_db()
    with _db() as conn:
        conn.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ?",
            (int(time.time()), chat_id),
        )


def count_messages() -> int:
    init_db()
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
    return int(row["n"] if row else 0)


# --- projects ---


def list_projects() -> list[dict]:
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, instructions, created_at FROM projects ORDER BY created_at ASC"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "instructions": r["instructions"] or "",
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_project(project_id: str) -> dict | None:
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, instructions, created_at FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "instructions": row["instructions"] or "",
        "created_at": row["created_at"],
    }


def create_project(name: str, instructions: str = "") -> str:
    init_db()
    pid = uuid.uuid4().hex
    now = int(time.time())
    name = (name or "Project").strip() or "Project"
    instructions = clip_tokens(instructions or "", PROJECT_INSTR_TOKEN_CAP)
    with _db() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, instructions, created_at) VALUES (?,?,?,?)",
            (pid, name, instructions, now),
        )
    return pid


def update_project(
    project_id: str,
    *,
    name: str | None = None,
    instructions: str | None = None,
) -> bool:
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, instructions FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            return False
        new_name = row["name"] if name is None else ((name or "").strip() or "Project")
        if instructions is None:
            new_instr = row["instructions"] or ""
        else:
            new_instr = clip_tokens(instructions, PROJECT_INSTR_TOKEN_CAP)
        conn.execute(
            "UPDATE projects SET name = ?, instructions = ? WHERE id = ?",
            (new_name, new_instr, project_id),
        )
    return True


def delete_project(project_id: str) -> bool:
    """Unassign chats; do not delete them."""
    init_db()
    with _db() as conn:
        row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE chats SET project_id = NULL WHERE project_id = ?",
            (project_id,),
        )
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return True


def project_instructions_for_chat(chat_id: str) -> str:
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT p.instructions FROM chats c "
            "LEFT JOIN projects p ON p.id = c.project_id "
            "WHERE c.id = ?",
            (chat_id,),
        ).fetchone()
    if not row:
        return ""
    return clip_tokens(row["instructions"] or "", PROJECT_INSTR_TOKEN_CAP)


# --- About You (user-written; never auto-extracted) ---


def get_about() -> dict:
    init_db()
    with _db() as conn:
        row = conn.execute("SELECT about, about_enabled FROM user_profile WHERE id = 1").fetchone()
    if not row:
        return {"about": "", "enabled": True, "tokens": 0}
    about = row["about"] or ""
    return {
        "about": about,
        "enabled": bool(row["about_enabled"]),
        "tokens": est_tokens(about),
    }


def set_about(about: str | None = None, enabled: bool | None = None) -> dict:
    init_db()
    cur = get_about()
    if about is not None:
        cur["about"] = clip_tokens(about, ABOUT_TOKEN_CAP)
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    with _db() as conn:
        conn.execute(
            "INSERT INTO user_profile (id, about, about_enabled) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET about = excluded.about, "
            "about_enabled = excluded.about_enabled",
            (cur["about"], 1 if cur["enabled"] else 0),
        )
    return get_about()


def about_for_prompt() -> str:
    """Empty string when disabled or blank — caller must skip injection."""
    p = get_about()
    if not p["enabled"]:
        return ""
    text = (p["about"] or "").strip()
    if not text:
        return ""
    return clip_tokens(text, ABOUT_TOKEN_CAP)


def data_info() -> dict:
    init_db()
    path = DB_PATH
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        size = 0
    with _db() as conn:
        chats_n = conn.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"]
        msgs_n = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        proj_n = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    about = get_about()
    return {
        "path": path,
        "size_bytes": int(size),
        "chat_count": int(chats_n),
        "message_count": int(msgs_n),
        "project_count": int(proj_n),
        "about_tokens": about["tokens"] if about["enabled"] else 0,
    }


def export_all() -> dict:
    init_db()
    with _db() as conn:
        projects = [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, instructions, created_at FROM projects"
            ).fetchall()
        ]
        chat_rows = conn.execute(
            "SELECT id, title, created_at, updated_at, project_id FROM chats "
            "ORDER BY updated_at DESC"
        ).fetchall()
        msg_rows = conn.execute(
            "SELECT chat_id, role, content, sources_json, created_at, route "
            "FROM messages ORDER BY id ASC"
        ).fetchall()
    return {
        "exported_at": int(time.time()),
        "about": get_about(),
        "projects": projects,
        "chats": [dict(r) for r in chat_rows],
        "messages": [
            {
                "chat_id": r["chat_id"],
                "role": r["role"],
                "content": r["content"],
                "sources": json.loads(r["sources_json"]) if r["sources_json"] else None,
                "created_at": r["created_at"],
                "route": r["route"],
            }
            for r in msg_rows
        ],
    }


def wipe_all() -> None:
    init_db()
    with _db() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS messages;
            DROP TABLE IF EXISTS facts;
            DROP TABLE IF EXISTS chats;
            DROP TABLE IF EXISTS projects;
            DROP TABLE IF EXISTS user_profile;
            """
        )
    init_db()


init_db()
