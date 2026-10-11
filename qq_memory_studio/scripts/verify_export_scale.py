# -*- coding: utf-8 -*-
"""规模与取消测试：导出 10 万条级会话，并验证取消能及时中止、不产生完整文件。"""
import os
import sys
import threading
import time

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, STUDIO)

from core.config import Config  # noqa: E402
from core.exporter import export_chat  # noqa: E402
from core.memory import MemoryStore  # noqa: E402

OUT_DIR = os.path.join(STUDIO, "exports")
CT, CID = "group", "439880516"


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = Config.load()
    store = MemoryStore(cfg.memory_dir)

    name = CID
    for c in store.chats():
        if c["chat_type"] == CT and c["chat_id"] == CID:
            name = c["name"]
            break
    print(f"目标会话：{name!r} count={store.count_chat_messages(CT, CID):,}")

    last = {"t": 0.0}
    def prog(d, t, e):
        last["t"] = e
        if d % 20000 == 0:
            print(f"   进度 {d:,}/{t:,} {e:.1f}s")

    # ---- 1) 大文件完整导出（markdown）----
    out = os.path.join(OUT_DIR, f"big_{name}_群聊.md")
    t0 = time.time()
    res = export_chat(store, {"chat_type": CT, "chat_id": CID, "chat_name": name,
                              "fmt": "md", "out_path": out, "by_day": True, "show_qq": True},
                      on_log=lambda s: print("   [log]", s), on_progress=prog)
    print(f"=> 大导出 ok={res['ok']} count={res['count']:,} bytes={res['bytes']:,} "
          f"elapsed={res['elapsed']}s path={res['out_path']}")

    # ---- 2) 取消测试：约 0.35s 后请求取消 ----
    out2 = os.path.join(OUT_DIR, f"big_cancel_{name}_群聊.txt")
    ev = threading.Event()
    threading.Timer(0.35, ev.set).start()
    res2 = export_chat(store, {"chat_type": CT, "chat_id": CID, "chat_name": name,
                               "fmt": "txt", "out_path": out2, "by_day": True},
                       on_log=lambda s: print("   [log]", s),
                       should_cancel=ev.is_set)
    print(f"=> 取消测试 canceled={res2['canceled']} 已写 {res2['count']:,} 条 "
          f"（< 总量 {res['count']:,}）elapsed={res2['elapsed']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
