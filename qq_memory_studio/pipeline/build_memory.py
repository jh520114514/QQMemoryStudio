# -*- coding: utf-8 -*-
"""从明文库 nt_msg_plain.db 全量构建长期记忆库：

   - messages.db        : 结构化消息表 + FTS5 中文全文索引
   - messages_all.jsonl : 全量消息快照（每行一条）

用法::

    python build_memory.py [--src nt_msg_plain.db] [--memory-dir DIR] [--db FILE] [--jsonl FILE]

也可被 refresh_memory.py / sync.py 作为库导入（复用 GROUP_SQL / flatten_text）。
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

from msgdb.c2c import parser as c2c_parser          # noqa: E402
from msgdb.group import exporter as gexp            # noqa: E402


GROUP_SQL = gexp.SELECT_SQL.replace(
    'FROM group_msg_table',
    ', "40090" AS card_name, "40093" AS nick_name, "40010" AS chat_type FROM group_msg_table')


def connect(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    c.row_factory = sqlite3.Row
    return c


def build_name_maps(src: sqlite3.Connection):
    uid2uin, group_names, c2c_names = {}, {}, {}
    try:
        for r in src.execute("SELECT * FROM nt_uid_mapping_table"):
            uid2uin[r["48902"]] = r["1002"]
    except Exception:
        pass
    cols = [x[1] for x in src.execute("PRAGMA table_info(recent_contact_v3_table)")]
    try:
        for r in src.execute("SELECT * FROM recent_contact_v3_table"):
            d = dict(zip(cols, r))
            name = (d.get("40094") or "").strip()
            gid = str(d.get("40021") or "")
            peer_qq = d.get("40030") or 0
            if d.get("41102") == 2 or (gid.isdigit() and len(gid) >= 6):
                if name:
                    group_names[gid] = name
            if peer_qq and name:
                c2c_names[int(peer_qq)] = name
    except Exception:
        pass
    return uid2uin, group_names, c2c_names


def flatten_text(msg, rec: dict) -> str:
    """把解析结果整理成可检索的正文。"""
    parts = []
    if rec.get("text"):
        parts.append(str(rec["text"]))
    ct = rec.get("content_type")
    ct_map = {1: "文本", 2: "图片", 3: "文件", 4: "语音", 5: "视频", 6: "表情", 7: "回复",
              8: "系统", 9: "转发", 10: "混合", 11: "表情", 12: "通话", 13: "名片", 14: "位置"}
    c = rec.get("content") or {}
    if isinstance(c, dict):
        if c.get("text"):
            parts.append(str(c["text"]))
        if c.get("content") and isinstance(c["content"], str):
            parts.append(c["content"])
        if c.get("filename"):
            parts.append(f"[文件:{c['filename']}]")
        if c.get("title"):
            parts.append(str(c["title"]))
        if c.get("summary"):
            parts.append(str(c["summary"]))
    if not parts:
        parts.append(f"[{ct_map.get(ct, '消息')}]")
    # 去重：纯文本消息顶层 text 与 content.text 常重复
    seen, uniq = set(), []
    for p in parts:
        p = str(p).strip()
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return " ".join(uniq)


def main() -> None:
    ap = argparse.ArgumentParser(description="从明文库全量构建 messages.db")
    ap.add_argument("--src", default="", help="nt_msg_plain.db 路径")
    ap.add_argument("--memory-dir", default="", help="记忆库输出目录")
    ap.add_argument("--db", default="", help="messages.db 输出路径")
    ap.add_argument("--jsonl", default="", help="jsonl 快照输出路径")
    args = ap.parse_args()

    mem = paths.memory_dir(args.memory_dir)
    os.makedirs(mem, exist_ok=True)
    src_path = args.src or os.path.join(paths.work_dir(), "nt_msg_plain.db")
    db_path = args.db or os.path.join(mem, "messages.db")
    jsonl_path = args.jsonl or os.path.join(mem, "messages_all.jsonl")

    if not os.path.isfile(src_path):
        sys.exit(f"[错误] 找不到明文库: {src_path}（可先运行 decrypt.py）")

    t0 = time.time()
    src = connect(src_path)
    uid2uin, group_names, c2c_names = build_name_maps(src)
    print(f"名称映射: 群 {len(group_names)} 个, 好友 {len(c2c_names)} 个, uid {len(uid2uin)} 个")

    if os.path.exists(db_path):
        os.remove(db_path)
    dst = sqlite3.connect(db_path)
    dbinit.ensure_schema(dst)
    dst.commit()
    jf = open(jsonl_path, "w", encoding="utf-8")
    stats = {"c2c": 0, "group": 0, "err": 0, "text": 0}
    buf, jbuf = [], []

    def flush():
        if not buf:
            return
        dst.executemany("INSERT INTO msg(ts,chat_type,chat_id,chat_name,sender_qq,sender_name,direction,"
                        "msg_type,content_type,text,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)", buf)
        dst.commit()
        jf.writelines(jbuf)
        buf.clear()
        jbuf.clear()

    for tbl, parser in (("c2c_msg_table", c2c_parser), ("group_msg_table", gexp)):
        sql = c2c_parser.SELECT_SQL if tbl.startswith("c2c") else GROUP_SQL
        is_group = tbl.startswith("group")
        try:
            cursor = src.execute(sql)
        except Exception as e:  # noqa: BLE001
            print(f"  ! 读取 {tbl} 失败: {e}", flush=True)
            continue
        for raw in cursor:
            try:
                m = parser.parse_row(raw)
                rec = dataclasses.asdict(m) if dataclasses.is_dataclass(m) else (
                    m.to_dict() if hasattr(m, "to_dict") else dict(m))
            except Exception:
                stats["err"] += 1
                continue
            d = dict(raw)
            text = flatten_text(m, rec)
            if d.get("timestamp") is None:
                stats["err"] += 1
                continue
            if is_group:
                gid = str(d.get("group_id") or d.get("group_qq") or "")
                cname = group_names.get(gid, "")
                sender_qq = d.get("sender_qq") or 0
                sname = (d.get("card_name") or d.get("nick_name") or "").strip()
                stats["group"] += 1
                chat_type = "group"
            else:
                peer = int(d.get("peer_qq") or 0)
                gid = str(peer)
                cname = c2c_names.get(peer, "")
                sender_qq = d.get("sender_qq") or 0
                sname = c2c_names.get(int(sender_qq), "") or ("我" if d.get("direction") in (1, 2) else "")
                stats["c2c"] += 1
                chat_type = "c2c"
            meta = json.dumps({k: v for k, v in rec.items() if k not in ("text",) and v not in (None, "", {}, [])},
                              ensure_ascii=False, default=str)
            if text and not text.startswith("["):
                stats["text"] += 1
            buf.append((d.get("timestamp"), chat_type, gid, cname, sender_qq, sname,
                        d.get("direction"), d.get("msg_type"), rec.get("content_type"), text, meta))
            jbuf.append(json.dumps(
                {"ts": d.get("timestamp"),
                 "t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.get("timestamp") or 0)),
                 "chat": f"{chat_type}:{gid}", "chat_name": cname,
                 "sender": sender_qq, "sender_name": sname,
                 "dir": d.get("direction"), "type": d.get("msg_type"), "text": text},
                ensure_ascii=False) + "\n")
            if len(buf) >= 20000:
                flush()
                print(f"  ... c2c {stats['c2c']:,} / group {stats['group']:,}  {time.time() - t0:.0f}s", flush=True)
    flush()
    print("重建普通 FTS 索引 ...", flush=True)
    try:
        dst.execute("INSERT INTO msg_fts(msg_fts) VALUES('rebuild')")
        dst.commit()
        dst.execute("ANALYZE")
        dst.commit()
    except Exception as e:  # noqa: BLE001
        print("  (FTS 重建跳过)", e)
    jf.close()
    print(f"完成: c2c {stats['c2c']:,} group {stats['group']:,} 解析失败 {stats['err']:,} 有文字 {stats['text']:,}")
    print("输出:", db_path, f"{os.path.getsize(db_path) / 1e6:.0f} MB")
    print(f"[提示] 中文检索需再运行 build_cjk_fts.py 建立 msg_fts_cjk 索引")
    dst.close()


if __name__ == "__main__":
    main()
