# -*- coding: utf-8 -*-
"""便携包（zip 解压）向导验证辅助。

子命令：
    launch            净化环境启动解压后的 exe
    cap <名> [title]  抓取窗口截图（PrintWindow）到 previews/<名>.png
    rect <title>      打印某窗口的窗口矩形 / 客户区矩形（用于换算点击坐标）
    click <title> <fx> <fy>
                      按客户区相对比例 (fx,fy∈[0,1]) 点击某窗口内一点
    rects <title>     列出该窗口内所有可见子控件矩形（调试用）
    info              打印 exe 目录 config.json 关键字段
    stop              结束 exe 进程

净化环境：PATH 仅 System32/Windows/Wbem；清空 ASTRBOT_ROOT/QQMEM_*；
USERPROFILE/TEMP/TMP/APPDATA/LOCALAPPDATA 指向沙盒内空目录。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

SANDBOX = r"C:\QQMS_Portable_Test"
EXE_DIR = os.path.join(SANDBOX, "QQMemoryStudio_portable")
EXE = os.path.join(EXE_DIR, "QQMemoryStudio.exe")
HOME = os.path.join(SANDBOX, "_home")
TMP = os.path.join(SANDBOX, "_temp")
PREVIEW = os.path.join(STUDIO_DIR, "previews")

MAIN_TITLE = "QQ Memory Studio · 长期记忆库管理"
GUIDE_TITLE = "首次使用向导"


def purified_env() -> dict:
    home = HOME
    return {
        "SystemRoot": r"C:\Windows", "SystemDrive": "C:", "windir": r"C:\Windows",
        "ComSpec": r"C:\Windows\System32\cmd.exe",
        "PATH": r"C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "TEMP": TMP, "TMP": TMP,
        "USERPROFILE": home, "HOMEDRIVE": "C:", "HOMEPATH": "\\QQMS_Portable_Test\\_home",
        "APPDATA": os.path.join(home, "AppData", "Roaming"),
        "LOCALAPPDATA": os.path.join(home, "AppData", "Local"),
        "USERNAME": "freshuser", "OS": "Windows_NT",
        "PROCESSOR_ARCHITECTURE": "AMD64", "NUMBER_OF_PROCESSORS": "4",
    }


def _find(keyword: str) -> int | None:
    import win32gui
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and keyword in t:
                found.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def _raise(hwnd: int) -> None:
    """把窗口强制提到最前（TOPMOST），避免被隐藏的控制台窗口挡住导致点击落空。"""
    import win32con
    import win32gui
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def cmd_launch(args) -> int:
    for d in (HOME, TMP):
        os.makedirs(d, exist_ok=True)
    cfg = os.path.join(EXE_DIR, "config.json")
    print("启动前 config.json 存在:", os.path.isfile(cfg))
    env = purified_env()
    print("PATH =", env["PATH"])
    p = subprocess.Popen([EXE], cwd=EXE_DIR, env=env)
    print("launched pid:", p.pid)
    return 0


def cmd_cap(args) -> int:
    import win32gui
    kw = args.title or GUIDE_TITLE
    hwnd = None
    for _ in range(80):
        hwnd = _find(kw)
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        print("窗口未找到:", kw)
        return 1
    title = win32gui.GetWindowText(hwnd)
    _raise(hwnd)
    time.sleep(0.6)
    os.makedirs(PREVIEW, exist_ok=True)
    out = os.path.join(PREVIEW, args.name + ".png")
    from _win_capture import capture_hwnd
    ok = capture_hwnd(hwnd, out)
    print(f"captured hwnd={hwnd} title={title!r} ok={ok} -> {out}")
    return 0 if ok else 1


def cmd_rect(args) -> int:
    import win32gui
    hwnd = _find(args.title)
    if not hwnd:
        print("窗口未找到:", args.title)
        return 1
    print("title:", win32gui.GetWindowText(hwnd))
    print("window rect:", win32gui.GetWindowRect(hwnd))
    print("client rect:", win32gui.GetClientRect(hwnd))
    print("dpi:", win32gui.GetDpiForWindow(hwnd) if hasattr(win32gui, "GetDpiForWindow") else "?")
    return 0


def cmd_rects(args) -> int:
    import win32gui
    hwnd = _find(args.title)
    if not hwnd:
        print("窗口未找到:", args.title)
        return 1
    out = []

    def cb(h, _):
        r = win32gui.GetWindowRect(h)
        t = win32gui.GetWindowText(h)
        cls = win32gui.GetClassName(h)
        out.append((h, t, cls, r))
    win32gui.EnumChildWindows(hwnd, cb, None)
    for h, t, cls, r in out:
        print(f"hwnd={h} class={cls} rect={r} text={t!r}")
    return 0


def cmd_click(args) -> int:
    import win32api
    import win32con
    import win32gui
    hwnd = _find(args.title)
    if not hwnd:
        print("窗口未找到:", args.title)
        return 1
    _raise(hwnd)
    time.sleep(0.5)
    cl = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (0, 0))
    cw, ch = cl[2] - cl[0], cl[3] - cl[1]
    x = int(tl[0] + cw * args.fx)
    y = int(tl[1] + ch * args.fy)
    win32api.SetCursorPos((x, y))
    time.sleep(0.15)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    print(f"clicked {args.title!r} rel=({args.fx},{args.fy}) -> screen=({x},{y}) client={cw}x{ch}")
    time.sleep(0.4)
    return 0


def cmd_key(args) -> int:
    import win32api
    import win32con
    import win32gui
    hwnd = _find(args.title)
    if hwnd:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    time.sleep(0.3)
    vk = int(args.vk, 0) if args.vk.lower().startswith("0x") else int(args.vk)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    print("sent key", vk)
    time.sleep(0.3)
    return 0


def cmd_info(args) -> int:
    cfg = os.path.join(EXE_DIR, "config.json")
    print("exe 目录 config.json:", cfg, "存在:", os.path.isfile(cfg))
    if os.path.isfile(cfg):
        d = json.load(open(cfg, encoding="utf-8-sig"))
        print(json.dumps({
            "paths": d.get("paths"),
            "embedding": d.get("embedding"),
            "ai": d.get("ai"),
            "ui": d.get("ui"),
        }, ensure_ascii=False, indent=2))
    return 0


def cmd_stop(args) -> int:
    out = subprocess.run(["taskkill", "/IM", "QQMemoryStudio.exe", "/T", "/F"],
                         capture_output=True, text=True)
    print(out.stdout, out.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("launch").set_defaults(fn=cmd_launch)
    p = sub.add_parser("cap"); p.add_argument("name"); p.add_argument("title", nargs="?", default=GUIDE_TITLE); p.set_defaults(fn=cmd_cap)
    p = sub.add_parser("rect"); p.add_argument("title"); p.set_defaults(fn=cmd_rect)
    p = sub.add_parser("rects"); p.add_argument("title"); p.set_defaults(fn=cmd_rects)
    p = sub.add_parser("click"); p.add_argument("title"); p.add_argument("fx", type=float); p.add_argument("fy", type=float); p.set_defaults(fn=cmd_click)
    p = sub.add_parser("key"); p.add_argument("title"); p.add_argument("vk"); p.set_defaults(fn=cmd_key)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
