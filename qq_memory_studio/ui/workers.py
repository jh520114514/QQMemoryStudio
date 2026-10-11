# -*- coding: utf-8 -*-
"""后台线程封装：所有长任务（统计/语义检索/同步/AI）都跑在这里，界面不卡。

约定：Worker 只负责把结果通过 Signal 抛出，绝不直接操作界面控件。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal


class StatsWorker(QThread):
    """读取记忆库统计 + QQ 账号探测 + 备份时间（只读）。"""
    done = Signal(dict)

    def __init__(self, memory_dir: str, dim: int = 1024, qq_data_dir: str = "", parent=None):
        super().__init__(parent)
        self.memory_dir = memory_dir
        self.dim = dim
        self.qq_data_dir = qq_data_dir

    def run(self) -> None:
        from core.memory import MemoryStore, human_size
        from core import qq
        result = {"stats": {}, "files": {}, "last_backup": "未知", "next_scheduled": "未知",
                  "accounts": [], "error": ""}
        try:
            ms = MemoryStore(self.memory_dir, self.dim)
            result["stats"] = ms.stats()
            result["files"] = ms.file_info()
            result["last_backup"] = qq.last_backup_time(self.memory_dir)
            result["accounts"] = qq.detect_accounts(self.qq_data_dir)
            result["next_scheduled"] = qq.next_scheduled_time()
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        self.done.emit(result)


class EmbedTestWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, memory_dir: str, embedding: dict, parent=None):
        super().__init__(parent)
        self.memory_dir = memory_dir
        self.embedding = embedding

    def run(self) -> None:
        from core.semantic import SemanticIndex
        try:
            si = SemanticIndex(self.memory_dir, self.embedding, self.embedding.get("dim"))
            ok, msg = si.test_connection()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        self.done.emit(ok, msg)


class SyncWorker(QThread):
    line = Signal(str)
    done = Signal(dict)

    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner

    def run(self) -> None:
        res = self.runner.run(on_line=lambda s: self.line.emit(s))
        self.done.emit(res)

    def cancel(self) -> None:
        self.runner.cancel()


class KeyFetchWorker(QThread):
    """「自动抓取密钥」worker：只跑抓取这一步，不触碰记忆库。"""
    line = Signal(str)
    done = Signal(dict)

    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner

    def run(self) -> None:
        res = self.runner.run(on_line=lambda s: self.line.emit(s))
        self.done.emit(res)

    def cancel(self) -> None:
        self.runner.cancel()


class SearchWorker(QThread):
    done = Signal(list, float, str)   # hits, elapsed, error

    def __init__(self, mode: str, store, semantic, query: str, k: int, chat: str, sender: str,
                 ts_from: int, ts_to: int, only_me: bool, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.store = store
        self.semantic = semantic
        self.query = query
        self.k = k
        self.chat = chat
        self.sender = sender
        self.ts_from = ts_from
        self.ts_to = ts_to
        self.only_me = only_me

    def run(self) -> None:
        try:
            if self.mode == "语义":
                hits, dt = self.semantic.search(self.query, k=self.k, chat=self.chat,
                                                sender=self.sender, ts_from=self.ts_from,
                                                ts_to=self.ts_to, only_me=self.only_me)
            else:
                hits, dt = self.store.keyword_search(self.query, k=self.k, chat=self.chat,
                                                     sender=self.sender, ts_from=self.ts_from,
                                                     ts_to=self.ts_to, only_me=self.only_me)
            self.done.emit(hits, float(dt), "")
        except Exception as e:  # noqa: BLE001
            self.done.emit([], 0.0, f"{type(e).__name__}: {e}")


class AIWorker(QThread):
    event = Signal(str, object)
    done = Signal(dict)

    def __init__(self, client, question: str, history: list, k: int = 8, parent=None):
        super().__init__(parent)
        self.client = client
        self.question = question
        self.history = history
        self.k = k

    def run(self) -> None:
        res = self.client.ask(self.question, history=self.history, k=self.k,
                              on_event=lambda kind, payload: self.event.emit(kind, payload))
        self.done.emit(res)


class ChatsWorker(QThread):
    """后台加载会话列表（chats() 需对 1GB 库做全表分组，必须在工作线程执行）。"""

    done = Signal(list, str)   # chats, error

    def __init__(self, memory_dir: str, dim: int = 1024, parent=None):
        super().__init__(parent)
        self.memory_dir = memory_dir
        self.dim = dim

    def run(self) -> None:
        from core.memory import MemoryStore
        try:
            ms = MemoryStore(self.memory_dir, self.dim)
            self.done.emit(ms.chats(), "")
        except Exception as e:  # noqa: BLE001
            self.done.emit([], f"{type(e).__name__}: {e}")


class ExportWorker(QThread):
    """会话导出 worker：在后台线程流式导出，全程只读记忆库。"""

    log = Signal(str)               # 实时日志
    progress = Signal(int, int, float)  # 已导出条数 / 总数 / 已用时（秒）
    done = Signal(dict)             # 结束结果（含 out_path / count / elapsed / error…）

    def __init__(self, store, params: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.params = params
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from core.exporter import export_chat
        try:
            res = export_chat(
                self.store, self.params,
                on_log=lambda s: self.log.emit(s),
                on_progress=lambda d, t, e: self.progress.emit(int(d), int(t), float(e)),
                should_cancel=self._cancel.is_set)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(e).__name__}: {e}", "out_path": "",
                   "count": 0, "elapsed": 0.0, "bytes": 0, "canceled": False}
        self.done.emit(res)
