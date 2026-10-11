# -*- coding: utf-8 -*-
"""调试2：用 win32 找到向导 HWND，再用 pywinauto(UIA) 包裹并列出子控件到文件。"""
from __future__ import annotations

import io
import sys
import time

import win32gui

out = io.StringIO()


def log(*a):
    print(*a, file=out)


def find_guide() -> int:
    found = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and "首次使用向导" in win32gui.GetWindowText(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else 0


h = find_guide()
log("guide hwnd:", h)
if h:
    from pywinauto import Application
    app = Application(backend="uia").connect(handle=h)
    dlg = app.window(handle=h)
    log("dlg text:", dlg.window_text())
    try:
        kids = dlg.descendants()
        log("descendants:", len(kids))
        for c in kids:
            try:
                log(f"  [{c.element_info.control_type}] {c.window_text()!r}")
            except Exception as e:  # noqa: BLE001
                log("  <err>", e)
    except Exception as e:  # noqa: BLE001
        log("descendants error:", repr(e))

with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("written")
