"""
Persistance SQLite partagée entre le bot Discord et l’app Flask.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

_lock = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "ticketmp.db")

LEGACY_TICKETS = os.path.join(BASE_DIR, "tickets.json")
LEGACY_WEB = os.path.join(BASE_DIR, "web_requests.json")
LEGACY_STATS = os.path.join(BASE_DIR, "stats.json")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    user_id TEXT PRIMARY KEY,
                    channel_id INTEGER NOT NULL UNIQUE,
                    guild_id TEXT NOT NULL,
                    category TEXT,
                    via TEXT,
                    opened_at REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS web_queue (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    category TEXT,
                    message TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS stats_kv (
                    k TEXT PRIMARY KEY,
                    v INTEGER NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS close_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    requested_by TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS recently_closed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    category TEXT,
                    closed_at REAL NOT NULL
                )
                """
            )
            cur.execute(
                "INSERT OR IGNORE INTO stats_kv (k, v) VALUES ('opened', 0)"
            )
            cur.execute(
                "INSERT OR IGNORE INTO stats_kv (k, v) VALUES ('closed', 0)"
            )
            conn.commit()
            _migrate_json_if_empty(cur, conn)
        finally:
            conn.close()


def _migrate_json_if_empty(cur: sqlite3.Cursor, conn: sqlite3.Connection) -> None:
    n = cur.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    if n == 0 and os.path.isfile(LEGACY_TICKETS):
        try:
            raw = json.loads(open(LEGACY_TICKETS, "r", encoding="utf-8").read() or "{}")
        except json.JSONDecodeError:
            raw = {}
        now = time.time()
        for uid, t in raw.items():
            if not isinstance(t, dict):
                continue
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO tickets
                    (user_id, channel_id, guild_id, category, via, opened_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uid),
                        int(t.get("channel_id", 0)),
                        str(t.get("guild_id", "")),
                        str(t.get("category", "")),
                        str(t.get("via", "discord")),
                        now,
                    ),
                )
            except (TypeError, ValueError, sqlite3.Error):
                continue
        conn.commit()

    w = cur.execute("SELECT COUNT(*) FROM web_queue").fetchone()[0]
    if w == 0 and os.path.isfile(LEGACY_WEB):
        try:
            raw = json.loads(open(LEGACY_WEB, "r", encoding="utf-8").read() or "{}")
        except json.JSONDecodeError:
            raw = {}
        now = time.time()
        for qid, row in raw.items():
            if not isinstance(row, dict):
                continue
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO web_queue
                    (id, user_id, guild_id, category, message, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(qid),
                        str(row.get("user_id", "")),
                        str(row.get("guild_id", "")),
                        str(row.get("category", "")),
                        str(row.get("message", "")),
                        now,
                    ),
                )
            except sqlite3.Error:
                continue
        conn.commit()

    sk = cur.execute("SELECT COUNT(*) FROM stats_kv WHERE k IN ('opened','closed')").fetchone()[0]
    if sk < 2 and os.path.isfile(LEGACY_STATS):
        try:
            raw = json.loads(open(LEGACY_STATS, "r", encoding="utf-8").read() or "{}")
        except json.JSONDecodeError:
            raw = {}
        if isinstance(raw, dict):
            op = int(raw.get("opened", 0) or 0)
            cl = int(raw.get("closed", 0) or 0)
            cur.execute(
                "INSERT INTO stats_kv (k, v) VALUES ('opened', ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (op,),
            )
            cur.execute(
                "INSERT INTO stats_kv (k, v) VALUES ('closed', ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (cl,),
            )
        conn.commit()


def get_tickets_dict() -> dict[str, dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, channel_id, guild_id, category, via FROM tickets"
            )
            out: dict[str, dict[str, Any]] = {}
            for uid, ch, gid, cat, via in cur.fetchall():
                out[str(uid)] = {
                    "channel_id": int(ch),
                    "guild_id": str(gid),
                    "category": cat or "",
                    "via": via or "discord",
                }
            return out
        finally:
            conn.close()


