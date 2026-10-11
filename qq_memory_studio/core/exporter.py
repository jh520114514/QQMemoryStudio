# -*- coding: utf-8 -*-
"""会话导出：把某个好友/群聊的全部聊天记录导出为 docx / md / txt / html。

安全约定：
* 只通过 MemoryStore（``file:...?mode=ro``）读取记忆库，任何情况下都不写入 memory\\ 目录。
* 输出文件写到用户选择的路径（默认落在 <workspace>\\qq_memory\\export）。
* 大文件用流式写入，逐条落盘，内存占用与消息量无关。
* 先写 ``xxx.<ext>.part`` 临时文件，全部成功后再 rename 成目标文件名；
  失败/取消时删除临时文件，绝不留半截文件冒充成品。

正文清洗（所有格式共用 ``clean_text``）：
* 删除/替换 XML 1.0 非法字符（U+0000–U+0008、U+000B、U+000C、U+000E–U+001F、
  U+FFFE、U+FFFF）以及孤立代理项（U+D800–U+DFFF）。
* \\t \\n \\r 保留；docx 里把换行拆成软换行，避免 XML 里出现裸 \\r。

docx 依赖 python-docx；若环境中没有该库，或 python-docx 写入过程中抛异常，
则自动降级为「zipfile + 最小 OOXML」自写，不引入任何新依赖（会在日志中说明）。
"""
from __future__ import annotations

import html as _html
import os
import re
import time
import zipfile
from datetime import datetime

from core.memory import NULL_TEXT, fmt_ts

CTYPE_LABEL = {"group": "群聊", "c2c": "私聊"}

# Windows 文件/目录名非法字符：\ / : * ? " < > | 以及 0x00-0x1f 控制字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# ------------------------------------------------------------------ 正文清洗
# XML 1.0 允许的字符：
#   #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
# 因此下列都在“非法”之列，XML 序列化（python-docx / lxml）会直接抛
# ValueError: All strings must be XML compatible: ...
#   * 控制字符 U+0000–U+0008、U+000B、U+000C、U+000E–U+001F（保留 \t \n \r）
#   * 非字符 U+FFFE、U+FFFF
#   * 孤立代理项 U+D800–U+DFFF（UTF-8 解码残留）
_XML_BAD_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def _xml_bad_repl(m: "re.Match[str]") -> str:
    """非法字符替换：控制字符 → 可见控制图形占位（U+2400+c）；其余（代理项/非字符）删除。"""
    ch = m.group(0)
    o = ord(ch)
    if o <= 0x1F:
        return chr(0x2400 + o)   # U+2401 ␁ … U+241F ␟（如 \x14 → ␔）
    return ""


def clean_text(s: str | None) -> tuple[str, bool]:
    """清洗正文里的 XML 非法字符。

    :return: (清洗后的文本, 是否发生了变化)
    \\t \\n \\r 原样保留。
    """
    if not s:
        return (s or ""), False
    if not _XML_BAD_RE.search(s):
        return s, False
    new = _XML_BAD_RE.sub(_xml_bad_repl, s)
    return new, True


def has_illegal(s: str | None) -> bool:
    """正文是否含 XML 非法字符（供扫描使用）。"""
    return bool(s) and bool(_XML_BAD_RE.search(s))


def chat_type_label(ct: str) -> str:
    """chat_type → 中文类型标签。"""
    return CTYPE_LABEL.get(ct, ct or "")


def sanitize_filename(name: str, fallback: str = "会话") -> str:
    """清洗 Windows 非法字符，去掉结尾的空格/点，并限制长度。"""
    s, _ = clean_text(name or "")
    s = _ILLEGAL.sub("_", s.strip())
    s = re.sub(r"\s+", " ", s).rstrip(" .")
    if not s:
        s = fallback
    return s[:120]


def default_filename(chat_name: str, chat_type: str, ext: str, when: float | None = None) -> str:
    """默认文件名：会话名_群聊或私聊_YYYYMMDD.<ext>（已做非法字符清洗）。"""
    day = time.strftime("%Y%m%d", time.localtime(when if when else time.time()))
    ext = ext.lstrip(".")
    return f"{sanitize_filename(chat_name)}_{chat_type_label(chat_type)}_{day}.{ext}"


