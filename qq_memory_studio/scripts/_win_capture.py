# -*- coding: utf-8 -*-
"""抓取任意进程窗口截图（PrintWindow），供冻结 exe 验证用。

用法：
    python scripts/_win_capture.py "QQ Memory Studio · 长期记忆库管理" previews\\x.png
"""
from __future__ import annotations

import ctypes
import sys
import time

import win32gui
import win32ui
import win32con
from PIL import Image


def find_windows(keyword: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title and keyword in title:
            out.append((hwnd, title))
    win32gui.EnumWindows(cb, None)
    return out


def capture_hwnd(hwnd: int, path: str) -> bool:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return False
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    # PW_RENDERFULLCONTENT = 2
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits,
                           "raw", "BGRX", 0, 1)
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return True


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: _win_capture.py <title-keyword> <out.png>")
        return 2
    kw, out = sys.argv[1], sys.argv[2]
    for _ in range(30):
        ws = find_windows(kw)
        if ws:
            hwnd, title = ws[0]
            ok = capture_hwnd(hwnd, out)
            print(f"found hwnd={hwnd} title={title!r} captured={ok} -> {out}")
            return 0 if ok else 1
        time.sleep(0.5)
    print("window not found:", kw)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
