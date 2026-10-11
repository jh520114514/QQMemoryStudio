# -*- coding: utf-8 -*-
"""2 级窗口 B：AI 检索助手（自然语言问答 + 工具调用 + 引用回显）。"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel, QLineEdit,
    QPushButton, QTextBrowser, QListWidget, QListWidgetItem, QSplitter, QMessageBox, QCheckBox,
)

from ui.workers import AIWorker


class AIWindow(QWidget):
    locate_requested = Signal(int)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.history: list[dict] = []
        self.citations: list[dict] = []
        self._worker: AIWorker | None = None
        self.setWindowTitle("QQ 记忆库 · AI 检索助手")
        self.resize(1100, 720)
        self._build()
        self._check_ready()

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        root = QVBoxLayout(self)

        api = QGroupBox("接口（本窗口临时覆盖，不改动配置文件）")
        g = QGridLayout(api)
        self.e_base = QLineEdit(self.config.get("ai.base_url") or "")
        self.e_key = QLineEdit(self.config.get("ai.api_key") or "")
        self.e_key.setEchoMode(QLineEdit.Password)
        self.e_model = QLineEdit(self.config.get("ai.model") or "")
        btn_reset = QPushButton("读取配置")
        btn_reset.clicked.connect(self._reset_api)
        g.addWidget(QLabel("base_url"), 0, 0); g.addWidget(self.e_base, 0, 1, 1, 3)
        g.addWidget(QLabel("api_key"), 1, 0); g.addWidget(self.e_key, 1, 1)
        g.addWidget(QLabel("model"), 1, 2); g.addWidget(self.e_model, 1, 3)
        g.addWidget(btn_reset, 1, 4)
        root.addWidget(api)

        seek = QHBoxLayout()
        seek.addWidget(QLabel("找什么"))
        self.intent = QLineEdit(); self.intent.setPlaceholderText("可选的检索意图，例如“关于搬家/聚会/借钱”…")
        seek.addWidget(self.intent, 1)
        self.k = QLineEdit("8"); self.k.setFixedWidth(48)
        seek.addWidget(QLabel("引用条数")); seek.addWidget(self.k)
        root.addLayout(seek)

        split = QSplitter(Qt.Horizontal)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        split.addWidget(self.view)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("引用的原始消息（点击可在检索窗口定位）"))
        self.refs = QListWidget()
        self.refs.itemClicked.connect(self._on_ref_clicked)
        rl.addWidget(self.refs, 1)
        split.addWidget(right)
        split.setSizes([720, 360])
        root.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.input = QLineEdit(); self.input.setPlaceholderText("用自然语言提问，回车发送…")
        self.input.returnPressed.connect(self.send)
        self.btn_send = QPushButton("发送"); self.btn_send.clicked.connect(self.send)
        self.btn_clear = QPushButton("清空对话"); self.btn_clear.clicked.connect(self.clear)
        self.status = QLabel("就绪")
        bottom.addWidget(self.input, 1); bottom.addWidget(self.btn_send)
        bottom.addWidget(self.btn_clear); bottom.addWidget(self.status)
        root.addLayout(bottom)

    def _reset_api(self) -> None:
        self.e_base.setText(self.config.get("ai.base_url") or "")
        self.e_key.setText(self.config.get("ai.api_key") or "")
        self.e_model.setText(self.config.get("ai.model") or "")

    def _temp_cfg(self) -> dict:
        return {"base_url": self.e_base.text().strip(), "api_key": self.e_key.text().strip(),
                "model": self.e_model.text().strip()}

    def _append(self, who: str, text: str, color: str = "#222222") -> None:
        self.view.append(f'<div style="margin:6px 0;"><b style="color:{color}">{who}：</b><br>'
                         f'<span style="white-space:pre-wrap;">{html.escape(text)}</span></div>')

    # ------------------------------------------------------------ 就绪检查
    def _check_ready(self) -> bool:
        """返回是否可发送；不可发送时给出可读中文说明。"""
        if not self.e_key.text().strip():
            self.view.setHtml(
                '<div style="padding:12px;color:#b00020;">'
                '<b>尚未配置 AI 接口</b><br>请在上方填写 base_url / api_key / model（OpenAI 兼容），'
                '或回到主窗口“基础设置”里保存。<br><br>'
                '提示：本助手需要支持 function calling 的对话模型；'
                '若只想做检索，可直接使用“向量数据库浏览与手动检索”窗口。</div>')
            self.btn_send.setEnabled(False)
            self.status.setText("未配置 AI Key")
            return False
        from core.memory import MemoryStore, memory_missing_title, GUIDE_STEPS
        if not MemoryStore(self.config.memory_dir).exists():
            # 空库：与主窗口 / 检索窗口 / 导出窗口同一套中文口径，避免工具调用抛英文异常
            self.view.setHtml(
                '<div style="padding:12px;color:#b00020;">'
                f'<b>{memory_missing_title(self.config.memory_dir)}</b><br>'
                '记忆库尚未建立或向量文件缺失，请先在主界面设置记忆库目录。<br><br>'
                '建议操作：<br>' + '<br>'.join(GUIDE_STEPS) + '</div>')
            self.btn_send.setEnabled(False)
            self.status.setText("未找到记忆库")
            return False
        self.btn_send.setEnabled(True)
        self.status.setText("就绪")
        return True

    # ------------------------------------------------------------ 发送
    def send(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        q = self.input.text().strip()
        if not q:
            return
        if not self.e_key.text().strip():
            self._check_ready()
            QMessageBox.information(self, "未配置", "请先填写 AI 接口的 base_url / api_key / model。")
            return
        if not self._check_ready():
            return
        intent = self.intent.text().strip()
        full_q = f"（检索意图：{intent}）{q}" if intent else q
        self._append("我", full_q, "#0b5cad")
        self.input.clear()
        self._pending_q = full_q
        try:
            k = int(self.k.text() or 8)
        except ValueError:
            k = 8

        from core.ai import AIClient
        from core.memory import MemoryStore
        from core.semantic import SemanticIndex
        mem = self.config.memory_dir
        dim = self.config.get("embedding.dim") or 1024
        client = AIClient(self._temp_cfg(), MemoryStore(mem, dim),
                          SemanticIndex(mem, self.config.get("embedding"), dim))
        self.btn_send.setEnabled(False)
        self.status.setText("思考中…")
        self._worker = AIWorker(client, full_q, list(self.history), k, self)
        self._worker.event.connect(self._on_event)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_event(self, kind: str, payload) -> None:
        if kind == "status":
            self.status.setText(str(payload))

    def _on_done(self, res: dict) -> None:
        self.btn_send.setEnabled(True)
        if res.get("error"):
            self.status.setText("失败")
            self._append("系统", res["error"], "#b00020")
            return
        answer = res.get("answer") or "(空回复)"
        self._append("助手", answer, "#1a7f37")
        self.history.append({"role": "user", "content": getattr(self, "_pending_q", "")})
        self.history.append({"role": "assistant", "content": answer})
        self.citations = res.get("citations") or []
        self.refs.clear()
        for h in self.citations:
            item = QListWidgetItem(
                f"[{h.get('cite')}] {h.get('time')} · {h.get('chat')} · {h.get('sender')}\n"
                f"    {str(h.get('text') or '')[:70]}")
            item.setData(Qt.UserRole, h.get("id"))
            self.refs.addItem(item)
        self.status.setText(f"完成 · 引用 {len(self.citations)} 条")

    def _on_ref_clicked(self, item: QListWidgetItem) -> None:
        mid = item.data(Qt.UserRole)
        if mid is not None:
            self.locate_requested.emit(int(mid))
            self.status.setText(f"请求定位 id={mid}")

    def clear(self) -> None:
        self.history.clear()
        self.citations.clear()
        self.refs.clear()
        self.view.clear()
        self._check_ready()

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
    w = AIWindow(C.Config.load())
    w.show()
    app.exec()
