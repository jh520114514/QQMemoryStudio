# -*- coding: utf-8 -*-
"""AI 检索助手：OpenAI 兼容接口 + 记忆检索工具调用。

流程：
  用户提问 -> LLM 决定是否调用工具 ``search_memory`` -> 本地执行（关键词/语义检索）
  -> 把命中片段作为 tool 消息回灌 -> LLM 汇总作答 -> 返回答案 + 引用原始消息。

无接口（未配 key）时返回可读提示而不是抛栈；工具执行失败也会把错误作为 tool 内容回灌，
让模型自行说明，避免整段对话崩掉。
"""
from __future__ import annotations

import json
import time
from typing import Callable

from .semantic import EmbedError

SYSTEM_PROMPT = (
    "你是 QQ 聊天记录回忆助手。回答用户关于过去聊天的问题时，必须优先调用工具 search_memory "
    "去检索本地记忆库，严禁凭空编造。找到证据后，用简洁中文回答，并在句尾用 [1][2] 标注引用编号。"
    "若检索不到，就明确说明“记忆库中没有找到相关记录”。"
)

TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": "在本地 QQ 聊天记忆库中检索历史消息。返回若干条最相关的原始消息片段。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索用的关键词或自然语言问题"},
                "mode": {"type": "string", "enum": ["semantic", "keyword"],
                         "description": "semantic=语义检索(默认)，keyword=关键词精确检索"},
                "k": {"type": "integer", "description": "返回条数，默认 8"},
                "chat": {"type": "string", "description": "限定会话名/群号（可选）"},
            },
            "required": ["query"],
        },
    },
}


class AIError(RuntimeError):
    pass


def _truncate(s: str, n: int = 120) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n] + "…"


class AIClient:
    def __init__(self, ai_cfg: dict, memory, semantic, mode_default: str = "semantic"):
        self.cfg = ai_cfg or {}
        self.memory = memory
        self.semantic = semantic
        self.mode_default = mode_default

    # ------------------------------------------------------------ 可用性
    def available(self) -> tuple[bool, str]:
        if not self.cfg.get("base_url"):
            return False, "未配置 AI base_url"
        if not self.cfg.get("api_key"):
            return False, "未配置 AI API Key（可在主窗口“基础设置”或本窗口顶部临时填写）"
        if not self.cfg.get("model"):
            return False, "未配置 AI model"
        return True, ""

    def _endpoint(self) -> str:
        return str(self.cfg.get("base_url", "")).rstrip("/") + "/chat/completions"

    # ------------------------------------------------------------ 原始调用
    def chat_once(self, messages: list[dict], tools: list | None = None,
                  temperature: float = 0.3) -> dict:
        import requests

        ok, why = self.available()
        if not ok:
            raise AIError(why)
        payload = {"model": self.cfg.get("model"), "messages": messages,
                   "temperature": temperature, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.cfg.get('api_key')}",
                   "Content-Type": "application/json"}
        try:
            r = requests.post(self._endpoint(), json=payload, headers=headers, timeout=120)
        except Exception as e:  # noqa: BLE001
            raise AIError(f"网络错误：{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise AIError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]
        except Exception as e:  # noqa: BLE001
            raise AIError(f"响应解析失败：{e}") from e

    # ------------------------------------------------------------ 工具执行
    def _run_tool(self, name: str, args: dict) -> tuple[str, list[dict]]:
        """执行工具，返回 (给模型的文本, 引用列表)。"""
        if name != "search_memory":
            return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False), []
        query = str(args.get("query") or "").strip()
        mode = args.get("mode") or self.mode_default
        try:
            k = int(args.get("k") or 8)
        except (TypeError, ValueError):
            k = 8
        chat = str(args.get("chat") or "")
        if not query:
            return json.dumps({"error": "query 为空"}, ensure_ascii=False), []
        hits: list[dict] = []
        note = ""
        if mode == "keyword":
            hits, _ = self.memory.keyword_search(query, k=k, chat=chat)
        else:
            ok, why = self.semantic.available()
            if ok:
                hits, _ = self.semantic.search(query, k=k, chat=chat)
            else:  # 语义不可用则退回关键词，并告知模型
                note = f"（语义检索不可用：{why}；已改用关键词检索）"
                hits, _ = self.memory.keyword_search(query, k=k, chat=chat)
        compact = [{"i": i + 1, "time": h["time"], "chat": h["chat"], "sender": h["sender"],
                    "text": _truncate(h["text"], 200), "score": h.get("score")} for i, h in enumerate(hits)]
        return json.dumps({"note": note, "count": len(compact), "results": compact},
                          ensure_ascii=False), hits

    # ------------------------------------------------------------ 对话主循环
    def ask(self, question: str, history: list[dict] | None = None, k: int = 8,
            max_steps: int = 4,
            on_event: Callable[[str, object], None] | None = None) -> dict:
        """返回 {answer, citations, error, steps}。history 为先前 [{'role','content'}]。"""
        on_event = on_event or (lambda kind, payload: None)
        ok, why = self.available()
        if not ok:
            return {"answer": "", "citations": [], "error": why, "steps": []}
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in (history or []):
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": question})

        citations: list[dict] = []
        seen: set = set()
        steps: list[str] = []
        for step in range(max_steps):
            on_event("status", f"第 {step + 1} 步：请求模型…")
            try:
                msg = self.chat_once(messages, tools=[TOOL_SPEC])
            except AIError as e:
                return {"answer": "", "citations": citations, "error": str(e), "steps": steps}
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return {"answer": msg.get("content") or "(空回复)", "citations": citations,
                        "error": "", "steps": steps}
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": tool_calls})
            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                steps.append(f"调用 {name}({_truncate(json.dumps(args, ensure_ascii=False), 80)})")
                on_event("status", f"执行工具 {name}：{args.get('query', '')}")
                try:
                    content, hits = self._run_tool(name, args)
                except Exception as e:  # noqa: BLE001  记忆库缺失等异常不应打断对话
                    content, hits = json.dumps(
                        {"error": f"检索失败：{e}"}, ensure_ascii=False), []
                for h in hits:
                    key = h.get("id")
                    if key not in seen:
                        seen.add(key)
                        h = dict(h)
                        h["cite"] = len(citations) + 1
                        citations.append(h)
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": content})
        return {"answer": "（达到最大工具调用步数，未得到最终回答）", "citations": citations,
                "error": "", "steps": steps}


if __name__ == "__main__":
    from . import config as C
    from .memory import MemoryStore
    from .semantic import SemanticIndex

    cfg = C.Config.load()
    ms = MemoryStore(cfg.memory_dir)
    si = SemanticIndex(cfg.memory_dir, cfg.get("embedding"))
    ai = AIClient(cfg.get("ai"), ms, si)
    print("available:", ai.available())
    print(ai.ask("我最近聊到过什么开心的事？"))
