# -*- coding: utf-8 -*-
"""2 级窗口 A：向量数据库浏览与手动检索。"""
from __future__ import annotations

import csv
import os
import time

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QLineEdit,
    QComboBox, QSpinBox, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem,
    QDateEdit, QMessageBox, QDialog, QPlainTextEdit, QFileDialog, QHeaderView,
)

from core.memory import fmt_ts, memory_missing_guide, memory_missing_title
from ui.workers import SearchWorker

# 空库时给用户看的可读中文口径（与主窗口、导出窗口一致）
EMPTY_MEMORY_MSG = "记忆库尚未建立或向量文件缺失，请先在主界面设置记忆库目录。"


def _d2ts(qd: QDate) -> int:
    return int(time.mktime((qd.year(), qd.month(), qd.day(), 0, 0, 0, 0, 0, -1)))


class DetailDialog(QDialog):
    """双击行时查看全文。"""

    def __init__(self, hit: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"消息详情 · id={hit.get('id')}")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        head = QLabel(
            f"时间：{hit.get('time')}\n会话：{hit.get('chat')}（{hit.get('chat_type')} {hit.get('chat_id')}）\n"
            f"发送人：{hit.get('sender')}\n来源：{hit.get('source')}   相关度：{hit.get('score')}")
        head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(head)
        body = QPlainTextEdit()
        body.setPlainText(str(hit.get("text") or ""))
        body.setReadOnly(True)
        lay.addWidget(body)
        btn = QPushButton("复制正文")
        btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(str(hit.get("text") or "")))
        lay.addWidget(btn, 0, Qt.AlignRight)


