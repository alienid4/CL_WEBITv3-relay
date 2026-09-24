"""收集紀錄／分析檔：每一次收集、自我檢查、修復，都把**當下的完整環境**留下來（2026-09-15）。

## 為什麼要有

公司機 .14 的 SAN 收集「paramiko 沒有 SSHClient」前後修了五輪都沒修好：
每一輪都是「我猜一個原因 → 出一包 patch → 使用者套 → 截圖 → 再猜」。
而且出現過自我檢查說好、收集卻失敗這種互相矛盾的狀況——只看畫面上一行訊息，
根本分不出是哪個行程、載到哪一個 paramiko、當下的工作目錄是不是已經被 patch 換掉。

使用者 2026-09-15：「根本沒用，修復至少 5 次。做個收集 LOG 或分析的。」

所以改成**失敗當下就把證據存起來**：一次收集失敗，紀錄裡就有
行程 PID、python 路徑、工作目錄、sys.path、paramiko 實際載自哪裡（`__file__`／`__path__`／
`find_spec`）、例外與 traceback。畫面上點開就看得到，也能下載成一個文字檔整份給開發者。

## 不放什麼

**密碼、帳號以外的憑證一律不進紀錄。** 呼叫端只傳 IP、訊息、例外；traceback 只有程式碼行，
不含區域變數的值。匯出檔會被人轉寄，這條要守住。

## 記錄本身絕不能讓收集失敗

寫紀錄的任何錯誤都吞掉（`record` 不丟例外）——紀錄是為了查問題，不能變成新的問題。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS collect_attempt_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,     -- san_collect / selfcheck / selfheal
    target      TEXT,              -- IP（沒有對象的動作留空）
    ok          INTEGER,
    message     TEXT,
    actor       TEXT,
    detail_json TEXT               -- env_snapshot() 的結果＋呼叫端補充
)
"""

#: 只留最近這麼多筆，避免無限長大
MAX_ROWS = 500

KIND_LABEL = {
    "san_collect": "SAN 收集",
    "selfcheck": "自我檢查",
    "selfheal": "一鍵修復",
    "dispatch": "開始收集",
    "ssh_probe": "SSH 測連線",
    "san_import": "SAN 離線匯入",
    "alive_check": "偵測存活",
    "post_onboard": "納管後自動收集",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 取不到就寫出為什麼取不到，不要讓整份快照失敗
        return f"(取不到：{type(exc).__name__}: {exc})"


def _app_version() -> str | None:
    p = Path(__file__).with_name("version.json")
    return json.loads(p.read_text(encoding="utf-8")).get("version")


def paramiko_state() -> dict:
    """paramiko **在這個行程裡**實際是什麼：已載入的模組物件＋重新找一次會找到哪裡。

    `__path__` 是關鍵：namespace 空目錄沒有 `__file__`，但 `__path__` 會列出它是哪個目錄。
    """
    st: dict = {}
    mod = sys.modules.get("paramiko")
    st["loaded"] = mod is not None
    if mod is not None:
        st["file"] = getattr(mod, "__file__", None)
        st["path"] = [str(x) for x in (getattr(mod, "__path__", None) or [])]
        st["has_SSHClient"] = hasattr(mod, "SSHClient")
        st["version"] = getattr(mod, "__version__", None)
    try:
        spec = importlib.util.find_spec("paramiko")
        st["find_spec_origin"] = getattr(spec, "origin", None) if spec else None
        st["find_spec_locations"] = (list(spec.submodule_search_locations or [])
                                     if spec else None)
    except Exception as exc:  # noqa: BLE001
        st["find_spec_error"] = f"{type(exc).__name__}: {exc}"
    return st


def env_snapshot(exc: BaseException | None = None) -> dict:
    """當下的收集端環境。**不含任何憑證。**"""
    snap = {
        "pid": os.getpid(),
        "python": sys.executable,
        "cwd": _safe(os.getcwd),
        "sys_path": list(sys.path)[:15],
        "paramiko": _safe(paramiko_state),
        "app_version": _safe(_app_version),
    }
    if exc is not None:
        snap["exception"] = f"{type(exc).__name__}: {exc}"
        snap["traceback"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
    return snap


def _ensure(conn) -> None:
    conn.execute(TABLE_SQL)


def record(conn, kind: str, target: str | None, ok: bool, message: str,
           actor: str | None = None, exc: BaseException | None = None,
           extra: dict | None = None) -> None:
    """寫一筆紀錄。**永不丟例外**（紀錄失敗不可以讓收集失敗）。"""
    try:
        detail = env_snapshot(exc)
        if extra:
            detail.update(extra)
        _ensure(conn)
        conn.execute(
            "INSERT INTO collect_attempt_log (at, kind, target, ok, message, actor, detail_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (_now(), kind, target, 1 if ok else 0, (message or "")[:2000], actor,
             json.dumps(detail, ensure_ascii=False, default=str)))
        conn.execute(
            "DELETE FROM collect_attempt_log WHERE id NOT IN "
            "(SELECT id FROM collect_attempt_log ORDER BY id DESC LIMIT ?)", (MAX_ROWS,))
        conn.commit()
    except Exception as log_exc:  # noqa: BLE001
        print(f"[collect_log] 寫紀錄失敗（不影響收集）：{type(log_exc).__name__}: {log_exc}")


def recent(conn, limit: int = 50, kinds: list[str] | None = None) -> list[dict]:
    _ensure(conn)
    where, params = "", []
    if kinds:
        where = "WHERE kind IN (" + ",".join("?" for _ in kinds) + ") "
        params = list(kinds)
    cur = conn.execute(
        "SELECT id, at, kind, target, ok, message, actor, detail_json "
        f"FROM collect_attempt_log {where}ORDER BY id DESC LIMIT ?", (*params, limit))
    cols = [d[0] for d in cur.description]
    out = []
    for tup in cur.fetchall():
        r = dict(zip(cols, tup))
        try:
            r["detail"] = json.loads(r.pop("detail_json") or "{}")
        except ValueError:
            r["detail"] = {}
        r["kind_label"] = KIND_LABEL.get(r["kind"], r["kind"])
        out.append(r)
    return out


def export_text(conn, limit: int = 200) -> str:
    """一份可以整份傳給開發者的文字分析檔：現在的環境＋最近的紀錄（新到舊）。"""
    lines = [
        "WebIT3 收集分析檔",
        f"產生時間：{_now()}",
        "（不含密碼；只有環境資訊、錯誤訊息與 traceback）",
        "",
        "===== 現在的收集端環境 =====",
        json.dumps(env_snapshot(), ensure_ascii=False, indent=2, default=str),
        "",
        f"===== 最近 {limit} 筆紀錄（新到舊）=====",
    ]
    for r in recent(conn, limit):
        lines += [
            "",
            f"--- #{r['id']} {r['at']}　{r['kind_label']}　{r['target'] or ''}　"
            f"{'成功' if r['ok'] else '失敗'}　{r['actor'] or ''}",
            f"訊息：{r['message']}",
            json.dumps(r["detail"], ensure_ascii=False, indent=2, default=str),
        ]
    return "\n".join(lines) + "\n"
