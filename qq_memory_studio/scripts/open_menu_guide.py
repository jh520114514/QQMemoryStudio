# -*- coding: utf-8 -*-
"""列出并操作主窗口菜单（UIA）。用于验证「帮助 → 使用向导」可手动再次打开。

用法：
    python scripts/open_menu_guide.py list
    python scripts/open_menu_guide.py open      # 打开 帮助->使用向导
"""
from __future__ import annotations

import sys
import time

import win32gui
from pywinauto import Application

MAIN_TITLE = "QQ Memory Studio"


def find_main() -> int:
    found = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and MAIN_TITLE in win32gui.GetWindowText(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else 0


def main_win():
    h = find_main()
    if not h:
        raise SystemExit("主窗口未找到")
    app = Application(backend="uia").connect(handle=h)
    return app.window(handle=h)


def cmd_list(w) -> int:
    for c in w.descendants():
        try:
            ct = c.element_info.control_type
            if ct in ("MenuBar", "MenuItem", "Menu"):
                print(ct, "|", repr(c.window_text()))
        except Exception:
            continue
    return 0


def find_popup() -> int:
    """Qt 弹出菜单是一个 class 含 'QWindowPopup' 的可见窗口。"""
    found = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and "QWindowPopup" in win32gui.GetClassName(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else 0


def cmd_open(w) -> int:
    # 已有弹出菜单则直接用；否则先点「帮助」
    if not find_popup():
        help_item = None
        for c in w.descendants(control_type="MenuItem"):
            try:
                if c.window_text() == "帮助":
                    help_item = c
                    break
            except Exception:
                continue
        if help_item is None:
            print("未找到『帮助』菜单")
            return 1
        help_item.click_input()
        print("clicked 帮助")
        time.sleep(0.8)
    pop = 0
    for _ in range(20):
        pop = find_popup()
        if pop:
            break
        time.sleep(0.2)
    if not pop:
        print("未找到弹出菜单窗口")
        return 1
    app = Application(backend="uia").connect(handle=pop)
    pw = app.window(handle=pop)
    target = None
    for item in pw.descendants(control_type="MenuItem"):
        try:
            if "使用向导" in item.window_text():
                target = item
                break
        except Exception:
            continue
    if target is None:
        print("未找到『使用向导』菜单项")
        return 1
    target.click_input()
    print("clicked 使用向导")
    time.sleep(0.8)
    return 0


def cmd_openmenu(w) -> int:
    if not find_popup():
        for c in w.descendants(control_type="MenuItem"):
            try:
                if c.window_text() == "帮助":
                    c.click_input()
                    print("clicked 帮助")
                    time.sleep(0.8)
                    break
            except Exception:
                continue
    pop = find_popup()
    print("popup hwnd:", pop)
    return 0 if pop else 1


def cmd_opencap(w, name: str) -> int:
    if not find_popup():
        for c in w.descendants(control_type="MenuItem"):
            try:
                if c.window_text() == "帮助":
                    c.click_input()
                    time.sleep(0.8)
                    break
            except Exception:
                continue
    pop = 0
    for _ in range(20):
        pop = find_popup()
        if pop:
            break
        time.sleep(0.2)
    if not pop:
        print("无弹出菜单")
        return 1
    import os
    import win32con
    try:
        win32gui.SetWindowPos(pop, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    except Exception:
        pass
    time.sleep(0.4)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    studio = os.path.dirname(scripts_dir)
    sys.path.insert(0, scripts_dir)
    from _win_capture import capture_hwnd
    out = os.path.join(studio, "previews", name + ".png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ok = capture_hwnd(pop, out)
    print(f"captured popup hwnd={pop} ok={ok} -> {out}")
    return 0 if ok else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    w = main_win()
    if cmd == "list":
        return cmd_list(w)
    if cmd == "open":
        return cmd_open(w)
    if cmd == "openmenu":
        return cmd_openmenu(w)
    if cmd == "opencap":
        return cmd_opencap(w, sys.argv[2])
    print("unknown")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