def get_ticket_by_channel(channel_id: int) -> tuple[str, dict[str, Any]] | None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, channel_id, guild_id, category, via FROM tickets WHERE channel_id = ?",
                (int(channel_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            uid, ch, gid, cat, via = row
            return str(uid), {
                "channel_id": int(ch),
                "guild_id": str(gid),
                "category": cat or "",
                "via": via or "discord",
            }
        finally:
            conn.close()


def upsert_ticket(
    user_id: str,
    channel_id: int,
    guild_id: str,
    category: str,
    via: str = "discord",
) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO tickets (user_id, channel_id, guild_id, category, via, opened_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    guild_id = excluded.guild_id,
                    category = excluded.category,
                    via = excluded.via,
                    opened_at = excluded.opened_at
                """,
                (
                    str(user_id),
                    int(channel_id),
                    str(guild_id),
                    category,
                    via,
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def delete_ticket(user_id: str) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM tickets WHERE user_id = ?", (str(user_id),))
            conn.commit()
        finally:
            conn.close()


def delete_ticket_by_channel(channel_id: int) -> str | None:
    """Supprime le ticket lié au salon ; retourne user_id ou None."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM tickets WHERE channel_id = ?", (int(channel_id),)
            )
            row = cur.fetchone()
            if not row:
                return None
            uid = str(row[0])
            cur.execute("DELETE FROM tickets WHERE channel_id = ?", (int(channel_id),))
            conn.commit()
            return uid
        finally:
            conn.close()


def stats_get() -> dict[str, int]:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT k, v FROM stats_kv WHERE k IN ('opened','closed')")
            rows = dict(cur.fetchall())
            return {
                "opened": int(rows.get("opened", 0)),
                "closed": int(rows.get("closed", 0)),
            }
        finally:
            conn.close()


def stats_inc_opened() -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE stats_kv SET v = v + 1 WHERE k = 'opened'"
            )
            conn.commit()
        finally:
            conn.close()


def stats_inc_closed() -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE stats_kv SET v = v + 1 WHERE k = 'closed'"
            )
            conn.commit()
        finally:
            conn.close()


def web_queue_list() -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, user_id, guild_id, category, message FROM web_queue ORDER BY created_at"
            )
            out = []
            for qid, uid, gid, cat, msg in cur.fetchall():
                out.append(
                    {
                        "id": str(qid),
                        "user_id": str(uid),
                        "guild_id": str(gid),
                        "category": str(cat or ""),
                        "message": str(msg or ""),
                    }
                )
            return out
        finally:
            conn.close()


def web_queue_add(
    qid: str,
    user_id: str,
    guild_id: str,
    category: str,
    message: str,
) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO web_queue (id, user_id, guild_id, category, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(qid), str(user_id), str(guild_id), category, message, time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def web_queue_delete(qid: str) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM web_queue WHERE id = ?", (str(qid),))
            conn.commit()
        finally:
            conn.close()


def close_queue_add(channel_id: int, requested_by: str | None) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO close_queue (channel_id, requested_by, created_at)
                VALUES (?, ?, ?)
                """,
                (int(channel_id), requested_by, time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def close_queue_list() -> list[tuple[int, int, str | None]]:
    """(row_id, channel_id, requested_by)"""
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, channel_id, requested_by FROM close_queue ORDER BY id"
            )
            return [(int(r[0]), int(r[1]), r[2]) for r in cur.fetchall()]
        finally:
            conn.close()


def close_queue_delete(row_id: int) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM close_queue WHERE id = ?", (int(row_id),))
            conn.commit()
        finally:
            conn.close()


def recently_closed_add(user_id: str, guild_id: str, category: str) -> None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO recently_closed (user_id, guild_id, category, closed_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(user_id), str(guild_id), category or "", time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def recently_closed_last_category(user_id: str, guild_id: str) -> str | None:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT category FROM recently_closed
                WHERE user_id = ? AND guild_id = ?
                ORDER BY closed_at DESC LIMIT 1
                """,
                (str(user_id), str(guild_id)),
            )
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None
        finally:
            conn.close()
