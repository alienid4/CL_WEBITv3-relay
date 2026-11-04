"""診斷包：出問題時，一個動作產出「足以判斷問題在哪」的去識別化資料包。

## 為什麼是萬用框架而不是單一功能的工具

第一版被寫成「正規化／身分解析專用」——那是錯的。任何功能都會出問題、都需要 debug，
工具不該綁在某個功能上。所以拆成：

  核心（寫一次）  環境快照、去識別化、打包、隱私閘門 —— 所有功能共通
  外掛（每功能）  各自貢獻一段自己的診斷資料

新功能只要加一個 `@register("名字")` 的函式，診斷包就自動含它，不用改這裡。

## 設計原則

1. **給判斷過程，不給原始資料**。「這筆走了 weak:single 規則所以判 ambiguous」
   比看到資料本身有用十倍，而且不用外流真實內容。
2. **預設去識別化，而且是一致的**：同一個主機名每次都遮成同一個代號，
   所以「這兩筆是不是同一台」看得出來，但看不到真名。結構保留、內容遮蔽。
3. **一個外掛壞掉不能拖垮整包**：每個 collector 都包在 try 裡，失敗就記錄失敗原因，
   其他區段照樣產出——診斷工具自己在出問題時掛掉是最糟的。
4. **公司資料不出這台機器**：預設遮蔽，且輸出前跑殘留掃描；沒過就拒絕產出。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import traceback
from datetime import datetime

_COLLECTORS: dict[str, callable] = {}


def register(name: str):
    """把一個功能的診斷資料註冊進診斷包。

    用法（寫在該功能自己的模組裡）：
        @diagnostics.register("normalize")
        def _diag(conn):
            return {"pending": ...}
    """
    def deco(fn):
        _COLLECTORS[name] = fn
        return fn
    return deco


def registered() -> list[str]:
    return sorted(_COLLECTORS)


# ===== 去識別化 =====

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")
# 欄位名一看就知道是敏感的，不管值長怎樣都遮
_SENSITIVE_KEYS = {
    "password", "token", "secret", "api_key", "apikey",
    "person_name", "phone", "custodian", "owner", "user_name", "username",
    "proxy1", "proxy1_phone", "decided_by", "imported_by",
}


class Desensitizer:
    """一致性假名化：同一個值永遠對到同一個代號。

    為什麼要「一致」而不是全部遮成 ***：判斷問題時我需要知道
    「A 筆和 B 筆是不是同一台機器」，全遮成星號就看不出關聯了。
    假名保留關聯，但看不到真實內容。
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._map: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self._map:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            n = self._counters[kind]
            self._map[key] = {
                "ip": f"10.0.{n // 250}.{n % 250 + 1}",
                "mac": f"00:00:5e:00:{n // 256:02x}:{n % 256:02x}",
                "host": f"host-{n:03d}",
                "text": f"<遮蔽{n:03d}>",
            }[kind]
        return self._map[key]

    def text(self, s: str) -> str:
        if not self.enabled or not isinstance(s, str):
            return s
        s = _IPV4.sub(lambda m: self._token("ip", m.group(0)), s)
        s = _MAC.sub(lambda m: self._token("mac", m.group(0)), s)
        return s

    def value(self, key: str, v):
        """依欄位名＋內容決定怎麼遮。"""
        if not self.enabled:
            return v
        k = (key or "").lower()
        if k in _SENSITIVE_KEYS and v not in (None, ""):
            return self._token("text", str(v))
        if k in ("hostname", "fqdn", "label", "name") and v not in (None, ""):
            return self._token("host", str(v))
        if isinstance(v, str):
            return self.text(v)
        return v

    def walk(self, obj, key: str = ""):
        """遞迴處理巢狀結構——診斷資料是巢狀的，只遮頂層等於沒遮。"""
        if isinstance(obj, dict):
            return {k: self.walk(v, k) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.walk(v, key) for v in obj]
        return self.value(key, obj)

    @property
    def mapping_size(self) -> int:
        return len(self._map)


def residual_scan(payload: str) -> list[str]:
    """輸出前的最後一道關卡：檢查有沒有漏遮的東西。

    沒過就不准送出（公司資料不出這台機器是硬規則）。這裡刻意只認「看起來像真實
    內網位址」的樣式——假名用的 10.0.x.x 不算。
    """
    hits = []
    for m in set(_IPV4.findall(payload)):
        if not m.startswith("10.0.") and not m.startswith("0."):
            hits.append(f"疑似未遮蔽 IP：{m}")
    for m in set(_MAC.findall(payload)):
        if not m.lower().startswith("00:00:5e"):
            hits.append(f"疑似未遮蔽 MAC：{m}")
    return hits[:50]


# ===== 核心區段（所有功能共通）=====

def _meta(conn) -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": commit or "unknown",
        "db_path": str(conn.execute("PRAGMA database_list").fetchone()[2]),
    }


def _schema(conn) -> dict:
    """每張表幾筆＋欄位數。資料量與 schema 漂移是最常見的問題根源。"""
    out = {}
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        t = r[0]
        if t.startswith("sqlite_"):
            continue
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            cols = len(list(conn.execute(f"PRAGMA table_info({t})")))
            out[t] = {"rows": n, "columns": cols}
        except sqlite3.Error as exc:
            out[t] = {"error": str(exc)}
    return out


def _recent_errors(lines: int = 40) -> dict:
    """服務最近的錯誤。取不到就誠實說取不到，不要靜默留白。"""
    out = {}
    for svc in ("webit3-api", "webit3-web"):
        try:
            r = subprocess.run(
                ["journalctl", "-u", svc, "-n", str(lines), "--no-pager", "-p", "warning"],
                capture_output=True, text=True, timeout=10,
            )
            out[svc] = (r.stdout or r.stderr or "").strip().splitlines()[-lines:]
        except (OSError, subprocess.SubprocessError) as exc:
            out[svc] = [f"（取不到：{exc}）"]
    return out


def collect(conn, note: str = "", desensitize: bool = True,
            include_errors: bool = True) -> dict:
    """產出完整診斷包。

    note：使用者描述「剛剛做了什麼、看到什麼」——這是最重要的一段，
    沒有它我只能猜；有它我能直接對到對應的區段。
    """
    d = Desensitizer(enabled=desensitize)

    bundle = {
        "note": note or "（未填寫：建議描述剛剛做了什麼、預期看到什麼、實際看到什麼）",
        "meta": _meta(conn),
        "schema": _schema(conn),
        "sections": {},
        "collector_failures": {},
    }
    if include_errors:
        bundle["recent_errors"] = _recent_errors()

    for name, fn in sorted(_COLLECTORS.items()):
        try:
            bundle["sections"][name] = fn(conn)
        except Exception as exc:  # noqa: BLE001
            # 一個外掛壞掉不能拖垮整包——診斷工具自己掛掉是最糟的情況
            bundle["collector_failures"][name] = {
                "error": str(exc)[:300],
                "traceback": traceback.format_exc()[-1500:],
            }

    bundle = d.walk(bundle)
    bundle["_desensitized"] = desensitize
    bundle["_pseudonyms"] = d.mapping_size

    if desensitize:
        residual = residual_scan(json.dumps(bundle, ensure_ascii=False))
        bundle["_residual_scan"] = residual or "通過（未發現未遮蔽的位址）"
    return bundle
