# -*- coding: utf-8 -*-
"""记忆库（messages.db）只读封装：统计、关键词检索、消息读取。

安全约定：
* 所有连接一律使用 ``file:<path>?mode=ro`` 只读 URI，绝不写入 memory\\ 下的任何文件。
* FTS 中文索引 msg_fts_cjk 是把每个中日韩字符用空格切开的“伪分词”索引，
  查询时对关键词做同样的切分，并用短语匹配（见 space_cjk）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time

try:  # 复用 pipeline 的中文切分（保持与构建索引时完全一致）
    from pipeline.build_cjk_fts import space_cjk  # type: ignore
except Exception:  # noqa: BLE001  # 兜底：与 build_cjk_fts.CJK 完全一致的实现
    _CJK = re.compile(r"([\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f])")

    def space_cjk(s: str) -> str:  # type: ignore
        return _CJK.sub(r" \1 ", s or "")

NULL_TEXT = "(无正文)"

# ---------------------------------------------------------------- 空库引导（统一口径）
# 主窗口状态区 / 检索窗口 / 会话导出窗口 三处共用这一套说法，避免各写各的。
# 触发场景：记忆库目录未设置、messages.db 不存在或读不到（首次运行的干净电脑）。
GUIDE_STEPS = (
    "① 确认「记忆库目录」设置是否正确；",
    "② 若本机已登录 QQ，可用「同步」生成记忆库；",
    "③ 已有记忆库，请把「记忆库目录」指向它所在的位置。",
)


def memory_missing_title(memory_dir: str = "") -> str:
    """单行标题：未找到记忆库：<路径>。"""
    return f"未找到记忆库：{memory_dir or '(未设置)'}"


#: 记忆库尚未生成时 MemoryStore.stats() 返回的 error 值。
#: 调用方据此区分「还没生成」（正常首启）与「真的读取失败」（异常）。
MISSING_MEMORY_ERROR = "记忆库文件不存在"


def is_missing_error(err: str) -> bool:
    return (err or "").strip() == MISSING_MEMORY_ERROR


def memory_missing_hint(memory_dir: str = "") -> str:
    """单行提示（用于状态标签 / 列表说明）。"""
    return memory_missing_title(memory_dir)


def memory_missing_guide(memory_dir: str = "", sep: str = "\n") -> str:
    """多行引导（用于对话框 / 日志）：标题 + 「建议操作」三步。"""
    return sep.join([memory_missing_title(memory_dir), "建议操作："] + list(GUIDE_STEPS))


def human_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_ts(ts) -> str:
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    return con


class MemoryStore:
    """对 <memory>/ 目录下记忆库的只读访问对象。"""

    def __init__(self, memory_dir: str, dim: int = 1024):
        self.memory_dir = memory_dir
        self.dim = int(dim or 1024)
        self.db = os.path.join(memory_dir, "messages.db")
        self.vecs = os.path.join(memory_dir, "vecs.f16")
        self.vectors_db = os.path.join(memory_dir, "vectors.db")
        self.state_path = os.path.join(memory_dir, "state.json")
        self.embed_input = os.path.join(memory_dir, "embed_input.jsonl")

    # ------------------------------------------------------------ 存在性
    def exists(self) -> bool:
        return os.path.isfile(self.db)

    def require(self) -> None:
        if not self.exists():
            raise FileNotFoundError(memory_missing_guide(self.memory_dir))

    # ------------------------------------------------------------ 文件信息
    def file_info(self) -> dict:
        def size(p: str) -> int:
            try:
                return os.path.getsize(p)
            except OSError:
                return 0

        return {
            "db_bytes": size(self.db),
            "db_mtime": os.path.getmtime(self.db) if os.path.isfile(self.db) else 0,
            "vecs_bytes": size(self.vecs),
            "vectors_bytes": size(self.vectors_db),
            "state_bytes": size(self.state_path),
        }

    def read_state(self) -> dict:
        if os.path.isfile(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:  # noqa: BLE001
                return {}
        return {}

    # ------------------------------------------------------------ 统计
    def stats(self) -> dict:
        info = self.file_info()
        out = {
            "memory_dir": self.memory_dir,
            "db_bytes": info["db_bytes"],
            "db_mtime": info["db_mtime"],
            "vecs_bytes": info["vecs_bytes"],
            "vectors_bytes": info["vectors_bytes"],
            "count": 0, "c2c": 0, "group": 0, "chats": 0,
            "c2c_chats": 0, "group_chats": 0,
            "ts_min": 0, "ts_max": 0,
            "vec_rows": 0, "vec_count": 0, "vec_dim": self.dim,
            "state": self.read_state(),
            "error": "",
        }
        if not self.exists():
            out["error"] = "记忆库文件不存在"
            return out
        try:
            con = _ro(self.db)
            c = con.cursor()
            out["count"] = c.execute("SELECT COUNT(*) FROM msg").fetchone()[0]
            out["c2c"] = c.execute("SELECT COUNT(*) FROM msg WHERE chat_type='c2c'").fetchone()[0]
            out["group"] = out["count"] - out["c2c"]
            out["chats"] = c.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM msg GROUP BY chat_type,chat_id)").fetchone()[0]
            out["c2c_chats"] = c.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM msg WHERE chat_type='c2c' GROUP BY chat_id)").fetchone()[0]
            out["group_chats"] = out["chats"] - out["c2c_chats"]
            r = c.execute("SELECT MIN(ts),MAX(ts) FROM msg").fetchone()
            out["ts_min"], out["ts_max"] = (r[0] or 0), (r[1] or 0)
            con.close()
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {e}"
        # 向量
        if os.path.isfile(self.vectors_db):
            try:
                con = _ro(self.vectors_db)
                out["vec_rows"] = con.execute("SELECT COUNT(*) FROM vec_map").fetchone()[0]
                con.close()
            except Exception:  # noqa: BLE001
                pass
        # 维度取自 embedding 配置（默认 1024）；向量条数 = 文件字节 / (维度 * 2)
        out["vec_dim"] = self.dim
        if info["vecs_bytes"]:
            if info["vecs_bytes"] % (2 * self.dim) == 0:
                out["vec_count"] = info["vecs_bytes"] // (2 * self.dim)
            else:  # 维度与文件对不齐，尝试用映射行数反推（仅粗略提示）
                out["vec_dim"] = 0
                out["vec_count"] = 0
        return out

    # ------------------------------------------------------------ 会话列表
    def chats(self) -> list[dict]:
        if not self.exists():
            return []
        con = _ro(self.db)
        rows = con.execute(
            "SELECT chat_type, chat_id, MAX(chat_name) AS cn, COUNT(*) n, MIN(ts), MAX(ts) "
            "FROM msg GROUP BY chat_type, chat_id ORDER BY n DESC").fetchall()
        con.close()
        out = []
        for ctype, cid, cn, n, tmin, tmax in rows:
            out.append({"chat_type": ctype, "chat_id": str(cid), "name": cn or str(cid),
                        "count": n, "ts_min": tmin, "ts_max": tmax})
        return out

    # ------------------------------------------------------------ 会话消息分批遍历（导出用）
    @staticmethod
    def _row_to_msg(r) -> dict:
        """把 msg 表 11 列的行转成统一的消息 dict（列顺序见 _MSG_COLS）。"""
        return {
            "id": r[0], "ts": r[1], "time": fmt_ts(r[1]), "chat_type": r[2],
            "chat_name": r[3], "chat_id": str(r[4]), "sender_name": r[5], "sender_qq": r[6],
            "dir": r[7], "msg_type": r[8], "content_type": r[9],
            "sender": "我" if r[7] == 2 else (r[5] or str(r[6] or "")),
            "text": r[10] if r[10] is not None else NULL_TEXT,
        }

    #: 与 _row_to_msg 一一对应的查询列（顺序不可随意调整）
    _MSG_COLS = ("id, ts, chat_type, chat_name, chat_id, sender_name, sender_qq, "
                 "direction, msg_type, content_type, text")

    def _chat_where(self, chat_type: str, chat_id: str, ts_from: int = 0, ts_to: int = 0,
                    only_me: bool = False):
        """构造会话过滤 WHERE 子句与参数（供计数/遍历复用，保证两者完全一致）。"""
        where = ["chat_type = ?", "chat_id = ?"]
        args: list = [chat_type, str(chat_id)]
        if ts_from:
            where.append("ts >= ?")
            args.append(int(ts_from))
        if ts_to:
            where.append("ts <= ?")
            args.append(int(ts_to))
        if only_me:
            where.append("direction = 2")
        return " AND ".join(where), args

    def count_chat_messages(self, chat_type: str, chat_id: str, ts_from: int = 0,
                            ts_to: int = 0, only_me: bool = False) -> int:
        """统计某会话在给定过滤条件下的消息条数（只读，单条 SQL）。"""
        self.require()
        cond, args = self._chat_where(chat_type, chat_id, ts_from, ts_to, only_me)
        con = _ro(self.db)
        try:
            return int(con.execute(f"SELECT COUNT(*) FROM msg WHERE {cond}", args).fetchone()[0])
        finally:
            con.close()

    def chat_summary(self, chat_type: str, chat_id: str, ts_from: int = 0, ts_to: int = 0,
                     only_me: bool = False) -> dict:
        """一次查询同时返回会话条数与真实时间范围（用于导出头部元信息）。"""
        self.require()
        cond, args = self._chat_where(chat_type, chat_id, ts_from, ts_to, only_me)
        con = _ro(self.db)
        try:
            r = con.execute(
                f"SELECT COUNT(*), "
                "MIN(CASE WHEN ts > 0 THEN ts END), MAX(CASE WHEN ts > 0 THEN ts END) "
                f"FROM msg WHERE {cond}", args).fetchone()
        finally:
            con.close()
        return {"count": int(r[0] or 0), "ts_min": int(r[1] or 0), "ts_max": int(r[2] or 0)}

    def iter_chat_messages(self, chat_type: str, chat_id: str, ts_from: int = 0, ts_to: int = 0,
                           only_me: bool = False, batch: int = 2000):
        """按 (ts, id) 升序**分批**遍历某会话的全部消息（生成器）。

        采用「键集分页」（keyset pagination）：每批用上一批结尾的 (ts, id) 作为游标，
        命中 idx_chat(chat_type, chat_id, ts) 索引，即便上亿条也不会一次性读进内存，
        更不会随偏移量增大而变慢。只读打开，绝不写库。

        产出 dict 字段与 get_message() 保持一致。
        """
        self.require()
        cond, args = self._chat_where(chat_type, chat_id, ts_from, ts_to, only_me)
        batch = max(1, int(batch))
        con = _ro(self.db)
        try:
            cur_ts: int | None = None
            cur_id: int | None = None
            while True:
                if cur_ts is None:
                    sql = (f"SELECT {self._MSG_COLS} FROM msg WHERE {cond} "
                           "ORDER BY ts ASC, id ASC LIMIT ?")
                    params = args + [batch]
                else:
                    sql = (f"SELECT {self._MSG_COLS} FROM msg WHERE {cond} "
                           "AND (ts > ? OR (ts = ? AND id > ?)) "
                           "ORDER BY ts ASC, id ASC LIMIT ?")
                    params = args + [cur_ts, cur_ts, cur_id, batch]
                rows = con.execute(sql, params).fetchall()
                if not rows:
                    break
                for r in rows:
                    yield self._row_to_msg(r)
                if len(rows) < batch:
                    break
                cur_ts, cur_id = int(rows[-1][1]), int(rows[-1][0])
        finally:
            con.close()

    # ------------------------------------------------------------ 关键词检索
    def keyword_search(self, keyword: str, k: int = 50, chat: str = "", sender: str = "",
                       ts_from: int = 0, ts_to: int = 0, only_me: bool = False) -> list[dict]:
        """中文子串关键词检索，返回按 bm25 相关度排序的结果。"""
        self.require()
        kw = (keyword or "").strip()
        if not kw:
            return []
        expr = " ".join(space_cjk(kw).split())
        where = []
        args: list = []
        if chat:
            where.append("(m.chat_name LIKE ? OR m.chat_id LIKE ?)")
            args += [f"%{chat}%", f"%{chat}%"]
        if sender:
            where.append("(m.sender_name LIKE ? OR CAST(m.sender_qq AS TEXT) LIKE ?)")
            args += [f"%{sender}%", f"%{sender}%"]
        if ts_from:
            where.append("m.ts >= ?")
            args.append(int(ts_from))
        if ts_to:
            where.append("m.ts <= ?")
            args.append(int(ts_to))
        if only_me:
            where.append("m.direction = 2")
        sql = (
            "SELECT m.id, m.ts, m.chat_type, m.chat_name, m.chat_id, m.sender_name, m.sender_qq, "
            "m.direction, m.text, bm25(msg_fts_cjk) AS s "
            "FROM msg_fts_cjk f JOIN msg m ON m.id = f.rowid "
            "WHERE msg_fts_cjk MATCH ?" + (" AND " + " AND ".join(where) if where else "") +
            " ORDER BY s LIMIT ?")
        con = _ro(self.db)
        t0 = time.time()
        try:
            rows = con.execute(sql, [expr] + args + [int(k)]).fetchall()
        finally:
            con.close()
        out = []
        for mid, ts, ctype, cname, cid, sname, sqq, direction, text, s in rows:
            out.append({
                "id": mid, "ts": ts, "time": fmt_ts(ts),
                "chat": cname or str(cid), "chat_type": ctype, "chat_id": str(cid),
                "sender": "我" if direction == 2 else (sname or str(sqq or "")),
                "sender_name": sname, "sender_qq": sqq, "dir": direction,
                "text": text or NULL_TEXT, "score": round(-float(s), 3), "source": "关键词",
            })
        return out, round(time.time() - t0, 3)

    # ------------------------------------------------------------ 单条读取
    def get_message(self, msg_id: int) -> dict | None:
        self.require()
        con = _ro(self.db)
        r = con.execute(
            "SELECT id, ts, chat_type, chat_name, chat_id, sender_name, sender_qq, direction, "
            "msg_type, content_type, text, meta FROM msg WHERE id=?", (int(msg_id),)).fetchone()
        con.close()
        if not r:
            return None
        return {
            "id": r[0], "ts": r[1], "time": fmt_ts(r[1]), "chat_type": r[2],
            "chat": r[3] or r[4], "chat_id": str(r[4]), "sender_name": r[5], "sender_qq": r[6],
            "dir": r[7], "msg_type": r[8], "content_type": r[9],
            "sender": "我" if r[7] == 2 else (r[5] or str(r[6] or "")),
            "text": r[10] or NULL_TEXT, "meta": r[11],
        }

    # ------------------------------------------------------------ 供 UI 的会话名列表
    def chat_names(self, limit: int = 500) -> list[str]:
        seen, out = set(), []
        for c in self.chats():
            nm = c.get("name") or ""
            if nm and nm not in seen:
                seen.add(nm)
                out.append(nm)
            if len(out) >= limit:
                break
        return out


if __name__ == "__main__":
    from core import config as _cfg  # noqa: F401

    c = _cfg.Config.load()
    ms = MemoryStore(c.memory_dir)
    print(json.dumps(ms.stats(), ensure_ascii=False, indent=2, default=str))
    hits, dt = ms.keyword_search("哈哈", k=3)
    print(f"关键词命中 {len(hits)} 条 ({dt}s)")
    for h in hits:
        print(" ", h["time"], h["chat"], h["sender"], str(h["text"])[:40])
