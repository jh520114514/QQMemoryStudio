# -*- coding: utf-8 -*-
"""干净环境（无 Python、无本项目痕迹）启动验证。

子命令：
    setup     建沙盒 C:\\QQMS_FreshTest 并复制 dist\\QQMemoryStudio 进去
    launch    用净化环境启动 exe，打印 PID
    info      打印 exe 同目录 config.json 内容 + 沙盒目录结构
    cap <名>   抓取顶层窗口截图到 previews/<名>.png（按标题关键字）
    menu <i>   （前台）用键盘打开「窗口」菜单第 i 项（0=检索窗口,1=AI,2=会话导出）
    stop      结束沙盒里的 QQMemoryStudio.exe
    status     列出沙盒里的 exe 进程

净化环境要点：PATH 去掉所有 Python 相关项；清空 ASTRBOT_ROOT/QQMEM_*；
USERPROFILE / TEMP / TMP / APPDATA / LOCALAPPDATA 指向沙盒内空目录。
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
sys.path.insert(0, SCRIPT_DIR)

SANDBOX = r"C:\QQMS_FreshTest"
SRC_DIST = r"C:\QQMS_build\dist\QQMemoryStudio"
EXE_DIR = os.path.join(SANDBOX, "QQMemoryStudio")
EXE = os.path.join(EXE_DIR, "QQMemoryStudio.exe")
HOME = os.path.join(SANDBOX, "_home")
TMP = os.path.join(SANDBOX, "_temp")
PREVIEW = os.path.join(STUDIO_DIR, "previews")

MAIN_TITLE = "QQ Memory Studio · 长期记忆库管理"
WIN_TITLES = {"search": "QQ 记忆库 · 检索与浏览", "ai": "QQ 记忆库 · AI 检索助手",
              "export": "QQ 记忆库 · 会话导出"}


def purified_env() -> dict:
    e = {
        "SystemRoot": r"C:\Windows", "SystemDrive": "C:", "windir": r"C:\Windows",
        "ComSpec": r"C:\Windows\System32\cmd.exe",
        # 纯净 PATH：无任何 Python / venv / components\python
        "PATH": r"C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "TEMP": TMP, "TMP": TMP,
        "USERPROFILE": HOME, "HOMEDRIVE": "C:", "HOMEPATH": "\\QQMS_FreshTest\\_home",
        "APPDATA": os.path.join(HOME, "AppData", "Roaming"),
        "LOCALAPPDATA": os.path.join(HOME, "AppData", "Local"),
        "USERNAME": "freshuser", "OS": "Windows_NT",
        "PROCESSOR_ARCHITECTURE": "AMD64", "NUMBER_OF_PROCESSORS": "4",
        # 明确不设 ASTRBOT_ROOT / QQMEM_CMD_CONFIG / QQMEM_* / PYTHON*
    }
    return e


def cmd_setup(args) -> int:
    if os.path.isdir(SANDBOX):
        if not args.keep:
            print("清理旧沙盒:", SANDBOX)
            shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    os.makedirs(HOME, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    if not os.path.isdir(EXE_DIR):
        print("复制", SRC_DIST, "->", EXE_DIR)
        shutil.copytree(SRC_DIST, EXE_DIR)
    print("沙盒就绪:", SANDBOX)
    print("exe:", EXE, "存在:", os.path.isfile(EXE))
    print("沙盒内是否含 config.json:", os.path.isfile(os.path.join(EXE_DIR, "config.json")))
    print("沙盒内是否含 qq_memory:", os.path.isdir(os.path.join(EXE_DIR, "qq_memory")))
    return 0


def cmd_launch(args) -> int:
    env = purified_env()
    print("PATH =", env["PATH"])
    print("USERPROFILE =", env["USERPROFILE"], "| TEMP =", env["TEMP"])
    print("ASTRBOT_ROOT in env:", "ASTRBOT_ROOT" in env, "| QQMEM_CMD_CONFIG:", "QQMEM_CMD_CONFIG" in env)
    p = subprocess.Popen([EXE], cwd=EXE_DIR, env=env)
    print("launched pid:", p.pid)
    return 0


def _find(keyword: str) -> int | None:
    import win32gui
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and keyword in t:
                found.append((hwnd, t))
    win32gui.EnumWindows(cb, None)
    return found[0][0] if found else None


def cmd_cap(args) -> int:
    import win32gui
    hwnd = None
    for _ in range(40):
        for kw in ([args.title] if args.title else WIN_TITLES.values()):
            hwnd = _find(kw)
            if hwnd:
                break
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        print("窗口未找到:", args.title or "任意已知标题")
        return 1
    title = win32gui.GetWindowText(hwnd)
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
    print(f"captured hwnd={hwnd} title={title!r} ok={ok} -> {out}")
    return 0 if ok else 1


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
    except Exception as e:
        print("前置窗口失败:", e)
    time.sleep(0.5)

    def tap(vk):
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.18)

    tap(win32con.VK_MENU)          # 聚焦菜单栏（第一个菜单：窗口）
    tap(win32con.VK_DOWN)          # 展开
    for _ in range(args.index):
        tap(win32con.VK_DOWN)
    tap(win32con.VK_RETURN)
    print(f"已发送菜单快捷键，index={args.index}")
    return 0


def cmd_info(args) -> int:
    cfg = os.path.join(EXE_DIR, "config.json")
    print("exe 目录:", EXE_DIR)
    print("config.json:", cfg, "存在:", os.path.isfile(cfg))
    if os.path.isfile(cfg):
        data = json.load(open(cfg, encoding="utf-8-sig"))
        print(json.dumps({k: data.get(k) for k in ("version", "paths", "qq")},
                         ensure_ascii=False, indent=2))
    for d in (os.path.join(EXE_DIR, "qq_memory"), os.path.join(SANDBOX, "qq_memory")):
        print("memdir 存在? ", d, os.path.isdir(d))
    return 0


def cmd_status(args) -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='QQMemoryStudio.exe'\" | "
         "Select-Object ProcessId,ExecutablePath | Format-Table -AutoSize | Out-String"],
        capture_output=True, text=True)
    print(out.stdout or out.stderr)
    return 0


def _paste_text(text: str) -> None:
    """通过剪贴板 + Ctrl+V 输入任意文本（含中文），比 SendInput 更可靠。"""
    import win32api
    import win32clipboard
    import win32con
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)


def cmd_type(args) -> int:
    """聚焦某窗口后点击其内容区相对坐标 (x,y)，输入文本，按回车。"""
    import win32api
    import win32con
    import win32gui
    hwnd = _find(args.title)
    if not hwnd:
        print("窗口未找到:", args.title)
        return 1
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    win32api.SetCursorPos((l + args.x, t + args.y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.3)
    _paste_text(args.text)
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
    print(f"typed {args.text!r} into {args.title!r} @({args.x},{args.y})")
    return 0


def cmd_stop(args) -> int:
    out = subprocess.run(
        ["taskkill", "/IM", "QQMemoryStudio.exe", "/T", "/F"],
        capture_output=True, text=True)
    print(out.stdout, out.stderr)
    return 0


def cmd_poke(args) -> int:
    """修改沙盒 config.json 的 memory_dir，用于验证「重启后配置仍在且被读取」。"""
    cfg = os.path.join(EXE_DIR, "config.json")
    data = json.load(open(cfg, encoding="utf-8-sig"))
    data.setdefault("paths", {})["memory_dir"] = args.memory_dir
    json.dump(data, open(cfg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写入 memory_dir =", args.memory_dir)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup"); p.add_argument("--keep", action="store_true"); p.set_defaults(fn=cmd_setup)
    sub.add_parser("launch").set_defaults(fn=cmd_launch)
    p = sub.add_parser("cap"); p.add_argument("name"); p.add_argument("--title", default=""); p.set_defaults(fn=cmd_cap)
    p = sub.add_parser("menu"); p.add_argument("index", type=int); p.set_defaults(fn=cmd_menu)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    p = sub.add_parser("type")
    p.add_argument("title"); p.add_argument("x", type=int); p.add_argument("y", type=int)
    p.add_argument("text", nargs="?", default=""); p.set_defaults(fn=cmd_type)
    p = sub.add_parser("poke"); p.add_argument("memory_dir"); p.set_defaults(fn=cmd_poke)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
