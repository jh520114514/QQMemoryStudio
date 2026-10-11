# -*- coding: utf-8 -*-
"""1 级主窗口：状态面板 + 基础设置 + 操作按钮 + 运行日志。"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QCheckBox, QPlainTextEdit, QComboBox, QMessageBox, QFileDialog,
    QSpinBox, QFormLayout,
)

from core.config import Config
from core.memory import (human_size, memory_missing_guide, memory_missing_title,
                         GUIDE_STEPS, is_missing_error)
from core.sync import SyncRunner, KeyFetchRunner, cached_key
from ui.workers import StatsWorker, EmbedTestWorker, SyncWorker, KeyFetchWorker
from ui.search_window import SearchWindow
from ui.ai_window import AIWindow
from ui.export_window import ExportWindow

DOT = {"unknown": "color:#9e9e9e;", "wait": "color:#e0a000;", "ok": "color:#1a7f37;",
       "fail": "color:#c62828;"}


class MainWindow(QMainWindow):
    def __init__(self, config: Config | None = None, first_run: bool = False):
        super().__init__()
        self.config = config or Config.load()
        self._first_run = bool(first_run)
        self.search_win: SearchWindow | None = None
        self.ai_win: AIWindow | None = None
        self.export_win: ExportWindow | None = None
        self._stats_worker = None
        self._embed_worker = None
        self._sync_worker = None
        self._key_worker = None
        # 本机缓存密钥（keys_found.json / keys/<account>.key）：用于「密钥状态」与同步前置校验
        self._cached_key = cached_key(self.config.work_dir, self.config.memory_dir,
                                      self.config.qq_account)
        self.setWindowTitle("QQ Memory Studio · 长期记忆库管理")
        self.resize(980, 780)
        self._build()
        self._load_config_into_ui()
        self.log("QQ Memory Studio 启动")
        self.log(f"记忆库目录：{self.config.memory_dir}")
        self.refresh_stats()
        # 首次运行（启动时 config.json 原本不存在）自动弹出向导；标记过就不再弹。
        if self._first_run and not self.config.get("ui.guide_shown", False):
            QTimer.singleShot(400, self.open_guide)

    # ================================================================ 界面
    def _build(self) -> None:
        root = QVBoxLayout()
        root.addWidget(self._build_status())
        root.addWidget(self._build_settings())
        root.addLayout(self._build_actions())
        root.addWidget(self._build_log(), 1)
        c = QWidget(); c.setLayout(root)
        self.setCentralWidget(c)

        menubar = self.menuBar()
        m = menubar.addMenu("窗口")
        for text, slot in (("向量数据库浏览与检索", self.open_search),
                           ("AI 检索助手", self.open_ai),
                           ("会话导出（好友/群聊全部聊天记录）", self.open_export)):
            act = QAction(text, self); act.triggered.connect(slot); m.addAction(act)
        m2 = menubar.addMenu("帮助")
        act_guide = QAction("使用向导（首次配置指引）", self)
        act_guide.triggered.connect(self.open_guide)
        m2.addAction(act_guide)
        act_about = QAction("关于", self); act_about.triggered.connect(self.about); m2.addAction(act_about)

    def _build_status(self) -> QGroupBox:
        box = QGroupBox("状态（只读）")
        g = QGridLayout(box)

        self.lbl_qq = QLabel("—")
        self.cmb_qq = QComboBox(); self.cmb_qq.setMinimumWidth(140)
        self.cmb_qq.currentTextChanged.connect(lambda t: self._on_account_pick(t))
        self.dot_api = QLabel("●"); self.dot_api.setStyleSheet(DOT["unknown"])
        self.lbl_api = QLabel("未检测")
        btn_refresh = QPushButton("刷新"); btn_refresh.clicked.connect(self.refresh_stats)

        g.addWidget(QLabel("当前 QQ 号"), 0, 0); g.addWidget(self.lbl_qq, 0, 1)
        g.addWidget(QLabel("检测到账号"), 0, 2); g.addWidget(self.cmb_qq, 0, 3)
        g.addWidget(btn_refresh, 0, 5, Qt.AlignRight)
        g.addWidget(QLabel("向量检索 API"), 1, 0)
        g.addWidget(self.dot_api, 1, 1)
        g.addWidget(self.lbl_api, 1, 2, 1, 4)

        self.lbl_db = QLabel("messages.db —")
        self.lbl_vec = QLabel("vecs.f16 —")
        self.lbl_count = QLabel("消息 —")
        self.lbl_chats = QLabel("会话 —")
        self.lbl_vcnt = QLabel("向量 —")
        for i, w in enumerate([self.lbl_db, self.lbl_count, self.lbl_chats, self.lbl_vcnt, self.lbl_vec]):
            g.addWidget(w, 2 + i // 3, (i % 3) * 2, 1, 2)

        self.lbl_last = QLabel("上次增量备份：未知")
        self.lbl_next = QLabel("下次计划时间：未知")
        g.addWidget(self.lbl_last, 4, 0, 1, 3)
        g.addWidget(self.lbl_next, 4, 3, 1, 3)

        # 空库 / 读取异常时的醒目提示：独立成行、可换行、默认隐藏，
        # 不再与下面的尺寸标签共用同一个 QLabel，避免被覆盖后“看不见”。
        self.lbl_notice = QLabel("")
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setVisible(False)
        self.lbl_notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_notice.setStyleSheet("color:#c62828; font-weight:bold;")
        g.addWidget(self.lbl_notice, 5, 0, 1, 6)
        return box

    def _build_settings(self) -> QGroupBox:
        box = QGroupBox("基础设置（保存到 config.json）")
        form = QFormLayout(box)
        self.e_qqdir = QLineEdit(); self.e_qqdir.setPlaceholderText("留空 = 自动探测 Tencent Files")
        self.e_qq = QLineEdit()
        self.e_mem = QLineEdit()
        self.e_dkey = QLineEdit(); self.e_dkey.setEchoMode(QLineEdit.Password)
        self.e_ebase = QLineEdit(); self.e_ekey = QLineEdit(); self.e_ekey.setEchoMode(QLineEdit.Password)
        self.e_emodel = QLineEdit()
        self.e_abse = QLineEdit(); self.e_akey = QLineEdit(); self.e_akey.setEchoMode(QLineEdit.Password)
        self.e_amodel = QLineEdit()
        self.chk_recall = QCheckBox("启用自动召回")
        # 密钥状态就地可见 + 一步到位的「自动抓取密钥」入口，
        # 不再让用户点了同步才被弹框拦住。
        self.lbl_key = QLabel("—")
        self.b_fetchkey = QPushButton("自动抓取密钥…")
        self.b_fetchkey.clicked.connect(self.fetch_key)
        row_key = QHBoxLayout()
        row_key.addWidget(self.lbl_key, 1); row_key.addWidget(self.b_fetchkey)
        row_btn = QHBoxLayout()
        b_save = QPushButton("保存配置"); b_save.clicked.connect(self.save_config)
        b_open = QPushButton("打开配置文件"); b_open.clicked.connect(self.open_config_file)
        b_reset = QPushButton("恢复默认"); b_reset.clicked.connect(self.reset_config)
        row_btn.addWidget(b_save); row_btn.addWidget(b_open); row_btn.addWidget(b_reset); row_btn.addStretch(1)

        form.addRow("QQ 数据目录", self.e_qqdir)
        form.addRow("QQ 号", self.e_qq)
        form.addRow("记忆库目录", self.e_mem)
        form.addRow("SQLCipher 密钥", self.e_dkey)
        form.addRow("密钥状态", row_key)
        form.addRow("embedding base_url", self.e_ebase)
        form.addRow("embedding api_key", self.e_ekey)
        form.addRow("embedding model", self.e_emodel)
        form.addRow("AI base_url", self.e_abse)
        form.addRow("AI api_key", self.e_akey)
        form.addRow("AI model", self.e_amodel)
        form.addRow("", self.chk_recall)
        form.addRow("", row_btn)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.b_embed = QPushButton("测试 embedding 连接"); self.b_embed.clicked.connect(self.test_embedding)
        self.b_inc = QPushButton("立即增量同步"); self.b_inc.clicked.connect(lambda: self.start_sync(False))
        self.b_full = QPushButton("全量重建"); self.b_full.clicked.connect(lambda: self.start_sync(True))
        self.b_cancel = QPushButton("取消同步"); self.b_cancel.clicked.connect(self.cancel_sync)
        self.b_cancel.setEnabled(False)
        self.b_dir = QPushButton("打开记忆库目录"); self.b_dir.clicked.connect(self.open_memory_dir)
        self.b_pick = QPushButton("选择记忆库目录…"); self.b_pick.clicked.connect(self.pick_memory_dir)
        self.b_cfg = QPushButton("打开配置"); self.b_cfg.clicked.connect(self.open_config_file)
        self.b_srch = QPushButton("检索窗口"); self.b_srch.clicked.connect(self.open_search)
        self.b_ai = QPushButton("AI 助手"); self.b_ai.clicked.connect(self.open_ai)
        self.b_export = QPushButton("会话导出"); self.b_export.clicked.connect(self.open_export)
        for b in (self.b_embed, self.b_inc, self.b_full, self.b_cancel, self.b_pick, self.b_dir,
                  self.b_cfg, self.b_srch, self.b_ai, self.b_export):
            row.addWidget(b)
        return row

    def _build_log(self) -> QGroupBox:
        box = QGroupBox("运行日志")
        lay = QVBoxLayout(box)
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        lay.addWidget(self.log_view)
        return box

    # ================================================================ 日志
    def log(self, msg: str) -> None:
        self.log_view.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # ================================================================ 配置
    def _load_config_into_ui(self) -> None:
        c = self.config
        self.e_qqdir.setText(c.qq_data_dir)
        self.e_qq.setText(c.qq_account)
        self.e_mem.setText(c.memory_dir)
        self.e_dkey.setText(c.db_key)
        self.e_ebase.setText(c.get("embedding.base_url") or "")
        self.e_ekey.setText(c.get("embedding.api_key") or "")
        self.e_emodel.setText(c.get("embedding.model") or "")
        self.e_abse.setText(c.get("ai.base_url") or "")
        self.e_akey.setText(c.get("ai.api_key") or "")
        self.e_amodel.setText(c.get("ai.model") or "")
        self.chk_recall.setChecked(bool(c.get("features.auto_recall")))
        self._refresh_key_status()

    def _refresh_key_status(self, fetched_ok: bool | None = None) -> None:
        """密钥状态就地可见，不靠弹框。fetched_ok 用于区分「刚抓到」和「本地已有」。"""
        if self.config.db_key:
            self.lbl_key.setText("<span style='color:#1a7f37;'>已配置（来自 config.json）</span>")
        elif self._cached_key:
            src = "刚抓取" if fetched_ok else "本机缓存"
            self.lbl_key.setText(f"<span style='color:#1a7f37;'>已可用（{src}，{len(self._cached_key)} 字符）</span>")
        else:
            self.lbl_key.setText(
                "<span style='color:#c62828;'>未配置 —— 同步前请填写，或点右侧自动抓取（需管理员 + QQ 已登录）</span>")

    # ================================================================ 自动抓取密钥
    def fetch_key(self) -> None:
        """设置区的「自动抓取密钥…」：只跑抓取，不触发同步。"""
        if self._key_worker and self._key_worker.isRunning():
            QMessageBox.information(self, "抓取中", "密钥抓取正在进行，请稍候。")
            return
        if QMessageBox.question(
                self, "自动抓取密钥",
                "将尝试从正在登录的 QQ 进程里抓取 SQLCipher 密钥。\n"
                "需要：以管理员身份运行本程序 + QQ 已登录。\n\n现在开始？") != QMessageBox.Yes:
            return
        # 先把设置区的输入落盘，避免用户刚填的账号/目录被用默认值覆盖
        self._apply_settings_to_config()
        runner = KeyFetchRunner(work_dir=self.config.work_dir,
                                data_dir=self.config.data_dir,
                                memory_dir=self.config.memory_dir)
        self.b_fetchkey.setEnabled(False)
        self.lbl_key.setText("<span style='color:#e0a000;'>抓取中…</span>")
        self.log("开始自动抓取密钥…")
        self._key_worker = KeyFetchWorker(runner, self)
        self._key_worker.line.connect(self.log)
        self._key_worker.done.connect(self._on_key_fetched)
        self._key_worker.start()

    def _on_key_fetched(self, res: dict) -> None:
        self.b_fetchkey.setEnabled(True)
        if res.get("key"):
            self._cached_key = res["key"]
            self._refresh_key_status(True)
            self.log(f"✓ 已抓取到候选密钥（{len(self._cached_key)} 字符），已写入 keys_found.json；"
                     "同步时会自动使用")
            return
        if res.get("cancelled"):
            self.log("密钥抓取已取消")
        else:
            self.log("✗ 未命中任何候选密钥 → 建议：确认 QQ 已登录、以管理员身份运行，"
                     "或在「SQLCipher 密钥」手动填写 db_key")
        self._refresh_key_status()

    def _apply_settings_to_config(self) -> None:
        """把设置区各输入框写回 config 对象（不落盘），供保存/抓取/同步前复用。"""
        c = self.config
        c.set("qq.data_dir", self.e_qqdir.text().strip())
        c.set("qq.account", self.e_qq.text().strip())
        c.set("paths.memory_dir", self.e_mem.text().strip() or c.memory_dir)
        c.set("paths.db_key", self.e_dkey.text())
        c.set("embedding.base_url", self.e_ebase.text().strip())
        c.set("embedding.api_key", self.e_ekey.text().strip())
        c.set("embedding.model", self.e_emodel.text().strip())
        c.set("ai.base_url", self.e_abse.text().strip())
        c.set("ai.api_key", self.e_akey.text().strip())
        c.set("ai.model", self.e_amodel.text().strip())
        c.set("features.auto_recall", self.chk_recall.isChecked())

    def save_config(self) -> None:
        self._apply_settings_to_config()
        try:
            self.config.save()      # 先落盘再提示，否则“已保存”是假的
            self.log(f"配置已保存：{self.config.path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))
        self._refresh_key_status()
        self.refresh_stats()

    def reset_config(self) -> None:
        if QMessageBox.question(self, "恢复默认", "将用默认值覆盖 config.json 并重新载入，继续？") != QMessageBox.Yes:
            return
        from core.config import default_config
        self.config.data = default_config()
        self.config.save()
        self._load_config_into_ui()
        self.log("已恢复默认配置")
        self.refresh_stats()

    def open_config_file(self) -> None:
        try:
            os.startfile(self.config.path)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            QMessageBox.information(self, "配置路径", f"{self.config.path}\n\n打开失败：{e}")

    def open_memory_dir(self) -> None:
        d = self.config.memory_dir
        if not os.path.isdir(d):
            # 目录不存在：明确提示，而不是静默创建或静默失败
            self.log(f"记忆库目录不存在：{d}")
            QMessageBox.information(
                self, "目录不存在",
                f"{memory_missing_title(d)}\n\n建议操作：\n" + "\n".join(GUIDE_STEPS))
            return
        try:
            os.startfile(d)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", f"{d}\n{e}")

    def pick_memory_dir(self) -> None:
        """一键选择「记忆库目录」：QFileDialog 选目录 → 写入 config → 刷新统计。"""
        cur = self.config.memory_dir
        # 起始目录取「当前配置或其最近的已存在祖先」，避免首次运行时指向不存在的路径
        # 导致原生选择框弹「位置不可用」。
        start = cur
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            if parent == start:
                break
            start = parent
        if not start or not os.path.isdir(start):
            start = os.path.expanduser("~") or os.environ.get("SystemDrive", "C:") + "\\"
        d = QFileDialog.getExistingDirectory(self, "选择记忆库目录（含 messages.db 的文件夹）", start)
        if not d:
            return
        d = os.path.abspath(d)
        self.config.set("paths.memory_dir", d)
        self.e_mem.setText(d)
        try:
            self.config.save()
            self.log(f"记忆库目录已更新：{d}（已写入配置）")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))
        # 已打开的子窗口同步换库，避免仍指向旧目录
        if self.search_win is not None:
            try:
                self.search_win.reload_backends()
                self.search_win.refresh_overview()
            except Exception:  # noqa: BLE001
                pass
        if self.export_win is not None:
            try:
                self.export_win.reload_chats()
            except Exception:  # noqa: BLE001
                pass
        self.refresh_stats()

    # ================================================================ 统计
    def refresh_stats(self) -> None:
        if self._stats_worker and self._stats_worker.isRunning():
            return
        self.log("读取记忆库统计…")
        dim = self.config.get("embedding.dim") or 1024
        self._stats_worker = StatsWorker(self.config.memory_dir, dim, self.config.qq_data_dir, self)
        self._stats_worker.done.connect(self._on_stats)
        self._stats_worker.start()

    def _on_stats(self, r: dict) -> None:
        if r.get("error"):
            self.log(f"统计失败：{r['error']}")
        s = r.get("stats") or {}
        mem_err = s.get("error") or ""
        mem_missing = is_missing_error(mem_err)
        if mem_err:
            if mem_missing:
                # 首次运行 / 库尚未生成：属于正常状态，不要说成“读取异常”，
                # 否则与下面那句「未找到记忆库」口径不一致（听起来像库坏了）。
                self.lbl_db.setText("messages.db：尚未生成")
            else:
                # 真的读不到（损坏 / 权限 / 被占用）：保留可行动的错误信息。
                self.lbl_db.setText(f"messages.db 读取异常：{mem_err}")
                self.log(f"messages.db 读取异常：{mem_err}")
        # 未配置账号但只探测到一个账号时，自动采用（仅写 studio 自己的 config.json）
        accounts = r.get("accounts") or []
        if not self.config.qq_account and len(accounts) == 1:
            self.config.set("qq.account", accounts[0])
            try:
                self.config.save()
            except OSError:
                pass
            self.e_qq.setText(accounts[0])
            self.log(f"自动采用探测到的 QQ 号：{accounts[0]}")
        self.lbl_qq.setText(self.config.qq_account or
                            ("未选择（可在右侧下拉选择）" if accounts else "未探测到"))
        if not mem_err:
            self.lbl_db.setText(f"messages.db：{human_size(s.get('db_bytes', 0))}")
        self.lbl_vec.setText(f"vecs.f16：{human_size(s.get('vecs_bytes', 0))}")
        self.lbl_count.setText(f"消息条数：{s.get('count', 0):,}")
        self.lbl_chats.setText(f"会话数：{s.get('chats', 0):,}（私聊 {s.get('c2c_chats', 0)} / 群 {s.get('group_chats', 0)}）")
        self.lbl_vcnt.setText(f"向量条数：{s.get('vec_count', 0):,} × {s.get('vec_dim', 0)} 维")
        self.lbl_last.setText(f"上次增量备份：{r.get('last_backup', '未知')}")
        self.lbl_next.setText(f"下次计划时间：{r.get('next_scheduled', '未知')}")
        # 空库 / 读取异常：状态区给出持续可见的醒目提示（独立 QLabel，不被任何尺寸标签覆盖）
        if mem_err or not s.get("db_bytes"):
            self.lbl_notice.setText(
                "⚠ " + memory_missing_title(self.config.memory_dir) + "\n"
                + "建议操作：\n" + "\n".join(GUIDE_STEPS))
            self.lbl_notice.setVisible(True)
            if not mem_err:
                self.log(f"未找到记忆库：{self.config.memory_dir}"
                         "（可点「选择记忆库目录…」或「立即增量同步」生成）")
        else:
            self.lbl_notice.setVisible(False)
            self.lbl_notice.setText("")
        # embedding 配置摘要
        base = self.config.get("embedding.base_url") or "—"
        model = self.config.get("embedding.model") or "—"
        keyed = "已配" if self.config.get("embedding.api_key") else "未配"
        self.lbl_api.setText(f"base_url={base} · model={model} · key={keyed}")
        # 账号下拉
        accounts = r.get("accounts") or []
        cur = self.config.qq_account
        if self.cmb_qq.count() == 0 and accounts:
            self.cmb_qq.blockSignals(True)
            self.cmb_qq.addItems(accounts)
            if cur in accounts:
                self.cmb_qq.setCurrentText(cur)
            self.cmb_qq.blockSignals(False)
        self.log(f"统计完成：{s.get('count', 0):,} 条 / {s.get('chats', 0)} 会话 / "
                 f"库 {human_size(s.get('db_bytes', 0))}")

    def _on_account_pick(self, text: str) -> None:
        if not text:
            return
        self.e_qq.setText(text)
        self.config.set("qq.account", text)
        try:
            self.config.save()
        except OSError:
            pass
        self.lbl_qq.setText(text)
        self.log(f"已选择账号：{text}（已写入配置）")

    # ================================================================ embedding 测试
    def test_embedding(self) -> None:
        if self._embed_worker and self._embed_worker.isRunning():
            return
        self.dot_api.setStyleSheet(DOT["wait"]); self.lbl_api.setText("测试中…")
        self.log("测试 embedding 连接…")
        self._embed_worker = EmbedTestWorker(
            self.config.memory_dir, self.config.get("embedding"), self)
        self._embed_worker.done.connect(self._on_embed)
        self._embed_worker.start()

    def _on_embed(self, ok: bool, msg: str) -> None:
        self.dot_api.setStyleSheet(DOT["ok"] if ok else DOT["fail"])
        self.lbl_api.setText(("连通 · " if ok else "失败 · ") + msg)
        self.log(("✓ embedding 连通：" if ok else "✗ embedding 失败：") + msg)

    # ================================================================ 同步
    def _precheck_sync(self) -> str:
        """同步前置校验：不通过就返回原因与下一步动作，绝不进入同步流程。

        回头路是：先在设置区给出原因 + 可点的密钥入口，而不是弹一个阻塞模态框
        （弹框期间主窗口 Enabled=False，用户看到的就是「卡死」）。
        """
        if not self.config.db_key and not self._cached_key:
            return ("缺少 SQLCipher 密钥。",
                    "请在「SQLCipher 密钥」填写 db_key，或点右侧「自动抓取密钥…」"
                    "（需管理员权限 + QQ 已登录）。")
        from core import qq as QQ
        if not QQ.find_nt_msg(self.config.qq_data_dir, self.config.qq_account):
            where = self.config.qq_data_dir or "（当前为自动探测）"
            return ("未找到 QQ 数据目录 / nt_msg.db。",
                    f"当前 QQ 数据目录：{where}。请在设置区确认「QQ 数据目录」与「QQ 号」，"
                    "或先登录一次 QQ 后重试。")
        return "", ""

    def _reject_sync(self, title: str, hint: str) -> None:
        """校验未通过：只给内联提示 + 日志，不弹阻塞模态框、不进入同步。"""
        self.lbl_notice.setText(f"⚠ 无法开始同步：{title}\n{hint}")
        self.lbl_notice.setVisible(True)
        self.log(f"✗ 无法开始同步：{title} → {hint}")
        self._refresh_key_status()

    def start_sync(self, full: bool) -> None:
        if self._sync_worker and self._sync_worker.isRunning():
            QMessageBox.information(self, "同步进行中", "当前已有同步任务在运行。")
            return
        # 先把界面上可能刚改过的配置落盘（否则用户填了密钥仍会报缺密钥）
        self._apply_settings_to_config()
        self._cached_key = cached_key(self.config.work_dir, self.config.memory_dir,
                                      self.config.qq_account)
        self._refresh_key_status()
        title, hint = self._precheck_sync()
        if title:
            self._reject_sync(title, hint)
            return
        key = self.config.db_key or self._cached_key
        # 密钥只走环境变量传递（argv 对同机任意进程可见），因此不再需要 auto_key 弹框分支。
        if full and QMessageBox.warning(
                self, "全量重建",
                "全量重建会清空 memory\\messages.db 与断点后从头解析，耗时较长。\n确定继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        runner = SyncRunner(
            memory_dir=self.config.memory_dir, qq_data_dir=self.config.qq_data_dir,
            account=self.config.qq_account, key=key, full=full, auto_key=False,
            data_dir=self.config.data_dir, work_dir=self.config.work_dir)
        self._sync_worker = SyncWorker(runner, self)
        self._sync_worker.line.connect(self.log)
        self._sync_worker.done.connect(self._on_sync_done)
        self._set_syncing(True)
        self.log(f"开始{'全量重建' if full else '增量同步'}…")
        self._sync_worker.start()

    def cancel_sync(self) -> None:
        if self._sync_worker and self._sync_worker.isRunning():
            self.log("请求取消同步…")
            self._sync_worker.cancel()

    def _set_syncing(self, on: bool) -> None:
        for b in (self.b_inc, self.b_full):
            b.setEnabled(not on)
        self.b_cancel.setEnabled(on)

    def _on_sync_done(self, res: dict) -> None:
        self._set_syncing(False)
        if res.get("ok"):
            self.log(f"✓ 同步完成（{res.get('elapsed', 0):.0f}s）")
        else:
            # 不再把原始 SYNC_FAIL 串丢给用户；给中文原因 + 下一步动作。
            reason = res.get("reason") or "未知原因"
            hint = (res.get("hint") or "").strip()
            tail = f" → 建议：{hint}" if hint else ""
            self.log(f"✗ 同步失败：{reason}（code={res.get('code')}）{tail}")
        self._cached_key = cached_key(self.config.work_dir, self.config.memory_dir,
                                      self.config.qq_account)
        self._refresh_key_status()
        self.refresh_stats()

    # ================================================================ 子窗口
    def open_search(self) -> None:
        if self.search_win is None:
            self.search_win = SearchWindow(self.config)
        self.search_win.reload_backends()
        self.search_win.refresh_overview()
        self.search_win.show(); self.search_win.raise_(); self.search_win.activateWindow()

    def open_ai(self) -> None:
        if self.ai_win is None:
            self.ai_win = AIWindow(self.config)
            self.ai_win.locate_requested.connect(self._locate)
        self.ai_win.show(); self.ai_win.raise_(); self.ai_win.activateWindow()

    def open_export(self) -> None:
        if self.export_win is None:
            self.export_win = ExportWindow(self.config)
        self.export_win.show(); self.export_win.raise_(); self.export_win.activateWindow()

    def _locate(self, msg_id: int) -> None:
        self.open_search()
        self.search_win.reveal(msg_id)

    # ================================================================ 退出清理
    def closeEvent(self, event) -> None:
        """退出前结束后台线程，避免 'QThread: Destroyed while thread is still running'。"""
        try:
            if self._sync_worker and self._sync_worker.isRunning():
                self._sync_worker.cancel()
                self._sync_worker.wait(8000)
            if self._key_worker and self._key_worker.isRunning():
                self._key_worker.cancel()
                self._key_worker.wait(8000)
            for w in (self._stats_worker, self._embed_worker):
                if w and w.isRunning():
                    w.wait(8000)
            for win in (self.search_win, self.ai_win, self.export_win):
                if win is not None:
                    win.stop_workers()
                    win.close()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)

    def open_guide(self) -> None:
        """打开「使用向导」（首次自动弹出，之后可由菜单手动再次打开）。"""
        from ui.guide_window import GuideDialog
        if getattr(self, "_guide_dialog", None) is not None:
            self._guide_dialog.raise_(); self._guide_dialog.activateWindow()
            return
        dlg = GuideDialog(self.config, self)
        self._guide_dialog = dlg
        dlg.applied.connect(self._on_guide_applied)
        self.log("打开使用向导")
        dlg.exec()
        self._guide_dialog = None

    def _on_guide_applied(self) -> None:
        """向导点「完成」：把配置同步到界面并刷新统计。"""
        self._load_config_into_ui()
        self.log(f"向导已应用：记忆库目录 = {self.config.memory_dir}")
        self.refresh_stats()

    def about(self) -> None:
        QMessageBox.information(
            self, "关于 QQ Memory Studio",
            "QQ 长期记忆库桌面管理器\n\n"
            "· 只读浏览 messages.db / 向量库\n"
            "· 复用 qq_memory 的流水线与检索模块\n"
            "· 独立程序，不依赖、不修改 AstrBot 实例\n\n"
            f"配置文件：{self.config.path}")
