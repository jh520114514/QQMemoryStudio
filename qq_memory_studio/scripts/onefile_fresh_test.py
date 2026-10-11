# -*- coding: utf-8 -*-
"""单文件(onefile)版本的干净环境验证辅助。

子命令：
    setup            建沙盒 C:\\QQMS_OneFileTest、复制单文件 exe 进去
    launch           净化环境启动，并测量「冷启动到主窗口出现」的耗时(秒)
    info             打印 exe 同目录 / TEMP 下是否出现 config.json
    cap <名>         抓取顶层窗口截图到 previews/<名>.png
    stop            结束沙盒里的 QQMemoryStudio.exe

净化环境：PATH 只留 System32/Windows/Wbem；清空 ASTRBOT_ROOT/QQMEM_*；
USERPROFILE/TEMP/TMP/APPDATA/LOCALAPPDATA 指向沙盒内空目录。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SCRIPT_DIR)

SANDBOX = r"C:\QQMS_OneFileTest"
SRC_EXE = r"C:\QQMS_build_one\dist\QQMemoryStudio.exe"
EXE = os.path.join(SANDBOX, "QQMemoryStudio.exe")
HOME = os.path.join(SANDBOX, "_home")
TMP = os.path.join(SANDBOX, "_temp")
PREVIEW = os.path.join(STUDIO_DIR, "previews")
MAIN_TITLE = "QQ Memory Studio"


def purified_env() -> dict:
    return {
        "SystemRoot": r"C:\Windows", "SystemDrive": "C:", "windir": r"C:\Windows",
        "ComSpec": r"C:\Windows\System32\cmd.exe",
        "PATH": r"C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "TEMP": TMP, "TMP": TMP,
        "USERPROFILE": HOME, "HOMEDRIVE": "C:", "HOMEPATH": "\\QQMS_OneFileTest\\_home",
        "APPDATA": os.path.join(HOME, "AppData", "Roaming"),
        "LOCALAPPDATA": os.path.join(HOME, "AppData", "Local"),
        "USERNAME": "freshuser", "OS": "Windows_NT",
        "PROCESSOR_ARCHITECTURE": "AMD64", "NUMBER_OF_PROCESSORS": "4",
    }


def _find(kw: str) -> int | None:
    import win32gui
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and kw in t:
                found.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def cmd_setup(args) -> int:
    if os.path.isdir(SANDBOX) and not args.keep:
        print("清理旧沙盒:", SANDBOX)
        shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    os.makedirs(HOME, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    shutil.copy2(SRC_EXE, EXE)
    print("沙盒就绪:", SANDBOX)
    print("exe:", EXE, "存在:", os.path.isfile(EXE),
          "大小:", os.path.getsize(EXE), "字节")
    print("沙盒内是否含 config.json:", os.path.isfile(os.path.join(SANDBOX, "config.json")))
    return 0


def cmd_launch(args) -> int:
    for p in (os.path.join(SANDBOX, "config.json"),):
        if os.path.isfile(p):
            print("警告：启动前已存在", p)
    env = purified_env()
    print("PATH =", env["PATH"])
    print("USERPROFILE =", env["USERPROFILE"], "| TEMP =", env["TEMP"])
    t0 = time.time()
    p = subprocess.Popen([EXE], cwd=SANDBOX, env=env)
    print("launched pid:", p.pid)
    hwnd = None
    while time.time() - t0 < 180:
        hwnd = _find(MAIN_TITLE)
        if hwnd:
            break
        time.sleep(0.1)
    dt = time.time() - t0
    if hwnd:
        print(f"冷启动到主窗口出现：{dt:.2f}s (hwnd={hwnd})")
    else:
        print(f"超过 180s 仍未出现主窗口（已耗时 {dt:.1f}s）")
    return 0 if hwnd else 1


def cmd_info(args) -> int:
    exe_cfg = os.path.join(SANDBOX, "config.json")
    print("exe 同目录 config.json:", os.path.isfile(exe_cfg), exe_cfg)
    if os.path.isfile(exe_cfg):
        d = json.load(open(exe_cfg, encoding="utf-8-sig"))
        print("  memory_dir =", d.get("paths", {}).get("memory_dir"))
    # TEMP 下是否也有 config.json（不应有）
    tmp_cfg = os.path.join(TMP, "config.json")
    print("TEMP 下 config.json:", os.path.isfile(tmp_cfg), tmp_cfg)
    for root, dirs, files in os.walk(SANDBOX):
        for f in files:
            if f == "config.json":
                print("  找到 config.json:", os.path.join(root, f))
    for d in (os.path.join(SANDBOX, "qq_memory"),):
        print("memdir 存在?", d, os.path.isdir(d))
    return 0


def cmd_cap(args) -> int:
    hwnd = None
    for _ in range(60):
        hwnd = _find(args.title)
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        print("窗口未找到:", args.title)
        return 1
    import win32gui
    try:
        win32gui.ShowWindow(hwnd, 5)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.6)
    os.makedirs(PREVIEW, exist_ok=True)
    out = os.path.join(PREVIEW, args.name + ".png")
    from _win_capture import capture_hwnd
    ok = capture_hwnd(hwnd, out)
    print(f"captured hwnd={hwnd} ok={ok} -> {out}")
    return 0 if ok else 1


def cmd_stop(args) -> int:
    out = subprocess.run(["taskkill", "/IM", "QQMemoryStudio.exe", "/T", "/F"],
                         capture_output=True, text=True)
    print(out.stdout, out.stderr)
    return 0


def cmd_menu(args) -> int:
    import win32api
    import win32con
    import win32gui
    hwnd = _find(MAIN_TITLE)
    if not hwnd:
        print("主窗口未找到")
        return 1
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.5)

    def tap(vk):
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.18)

    tap(win32con.VK_MENU)
    tap(win32con.VK_DOWN)
    for _ in range(args.index):
        tap(win32con.VK_DOWN)
    tap(win32con.VK_RETURN)
    print(f"已发送菜单快捷键，index={args.index}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup"); p.add_argument("--keep", action="store_true"); p.set_defaults(fn=cmd_setup)
    sub.add_parser("launch").set_defaults(fn=cmd_launch)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    p = sub.add_parser("cap"); p.add_argument("name"); p.add_argument("--title", default=MAIN_TITLE); p.set_defaults(fn=cmd_cap)
    p = sub.add_parser("menu"); p.add_argument("index", type=int); p.set_defaults(fn=cmd_menu)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
