"""CMDB Gateway 整合：公司 CMDB 是資產/人員的權威來源（已跟 CIA 同步）。

連線資訊（URL + Bearer token）存 connections 表（connection_type='CMDB Gateway'），
IP 或 token 更換時只改「系統設定→連線設定」一處，不動程式——符合「API 跟帳密放一起、好維護」。

讀：GET  /api/gateway/assets[?group=hardware|software|data|person]
寫：POST /api/gateway/import-assets   body {"data":{"items":[{...}]}}

家裡連不到公司網（10.99.x），實際拉取要在能到公司網的機器跑；這裡的 parsing/mapping 可用假資料測。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

GATEWAY_TYPE = "CMDB Gateway"
GROUPS = ("hardware", "software", "data", "person")


def get_gateway_conn(conn):
    """從 connections 找 CMDB Gateway 那筆（type=CMDB Gateway）。回 row 或 None。"""
    for c in conn.execute("SELECT * FROM connections WHERE connection_type = ? ORDER BY id LIMIT 1", (GATEWAY_TYPE,)):
        return c
    return None


def _endpoint(conn):
    gw = get_gateway_conn(conn)
    if not gw:
        raise ConnectionError("尚未設定 CMDB Gateway 連線（系統設定→連線設定，類型填 CMDB Gateway）")
    base = (gw["target"] or "").strip().rstrip("/")
    token = gw["password"]
    if not base or not token:
        raise ConnectionError("CMDB Gateway 連線缺 URL 或 token")
    return base, token


def extract_items(payload) -> list:
    """回應結構未定（會陸續增加），容錯地取出 items 陣列。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "assets", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = v.get("items")
                if isinstance(inner, list):
                    return inner
    return []


def fetch_group(conn, group: str | None = None, timeout: int = 15) -> list:
    """GET 資產（可指定 group）。連不到/認證失敗要 raise ConnectionError，不吞。"""
    base, token = _endpoint(conn)
    url = f"{base}/api/gateway/assets" + (f"?group={group}" if group else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"CMDB Gateway 回 HTTP {e.code}（token 或權限問題？）") from e
    except Exception as e:  # noqa: BLE001 - 連線層錯誤型態多，統一轉 ConnectionError
        raise ConnectionError(f"連不到 CMDB Gateway：{e}") from e
    return extract_items(payload)


def seen_fields(items: list, sample: int = 30) -> list:
    """回應裡出現過的欄位名（給前端/開發看 schema，方便對應到我們的欄位）。"""
    fields: set[str] = set()
    for it in items[:sample]:
        if isinstance(it, dict):
            fields.update(it.keys())
    return sorted(fields)
