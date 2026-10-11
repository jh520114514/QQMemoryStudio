# -*- coding: utf-8 -*-
"""从 QQ(NTQQ) 抓取数据库密钥（Windows）。

原理（参考开源项目 QQBackup/qq-win-db-key）：
  1) 静态解析 wrapper.node，找到字符串 "nt_sqlite3_key_v2: db=%p zDb=%s"
  2) 找到引用该字符串的 LEA 指令 -> 所属函数 RVA（用 .pdata 异常表定位）
  3) 以调试方式启动 QQ.exe，在 wrapper.node+函数RVA 处下 INT3 断点
  4) 断点命中时，密钥字符串指针在 R8（x64 调用约定第3参），读出来即密钥
  5) 抓完用 DebugActiveProcessStop 干净脱离，QQ 继续正常运行

用法::

    python extract_key.py [--qq-exe QQ.exe] [--wrapper wrapper.node] [--out keys_found.json]

留空参数时自动探测本机 QQ 安装路径与 wrapper.node 版本目录（支持多版本）。
需要在管理员权限下运行，且 QQ 需处于登录状态。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import struct
import sys
import time

import paths

# ---------------------------------------------------------------- 自动探测
def detect_qq_exe() -> str:
    cands = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    for base in (pf, pf86):
        cands.append(os.path.join(base, "Tencent", "QQNT", "QQ.exe"))
        cands.append(os.path.join(base, "Tencent", "QQ", "QQ.exe"))
    if local:
        cands.append(os.path.join(local, "Programs", "Tencent", "QQNT", "QQ.exe"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0] if cands else ""


def detect_wrapper(qq_exe: str) -> str:
    if not qq_exe:
        return ""
    base = os.path.dirname(qq_exe)
    pats = [
        os.path.join(base, "versions", "*", "resources", "app", "wrapper.node"),
        os.path.join(base, "resources", "app", "wrapper.node"),
    ]
    hits: list[str] = []
    for p in pats:
        hits.extend(glob.glob(p))
    if not hits:
        return ""
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0]


# ---------------------------------------------------------------- PE 静态分析
def find_function_rva(path, pattern=b"nt_sqlite3_key_v2: db=%p zDb=%s"):
    data = open(path, "rb").read()
    if data[:2] != b"MZ":
        raise RuntimeError("不是 PE 文件")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    assert struct.unpack_from("<I", data, e_lfanew)[0] == 0x00004550, "PE 签名错误"
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    sizeopt = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    assert struct.unpack_from("<H", data, opt)[0] == 0x20B, "需要 PE32+"
    ddir = opt + 112
    exc_rva, exc_size = struct.unpack_from("<II", data, ddir + 3 * 8)
    secs = {}
    sh = opt + sizeopt
    for i in range(nsec):
        o = sh + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("ascii", "ignore")
        vsz, va, rawsz, rawptr = struct.unpack_from("<IIII", data, o + 8)
        secs[name] = (va, vsz, rawptr, rawsz)
    rva2off = lambda r: next(off + (r - va) for (va, vsz, off, sz) in secs.values() if va <= r < va + max(vsz, sz))

    rva, vsz, rawptr, rawsz = secs[".rdata"]
    i = data.find(pattern, rawptr, rawptr + rawsz)
    if i < 0:
        raise RuntimeError("找不到目标字符串")
    str_rva = rva + (i - rawptr)
    print(f"[i] 目标字符串 RVA = 0x{str_rva:X}")

    tva, tvsz, tptr, tsz = secs[".text"]
    text = data[tptr:tptr + tsz]
    lea_rva = None
    for j in range(1, len(text) - 7):
        if text[j] != 0x8D:
            continue
        rex = text[j - 1]
        if (rex & 0xF8) != 0x48:
            continue
        modrm = text[j + 1]
        if (modrm & 0xC7) != 0x05:
            continue
        disp = struct.unpack_from("<i", text, j + 2)[0]
        instr_rva = tva + (j - 1)
        target = instr_rva + 7 + disp
        if target == str_rva:
            lea_rva = instr_rva
            break
    if lea_rva is None:
        raise RuntimeError("找不到引用该字符串的 LEA 指令")
    print(f"[i] LEA 指令 RVA = 0x{lea_rva:X}")

    sec = next(v for v in secs.values() if v[0] <= exc_rva < v[0] + max(v[1], v[3]))
    eoff = sec[2] + (exc_rva - sec[0])
    n = exc_size // 12
    lo, hi, func_rva = 0, n - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        b, e = struct.unpack_from("<II", data, eoff + mid * 12)
        if lea_rva < b:
            hi = mid - 1
        elif lea_rva >= e:
            lo = mid + 1
        else:
            func_rva = b
            break
    if func_rva is None:
        raise RuntimeError("异常表里找不到所属函数")
    print(f"[i] 目标函数 RVA = 0x{func_rva:X}")
    return func_rva


# ---------------------------------------------------------------- Win32 调试
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
READABLE = (0x02, 0x04, 0x08, 0x20, 0x40)

DEBUG_ONLY_THIS_PROCESS = 0x00000002
DBG_CONTINUE = 0x00010002
EXCEPTION_DEBUG_EVENT = 1
LOAD_DLL_DEBUG_EVENT = 6
EXIT_PROCESS_DEBUG_EVENT = 5
EXCEPTION_BREAKPOINT = 0x80000003
EXCEPTION_SINGLE_STEP = 0x80000004
CONTEXT_ALL = 0x0010001B


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)), ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("dwDebugEventCode", wt.DWORD), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
                ("_pad", wt.DWORD), ("u", ctypes.c_byte * 160)]


class M128A(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_uint64), ("High", ctypes.c_int64)]


class CONTEXT64(ctypes.Structure):
    _pack_ = 8
    _fields_ = [("P1Home", ctypes.c_uint64), ("P2Home", ctypes.c_uint64), ("P3Home", ctypes.c_uint64),
                ("P4Home", ctypes.c_uint64), ("P5Home", ctypes.c_uint64), ("P6Home", ctypes.c_uint64),
                ("ContextFlags", wt.DWORD), ("MxCsr", wt.DWORD),
                ("SegCs", wt.WORD), ("SegDs", wt.WORD), ("SegEs", wt.WORD), ("SegFs", wt.WORD),
                ("SegGs", wt.WORD), ("SegSs", wt.WORD), ("EFlags", wt.DWORD),
                ("Dr0", ctypes.c_uint64), ("Dr1", ctypes.c_uint64), ("Dr2", ctypes.c_uint64),
                ("Dr3", ctypes.c_uint64), ("Dr6", ctypes.c_uint64), ("Dr7", ctypes.c_uint64),
                ("Rax", ctypes.c_uint64), ("Rcx", ctypes.c_uint64), ("Rdx", ctypes.c_uint64),
                ("Rbx", ctypes.c_uint64), ("Rsp", ctypes.c_uint64), ("Rbp", ctypes.c_uint64),
                ("Rsi", ctypes.c_uint64), ("Rdi", ctypes.c_uint64),
                ("R8", ctypes.c_uint64), ("R9", ctypes.c_uint64), ("R10", ctypes.c_uint64), ("R11", ctypes.c_uint64),
                ("R12", ctypes.c_uint64), ("R13", ctypes.c_uint64), ("R14", ctypes.c_uint64), ("R15", ctypes.c_uint64),
                ("Rip", ctypes.c_uint64),
                ("FltSave", ctypes.c_byte * 512),
                ("VectorRegister", M128A * 26),
                ("VectorControl", ctypes.c_uint64),
                ("DebugControl", ctypes.c_uint64), ("LastBranchToRip", ctypes.c_uint64),
                ("LastBranchFromRip", ctypes.c_uint64), ("LastExceptionToRip", ctypes.c_uint64),
                ("LastExceptionFromRip", ctypes.c_uint64)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)), ("modBaseSize", wt.DWORD),
                ("hModule", wt.HMODULE), ("szModule", ctypes.c_wchar * 256), ("szExePath", ctypes.c_wchar * 260)]


K32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                               ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                               ctypes.POINTER(PROCESS_INFORMATION)]
K32.WaitForDebugEvent.argtypes = [ctypes.POINTER(DEBUG_EVENT), wt.DWORD]
K32.ContinueDebugEvent.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD]
K32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
K32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
K32.GetThreadContext.argtypes = [wt.HANDLE, ctypes.POINTER(CONTEXT64)]
K32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
K32.OpenThread.restype = wt.HANDLE
K32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
K32.CreateToolhelp32Snapshot.restype = wt.HANDLE
K32.CloseHandle.argtypes = [wt.HANDLE]
K32.CloseHandle.restype = wt.BOOL
K32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
K32.Module32FirstW.restype = wt.BOOL
K32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
K32.Module32NextW.restype = wt.BOOL
K32.DebugActiveProcessStop.argtypes = [wt.DWORD]
K32.DebugActiveProcessStop.restype = wt.BOOL
K32.DebugSetProcessKillOnExit.argtypes = [wt.BOOL]
K32.DebugSetProcessKillOnExit.restype = wt.BOOL
K32.FlushInstructionCache.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
K32.FlushInstructionCache.restype = wt.BOOL


def read_mem(h, addr, n):
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t(0)
    if K32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) and got.value:
        return buf.raw[:got.value]
    return b""


def write_mem(h, addr, data):
    got = ctypes.c_size_t(0)
    return bool(K32.WriteProcessMemory(h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(got)))


def module_base(pid, name):
    snap = K32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
    if not snap or snap == -1:
        return 0
    try:
        e = MODULEENTRY32W()
        e.dwSize = ctypes.sizeof(MODULEENTRY32W)
        if K32.Module32FirstW(snap, ctypes.byref(e)):
            while True:
                if e.szModule.lower() == name.lower():
                    return ctypes.cast(e.modBaseAddr, ctypes.c_void_p).value
                if not K32.Module32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        K32.CloseHandle(snap)
    return 0


def run(qq_exe: str, wrapper: str, out: str, timeout: int = 900) -> list:
    func_rva = find_function_rva(wrapper)
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    pi = PROCESS_INFORMATION()
    ok = K32.CreateProcessW(qq_exe, ctypes.c_wchar_p(qq_exe), None, None, False,
                            DEBUG_ONLY_THIS_PROCESS, None, os.path.dirname(qq_exe),
                            ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise RuntimeError(f"CreateProcess 失败: {ctypes.get_last_error()}")
    print(f"[i] QQ 已启动并附加调试器 PID={pi.dwProcessId}, 等待 wrapper.node 加载...(请在 QQ 登录)")
    K32.DebugSetProcessKillOnExit(False)

    bp_addr = 0
    orig_byte = None
    keys = []
    seen = set()
    t0 = time.time()
    deadline = t0 + timeout
    while time.time() < deadline:
        ev = DEBUG_EVENT()
        if not K32.WaitForDebugEvent(ctypes.byref(ev), 3000):
            if ctypes.get_last_error() == 121:
                if int(time.time() - t0) % 30 < 4:
                    print(f"[.] 等待中... {int(time.time() - t0)}s 已抓到 {len(keys)} 个密钥")
                continue
            break
        code, status = ev.dwDebugEventCode, DBG_CONTINUE
        if code == LOAD_DLL_DEBUG_EVENT and bp_addr == 0:
            h = struct.unpack_from("<Q", ev.u, 0)[0]
            if h not in (0, 0xFFFFFFFFFFFFFFFF):
                K32.CloseHandle(ctypes.c_void_p(h))
            base = module_base(pi.dwProcessId, "wrapper.node")
            if base:
                bp_addr = base + func_rva
                orig_byte = read_mem(pi.hProcess, bp_addr, 1)
                write_mem(pi.hProcess, bp_addr, b"\xCC")
                K32.FlushInstructionCache(pi.hProcess, ctypes.c_void_p(bp_addr), 1)
                print(f"[i] wrapper.node base=0x{base:X}, 断点设在 0x{bp_addr:X}")
        elif code == EXCEPTION_DEBUG_EVENT and bp_addr:
            exc_code = struct.unpack_from("<I", ev.u, 0)[0]
            exc_addr = struct.unpack_from("<Q", ev.u, 16)[0]
            if exc_code == EXCEPTION_BREAKPOINT and exc_addr == bp_addr:
                if orig_byte:
                    write_mem(pi.hProcess, bp_addr, orig_byte)
                    K32.FlushInstructionCache(pi.hProcess, ctypes.c_void_p(bp_addr), 1)
                ht = K32.OpenThread(0x1FFFFF, False, ev.dwThreadId)
                try:
                    ctx = CONTEXT64()
                    ctx.ContextFlags = CONTEXT_ALL
                    if K32.GetThreadContext(ht, ctypes.byref(ctx)):
                        nkey = ctx.R9 & 0xFFFFFFFF
                        raw = read_mem(pi.hProcess, ctx.R8, 96)
                        zdb = read_mem(pi.hProcess, ctx.Rdx, 64).split(b"\x00")[0]
                        body = raw[:nkey] if 0 < nkey <= 96 else raw.split(b"\x00")[0]
                        try:
                            txt = body.decode("ascii")
                        except Exception:
                            txt = body.hex()
                        print(f"[★] key={txt!r} (nKey={nkey}) zDb={zdb!r}", flush=True)
                        if txt not in seen:
                            seen.add(txt)
                            keys.append({"nKey": nkey, "key": txt, "raw": body.hex(),
                                         "zDb": zdb.decode("utf-8", "ignore"),
                                         "time": time.strftime("%H:%M:%S")})
                            json.dump(keys, open(out, "w", encoding="utf-8"),
                                      ensure_ascii=False, indent=1)
                finally:
                    K32.CloseHandle(ht)
                write_mem(pi.hProcess, bp_addr, b"\xCC")
                K32.FlushInstructionCache(pi.hProcess, ctypes.c_void_p(bp_addr), 1)
                if any(k["nKey"] == 16 for k in keys) and time.time() - t0 > 30:
                    print("[i] 已拿到 16 字符密钥，结束抓取", flush=True)
                    break
            elif exc_code == EXCEPTION_SINGLE_STEP:
                pass
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            print("[!] QQ 进程退出")
            break
        K32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, status)

    if bp_addr and orig_byte:
        write_mem(pi.hProcess, bp_addr, orig_byte)
    try:
        K32.DebugActiveProcessStop(pi.dwProcessId)
        print("[i] 已脱离调试，QQ 继续正常运行")
    except Exception as e:  # noqa: BLE001
        print("[!] 脱离失败:", e)
    json.dump(keys, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[=] 共 {len(keys)} 个候选密钥 -> {out}")
    return keys


def main() -> None:
    # 冻结成 exe 后按系统代码页输出会让父进程(UTF-8)读到乱码，先统一为 UTF-8。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="抓取 NTQQ 数据库密钥（需管理员 + QQ 登录）")
    ap.add_argument("--qq-exe", default="", help="QQ.exe 路径（留空自动探测）")
    ap.add_argument("--wrapper", default="", help="wrapper.node 路径（留空自动探测）")
    ap.add_argument("--out", default="", help="候选密钥输出 json")
    ap.add_argument("--timeout", type=int, default=900, help="最长等待秒数")
    args = ap.parse_args()

    if os.name != "nt":
        raise SystemExit("[错误] 本脚本仅支持 Windows")

    qq = args.qq_exe or detect_qq_exe()
    wrapper = args.wrapper or detect_wrapper(qq)
    if not qq or not os.path.isfile(qq):
        raise SystemExit("[错误] 未找到 QQ.exe（用 --qq-exe 指定）")
    if not wrapper or not os.path.isfile(wrapper):
        raise SystemExit("[错误] 未找到 wrapper.node（用 --wrapper 指定）")
    out = args.out or os.path.join(paths.work_dir(), "keys_found.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f"[i] QQ.exe   = {qq}")
    print(f"[i] wrapper  = {wrapper}")
    run(qq, wrapper, out, args.timeout)


if __name__ == "__main__":
    main()
