# -*- coding: utf-8 -*-
"""增量刷新长期记忆库（主力路径）。

流程：
  1. nt_msg.db（SQLCipher）剥掉 1024 字节头 -> <work>/nt_msg_live.db（快照）
  2. 用 sqlcipher3 直接读取，按主键 40001 增量拉取新消息
  3. 解析后追加进 messages.db（结构化表 + CJK 全文索引）
  4. 进度写入 memory/state.json，下次从断点继续

QQ 运行时也能跑；损坏分段自动跳过。

用法::

    python refresh_memory.py --db <nt_msg.db> --key <密钥> [--memory-dir DIR] [--full]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import sys
import time

import paths
import dbinit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools", "util"))
sys.path.insert(0, HERE)

PAGE = 4096
BATCH = 5000

from msgdb.c2c import parser as c2c_parser                                  # noqa: E402
from msgdb.group import exporter as gexp                                    # noqa: E402
from build_memory import GROUP_SQL, flatten_text                            # noqa: E402
from build_cjk_fts import space_cjk                                         # noqa: E402


def strip_header(src: str, dst: str) -> None:
    t0 = time.time()
    with open(src, "rb") as f, open(dst, "wb") as o:
        f.seek(1024)
        while True:
            b = f.read(64 << 20)
            if not b:
                break
            o.write(b)
    print(f"  快照 {os.path.getsize(dst) / 1e6:.0f} MB  {time.time() - t0:.0f}s", flush=True)


def open_enc(path: str, key: str):
    import sqlcipher3.dbapi2 as sc

    c = sc.connect(path, isolation_level=None)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    try:
        c.row_factory = sc.dbapi2.Row
    except Exception:
        c.row_factory = None
    c.execute("PRAGMA cipher_page_size = %d;" % PAGE)
    c.execute("PRAGMA key = '%s';" % key.replace("'", "''"))
    c.execute("PRAGMA kdf_iter = 4000;")
    c.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
    c.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
    c.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    return c


def inc_sql(base: str) -> str:
    """把整表查询包一层，变成按 msg_id 增量的查询。"""
    b = base.strip().rstrip(";")
    return f"SELECT * FROM (\n{b}\n) WHERE msg_id > ? ORDER BY msg_id LIMIT ?"


def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return {"c2c_last": 0, "group_last": 0}


def run(nt_msg_path: str, key: str, memory_dir: str = "", work: str = "",
        full: bool = False) -> dict:
    """执行一次（增量/全量）刷新，返回统计 dict。"""
    t0 = time.time()
    mem = paths.memory_dir(memory_dir)
    work = paths.work_dir(work)
    os.makedirs(mem, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    db = os.path.join(mem, "messages.db")
    state_path = os.path.join(mem, "state.json")
    live = os.path.join(work, "nt_msg_live.db")

    if full:
        for p in (db, state_path, db + "-wal", db + "-shm"):
            try:
                os.remove(p)
            except OSError:
                pass
        print("  [全量] 已清空旧记忆库与断点")

    print("1) 抓取 QQ 数据库快照 ...", flush=True)
    strip_header(nt_msg_path, live)
    enc = open_enc(live, key)
    state = load_state(state_path)

    dst = sqlite3.connect(db, timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.text_factory = lambda b: b.decode("utf-8", "replace")
    dbinit.ensure_schema(dst)

    # 断点自愈
    _existing = dst.execute("SELECT COUNT(*) FROM msg").fetchone()[0]
    if _existing and not state.get("c2c_last") and not state.get("group_last"):
        ok = True
        for _tbl, _key in (("c2c_msg_table", "c2c_last"), ("group_msg_table", "group_last")):
            try:
                _v = enc.execute(f"SELECT MAX(msg_id) FROM {_tbl}").fetchone()
                state[_key] = int((_v or [0])[0] or 0)
            except Exception as _exc:  # noqa: BLE001
                ok = False
                print(f"  ! 读取源库断点失败 {_tbl}: {_exc}", flush=True)
        if not ok:
            print("!! 目标库已有数据但断点未知，为避免重复插入，本次放弃执行", flush=True)
            enc.close()
            dst.close()
            return {"ok": False, "reason": "state_lost"}
        json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  (断点自动初始化 {state}；目标库现有 {_existing:,} 条)", flush=True)

    names = {}
    for cname, cid, ctype in dst.execute(
            "SELECT chat_name, chat_id, chat_type FROM msg WHERE chat_name != '' "
            "GROUP BY chat_type, chat_id"):
        names[(ctype, str(cid))] = cname

    added = {"c2c": 0, "group": 0, "err": 0}
    buf = []
    max_id = dst.execute("SELECT COALESCE(MAX(id),0) FROM msg").fetchone()[0]

    def flush():
        nonlocal max_id
        if not buf:
            return
        dst.executemany(
            "INSERT INTO msg(ts,chat_type,chat_id,chat_name,sender_qq,sender_name,"
            "direction,msg_type,content_type,text,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)", buf)
        dst.commit()
        new = dst.execute("SELECT id, text, chat_name, sender_name FROM msg WHERE id > ?",
                          (max_id,)).fetchall()
        if new:
            dst.executemany(
                "INSERT INTO msg_fts_cjk(rowid,text,chat_name,sender_name) VALUES (?,?,?,?)",
                [(r[0], space_cjk(r[1]), space_cjk(r[2]), space_cjk(r[3])) for r in new])
            max_id = new[-1][0]
        dst.commit()
        buf.clear()

    for tbl, parser, keyname in (("c2c_msg_table", c2c_parser, "c2c_last"),
                                 ("group_msg_table", gexp, "group_last")):
        is_group = tbl.startswith("group")
        base = GROUP_SQL if is_group else c2c_parser.SELECT_SQL
        sql = inc_sql(base)
        last = int(state.get(keyname, 0))
        limit = BATCH
        n = 0
        while True:
            try:
                cur = enc.execute(sql, (last, limit))
                rows = cur.fetchall()
                cols = [d[0] for d in (cur.description or [])]
            except Exception as exc:
                print(f"  ! {tbl} 读取异常({exc})，重连并缩小批量", flush=True)
                try:
                    enc.close()
                except Exception:
                    pass
                enc = open_enc(live, key)
                limit = max(1, limit // 4)
                if limit == 1:
                    last += 1
                    limit = 200
                continue
            if not rows:
                break
            limit = BATCH
            rows = [dict(zip(cols, r)) if not hasattr(r, "keys") else r for r in rows]
            last = rows[-1]["msg_id"]
            for raw in rows:
                d = dict(raw)
                try:
                    m = parser.parse_row(raw)
                    rec = dataclasses.asdict(m) if dataclasses.is_dataclass(m) else dict(m)
                except Exception:
                    added["err"] += 1
                    continue
                if not d.get("timestamp"):
                    continue
                text = flatten_text(m, rec)
                if is_group:
                    gid = str(d.get("group_id") or d.get("group_qq") or "")
                    ctype, cname = "group", names.get(("group", gid), "")
                    sname = (d.get("card_name") or d.get("nick_name") or "").strip()
                else:
                    gid = str(int(d.get("peer_qq") or 0))
                    ctype, cname = "c2c", names.get(("c2c", gid), "")
                    sname = "我" if d.get("direction") in (1, 2) else ""
                meta = json.dumps({k: v for k, v in rec.items()
                                   if k != "text" and v not in (None, "", {}, [])},
                                  ensure_ascii=False, default=str)
                buf.append((d.get("timestamp"), ctype, gid, cname, d.get("sender_qq") or 0, sname,
                            d.get("direction"), d.get("msg_type"), rec.get("content_type"),
                            text, meta))
                added["group" if is_group else "c2c"] += 1
                n += 1
            flush()
            if n and n % 20000 < BATCH:
                print(f"  ... {tbl} +{n:,}  {time.time() - t0:.0f}s", flush=True)
        state[keyname] = last
        print(f"  ✓ {tbl} 新增 {n:,} 条（累计 {time.time() - t0:.0f}s）", flush=True)
        flush()

    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    dst.commit()
    total = dst.execute("SELECT COUNT(*) FROM msg").fetchone()[0]
    print("完成：", added, "| 记忆库总量:", f"{total:,}", flush=True)
    print("记忆库：", db, f"{os.path.getsize(db) / 1e6:.0f} MB", flush=True)
    dst.close()
    enc.close()
    return {"ok": True, "added": added, "total": total, "db": db, "elapsed": time.time() - t0}


def main() -> None:
    ap = argparse.ArgumentParser(description="增量/全量刷新记忆库")
    ap.add_argument("--db", default="", help="nt_msg.db 路径（留空自动探测）")
    ap.add_argument("--key", default="", help="SQLCipher 密钥（或用 QQMEM_DB_KEY）")
    ap.add_argument("--account", default="", help="QQ 号（多账号时指定）")
    ap.add_argument("--memory-dir", default="", help="记忆库目录")
    ap.add_argument("--work-dir", default="", help="快照中间目录")
    ap.add_argument("--full", action="store_true", help="全量重建（清空旧库）")
    args = ap.parse_args()

    key = args.key or os.environ.get("QQMEM_DB_KEY", "")
    db = args.db or paths.find_nt_msg("", args.account)
    if not db or not os.path.isfile(db):
        raise SystemExit("[错误] 未找到 nt_msg.db")
    if not key:
        raise SystemExit("[错误] 未提供密钥（--key 或 QQMEM_DB_KEY）")
    r = run(db, key, args.memory_dir, args.work_dir, full=args.full)
    if not r.get("ok"):
        sys.exit(2)


if __name__ == "__main__":
    main()
