"""Teams／信件通知的共同格式（2.060.x，使用者 11-05「看不懂重點」→ 交接摘要 2026-11-04_NB 的新格式）。

一則通知長這樣（純文字看）：
    第 1 行：燈號 emoji＋級別｜什麼事：幾台          ← 信件主旨也用這一行（信件內文不再重複）
    一台一行，最多 5 行
    另有 N 台                                       ← 超過 5 台才有
    開啟系統：<連結>                                 ← 卡片是按鈕，信件是一行連結
不放事件ID、不放資料來源說明、不重複標題（Power Automate 流程貼的是 主旨＋內文）。
"""
from __future__ import annotations

MAX_LINES = 5
EMOJI = {"critical": "🔴", "warning": "🟠", "info": "📊", "ok": "🟢", "test": "🧪"}


def head(emoji: str, level: str, what: str, n: int | None = None, unit: str = "台") -> str:
    """第一行：『🔴 緊急｜磁碟使用率 ≥90%：3 台』。n 為 None 時不寫台數。"""
    return f"{emoji} {level}｜{what}" + (f"：{n} {unit}" if n is not None else "")


def cap(lines: list[str], unit: str = "台", limit: int = MAX_LINES) -> list[str]:
    """最多 limit 行；多的寫成一行『另有 N 台』。"""
    lines = list(lines)
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"另有 {len(lines) - limit} {unit}"]


def _clip_head(first: str, limit: int = 240) -> str:
    """第一行太長時截中間、保留結尾（「：N 台」是重點）；先截原文再跳脫，不會把跳脫序列切一半（2.060.16 審查 P2）。"""
    import teams_notify as tn
    t = first or ""
    if len(t) > limit:
        tail = t[t.rfind("："):] if "：" in t[-20:] else t[-20:]
        t = t[: max(0, limit - len(tail) - 1)] + "…" + tail
    return tn.md_escape(t)


def card(first: str, lines: list[str], link: str | None = None, color: str = "Warning") -> dict:
    """Adaptive Card：第一個 TextBlock＝第一行（粗體），其餘每行一個 TextBlock；有連結就加「開啟系統」按鈕。"""
    import teams_notify as tn
    body = [{"type": "TextBlock", "text": _clip_head(first), "weight": "Bolder", "size": "Medium", "color": color, "wrap": True}]
    body += [{"type": "TextBlock", "text": tn.md_escape(x)[:600], "wrap": True, "spacing": "None"} for x in lines]
    content: dict = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": body}
    if link:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "開啟系統", "url": link}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": content}]}


def plain(first: str, lines: list[str], link: str | None = None) -> str:
    """同一則通知的純文字樣子（測試與回報用；信件內文＝去掉第一行）。"""
    return "\n".join([first, *lines] + ([f"開啟系統：{link}"] if link else []))


def link_of(conn, path: str) -> str | None:
    """用 Teams 通知設定裡驗證過的網址組連結；讀不到設定就不放連結。"""
    import teams_notify as tn
    try:
        return tn._link(tn.load_settings(conn), path)
    except tn.SettingsCorrupt:
        return None
