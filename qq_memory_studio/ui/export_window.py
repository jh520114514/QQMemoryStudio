# -*- coding: utf-8 -*-
"""3 级窗口：会话导出（把某个好友/群聊的全部聊天记录导出为文档）。

左侧选会话，右侧配置格式与过滤条件，导出在后台线程执行、只读记忆库。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem, QDateEdit,
    QMessageBox, QPlainTextEdit, QFileDialog, QHeaderView, QRadioButton, QButtonGroup,
    QProgressBar, QSplitter, QApplication,
)

from core.exporter import chat_type_label, default_filename, sanitize_filename
from core.memory import fmt_ts, memory_missing_guide, memory_missing_title, GUIDE_STEPS
from ui.workers import ChatsWorker, ExportWorker

# 导出格式：显示名 → (扩展名, 过滤器)
FORMATS = [
    ("Word 文档 (.docx)", "docx", "Word 文档 (*.docx)"),
    ("Markdown (.md)", "md", "Markdown (*.md)"),
    ("纯文本 (.txt)", "txt", "文本文件 (*.txt)"),
    ("HTML (.html)", "html", "网页 (*.html)"),
]


def _d2ts(qd: QDate) -> int:
    return int(time.mktime((qd.year(), qd.month(), qd.day(), 0, 0, 0, 0, 0, -1)))


class ExportWindow(QWidget):
    """会话导出窗口（只读记忆库，不修改任何数据）。"""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.store = None
        self.chats: list[dict] = []
        self._chats_worker: ChatsWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._path_edited = False   # 用户是否手动改过输出路径
        self.setWindowTitle("QQ 记忆库 · 会话导出")
        self.resize(1320, 760)
        self._build()
        self._reload_store()
        self.reload_chats()

    # ================================================================ 界面
    def _build(self) -> None:
        root = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setSizes([720, 600])
        root.addWidget(split, 1)

    # ---- 左侧：会话列表 ----
    def _build_left(self) -> QWidget:
        box = QGroupBox("会话列表（按消息数排序）")
        lay = QVBoxLayout(box)

        top = QHBoxLayout()
        self.f_filter = QLineEdit()
        self.f_filter.setPlaceholderText("按名称或群号/QQ号 模糊过滤…")
        self.f_filter.textChanged.connect(self._apply_filter)
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.clicked.connect(self.reload_chats)
        top.addWidget(QLabel("过滤")); top.addWidget(self.f_filter, 1); top.addWidget(self.btn_reload)
        lay.addLayout(top)

        self.lbl_count = QLabel("共 0 个会话")
        lay.addWidget(self.lbl_count)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["类型", "名称", "消息数", "起止时间", "chat_id"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        # 名称列固定 200px（可手动拖拽调宽），避免长中文群名被挤成“…”；
        # 其余列保留各自宽度，不被名称列挤压；整表放不下时横向滚动。
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        self.table.setColumnWidth(0, 56); self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 84)
        self.table.setColumnWidth(3, 190); self.table.setColumnWidth(4, 130)
        self.table.itemSelectionChanged.connect(self._on_pick_chat)
        self.table.itemDoubleClicked.connect(lambda *_: self.start_export())
        lay.addWidget(self.table, 1)
        return box

    # ---- 右侧：导出选项 ----
    def _build_right(self) -> QWidget:
        box = QGroupBox("导出选项")
        lay = QVBoxLayout(box)

        grid = QGridLayout()
        self.cmb_fmt = QComboBox()
        for label, _ext, _flt in FORMATS:
            self.cmb_fmt.addItem(label)
        self.cmb_fmt.currentIndexChanged.connect(self._on_fmt_changed)
        grid.addWidget(QLabel("格式"), 0, 0); grid.addWidget(self.cmb_fmt, 0, 1, 1, 3)

        # 时间范围
        self.rb_all = QRadioButton("全部"); self.rb_all.setChecked(True)
        self.rb_range = QRadioButton("自定义")
        self.rb_all.toggled.connect(self._on_range_toggle)
        g_range = QButtonGroup(self); g_range.addButton(self.rb_all); g_range.addButton(self.rb_range)
        self.d_from = QDateEdit(QDate.currentDate().addYears(-2)); self.d_from.setCalendarPopup(True)
        self.d_to = QDateEdit(QDate.currentDate()); self.d_to.setCalendarPopup(True)
        self.d_from.setEnabled(False); self.d_to.setEnabled(False)
        grid.addWidget(QLabel("时间范围"), 1, 0)
        grid.addWidget(self.rb_all, 1, 1)
        grid.addWidget(self.rb_range, 1, 2)
        grid.addWidget(self.d_from, 1, 3)
        grid.addWidget(self.d_to, 1, 4)
        grid.addWidget(QLabel("起"), 1, 3, Qt.AlignLeft)
        lay.addLayout(grid)

        opt = QHBoxLayout()
        self.chk_only_me = QCheckBox("只导出我发的")
        self.chk_qq = QCheckBox("显示发送人 QQ")
        self.chk_byday = QCheckBox("按日期分段插入标题")
        self.chk_byday.setChecked(True)
        opt.addWidget(self.chk_only_me); opt.addWidget(self.chk_qq)
        opt.addWidget(self.chk_byday); opt.addStretch(1)
        lay.addLayout(opt)

        # 输出路径
        prow = QHBoxLayout()
        self.e_out = QLineEdit()
        self.e_out.textEdited.connect(lambda *_: setattr(self, "_path_edited", True))
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.clicked.connect(self.browse_output)
        self.btn_open = QPushButton("打开目录")
        self.btn_open.clicked.connect(self.open_out_dir)
        prow.addWidget(QLabel("输出"), 0)
        prow.addWidget(self.e_out, 1); prow.addWidget(self.btn_browse); prow.addWidget(self.btn_open)
        lay.addLayout(prow)

        # 操作
        arow = QHBoxLayout()
        self.btn_export = QPushButton("导出")
        self.btn_export.clicked.connect(self.start_export)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.cancel_export)
        self.btn_cancel.setEnabled(False)
        arow.addWidget(self.btn_export); arow.addWidget(self.btn_cancel); arow.addStretch(1)
        lay.addLayout(arow)

        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.lbl_progress = QLabel("就绪")
        lay.addWidget(self.lbl_progress)

        lay.addWidget(QLabel("日志"))
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        lay.addWidget(self.log_view, 1)
        return box

    # ================================================================ 数据
    def _reload_store(self) -> None:
        from core.memory import MemoryStore
        dim = self.config.get("embedding.dim") or 1024
        self.store = MemoryStore(self.config.memory_dir, dim)

    def default_dir(self) -> str:
        # <workspace>\\qq_memory\\export
        return os.path.join(os.path.dirname(os.path.abspath(self.config.memory_dir)), "export")

    def reload_chats(self) -> None:
        if self._chats_worker and self._chats_worker.isRunning():
            return
        self._reload_store()
        self.log("正在读取会话列表…（1GB 级数据库，请稍候）")
        self.lbl_count.setText("共 … 个会话")
        self.btn_reload.setEnabled(False)
        dim = self.config.get("embedding.dim") or 1024
        self._chats_worker = ChatsWorker(self.config.memory_dir, dim, self)
        self._chats_worker.done.connect(self._on_chats)
        self._chats_worker.start()

    def _on_chats(self, chats: list, error: str) -> None:
        self.btn_reload.setEnabled(True)
        if error:
            self.chats = []
            self.log(f"会话列表读取失败：{error}")
            QMessageBox.warning(self, "读取失败", error)
            return
        self.chats = chats
        if not chats:
            # 空库：会话数为 0 —— 列表区 + 日志给出明确说明与统一引导
            self.table.setRowCount(0)
            self.lbl_count.setText("未找到可导出的会话：记忆库为空或目录未设置")
            self.log("未找到可导出的会话：记忆库为空或目录未设置")
            self.log(memory_missing_title(self.config.memory_dir))
            self.log("建议操作：" + " ".join(GUIDE_STEPS))
            self.lbl_progress.setText("记忆库为空，暂无可导出的会话")
            return
        self.log(f"会话列表就绪：共 {len(chats)} 个会话")
        self._apply_filter()

    def _apply_filter(self) -> None:
        kw = self.f_filter.text().strip().lower()
        rows = [c for c in self.chats
                if not kw or kw in (c.get("name") or "").lower()
                or kw in str(c.get("chat_id") or "").lower()]
        self.table.setRowCount(len(rows))
        for i, c in enumerate(rows):
            span = ""
            if c.get("ts_min") or c.get("ts_max"):
                span = f"{fmt_ts(c.get('ts_min')) or '?'} ~ {fmt_ts(c.get('ts_max')) or '?'}"
            vals = [chat_type_label(c.get("chat_type")), c.get("name") or "", f"{c.get('count', 0):,}",
                    span, str(c.get("chat_id"))]
            for col, val in enumerate(vals):
                text = str(val)
                item = QTableWidgetItem(text)
                # 悬停 tooltip 显示完整内容（尤其「名称」列被截断时可看全名）
                item.setToolTip(text)
                if col == 4:
                    item.setData(Qt.UserRole, c)
                if col == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, col, item)
        con_name = f"（过滤后 {len(rows)} 个）" if kw else ""
        self.lbl_count.setText(f"共 {len(self.chats)} 个会话{con_name}")

    def current_chat(self) -> dict | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 4)
        return item.data(Qt.UserRole) if item else None

    def _on_pick_chat(self) -> None:
        c = self.current_chat()
        if c and not self._path_edited:
            self.e_out.setText(self._suggested_path(c))

    def _on_fmt_changed(self) -> None:
        c = self.current_chat()
        if c and not self._path_edited:
            self.e_out.setText(self._suggested_path(c))

    def _on_range_toggle(self) -> None:
        custom = self.rb_range.isChecked()
        self.d_from.setEnabled(custom); self.d_to.setEnabled(custom)

    def _suggested_path(self, c: dict) -> str:
        ext = FORMATS[self.cmb_fmt.currentIndex()][1]
        name = default_filename(c.get("name") or str(c.get("chat_id")), c.get("chat_type") or "", ext)
        return os.path.join(self.default_dir(), name)

    def browse_output(self) -> None:
        c = self.current_chat()
        suggested = self.e_out.text().strip() or (
            self._suggested_path(c) if c else os.path.join(self.default_dir(), "会话导出.txt"))
        d = os.path.dirname(suggested) or self.default_dir()
        os.makedirs(d, exist_ok=True)
        filt = FORMATS[self.cmb_fmt.currentIndex()][2]
        path, _ = QFileDialog.getSaveFileName(self, "选择导出文件", suggested, filt)
        if path:
            self.e_out.setText(path)
            self._path_edited = True

    def open_out_dir(self) -> None:
        d = os.path.dirname(self.e_out.text().strip()) or self.default_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606 (Windows)
        except Exception as e:  # noqa: BLE001
            QMessageBox.information(self, "目录", f"{d}\n\n打开失败：{e}")

    def log(self, msg: str) -> None:
        self.log_view.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # ================================================================ 导出
    def start_export(self) -> None:
        if self._export_worker and self._export_worker.isRunning():
            QMessageBox.information(self, "导出中", "当前已有导出任务在运行。")
            return
        c = self.current_chat()
        if not c:
            QMessageBox.information(self, "未选择会话", "请先在左侧选择要导出的好友或群聊。")
            return
        out = self.e_out.text().strip()
        if not out:
            out = self._suggested_path(c)
            self.e_out.setText(out)
        # 规范化扩展名
        ext = FORMATS[self.cmb_fmt.currentIndex()][1]
        if not out.lower().endswith("." + ext):
            out = out + "." + ext
            self.e_out.setText(out)
        # 清洗文件名（目录部分保留）
        d, base = os.path.split(out)
        out = os.path.join(d, sanitize_filename(base, fallback="会话导出"))
        self.e_out.setText(out)

        # 安全红线：不得写入记忆库目录
        mem = os.path.abspath(self.config.memory_dir)
        if os.path.abspath(out).startswith(mem + os.sep):
            QMessageBox.warning(self, "路径非法", "输出路径位于记忆库目录内，已拒绝。")
            return

        if os.path.exists(out):
            if QMessageBox.question(self, "文件已存在", f"{out}\n是否覆盖？") != QMessageBox.Yes:
                return

        ts_from = ts_to = 0
        if self.rb_range.isChecked():
            ts_from = _d2ts(self.d_from.date())
            ts_to = _d2ts(self.d_to.date()) + 86399
            if ts_to < ts_from:
                QMessageBox.warning(self, "时间范围", "结束日期早于起始日期。")
                return

        params = {
            "chat_type": c.get("chat_type"), "chat_id": c.get("chat_id"),
            "chat_name": c.get("name") or str(c.get("chat_id")),
            "fmt": ext, "out_path": out, "ts_from": ts_from, "ts_to": ts_to,
            "only_me": self.chk_only_me.isChecked(), "show_qq": self.chk_qq.isChecked(),
            "by_day": self.chk_byday.isChecked(),
        }
        self._reload_store()
        self.log(f"开始导出：{c.get('name')} → {out}")
        self.progress.setValue(0)
        self.lbl_progress.setText("导出中…")
        self.btn_export.setEnabled(False); self.btn_cancel.setEnabled(True)
        self.btn_reload.setEnabled(False)

        self._export_worker = ExportWorker(self.store, params, self)
        self._export_worker.log.connect(self.log)
        self._export_worker.progress.connect(self._on_progress)
        self._export_worker.done.connect(self._on_done)
        self._export_worker.start()

    def _on_progress(self, done: int, total: int, elapsed: float) -> None:
        pct = int(done / total * 100) if total else 0
        self.progress.setValue(min(100, pct))
        self.lbl_progress.setText(
            f"已导出 {done:,}/{total:,} 条 · {pct}% · 已用时 {elapsed:.1f}s")

    def _on_done(self, res: dict) -> None:
        self.btn_export.setEnabled(True); self.btn_cancel.setEnabled(False)
        self.btn_reload.setEnabled(True)
        if res.get("canceled"):
            self.lbl_progress.setText(f"已取消（临时文件已丢弃，未生成成品）")
            self.log(f"导出已取消：未生成成品文件（临时 .part 已清理），"
                     f"用时 {res.get('elapsed', 0)}s")
            return
        if res.get("error"):
            self.lbl_progress.setText("导出失败")
            msg = res.get("error") or "未知错误"
            detail = res.get("error_detail") or ""
            self.log(f"导出失败：{msg}" + (f"（{detail}）" if detail else ""))
            nl = chr(10) * 2
            box = msg + (f"{nl}详情：{detail}" if detail else "")
            QMessageBox.warning(self, "导出失败", box)
            return
        self.progress.setValue(100)
        deg = "（docx 降级写入）" if res.get("degraded") else ""
        cleaned = res.get("cleaned", 0)
        clean_txt = f" · 清洗 {cleaned:,} 条" if cleaned else ""
        self.lbl_progress.setText(
            f"完成：{res.get('count', 0):,} 条{clean_txt} · {res.get('elapsed', 0)}s · "
            f"{res.get('bytes', 0):,} 字节")
        self.log(f"✓ 导出完成{deg}：{res.get('out_path')}  "
                 f"共 {res.get('count', 0):,} 条{clean_txt}，用时 {res.get('elapsed', 0)}s")

    def cancel_export(self) -> None:
        if self._export_worker and self._export_worker.isRunning():
            self.log("请求取消导出…")
            self._export_worker.cancel()

    # ================================================================ 清理
    def stop_workers(self) -> None:
        for w in (self._chats_worker, self._export_worker):
            if w and w.isRunning():
                if isinstance(w, ExportWorker):
                    w.cancel()
                w.wait(8000)

    def closeEvent(self, event) -> None:
        self.stop_workers()
        super().closeEvent(event)


if __name__ == "__main__":
    from core import config as C
    app = QApplication([])
    w = ExportWindow(C.Config.load())
    w.show()
    app.exec()
