# -*- coding: utf-8 -*-
"""同步 / 密钥抓取的子进程封装。

为什么用子进程？
* 可以真实地“取消”（terminate/kill 整个进程），且进度输出可逐行实时捕获；
* 与 GUI 进程隔离，崩溃/卡死互不影响。

进度格式（pipeline 输出）：
    结束时打印 ``SYNC_OK ...`` 或 ``SYNC_FAIL <原因>``，
    本模块据此判定成功/失败并把原始行交给 UI 日志。

编码：冻结成 exe 后子进程的 PYTHONUTF8/PYTHONIOENCODING 不生效（见 app.py），
子进程会按系统 ANSI 代码页写 stdout。所以这里不依赖 text=True 解出的字符串，
而是抓原始字节后做两级解码（严格 UTF-8 -> 本地代码页兜底），避免中文乱码或整行丢失。
"""
from __future__ import annotations

import locale
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Callable

from . import config as C

PIPELINE_SYNC = os.path.join(C.STUDIO_DIR, "pipeline", "sync.py")
PIPELINE_EXTRACT_KEY = os.path.join(C.STUDIO_DIR, "pipeline", "extract_key.py")

#: 连续多久没有任何输出就判定为卡死（秒）
HEARTBEAT_TIMEOUT = 300.0

#: pipeline/sync.py 的退出码 -> (人类可读原因, 下一步动作)
EXIT_HINTS: dict[int, tuple[str, str]] = {
    2: ("刷新未完成",
        "确认 QQ 已登录、nt_msg.db 未被占用；必要时关闭 QQ 后重试。"),
    3: ("未找到 QQ 数据目录 / nt_msg.db",
        "在「QQ 数据目录」里填写 Tencent Files 所在位置，或确认账号是否正确。"),
    4: ("未取到密钥",
        "在「SQLCipher 密钥」填入 db_key，或点「自动抓取密钥…」（需管理员权限 + QQ 已登录）。"),
    5: ("刷新异常",
        "查看上方日志里的异常信息；确认 QQ 数据库未被其它程序占用后重试。"),
}

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def key_output_path(work_dir: str) -> str:
    return os.path.join(work_dir, "keys", "keys_found.json")


def cached_key(work_dir: str, memory_dir: str = "", account: str = "") -> str:
    """读取本机缓存的密钥（与 pipeline/sync.py 的 load_cached_key 一致但无重依赖）。

    仅用于界面展示「密钥状态」，不做任何抓取 / 解密动作。
    """
    import json

    try:
        if account:
            p = os.path.join(work_dir, "keys", f"{account}.key")
            if os.path.isfile(p):
                k = open(p, encoding="utf-8").read().strip()
                if k:
                    return k
        for jf in (key_output_path(work_dir),
                   os.path.join(work_dir, "keys_found.json"),
                   os.path.join(memory_dir, "keys_found.json") if memory_dir else ""):
            if not jf or not os.path.isfile(jf):
                continue
            data = json.load(open(jf, encoding="utf-8"))
            items = data if isinstance(data, list) else [data]
            fallback = ""
            for it in items:
                if not isinstance(it, dict) or not it.get("key"):
                    continue
                if str(it.get("nKey")) == "16":
                    return str(it["key"])
                fallback = fallback or str(it["key"])
            if fallback:
                return fallback
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _ansi_encoding() -> str:
    """系统 ANSI 代码页（中文 Windows = cp936）。

    不用 locale.getpreferredencoding()：Python 处于 UTF-8 模式时它返回 "utf-8"，
    拿它当兜底等于没兜底。
    """
    if os.name == "nt":
        return "mbcs"
    return locale.getpreferredencoding(False) or "utf-8"


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"
               or "\uff00" <= ch <= "\uffef" for ch in s)


def _decode_line(raw: bytes) -> str:
    """按 UTF-8 优先、系统 ANSI 代码页兜底解出一行文本。

    两层保护：
    1. 严格 UTF-8 成功但结果里没有任何中日韩字符，而按 ANSI 重新解却含中文
       → 判定为“GBK 字节恰好是合法 UTF-8”的假成功（如 未=CE B4 被读成 δ），
       采用 ANSI 结果而不是把乱码丢给用户（这正是原日志里 δȡ 的成因）；
    2. 严格 UTF-8 直接报错（非法字节序列）→ 退用 ANSI + replace。
    """
    ansi = _ansi_encoding()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(ansi, "replace")
    if all(ord(ch) < 128 for ch in text):
        return text
    if _has_cjk(text):
        return text
    try:
        alt = raw.decode(ansi)
    except (UnicodeDecodeError, LookupError):
        return text
    return alt if _has_cjk(alt) else text


