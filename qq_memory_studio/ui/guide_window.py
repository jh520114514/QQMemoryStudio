# -*- coding: utf-8 -*-
"""首次使用向导（QDialog，三步，中文）。

面向「全新电脑、解压双击就跑」的用户：第一眼给出人话引导。

* 第 1 步 说明：这是本地长期记忆库管理工具，数据都在本机；提示放到可写目录。
* 第 2 步 记忆库目录：默认预填（exe 同目录 qq_memory\\memory），可另选。
* 第 3 步 embedding / AI（可跳过）：base_url / api_key / model，用现有 config 预填。

约定：
* 「完成」→ 把这几项写入 config.json 并发出 applied 信号（主窗口据此刷新统计）。
* 直接关窗 = 跳过，但标记 ``ui.guide_shown=true`` 照写，保证不再反复弹出。
* 全程不涉及密钥提取 / 数据库解密；对「同步」只带一句中性说明，不展开细节。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
    QStackedWidget, QFileDialog, QMessageBox, QFormLayout, QFrame, QSizePolicy,
)

WINDOW_TITLE = "首次使用向导 · QQ Memory Studio"
STEP_TITLES = ("第 1 步 / 共 3 步 · 这是什么", "第 2 步 / 共 3 步 · 记忆库目录",
               "第 3 步 / 共 3 步 · 语义检索与 AI（可跳过）")


class GuideDialog(QDialog):
    """三步向导。``applied`` 在「完成」（写入配置）后发出。"""

    applied = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._step = 0
        self._marked = False
        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(True)
        self.resize(600, 420)
        self.setMinimumWidth(560)
        self._build()
        self._load_from_config()
        self._sync_step()

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.lbl_head = QLabel(STEP_TITLES[0])
        self.lbl_head.setStyleSheet("font-size:14px; font-weight:bold; color:#1a4f8b;")
        root.addWidget(self.lbl_head)

        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken)
        root.addWidget(line)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_intro())
        self.stack.addWidget(self._page_memory())
        self.stack.addWidget(self._page_embedding())
        root.addWidget(self.stack, 1)

        root.addLayout(self._nav_row())

    def _wrapped(self, html: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(html)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        lay.addWidget(lbl)
        lay.addStretch(1)
        return w

    def _page_intro(self) -> QWidget:
        return self._wrapped(
            "<p>欢迎使用 <b>QQ Memory Studio</b> —— 一个跑在你电脑上的「长期记忆库」管理工具。</p>"
            "<ul>"
            "<li><b>数据都在本机</b>：程序只做本地浏览、检索与导出，<b>不会把你的聊天记录上传到任何地方</b>。</li>"
            "<li>生成的记忆库、导出文件，都会放在<b>你自己指定的目录</b>里。</li>"
            "<li>本程序是绿色便携软件：删掉整个文件夹就等于卸载，不写注册表、不装服务。</li>"
            "</ul>"
            "<p style='color:#c62828;'><b>重要：请把本程序放在一个可写目录里（例如桌面、D 盘自建文件夹），"
            "不要放在 C:\\Program Files 这类受保护目录</b>，否则配置和数据无法写入。</p>"
            "<p style='color:#666;'>下面还有 2 步，大约 1 分钟；也可以随时关掉，需要时再从菜单「帮助 → 使用向导」打开。</p>")

    def _page_memory(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        tip = QLabel("<p><b>记忆库目录</b>：程序在这里读取 / 生成记忆数据。</p>"
                     "<p>已经有记忆库的，就把目录指过去；没有的，保持默认即可"
                     "（默认是程序同目录下的 <code>qq_memory\\memory</code>）。</p>")
        tip.setWordWrap(True); tip.setTextFormat(Qt.RichText)
        lay.addWidget(tip)

        row = QHBoxLayout()
        self.e_mem = QLineEdit()
        self.e_mem.setPlaceholderText("例如 D:\\QQMemory\\memory")
        b_pick = QPushButton("选择其他目录…")
        b_pick.clicked.connect(self._pick_memory)
        row.addWidget(self.e_mem, 1)
        row.addWidget(b_pick)
        lay.addLayout(row)

        note = QLabel("<span style='color:#666;'>提示：记忆库需要先有数据才能检索。目录可以先不建，"
                      "程序不会因为目录为空而报错。</span>")
        note.setWordWrap(True); note.setTextFormat(Qt.RichText)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _page_embedding(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        tip = QLabel("<p><b>语义检索与 AI 助手（可选）</b>："
                     "<b>不填也能用关键词检索</b>；填了才启用语义检索与 AI 助手。</p>"
                     "<p style='color:#666;'>任何 OpenAI 兼容接口都可以，三项目都填上即可；"
                     "不想填就直接点「完成」跳过。</p>")
        tip.setWordWrap(True); tip.setTextFormat(Qt.RichText)
        lay.addWidget(tip)

        form = QFormLayout()
        self.e_ebase = QLineEdit(); self.e_ebase.setPlaceholderText("https://api.example.com/v1")
        self.e_ekey = QLineEdit(); self.e_ekey.setEchoMode(QLineEdit.Password)
        self.e_ekey.setPlaceholderText("sk-…")
        self.e_emodel = QLineEdit(); self.e_emodel.setPlaceholderText("例如 BAAI/bge-m3")
        form.addRow("base_url", self.e_ebase)
        form.addRow("api_key", self.e_ekey)
        form.addRow("model", self.e_emodel)
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _nav_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.lbl_step = QLabel("")
        self.lbl_step.setStyleSheet("color:#888;")
        row.addWidget(self.lbl_step)
        row.addStretch(1)
        self.b_prev = QPushButton("上一步"); self.b_prev.clicked.connect(self._prev)
        self.b_next = QPushButton("下一步"); self.b_next.clicked.connect(self._next)
        b_skip = QPushButton("跳过"); b_skip.clicked.connect(self.reject)
        row.addWidget(self.b_prev)
        row.addWidget(b_skip)
        row.addWidget(self.b_next)
        return row

    # ---------------------------------------------------------------- 数据
    def _load_from_config(self) -> None:
        c = self.config
        self.e_mem.setText(c.memory_dir)
        self.e_ebase.setText(c.get("embedding.base_url") or "")
        self.e_ekey.setText(c.get("embedding.api_key") or "")
        self.e_emodel.setText(c.get("embedding.model") or "")

    def _pick_memory(self) -> None:
        cur = self.e_mem.text().strip() or self.config.memory_dir
        start = cur
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            if parent == start:
                break
            start = parent
        if not start or not os.path.isdir(start):
            start = os.path.expanduser("~") or (os.environ.get("SystemDrive", "C:") + "\\")
        d = QFileDialog.getExistingDirectory(self, "选择记忆库目录（含 messages.db 的文件夹）", start)
        if d:
            self.e_mem.setText(os.path.abspath(d))

    # ---------------------------------------------------------------- 导航
    def _sync_step(self) -> None:
        self.stack.setCurrentIndex(self._step)
        self.lbl_head.setText(STEP_TITLES[self._step])
        self.lbl_step.setText(f"{self._step + 1} / 3")
        self.b_prev.setEnabled(self._step > 0)
        self.b_next.setText("完成" if self._step == 2 else "下一步")

    def _prev(self) -> None:
        if self._step > 0:
            self._step -= 1
            self._sync_step()

    def _next(self) -> None:
        if self._step < 2:
            self._step += 1
            self._sync_step()
        else:
            self._finish()

    # ---------------------------------------------------------------- 落盘
    def _finish(self) -> None:
        c = self.config
        mem = self.e_mem.text().strip()
        if mem:
            try:
                mem = os.path.abspath(mem)
            except Exception:  # noqa: BLE001
                pass
            c.set("paths.memory_dir", mem)
        c.set("embedding.base_url", self.e_ebase.text().strip())
        c.set("embedding.api_key", self.e_ekey.text().strip())
        c.set("embedding.model", self.e_emodel.text().strip())
        c.set("ui.guide_shown", True)
        try:
            c.save()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", f"配置未能写入：\n{e}")
        self._marked = True
        self.applied.emit()
        self.accept()

    def _mark_shown(self) -> None:
        """无论「完成」还是直接关掉，都记一次已看过，保证向导不再反复弹出。"""
        if self._marked:
            return
        self._marked = True
        self.config.set("ui.guide_shown", True)
        try:
            self.config.save()
        except OSError:
            pass

    def done(self, r: int) -> None:  # accept()/reject() 都会走到这里
        self._mark_shown()
        super().done(r)
