# -*- coding: utf-8 -*-
"""无头验证：把群 1027129163 的全部记录导出为 md/txt/html/docx，并做校验。"""
import os
import sys
import time

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, STUDIO)

from core.config import Config  # noqa: E402
from core.exporter import export_chat, default_filename  # noqa: E402
from core.memory import MemoryStore  # noqa: E402

OUT_DIR = os.path.join(STUDIO, "exports")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = Config.load()
    store = MemoryStore(cfg.memory_dir)
    ct, cid = "group", "1027129163"

    # 取会话名
    name = cid
    for c in store.chats():
        if c["chat_type"] == ct and c["chat_id"] == cid:
            name = c["name"]
            break
    print(f"会话名={name!r}")

    for fmt in ("md", "txt", "html", "docx"):
        fname = default_filename(name, ct, fmt)
        path = os.path.join(OUT_DIR, "verify_" + fname)
        t0 = time.time()
        res = export_chat(
            store,
            {"chat_type": ct, "chat_id": cid, "chat_name": name, "fmt": fmt,
             "out_path": path, "by_day": True, "show_qq": True},
            on_log=lambda s: print("   [log]", s),
            on_progress=None)
        print(f"=> {fmt}: ok={res['ok']} count={res['count']} bytes={res['bytes']} "
              f"elapsed={res['elapsed']}s degrad={res['degraded']} err={res['error']}")
        print(f"   文件：{path}")

    # 校验 docx
    docx_path = os.path.join(OUT_DIR, "verify_" + default_filename(name, ct, "docx"))
    try:
        from docx import Document
        d = Document(docx_path)
        paras = d.paragraphs
        print(f"[docx] python-docx 打开成功：段落数={len(paras)}，标题数={sum(1 for p in paras if p.style.name.startswith('Heading'))}")
        print("[docx] 前 8 段：")
        for p in paras[:8]:
            print("   |", p.style.name, "|", p.text[:80])
    except Exception as e:  # noqa: BLE001
        print("[docx] 打开失败：", e)

    # 校验 md 前 20 行
    md_path = os.path.join(OUT_DIR, "verify_" + default_filename(name, ct, "md"))
    print("[md] 前 20 行：")
    with open(md_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 20:
                break
            print("   ", line.rstrip("\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
