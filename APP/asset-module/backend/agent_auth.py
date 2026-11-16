"""Push agent 用的機器對機器 key 驗證。

跟 auth.py 的 session token 是完全不同的東西：session 是給人登入用的（cookie），
這裡是給每台主機的 push agent 用的（HTTP header）。不共用 users/sessions 表，
獨立一張 host_api_key，key 只存 SHA-256 hash——明文只在種 agent 當下
（issue_host_key()）出現一次，之後 DB 裡再也查不到明文，只能撤銷重發。

跟 session 的可撤銷精神一致：撤銷＝把 revoked_at 填上去，不用另外搞黑名單。

這裡只放純邏輯（不依賴 FastAPI），比照 auth.py 的分工——實際掛在路由上的
`require_host_key` FastAPI dependency（需要 Depends(get_db)）定義在 api.py，
避免 api.py 跟這個模組互相匯入。
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def issue_host_key(conn: sqlite3.Connection, asset_serial: str) -> str:
    """種 agent 當下呼叫一次，回傳明文 key（只有這裡看得到明文，DB 只存 hash）。

    同一台資產重複呼叫＝重發新 key、舊 key 失效（用 UNIQUE(asset_serial) + upsert，
    不會讓一台資產同時有兩把有效 key 而搞不清楚哪把才是現在用的）。
    """
    key = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO host_api_key (asset_serial, key_hash, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(asset_serial) DO UPDATE SET "
        "key_hash = excluded.key_hash, created_at = excluded.created_at, "
        "revoked_at = NULL, last_seen_at = NULL",
        (asset_serial, _hash_key(key), _now_iso()),
    )
    conn.commit()
    return key


def revoke_host_key(conn: sqlite3.Connection, asset_serial: str) -> None:
    conn.execute(
        "UPDATE host_api_key SET revoked_at = ? WHERE asset_serial = ?",
        (_now_iso(), asset_serial),
    )
    conn.commit()


def resolve_host_key(conn: sqlite3.Connection, key: str | None) -> str | None:
    """依明文 key 查有效（未撤銷）的 asset_serial，順便更新心跳 last_seen_at。
    key 缺失/查無/已撤銷都回傳 None——由呼叫端（api.py 的 require_host_key）轉成 401。
    """
    if not key:
        return None
    key_hash = _hash_key(key)
    row = conn.execute(
        "SELECT asset_serial FROM host_api_key WHERE key_hash = ? AND revoked_at IS NULL",
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE host_api_key SET last_seen_at = ? WHERE key_hash = ?",
        (_now_iso(), key_hash),
    )
    conn.commit()
    return row["asset_serial"]
