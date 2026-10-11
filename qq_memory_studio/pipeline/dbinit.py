# -*- coding: utf-8 -*-
"""记忆库结构初始化（幂等）。"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS msg(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER, chat_type TEXT, chat_id TEXT, chat_name TEXT,
  sender_qq INTEGER, sender_name TEXT, direction INTEGER,
  msg_type INTEGER, content_type INTEGER, text TEXT, meta TEXT);
CREATE INDEX IF NOT EXISTS idx_chat ON msg(chat_type, chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_ts ON msg(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(text, chat_name, sender_name, content='msg', content_rowid='id');
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts_cjk USING fts5(text, chat_name, sender_name, content='');
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