class _Job:
    """一次子进程执行：读字节 -> 逐行回调，带取消与无输出看门狗。"""

    def __init__(self, cmd: list[str], env: dict):
        self.cmd = cmd
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._cancel = False

    # ---- 生命周期 ----
    def cancel(self) -> None:
        self._cancel = True
        self._terminate()

    def _terminate(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

    # ---- 执行 ----
    def start(self, on_line: Callable[[str], None],
              should_cancel: Callable[[], bool] | None = None) -> dict:
        try:
            self.proc = subprocess.Popen(
                self.cmd, cwd=C.STUDIO_DIR, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, creationflags=_NO_WINDOW)
        except Exception as e:  # noqa: BLE001
            on_line(f"[sync] 启动失败：{type(e).__name__}: {e}")
            return {"ok": False, "code": -1, "reason": f"启动失败：{e}",
                    "hint": "", "lines": [], "elapsed": 0.0,
                    "stalled": False, "cancelled": False, "pid": 0}

        pid = self.proc.pid
        on_line(f"[sync] 已启动子进程 PID={pid}")
        on_line(f"[sync] 命令：{' '.join(self.cmd)}")

        chunks: "queue.Queue[bytes | None]" = queue.Queue()

        def _reader() -> None:
            assert self.proc is not None and self.proc.stdout is not None
            try:
                for raw in iter(self.proc.stdout.readline, b""):
                    chunks.put(raw)
            except Exception:  # noqa: BLE001
                pass
            finally:
                chunks.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        t0 = time.time()
        lines: list[str] = []
        last_output = t0
        stalled = cancelled = False
        while True:
            if self._cancel or (should_cancel and should_cancel()):
                cancelled = True
                self._terminate()
                break
            try:
                raw = chunks.get(timeout=0.5)
            except queue.Empty:
                if time.time() - last_output > HEARTBEAT_TIMEOUT:
                    stalled = True
                    on_line(f"[sync] 超过 {HEARTBEAT_TIMEOUT:.0f}s 无任何输出，"
                            f"判定卡死并终止子进程"
                            f"（上次输出距今 {time.time() - last_output:.0f}s）")
                    self._terminate()
                    break
                continue
            if raw is None:
                break
            line = _decode_line(raw).rstrip("\r\n")
            if not line:
                continue
            last_output = time.time()
            lines.append(line)
            on_line(line)

        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self.proc.wait()

        return {"ok": False, "code": self.proc.returncode, "lines": lines,
                "elapsed": time.time() - t0, "stalled": stalled,
                "cancelled": cancelled, "pid": pid}


def _env(memory_dir: str, data_dir: str, work_dir: str, key: str = "") -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 随包 msgdb 目录层级较深，写 __pycache__ 可能触发 Windows 260 字符路径限制；
    # 禁用字节码写入即可，不影响功能。
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QQMEM_MEMORY_DIR"] = memory_dir
    env["QQMEM_DATA_DIR"] = data_dir
    env["QQMEM_WORK_DIR"] = work_dir
    if key:
        # 密钥只走环境变量：argv 对同机任意进程可见（任务管理器 / WMI 都能读到）。
        env["QQMEM_DB_KEY"] = key
    return env


class SyncRunner:
    """构造并运行一次同步。阻塞式 run()，由 UI 放进后台线程调用。"""

    def __init__(self, memory_dir: str, qq_data_dir: str = "", account: str = "",
                 key: str = "", full: bool = False, vectors: bool = False,
                 auto_key: bool = False, data_dir: str = "", work_dir: str = ""):
        self.memory_dir = memory_dir
        self.qq_data_dir = qq_data_dir
        self.account = str(account or "")
        self.key = key or ""
        self.full = bool(full)
        self.vectors = bool(vectors)
        self.auto_key = bool(auto_key)
        self.data_dir = data_dir or os.path.dirname(memory_dir)
        self.work_dir = work_dir or self.data_dir
        self.proc: subprocess.Popen | None = None
        self._job: _Job | None = None
        self._cancel = False

    # ------------------------------------------------------------ 命令
    def command(self) -> list[str]:
        args = ["--memory-dir", self.memory_dir, "--data-dir", self.data_dir,
                "--work-dir", self.work_dir]
        if self.qq_data_dir:
            args += ["--qq-data-dir", self.qq_data_dir]
        if self.account:
            args += ["--account", self.account]
        if self.full:
            args += ["--full"]
        if self.vectors:
            args += ["--vectors"]
        if self.auto_key:
            args += ["--auto-key"]
        if is_frozen():
            # 冻结成 exe 时：复用自身可执行文件，加内部开关跑同步（见 app.py）
            return [sys.executable, "--studio-run-sync"] + args
        return [sys.executable, "-u", PIPELINE_SYNC] + args

    def env(self) -> dict:
        return _env(self.memory_dir, self.data_dir, self.work_dir, self.key)

    # ------------------------------------------------------------ 运行
    def cancel(self) -> None:
        self._cancel = True
        if self._job:
            self._job.cancel()

    def _failure_reason(self, code: int, lines: list[str]) -> str:
        """退出码 / SYNC_FAIL 行 -> 可读原因（优先人类可读的映射表）。"""
        hint = EXIT_HINTS.get(code)
        if hint:
            return hint[0]
        fail = [l for l in lines if l.startswith("SYNC_FAIL")]
        if fail:
            return fail[-1][len("SYNC_FAIL"):].strip() or "同步失败"
        return f"退出码 {code}" if code != 0 else "未收到 SYNC_OK"

    def run(self, on_line: Callable[[str], None] | None = None,
            should_cancel: Callable[[], bool] | None = None) -> dict:
        on_line = on_line or (lambda s: None)
        self._job = _Job(self.command(), self.env())
        # 让 UI 的 cancel() 也能 terminate 到真实进程
        self.proc = self._job.proc
        res = self._job.start(on_line, should_cancel)
        lines = res["lines"]
        code = res["code"]
        ok = (code == 0) and any(l.startswith("SYNC_OK") for l in lines)
        reason = ""
        hint = ""
        if not ok:
            if res["cancelled"] or self._cancel or (should_cancel and should_cancel()):
                reason = "已取消"
            elif res["stalled"]:
                reason = "同步卡死（长时间无输出，已终止）"
                hint = "关闭 QQ 后重试；若反复出现请把日志一并反馈。"
            elif code == -1:
                reason = res.get("reason", "启动失败")
                hint = "确认程序文件完整（未被杀软隔离）后重试。"
            else:
                reason = self._failure_reason(code, lines)
                hint = EXIT_HINTS.get(code, ("", ""))[1]
        on_line(f"[sync] 结束：code={code} ok={ok} 用时 {res['elapsed']:.0f}s")
        return {"ok": ok, "code": code, "reason": reason, "hint": hint,
                "lines": lines, "elapsed": res["elapsed"],
                "stalled": res["stalled"], "pid": res["pid"]}


class KeyFetchRunner:
    """只跑「抓取密钥」这一步：不触碰记忆库，供设置区的「自动抓取密钥…」使用。"""

    def __init__(self, work_dir: str, data_dir: str = "", memory_dir: str = ""):
        self.work_dir = work_dir
        self.data_dir = data_dir or work_dir
        self.memory_dir = memory_dir or self.data_dir
        self.out = key_output_path(self.work_dir)
        self.proc: subprocess.Popen | None = None
        self._job: _Job | None = None
        self._cancel = False

    def command(self) -> list[str]:
        if is_frozen():
            # 冻结后必须走 app.py 的内部子命令；直接 "exe -u script.py" 会再开一个 GUI。
            return [sys.executable, "--studio-run-extract-key", "--out", self.out]
        return [sys.executable, "-u", PIPELINE_EXTRACT_KEY, "--out", self.out]

    def env(self) -> dict:
        return _env(self.memory_dir, self.data_dir, self.work_dir)

    def cancel(self) -> None:
        self._cancel = True
        if self._job:
            self._job.cancel()

    def run(self, on_line: Callable[[str], None] | None = None,
            should_cancel: Callable[[], bool] | None = None) -> dict:
        on_line = on_line or (lambda s: None)
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        self._job = _Job(self.command(), self.env())
        res = self._job.start(on_line, should_cancel)
        key = ""
        if os.path.isfile(self.out):
            try:
                import json
                data = json.load(open(self.out, encoding="utf-8"))
                items = data if isinstance(data, list) else [data]
                for it in items:
                    if isinstance(it, dict) and it.get("key"):
                        key = str(it["key"])
                        if str(it.get("nKey")) == "16":
                            break
            except Exception:  # noqa: BLE001
                key = ""
        ok = bool(key) and not res["cancelled"]
        on_line(f"[key] {'已获取候选密钥' if ok else '未命中任何候选密钥'}")
        return {"ok": ok, "key": key, "code": res["code"], "lines": res["lines"],
                "elapsed": res["elapsed"], "cancelled": res["cancelled"]}


if __name__ == "__main__":
    print(" ".join(SyncRunner(C.DEFAULT_MEMORY_DIR).command()))
    print(" ".join(KeyFetchRunner(C.DEFAULT_DATA_DIR).command()))
