from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True)
class StoredMessage:
    id: int
    created_at: int
    platform: str
    self_id: str
    group_id: str
    message_id: str
    sender_id: str
    sender_name: str
    message_str: str
    rule_name: str


class MessageStore:
    def __init__(self, db_path: Path, *, max_records: int) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_records = int(max_records)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    self_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    message_str TEXT NOT NULL,
                    message_chain_json TEXT,
                    raw_message_json TEXT,
                    rule_name TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_id, created_at DESC)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(created_at DESC)")

    def insert_message(
        self,
        *,
        created_at: int,
        platform: str,
        self_id: str,
        group_id: str,
        message_id: str,
        sender_id: str,
        sender_name: str,
        message_str: str,
        message_chain_json: str | None,
        raw_message_json: str | None,
        rule_name: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages (
                    created_at, platform, self_id, group_id, message_id, sender_id, sender_name,
                    message_str, message_chain_json, raw_message_json, rule_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(created_at),
                    platform,
                    self_id,
                    group_id,
                    message_id,
                    sender_id,
                    sender_name,
                    message_str,
                    message_chain_json,
                    raw_message_json,
                    rule_name,
                ),
            )
            inserted_id = int(cur.lastrowid)
            if self._max_records > 0:
                conn.execute(
                    """
                    DELETE FROM messages
                    WHERE id IN (
                        SELECT id FROM messages
                        ORDER BY created_at DESC, id DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (self._max_records,),
                )
            return inserted_id

    def count_messages(self, *, group_id: str | None, q: str | None) -> int:
        where, params = _build_where(group_id=group_id, q=q)
        sql = "SELECT COUNT(1) AS c FROM messages" + where
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return int(row["c"]) if row else 0

    def list_messages(
        self,
        *,
        limit: int,
        offset: int,
        group_id: str | None,
        q: str | None,
    ) -> list[StoredMessage]:
        where, params = _build_where(group_id=group_id, q=q)
        sql = (
            """
            SELECT id, created_at, platform, self_id, group_id, message_id, sender_id, sender_name, message_str, rule_name
            FROM messages
            """
            + where
            + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        params = (*params, int(limit), int(offset))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                StoredMessage(
                    id=int(r["id"]),
                    created_at=int(r["created_at"]),
                    platform=str(r["platform"]),
                    self_id=str(r["self_id"]),
                    group_id=str(r["group_id"]),
                    message_id=str(r["message_id"]),
                    sender_id=str(r["sender_id"]),
                    sender_name=str(r["sender_name"]),
                    message_str=str(r["message_str"]),
                    rule_name=str(r["rule_name"]),
                )
                for r in rows
            ]

    def list_groups(self) -> list[dict[str, Any]]:
        sql = """
        SELECT
            group_id,
            COUNT(1) AS cnt,
            MAX(created_at) AS last_ts
        FROM messages
        GROUP BY group_id
        ORDER BY last_ts DESC
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
            return [
                {"group_id": str(r["group_id"]), "count": int(r["cnt"]), "last_ts": int(r["last_ts"] or 0)}
                for r in rows
            ]


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def serialize_message_chain(chain: Any) -> str:
    if not isinstance(chain, list):
        return "[]"
    items: list[dict[str, Any]] = []
    for c in chain:
        if c is None:
            continue
        data: dict[str, Any] = {"type": c.__class__.__name__}
        payload: dict[str, Any] = {}
        if hasattr(c, "__dict__") and isinstance(c.__dict__, dict):
            for k, v in c.__dict__.items():
                if isinstance(v, (str, int, float, bool)) or v is None:
                    payload[k] = v
                else:
                    payload[k] = str(v)
        data["data"] = payload if payload else str(c)
        items.append(data)
    return safe_json_dumps(items)


def serialize_raw_message(raw: Any) -> str:
    if raw is None:
        return "null"
    if isinstance(raw, (dict, list, str, int, float, bool)):
        return safe_json_dumps(raw)
    if hasattr(raw, "dict"):
        try:
            return safe_json_dumps(raw.dict())
        except Exception:
            pass
    return safe_json_dumps(str(raw))


def _build_where(*, group_id: str | None, q: str | None) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if group_id:
        clauses.append("group_id = ?")
        params.append(group_id)
    if q:
        clauses.append("message_str LIKE ?")
        params.append(f"%{q}%")
    if not clauses:
        return "", ()
    return " WHERE " + " AND ".join(clauses), tuple(params)

