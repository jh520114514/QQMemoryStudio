# -*- coding: utf-8 -*-
"""验证 docx 降级路径：屏蔽 python-docx 后走 zipfile + 最小 OOXML。"""
import os
import sys
import zipfile

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, STUDIO)

# 强制 import docx 失败
sys.modules["docx"] = None  # type: ignore

import xml.dom.minidom as minidom  # noqa: E402

from core.config import Config  # noqa: E402
from core.exporter import export_chat  # noqa: E402
from core.memory import MemoryStore  # noqa: E402


def main() -> int:
    out = os.path.join(STUDIO, "exports", "verify_fallback_docx.docx")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cfg = Config.load()
    store = MemoryStore(cfg.memory_dir)
    res = export_chat(store, {"chat_type": "group", "chat_id": "1027129163",
                              "chat_name": "小兽太实验舱", "fmt": "docx", "out_path": out,
                              "by_day": True, "show_qq": True},
                      on_log=lambda s: print("  [log]", s))
    print("result:", res)
    with zipfile.ZipFile(out) as z:
        print("zip 成员:", z.namelist())
        xml = z.read("word/document.xml").decode("utf-8")
    minidom.parseString(xml)  # XML 合法性校验
    print("document.xml 合法，长度 =", len(xml))
    # 用 python-docx 尝试打开（验证互操作性）
    del sys.modules["docx"]
    from docx import Document
    d = Document(out)
    print("python-docx 也能打开降级文件：段落数 =", len(d.paragraphs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
