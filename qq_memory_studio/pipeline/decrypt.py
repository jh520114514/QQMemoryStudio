# -*- coding: utf-8 -*-
"""解密 nt_msg.db -> 明文 SQLite。

用法::

    python decrypt.py --db <nt_msg.db> --key <16字符密钥> [--work-dir DIR] [--plain FILE]
    # 密钥也可用环境变量 QQMEM_DB_KEY 传入；--db 省略则自动探测本机 QQ 数据目录。

步骤：
  1) 剥掉 1024 字节 NTQQ 自定义头 -> nt_msg_clear.db
  2) sqlcipher3 验证密钥（PRAGMA 顺序敏感）
  3) 优先 sqlcipher_export 导出明文；遇损坏页退化为「rowid 游标 + 跳过坏页」逐表导出

依赖：sqlcipher3
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

import paths

HEADER = 1024
BATCH = 5000
PRAGMAS = (
    "PRAGMA cipher_page_size = 4096;",       # 必须在 key 之前
    "PRAGMA key = '{key}';",
    "PRAGMA kdf_iter = 4000;",
    "PRAGMA cipher_hmac_algorithm = HMAC_SHA1;",
    "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;",
)


def strip_header(src: str, dst: str) -> None:
    exp = os.path.getsize(src) - HEADER
    if os.path.exists(dst) and os.path.getsize(dst) == exp:
        print(f"[跳过] {os.path.basename(dst)} 已存在")
        return
    print(f"[1/3] 剥离 {HEADER} 字节头 -> {os.path.basename(dst)} ...", flush=True)
    t0 = time.time()
    with open(src, "rb") as f, open(dst, "wb") as o:
        f.seek(HEADER)
        while True:
            b = f.read(64 << 20)
            if not b:
                break
            o.write(b)
    print(f"      {os.path.getsize(dst):,} bytes  {time.time() - t0:.0f}s")


def open_enc(path: str, key: str):
    import sqlcipher3.dbapi2 as sc

    conn = sc.connect(str(path), isolation_level=None)
    conn.execute(PRAGMAS[0])
    conn.execute(PRAGMAS[1].format(key=key.replace("'", "''")))
    for p in PRAGMAS[2:]:
        conn.execute(p)
    conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    return conn


def export_plaintext(enc, plain_path: str) -> bool:
    """优先用 sqlcipher_export；失败返回 False。"""
    try:
        if os.path.exists(plain_path):
            os.remove(plain_path)
        enc.execute(f"ATTACH DATABASE '{plain_path}' AS plaintext KEY '';")
        enc.execute("SELECT sqlcipher_export('plaintext');")
        enc.execute("DETACH DATABASE plaintext;")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"      sqlcipher_export 失败（{e}），改用逐表游标导出", flush=True)
        return False


def export_table(clear_path: str, key: str, plain: sqlite3.Connection, table: str) -> tuple[int, int]:
    """rowid 游标分页导出，遇损坏页自动跳过。"""
    enc = open_enc(clear_path, key)
    ddl = enc.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    import re

    ddl_safe = re.sub(r"^CREATE\s+TABLE\s+", "CREATE TABLE IF NOT EXISTS ", ddl, flags=re.IGNORECASE)
    plain.execute(ddl_safe)
    plain.commit()
    ncols = len(enc.execute(f'PRAGMA table_info("{table}")').fetchall())
    ph = ",".join("?" * ncols)
    try:
        last = plain.execute(f'SELECT max(rowid) FROM "{table}"').fetchone()[0] or 0
    except Exception:
        last = 0
    ok = bad = 0
    batch = BATCH
    t0 = time.time()
    while True:
        try:
            rows = enc.execute(
                f'SELECT rowid, * FROM "{table}" WHERE rowid > ? ORDER BY rowid LIMIT ?',
                (last, batch),
            ).fetchall()
        except Exception:
            enc.close()
            enc = open_enc(clear_path, key)
            if batch == 1:
                last += 1
                bad += 1
                batch = min(BATCH, 100)
            else:
                batch = max(1, batch // 4)
            continue
        if not rows:
            break
        last = rows[-1][0]
        plain.executemany(f'INSERT OR IGNORE INTO "{table}" VALUES ({ph})', [r[1:] for r in rows])
        plain.commit()
        ok += len(rows)
        if batch < BATCH:
            batch = min(BATCH, batch * 2)
    enc.close()
    print(f"      {table}: {ok:,} 行，跳过 {bad} 处损坏  {time.time() - t0:.0f}s", flush=True)
    return ok, bad


def main() -> None:
    ap = argparse.ArgumentParser(description="解密 NTQQ nt_msg.db")
    ap.add_argument("--db", default="", help="nt_msg.db 路径（留空自动探测）")
    ap.add_argument("--key", default="", help="16 字符 SQLCipher 密钥（或用 QQMEM_DB_KEY）")
    ap.add_argument("--account", default="", help="QQ 号（多账号时指定）")
    ap.add_argument("--work-dir", default="", help="中间产物目录，默认插件 data 目录")
    ap.add_argument("--plain", default="", help="明文库输出路径，默认 <work>/nt_msg_plain.db")
    args = ap.parse_args()

    key = args.key or os.environ.get("QQMEM_DB_KEY", "")
    db = args.db or paths.find_nt_msg("", args.account)
    if not db:
        sys.exit("[错误] 未找到 QQ 数据目录 / nt_msg.db（可用 --db 指定）")
    if not os.path.isfile(db):
        sys.exit(f"[错误] 找不到 nt_msg.db: {db}")
    if not key:
        sys.exit("[错误] 未提供密钥（--key 或环境变量 QQMEM_DB_KEY）")

    work = paths.work_dir(args.work_dir)
    os.makedirs(work, exist_ok=True)
    clear = os.path.join(work, "nt_msg_clear.db")
    plain = args.plain or os.path.join(work, "nt_msg_plain.db")

    print(f"[i] 源库: {db}  ({os.path.getsize(db) / 1e6:.0f} MB)")
    strip_header(db, clear)

    print(f"[2/3] 验证密钥 (长度 {len(key)}) ...", flush=True)
    try:
        enc = open_enc(clear, key)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"[错误] 解密失败，密钥可能不正确: {e}")
    tables = [r[0] for r in enc.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f"      ✅ 密钥正确，共 {len(tables)} 张表")

    print(f"[3/3] 导出明文 -> {os.path.basename(plain)} ...", flush=True)
    if not export_plaintext(enc, plain):
        enc.close()
        pconn = sqlite3.connect(plain)
        for t in tables:
            if t.startswith("sqlite_"):
                continue
            export_table(clear, key, pconn, t)
        pconn.close()
    else:
        enc.close()

    size = os.path.getsize(plain) if os.path.exists(plain) else 0
    print(f"      ✅ 明文库 {size / 1024 ** 3:.2f} GB -> {plain}")
    print(f"[完成] 下一步可运行 build_memory.py 构建记忆库")


if __name__ == "__main__":
    main()