def format_line(msg: dict, show_qq: bool = False) -> str:
    """把单条消息格式化为『[时间] 发送人: 正文』。"""
    ts = msg.get("time") or fmt_ts(msg.get("ts")) or "无时间戳"
    if msg.get("dir") == 2:
        sender = "我"
    else:
        sender = (msg.get("sender_name") or str(msg.get("sender_qq") or "")).strip() or "未知"
        if show_qq and msg.get("sender_qq"):
            sender = f"{sender}({msg['sender_qq']})"
    text = msg.get("text")
    if text is None:
        text = NULL_TEXT
    return f"[{ts}] {sender}: {text}"


def _day_key(msg: dict) -> str:
    """按天分段的日期标题（ts<=0 归为『未知日期』）。"""
    ts = int(msg.get("ts") or 0)
    if ts <= 0:
        return "未知日期"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def build_meta(chat_name: str, chat_type: str, chat_id: str, total: int,
               ts_min: int, ts_max: int) -> dict:
    """构造文档头部元信息。"""
    rng = "—"
    if ts_min or ts_max:
        lo = fmt_ts(ts_min) or "未知"
        hi = fmt_ts(ts_max) or "未知"
        rng = f"{lo} ~ {hi}"
    return {
        "name": chat_name or str(chat_id),
        "type_label": chat_type_label(chat_type),
        "chat_id": str(chat_id),
        "total": int(total),
        "range": rng,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------- 写入器
class _BaseWriter:
    """写入器基类：start() 写头部 → day() 写日期分段 → line() 逐条 → finish() 收尾。

    所有文本统一经过 :func:`clean_text` 清洗；``self.cleaned`` 记录被清洗过的行数。
    """

    def __init__(self, path: str):
        self.path = path
        self.count = 0
        self.cleaned = 0

    def start(self, meta: dict) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def day(self, date_str: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def line(self, msg: dict, show_qq: bool) -> None:  # pragma: no cover
        raise NotImplementedError

    def finish(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def abort(self) -> None:
        """取消/失败时调用：关闭句柄但**不**保证内容完整（临时文件随后会被删除）。"""
        return None


class _PlainTextWriter(_BaseWriter):
    """纯文本 / Markdown 共用实现（二者仅头部与分段标题不同）。"""

    def __init__(self, path: str, markdown: bool = False):
        super().__init__(path)
        self.markdown = markdown
        self._f = None

    def start(self, meta: dict) -> None:
        self._f = open(self.path, "w", encoding="utf-8", newline="\n")
        m = {k: (clean_text(v)[0] if isinstance(v, str) else v) for k, v in meta.items()}
        if self.markdown:
            self._f.write(f"# 会话导出 · {m['name']}\n\n")
            self._f.write(f"- **类型**：{m['type_label']}\n")
            self._f.write(f"- **chat_id**：{m['chat_id']}\n")
            self._f.write(f"- **消息总数**：{m['total']}\n")
            self._f.write(f"- **时间范围**：{m['range']}\n")
            self._f.write(f"- **导出时间**：{m['exported_at']}\n\n---\n\n")
        else:
            self._f.write(f"会话导出：{m['name']}\n")
            self._f.write(f"类型：{m['type_label']}\n")
            self._f.write(f"chat_id：{m['chat_id']}\n")
            self._f.write(f"消息总数：{m['total']}\n")
            self._f.write(f"时间范围：{m['range']}\n")
            self._f.write(f"导出时间：{m['exported_at']}\n")
            self._f.write("=" * 60 + "\n\n")

    def day(self, date_str: str) -> None:
        date_str = clean_text(date_str)[0]
        if self.markdown:
            self._f.write(f"\n## {date_str}\n\n")
        else:
            self._f.write(f"\n===== {date_str} =====\n")

    def line(self, msg: dict, show_qq: bool) -> None:
        text, changed = clean_text(format_line(msg, show_qq))
        if changed:
            self.cleaned += 1
        self._f.write(text + "\n")
        self.count += 1

    def finish(self) -> None:
        if self._f:
            self._f.close()
            self._f = None

    def abort(self) -> None:
        if self._f:
            try:
                self._f.close()
            except Exception:  # noqa: BLE001
                pass
            self._f = None


class _HtmlWriter(_BaseWriter):
    """自包含 HTML（无外部依赖、无 CDN）。"""

    _HEAD = ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
             "<title>{title}</title>\n<style>\n"
             "body{{font-family:'Microsoft YaHei',system-ui,sans-serif;line-height:1.6;"
             "max-width:960px;margin:24px auto;padding:0 16px;color:#222;}}\n"
             "h1{{font-size:20px;}} .meta{{background:#f6f8fa;border:1px solid #e1e4e8;"
             "border-radius:6px;padding:10px 14px;font-size:13px;color:#444;}}\n"
             "h2{{margin-top:26px;padding-bottom:4px;border-bottom:1px solid #eaecef;"
             "font-size:16px;color:#0b5cad;}}\n"
             ".msg{{font-family:Consolas,'Courier New',monospace;font-size:13px;"
             "white-space:pre-wrap;word-break:break-word;margin:2px 0;}}\n"
             "</style>\n</head>\n<body>\n")

    def __init__(self, path: str):
        super().__init__(path)
        self._f = None

    def start(self, meta: dict) -> None:
        self._f = open(self.path, "w", encoding="utf-8", newline="\n")
        m = {k: (clean_text(v)[0] if isinstance(v, str) else v) for k, v in meta.items()}
        self._f.write(self._HEAD.format(title=_html.escape(m["name"])))
        self._f.write(f"<h1>会话导出 · {_html.escape(m['name'])}</h1>\n")
        self._f.write("<div class=\"meta\">")
        self._f.write(f"<div>类型：{_html.escape(m['type_label'])}</div>")
        self._f.write(f"<div>chat_id：{_html.escape(m['chat_id'])}</div>")
        self._f.write(f"<div>消息总数：{m['total']}</div>")
        self._f.write(f"<div>时间范围：{_html.escape(m['range'])}</div>")
        self._f.write(f"<div>导出时间：{_html.escape(m['exported_at'])}</div>")
        self._f.write("</div>\n")

    def day(self, date_str: str) -> None:
        self._f.write(f"<h2>{_html.escape(clean_text(date_str)[0])}</h2>\n")

    def line(self, msg: dict, show_qq: bool) -> None:
        text, changed = clean_text(format_line(msg, show_qq))
        if changed:
            self.cleaned += 1
        self._f.write(f"<div class=\"msg\">{_html.escape(text)}</div>\n")
        self.count += 1

    def finish(self) -> None:
        if self._f:
            self._f.write("</body>\n</html>\n")
            self._f.close()
            self._f = None

    def abort(self) -> None:
        if self._f:
            try:
                self._f.close()
            except Exception:  # noqa: BLE001
                pass
            self._f = None


class _DocxWriter(_BaseWriter):
    """Word .docx：优先 python-docx；不可用或写入抛异常时降级为自写最小 OOXML。

    ⚠ python-docx 的 ``add_paragraph`` 会为定位结尾 ``w:sectPr`` 而扫描全部已有子节点，
    逐条插入 → O(n²)：10 万条会从约 10s 恶化到 4 分钟以上。这里改为直接把 ``<w:p>``
    append 到 ``<w:body>``（O(1)），并在保存前把 sectPr 补回末尾，输出与 python-docx
    完全一致。

    为保证「中途降级」也能重新生成完整文档，所有段落文本始终在内存里累积一份
    （10 万条规模约几十 MB，可接受）；一旦 python-docx 失败即整体改用 OOXML 重写。
    """

    def __init__(self, path: str):
        super().__init__(path)
        self.degraded = False
        self.degrade_reason = ""
        self._doc = None
        self._body = None       # python-docx 的 _Body 包装对象（作为 Paragraph 的 parent）
        self._body_el = None    # <w:body> 元素
        self._sectPr = None     # 临时摘除的结尾 sectPr，保存前补回
        self._para_texts: list[tuple[str, int]] = []  # (text, level) level=0 正文 / 1 一级 / 2 二级
        try:
            import docx  # noqa: F401  (python-docx)
            from docx import Document
            from docx.oxml.ns import qn
            self._doc = Document()
            # 统一正文中文字体
            try:
                self._doc.styles["Normal"].font.name = "Microsoft YaHei"
            except Exception:  # noqa: BLE001
                pass
            self._body = self._doc._body
            self._body_el = self._doc.element.body
            sect = self._body_el.find(qn("w:sectPr"))
            if sect is not None:
                self._body_el.remove(sect)   # 摘除，保证新段落处于正确位置
            self._sectPr = sect
        except Exception as e:  # noqa: BLE001
            self._degrade(e)

    # ---- 降级 ----
    def _degrade(self, exc: BaseException) -> None:
        self._doc = None
        self._body = None
        self._body_el = None
        self.degraded = True
        if not self.degrade_reason:
            self.degrade_reason = f"{type(exc).__name__}: {exc}"

    # ---- 追加段落（O(1)） ----
    def _emit_docx(self, text: str, level: int) -> None:
        from docx.oxml import OxmlElement
        from docx.text.paragraph import Paragraph
        p_el = OxmlElement("w:p")
        self._body_el.append(p_el)
        para = Paragraph(p_el, self._body)
        if level == 1:
            para.style = "Heading 1"
        elif level == 2:
            para.style = "Heading 2"
        # 归一化换行：\r\n / 裸 \r → \n，再用软换行拆分，避免 XML 里出现裸 \r
        norm = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        parts = norm.split("\n")
        run = para.add_run(parts[0])
        for extra in parts[1:]:
            run.add_break()
            run.add_text(extra)

    def _add(self, text: str, level: int) -> None:
        self._para_texts.append((text, level))
        if self._doc is not None:
            try:
                self._emit_docx(text, level)
            except Exception as e:  # noqa: BLE001
                self._degrade(e)

    # ---- 头部 / 分段 / 正文 ----
    def start(self, meta: dict) -> None:
        m = {k: (clean_text(v)[0] if isinstance(v, str) else v) for k, v in meta.items()}
        self._add(f"会话导出 · {m['name']}", 1)
        for label, key in (("类型", "type_label"), ("chat_id", "chat_id"),
                           ("消息总数", "total"), ("时间范围", "range"),
                           ("导出时间", "exported_at")):
            self._add(f"{label}：{m[key]}", 0)

    def day(self, date_str: str) -> None:
        self._add(clean_text(date_str)[0], 2)

    def line(self, msg: dict, show_qq: bool) -> None:
        text, changed = clean_text(format_line(msg, show_qq))
        if changed:
            self.cleaned += 1
        self._add(text, 0)
        self.count += 1

    def finish(self) -> None:
        if self._doc is not None:
            try:
                if self._sectPr is not None:
                    self._body_el.append(self._sectPr)   # 补回末尾，保证 OOXML 合法
                self._doc.save(self.path)
                return
            except Exception as e:  # noqa: BLE001
                self._degrade(e)
        # ---- 降级：zipfile + 最小 OOXML ----
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _OXML_CONTENT_TYPES)
            z.writestr("_rels/.rels", _OXML_RELS)
            z.writestr("word/document.xml", self._build_document_xml())

    def abort(self) -> None:
        self._doc = None
        self._body = None
        self._body_el = None
        self._para_texts = []

    def _build_document_xml(self) -> str:
        body = []
        for text, level in self._para_texts:
            norm = (text or "").replace("\r\n", "\n").replace("\r", "\n")
            segs = norm.split("\n")
            runs = []
            for i, seg in enumerate(segs):
                if i:
                    runs.append("<w:r><w:br/></w:r>")
                runs.append(f"<w:r><w:t xml:space=\"preserve\">{_xml_escape(seg)}</w:t></w:r>")
            if level == 1:
                ppr = "<w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
            elif level == 2:
                ppr = "<w:pPr><w:pStyle w:val=\"Heading2\"/></w:pPr>"
            else:
                ppr = ""
            body.append(f"<w:p>{ppr}{''.join(runs)}</w:p>")
        body.append("<w:sectPr>"
                    "<w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
                    "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/>"
                    "</w:sectPr>")
        return (_OXML_DOC_HEAD + "".join(body) + "</w:body></w:document>")


_OXML_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')

_OXML_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')

_OXML_DOC_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:body>')


def _xml_escape(s: str) -> str:
    """XML 文本转义 + 去掉非法控制字符（保底，正常路径已在 clean_text 处理）。"""
    s, _ = clean_text(s)
    return _html.escape(s, quote=False)


def open_writer(fmt: str, path: str) -> _BaseWriter:
    """按格式选择写入器。"""
    fmt = (fmt or "").lower().lstrip(".")
    if fmt == "md":
        return _PlainTextWriter(path, markdown=True)
    if fmt in ("txt", "text"):
        return _PlainTextWriter(path, markdown=False)
    if fmt in ("html", "htm"):
        return _HtmlWriter(path)
    if fmt == "docx":
        return _DocxWriter(path)
    raise ValueError(f"不支持的导出格式：{fmt}")


# ---------------------------------------------------------------------------- 主流程
def _friendly_error(e: BaseException) -> str:
    """把异常翻译成可读的中文提示（UI 直接展示）。"""
    name = type(e).__name__
    msg = str(e)
    if isinstance(e, PermissionError):
        return f"没有写入权限，或目标文件正被其它程序占用：{msg}"
    if isinstance(e, FileNotFoundError):
        return f"目标路径不存在或不可访问：{msg}"
    if isinstance(e, OSError):
        return f"文件写入失败（磁盘空间 / 权限 / 路径问题）：{msg}"
    if isinstance(e, UnicodeError):
        return f"文本编码错误：{msg}"
    if isinstance(e, ValueError) and "XML" in msg:
        return f"文档内容包含非法 XML 字符，写入失败：{msg}"
    if isinstance(e, ValueError):
        return f"参数或格式错误：{msg}"
    return f"导出失败（{name}）：{msg}"


def export_chat(store, params: dict, on_log=None, on_progress=None, should_cancel=None) -> dict:
    """把一个会话的全部（或按条件过滤的）聊天记录导出为文档。

    写入采用「临时文件 + 成功后 rename」：先写 ``<out>.part``，成功再 os.replace 成
    目标文件名；失败或取消会删除 ``.part``，绝不留下半截文件。

    :param store: MemoryStore（只读）
    :param params: chat_type / chat_id / chat_name / fmt / out_path / ts_from / ts_to
                   / only_me / show_qq / by_day
    :param on_log: callable(str) 日志回调
    :param on_progress: callable(done, total, elapsed) 进度回调
    :param should_cancel: callable() -> bool，返回 True 时尽快中止
    :return: 结果 dict（ok / out_path / count / elapsed / bytes / degraded / canceled
             / cleaned / error / error_detail）
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    def progress(done: int, total: int, elapsed: float) -> None:
        if on_progress:
            on_progress(done, total, elapsed)

    chat_type = params.get("chat_type") or ""
    chat_id = str(params.get("chat_id") or "")
    chat_name = params.get("chat_name") or chat_id
    fmt = (params.get("fmt") or "txt").lower()
    out_path = os.path.abspath(params.get("out_path") or "")
    ts_from = int(params.get("ts_from") or 0)
    ts_to = int(params.get("ts_to") or 0)
    only_me = bool(params.get("only_me"))
    show_qq = bool(params.get("show_qq"))
    by_day = bool(params.get("by_day"))

    result = {"ok": False, "out_path": out_path, "count": 0, "elapsed": 0.0, "bytes": 0,
              "degraded": False, "canceled": False, "cleaned": 0, "error": "", "error_detail": ""}
    t0 = time.time()

    part_path = out_path + ".part"
    writer = None
    committed = False
    try:
        if not out_path:
            raise ValueError("未指定输出路径")
        # 安全红线：绝不写入记忆库目录
        mem_dir = os.path.abspath(getattr(store, "memory_dir", "") or "")
        if mem_dir and os.path.abspath(out_path).startswith(mem_dir + os.sep):
            raise ValueError("输出路径位于记忆库目录内，已拒绝（只读保护）")
        out_dir = os.path.dirname(out_path) or "."
        os.makedirs(out_dir, exist_ok=True)

        summ = store.chat_summary(chat_type, chat_id, ts_from, ts_to, only_me)
        total = summ["count"]
        log(f"会话「{chat_name}」（{chat_type_label(chat_type)} {chat_id}）"
            f" 待导出 {total:,} 条，过滤条件："
            f"时间={_range_desc(ts_from, ts_to)}，只看我发的={only_me}")
        meta = build_meta(chat_name, chat_type, chat_id, total, summ["ts_min"], summ["ts_max"])

        writer = open_writer(fmt, part_path)
        if isinstance(writer, _DocxWriter) and writer.degraded:
            log("未检测到 python-docx，已降级为内置最小 OOXML 写入（无新依赖）")
            result["degraded"] = True

        writer.start(meta)

        done = 0
        canceled = False
        last_day = None
        last_report = 0.0
        for msg in store.iter_chat_messages(chat_type, chat_id, ts_from, ts_to, only_me, batch=2000):
            if should_cancel and should_cancel():
                canceled = True
                break
            if by_day:
                d = _day_key(msg)
                if d != last_day:
                    writer.day(d)
                    last_day = d
            writer.line(msg, show_qq)
            done += 1
            now = time.time()
            if done == 1 or now - last_report >= 0.25:
                last_report = now
                progress(done, total, now - t0)

        result["count"] = done
        result["cleaned"] = getattr(writer, "cleaned", 0)

        if canceled:
            result["canceled"] = True
            writer.abort()
            log(f"已取消导出：临时文件未提交，已丢弃半成品（本可写 {done:,}/{total:,} 条）。")
        else:
            writer.finish()
            # python-docx 在写入/收尾阶段失败 → 已自动降级重写
            if isinstance(writer, _DocxWriter) and writer.degraded and not result["degraded"]:
                log(f"python-docx 写入失败，已自动退化到内置 OOXML 写入器重新生成"
                    f"（原因：{writer.degrade_reason}）")
                result["degraded"] = True
            os.replace(part_path, out_path)   # 只在全部成功后才提交成品
            committed = True
            result["ok"] = True
            if result["cleaned"]:
                log(f"正文清洗：已清洗 {result['cleaned']:,} 条含 XML 非法字符/孤立代理项的正文"
                    f"（控制字符替换为可见占位 ␔ 等）。")
            else:
                log("正文清洗：本次未发现 XML 非法字符。")
            progress(done, total, time.time() - t0)
    except Exception as e:  # noqa: BLE001
        result["error"] = _friendly_error(e)
        result["error_detail"] = f"{type(e).__name__}: {e}"
        log(f"导出失败：{result['error']}（{result['error_detail']}）")
    finally:
        if not committed:
            if writer is not None:
                try:
                    writer.abort()
                except Exception:  # noqa: BLE001
                    pass
            if os.path.isfile(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass

    result["elapsed"] = round(time.time() - t0, 2)
    try:
        result["bytes"] = os.path.getsize(out_path) if (committed and os.path.isfile(out_path)) else 0
    except OSError:
        result["bytes"] = 0
    return result


def _range_desc(ts_from: int, ts_to: int) -> str:
    if not ts_from and not ts_to:
        return "全部"
    lo = fmt_ts(ts_from) or "不限"
    hi = fmt_ts(ts_to) or "不限"
    return f"{lo} ~ {hi}"


if __name__ == "__main__":
    # 手动冒烟：python core/exporter.py <chat_type> <chat_id> <fmt> <out>
    import sys

    from core.config import Config
    from core.memory import MemoryStore

    if len(sys.argv) < 5:
        print("用法：python core/exporter.py <chat_type> <chat_id> <fmt> <out>")
        raise SystemExit(2)
    _ct, _cid, _fmt, _out = sys.argv[1:5]
    _cfg = Config.load()
    _store = MemoryStore(_cfg.memory_dir)
    _res = export_chat(
        _store,
        {"chat_type": _ct, "chat_id": _cid, "chat_name": _cid, "fmt": _fmt, "out_path": _out,
         "by_day": True, "show_qq": True},
        on_log=lambda s: print("[log]", s),
        on_progress=lambda d, t, e: print(f"[prog] {d}/{t} {e:.1f}s"))
    print("[result]", _res)
