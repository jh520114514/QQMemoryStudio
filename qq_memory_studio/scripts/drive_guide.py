# -*- coding: utf-8 -*-
"""用 pywinauto(UIA) + win32 驱动「首次使用向导」：按控件名点击、填入文本。

（pywinauto 的 Desktop().windows() 未把该模态对话框列为顶层窗口，故先用 win32
 按标题找到 HWND，再用 UIA 按 handle 连接。）

用法：
    python scripts/drive_guide.py head
    python scripts/drive_guide.py next                 # 点「下一步」或「完成」
    python scripts/drive_guide.py click <按钮名>
    python scripts/drive_guide.py setmem <路径>
    python scripts/drive_guide.py setemb <base> <key> <model>
    python scripts/drive_guide.py texts                # 打印所有 Text 控件
"""
from __future__ import annotations

import sys
import time

import win32gui
from pywinauto import Application


def find_guide() -> int:
    found = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and "首次使用向导" in win32gui.GetWindowText(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else 0


def dlg():
    for _ in range(40):
        h = find_guide()
        if h:
            app = Application(backend="uia").connect(handle=h)
            return app.window(handle=h)
        time.sleep(0.25)
    raise SystemExit("向导窗口未找到")


def _button(w, name):
    for c in w.descendants(control_type="Button"):
        try:
            if c.window_text() == name:
                return c
        except Exception:
            continue
    return None


def cmd_head(w) -> int:
    for c in w.descendants(control_type="Text"):
        try:
            n = c.window_text()
            if n.startswith("第 ") and n.endswith("步 · 这是什么") or (n.startswith("第 ") and "步 /" in n):
                print("HEAD:", n)
                return 0
        except Exception:
            continue
    print("HEAD: (未找到)")
    return 1


def cmd_texts(w) -> int:
    for c in w.descendants(control_type="Text"):
        try:
            print("TEXT:", c.window_text())
        except Exception:
            continue
    return 0


def cmd_next(w) -> int:
    for name in ("下一步", "完成"):
        b = _button(w, name)
        if b:
            b.click_input()
            print("clicked:", name)
            time.sleep(0.6)
            return 0
    print("未找到「下一步/完成」")
    return 1


def cmd_click(w, name) -> int:
    b = _button(w, name)
    if not b:
        print("按钮未找到:", name)
        return 1
    b.click_input()
    print("clicked:", name)
    time.sleep(0.6)
    return 0


def _visible_edits(w):
    out = []
    for c in w.descendants(control_type="Edit"):
        try:
            if c.is_visible() and c.is_enabled():
                out.append(c)
        except Exception:
            continue
    return out


def cmd_setmem(w, path) -> int:
    edits = _visible_edits(w)
    print("visible edits:", len(edits))
    if not edits:
        return 1
    edits[0].set_edit_text(path)
    print("set mem dir ->", path)
    return 0


def cmd_setemb(w, base, key, model) -> int:
    edits = _visible_edits(w)
    vals = [base, key, model]
    for i, e in enumerate(edits[:3]):
        e.set_edit_text(vals[i])
    print("set emb:", vals)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    w = dlg()
    if cmd == "head":
        return cmd_head(w)
    if cmd == "texts":
        return cmd_texts(w)
    if cmd == "next":
        return cmd_next(w)
    if cmd == "click":
        return cmd_click(w, sys.argv[2])
    if cmd == "setmem":
        return cmd_setmem(w, sys.argv[2])
    if cmd == "setemb":
        return cmd_setemb(w, sys.argv[2], sys.argv[3], sys.argv[4])
    print("unknown cmd")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
