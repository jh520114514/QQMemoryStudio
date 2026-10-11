# -*- coding: utf-8 -*-
"""调试：把 UIA / win32 顶层窗口标题写到文件，排查 pywinauto 是否能看见向导窗口。"""
from __future__ import annotations

import io
import sys

out = io.StringIO()


def log(*a):
    print(*a, file=out)


try:
    from pywinauto import Desktop
    log("== uia top-level windows ==")
    d = Desktop(backend="uia")
    for w in d.windows():
        try:
            log(repr(w.window_text()), "|", w.element_info.control_type, "|", w.handle)
        except Exception as e:  # noqa: BLE001
            log("  <err>", e)
except Exception as e:  # noqa: BLE001
    log("uia error:", repr(e))

try:
    import win32gui
    log("== win32 top-level visible windows ==")
    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if t:
                log(repr(t), win32gui.GetClassName(h), h)
    win32gui.EnumWindows(cb, None)
except Exception as e:  # noqa: BLE001
    log("win32 error:", repr(e))

with open(sys.argv[1] if len(sys.argv) > 1 else "uia_debug.txt", "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("written")