class SearchWindow(QWidget):
    """独立检索窗口（不修改任何数据，只读）。"""

    locate_requested = Signal(int)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.store = None
        self.semantic = None
        self.hits: list[dict] = []
        self._worker: SearchWorker | None = None
        self.setWindowTitle("QQ 记忆库 · 检索与浏览")
        self.resize(1080, 680)
        self._build()
        self.reload_backends()
        self.refresh_overview()

    # ------------------------------------------------------------ 构建界面
    def _build(self) -> None:
        root = QVBoxLayout(self)

        bar = QGroupBox("检索")
        grid = QGridLayout(bar)
        self.mode = QComboBox(); self.mode.addItems(["语义", "关键词"])
        self.query = QLineEdit(); self.query.setPlaceholderText("输入自然语言或关键词，回车检索…")
        self.query.returnPressed.connect(self.do_search)
        self.k = QSpinBox(); self.k.setRange(1, 200); self.k.setValue(20)
        self.btn_search = QPushButton("检索"); self.btn_search.clicked.connect(self.do_search)
        grid.addWidget(QLabel("模式"), 0, 0); grid.addWidget(self.mode, 0, 1)
        grid.addWidget(QLabel("关键字/问题"), 0, 2); grid.addWidget(self.query, 0, 3, 1, 4)
        grid.addWidget(QLabel("条数 K"), 0, 7); grid.addWidget(self.k, 0, 8)
        grid.addWidget(self.btn_search, 0, 9)

        self.f_chat = QLineEdit(); self.f_chat.setPlaceholderText("会话名/群号 包含")
        self.f_sender = QLineEdit(); self.f_sender.setPlaceholderText("发送人昵称/QQ 包含")
        self.only_me = QCheckBox("只看我发的")
        self.use_from = QCheckBox("起始"); self.from_date = QDateEdit(QDate.currentDate().addYears(-2)); self.from_date.setCalendarPopup(True)
        self.use_to = QCheckBox("结束"); self.to_date = QDateEdit(QDate.currentDate()); self.to_date.setCalendarPopup(True)
        grid.addWidget(QLabel("会话"), 1, 0); grid.addWidget(self.f_chat, 1, 1, 1, 2)
        grid.addWidget(QLabel("发送人"), 1, 3); grid.addWidget(self.f_sender, 1, 4, 1, 2)
        grid.addWidget(self.only_me, 1, 6)
        grid.addWidget(self.use_from, 1, 7); grid.addWidget(self.from_date, 1, 8)
        grid.addWidget(self.use_to, 1, 9); grid.addWidget(self.to_date, 1, 10)
        root.addWidget(bar)

        ov = QHBoxLayout()
        self.overview = QLabel("向量库：—")
        self.overview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel("就绪")
        ov.addWidget(self.overview); ov.addStretch(1); ov.addWidget(self.status)
        root.addLayout(ov)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["时间", "会话", "发送人", "来源", "相关度", "内容"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 145); self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 110); self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 70)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemDoubleClicked.connect(self.show_detail)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        for text, slot in (("复制正文", self.copy_body), ("导出 CSV", self.export_csv),
                           ("查看全文", self.show_detail_current), ("打开记忆库目录", self.open_dir)):
            b = QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

    # ------------------------------------------------------------ 后端
    def reload_backends(self) -> None:
        from core.memory import MemoryStore
        from core.semantic import SemanticIndex
        mem = self.config.memory_dir
        dim = self.config.get("embedding.dim") or 1024
        self.store = MemoryStore(mem, dim)
        self.semantic = SemanticIndex(mem, self.config.get("embedding"), dim)

    def memory_ready(self) -> bool:
        """记忆库目录下是否存在可读的 messages.db。"""
        return bool(self.store is not None and self.store.exists())

    def _empty_dialog(self, title: str) -> None:
        """统一的空库提示：标题句 + 与主界面一致的引导三步。"""
        QMessageBox.information(
            self, title,
            EMPTY_MEMORY_MSG + "\n\n" + memory_missing_guide(self.config.memory_dir))

    def refresh_overview(self) -> None:
        if not self.memory_ready():
            # 空库时不要显示技术味很重的 “未找到向量文件：…”，用统一中文口径
            self.overview.setText(memory_missing_title(self.config.memory_dir))
            self.status.setText("未找到记忆库")
            return
        try:
            ov = self.semantic.overview()
            from core.memory import human_size
            avail, why = self.semantic.available()
            tag = "可用" if avail else "不可用"
            self.overview.setText(
                f"向量库：{ov['vec_count']:,} 条 × {ov['dim']} 维 · vecs.f16 {human_size(ov['vecs_bytes'])} · "
                f"映射 {ov['vec_rows']:,} 行 · 模型 {ov['model'] or '—'} · 状态 {tag}")
            if not avail:
                # 库里没有向量（尚未 embed）时，也给同一套引导，而不是英文/技术异常
                self.overview.setText(self.overview.text() + "　·　" + EMPTY_MEMORY_MSG)
        except Exception as e:  # noqa: BLE001
            self.overview.setText(f"向量库概览读取失败：{e}")

    # ------------------------------------------------------------ 检索
    def do_search(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        q = self.query.text().strip()
        if not q:
            self.status.setText("请输入检索内容")
            return
        self.reload_backends()
        # 空库拦截：给出可读中文提示，不进入检索线程（避免 EmbedError 英文堆栈弹窗）
        if not self.memory_ready():
            self.status.setText("未找到记忆库")
            self._empty_dialog("未找到记忆库")
            return
        if self.mode.currentText() == "语义":
            ok, why = self.semantic.available()
            if not ok:
                self.status.setText("语义检索不可用")
                self._empty_dialog("语义检索不可用")
                return
        ts_from = _d2ts(self.from_date.date()) if self.use_from.isChecked() else 0
        ts_to = _d2ts(self.to_date.date()) + 86399 if self.use_to.isChecked() else 0
        self.btn_search.setEnabled(False)
        self.status.setText("检索中…")
        self._worker = SearchWorker(
            self.mode.currentText(), self.store, self.semantic, q, self.k.value(),
            self.f_chat.text().strip(), self.f_sender.text().strip(),
            ts_from, ts_to, self.only_me.isChecked(), self)
        self._worker.done.connect(self.on_results)
        self._worker.start()

    def on_results(self, hits: list, elapsed: float, error: str) -> None:
        self.btn_search.setEnabled(True)
        if error:
            # 把 EmbedError / 缺向量文件等“技术异常”翻译成可读中文口径
            if ("EmbedError" in error or "未找到向量" in error or "未找到向量映射库" in error
                    or "未配置 embedding" in error or "向量文件为空" in error
                    or "未找到记忆库" in error):
                self.status.setText("检索不可用")
                self._empty_dialog("检索不可用")
            else:
                self.status.setText(f"检索失败：{error}")
                QMessageBox.warning(self, "检索失败", error)
            return
        self.hits = hits
        self.table.setRowCount(len(hits))
        for i, h in enumerate(hits):
            text = str(h.get("text") or "")
            cell = text if len(text) <= 80 else text[:80] + "…"
            for col, val in enumerate([h.get("time", ""), h.get("chat", ""), h.get("sender", ""),
                                       h.get("source", ""), h.get("score", ""), cell]):
                item = QTableWidgetItem(str(val))
                if col == 5:
                    item.setToolTip(text)
                self.table.setItem(i, col, item)
        self.status.setText(f"命中 {len(hits)} 条，用时 {elapsed:.2f}s（模式：{self.mode.currentText()}）")

    # ------------------------------------------------------------ 定位
    def reveal(self, msg_id: int) -> None:
        """在结果表中定位某条消息（AI 窗口引用点击时调用）。"""
        for i, h in enumerate(self.hits):
            if int(h.get("id", -1)) == int(msg_id):
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 0))
                self.status.setText(f"已定位消息 id={msg_id}")
                return
        self.status.setText(f"当前结果中无 id={msg_id}（可先检索后再定位）")

    # ------------------------------------------------------------ 操作
    def current_hit(self) -> dict | None:
        r = self.table.currentRow()
        return self.hits[r] if 0 <= r < len(self.hits) else None

    def copy_body(self) -> None:
        h = self.current_hit()
        if h:
            QGuiApplication.clipboard().setText(str(h.get("text") or ""))
            self.status.setText("已复制正文到剪贴板")

    def show_detail(self, *_) -> None:
        h = self.current_hit()
        if h:
            DetailDialog(h, self).exec()

    def show_detail_current(self) -> None:
        self.show_detail()

    def export_csv(self) -> None:
        if not self.hits:
            self.status.setText("无结果可导出")
            return
        default = os.path.join(os.path.dirname(self.config.memory_dir), "export",
                               f"search_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        os.makedirs(os.path.dirname(default), exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "导出结果 CSV", default, "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["时间", "会话", "会话类型", "发送人", "来源", "相关度", "正文"])
            for h in self.hits:
                w.writerow([h.get("time", ""), h.get("chat", ""), h.get("chat_type", ""),
                            h.get("sender", ""), h.get("source", ""), h.get("score", ""),
                            str(h.get("text") or "")])
        self.status.setText(f"已导出 {len(self.hits)} 条到 {path}")

    def open_dir(self) -> None:
        d = self.config.memory_dir
        if not os.path.isdir(d):
            self.status.setText("记忆库目录不存在")
            self._empty_dialog("目录不存在")
            return
        try:
            os.startfile(d)  # noqa: S606 (Windows)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", f"{d}\n{e}")

    def stop_workers(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.wait(8000)

    def closeEvent(self, event) -> None:
        self.stop_workers()
        super().closeEvent(event)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from core import config as C
    app = QApplication([])
    w = SearchWindow(C.Config.load())
    w.show()
    app.exec()
