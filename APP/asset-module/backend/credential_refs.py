"""收集憑證的「被誰引用」與「最近使用時間」（9-3 收集憑證頁、刪除前檢查用）。D5 認證與憑證危險區。

只回 metadata（名稱、去向連結、時間），**永不含帳號密碼或密文**。

引用來源（各整合頁只存憑證「名稱」，不存帳密）：
* WhatsUp Gold／Aria Operations：app_settings 裡人挑的那一筆；沒挑但已設網址、靠「舊固定名稱／同類型唯一一筆」自動選到的也算
* 儲存設備：storage_devices.credential
* 主機指定：host_credential_pref（納管時人選的「這台用哪一組」）
SAN／HMC 收集頁是「當下挑、不留存」，沒有持久引用，所以不在這裡。
"""
from __future__ import annotations

import sqlite3

import credential_store
from db import get_setting

# 連結都是前端既有頁面（設定頁的外部整合子頁、主機查詢）
LINK_WHATSUP = "/v2/settings?st=integrations&sub=whatsup"
LINK_VROPS = "/v2/settings?st=integrations&sub=vrops"
LINK_STORAGE = "/v2/settings?st=integrations&sub=storage"
MAX_HOST_REFS = 20           # 主機指定可能上千台：明細只列前 20 台，總數另給 ref_count


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _integration_ref(conn, *, kind: str, label: str, link: str, k_cred: str, k_url: str, cred_name: str) -> tuple[str, dict] | None:
    """某個整合（WhatsUp／vROps）目前實際用的憑證名稱；沒設網址也沒挑＝整合沒在用，不算引用。"""
    chosen = (get_setting(conn, k_cred, "") or "").strip()
    url = (get_setting(conn, k_url, "") or "").strip()
    if not chosen and not url:
        return None
    eff = credential_store.resolve_for_kind(conn, kind, chosen or None, cred_name)
    if not eff:
        return None
    return eff, {"type": kind, "label": label, "link": link}


def refs_by_name(conn: sqlite3.Connection) -> dict[str, dict]:
    """{憑證名稱: {"refs": [{type,label,link}], "ref_count": 總引用數}}。沒被引用的名稱不在結果裡。"""
    import vrops_import
    import whatsup_service

    out: dict[str, dict] = {}

    def add(name: str, ref: dict, n: int = 1) -> None:
        slot = out.setdefault(name, {"refs": [], "ref_count": 0, "block_count": 0})
        slot["ref_count"] += n
        slot["block_count"] += n          # 整合／設備的引用會擋刪除；主機指定（host_credential_pref）刪憑證時一併清掉，不擋
        slot["refs"].append(ref)

    hit = _integration_ref(conn, kind="whatsup", label="外部整合：WhatsUp Gold", link=LINK_WHATSUP,
                           k_cred=whatsup_service.K_CRED, k_url=whatsup_service.K_URL, cred_name=whatsup_service.CRED_NAME)
    if hit:
        add(hit[0], hit[1])
    hit = _integration_ref(conn, kind="vrops", label="外部整合：Aria Operations", link=LINK_VROPS,
                           k_cred=vrops_import.K_CRED, k_url=vrops_import.K_URL, cred_name=vrops_import.CRED_NAME)
    if hit:
        add(hit[0], hit[1])

    if _table_exists(conn, "storage_devices"):
        for r in conn.execute("SELECT name, credential FROM storage_devices WHERE credential <> '' ORDER BY name"):
            add(r["credential"], {"type": "storage", "label": f"儲存設備：{r['name']}", "link": LINK_STORAGE})

    if credential_store._pref_table_exists(conn):
        for r in conn.execute("SELECT name, COUNT(*) AS n FROM host_credential_pref GROUP BY name"):
            ips = [x["ip"] for x in conn.execute(
                "SELECT ip FROM host_credential_pref WHERE name = ? ORDER BY ip LIMIT ?", (r["name"], MAX_HOST_REFS))]
            for ip in ips:
                out.setdefault(r["name"], {"refs": [], "ref_count": 0, "block_count": 0})["refs"].append(
                    {"type": "host", "label": f"主機 {ip}（納管時指定）", "link": f"/v2/hosts?q={ip}"})
            out.setdefault(r["name"], {"refs": [], "ref_count": 0, "block_count": 0})["ref_count"] += r["n"]
    return out


def refs_of(conn: sqlite3.Connection, name: str) -> dict:
    return refs_by_name(conn).get(name, {"refs": [], "ref_count": 0, "block_count": 0})


def last_used_by_name(conn: sqlite3.Connection) -> dict[str, dict]:
    """每組憑證最近一次被使用（credential_use_audit）：{名稱: {"at": 時間, "ok": 0/1}}。"""
    rows = conn.execute(
        "SELECT a.credential_name AS n, a.created_at AS at, a.ok AS ok FROM credential_use_audit a "
        "WHERE a.id = (SELECT MAX(id) FROM credential_use_audit b WHERE b.credential_name = a.credential_name)")
    return {r["n"]: {"at": r["at"], "ok": r["ok"]} for r in rows}


def list_with_usage(conn: sqlite3.Connection) -> list[dict]:
    """list_public ＋ 被誰引用 ＋ 最近使用。仍然永不含密碼／密文。"""
    refs = refs_by_name(conn)
    used = last_used_by_name(conn)
    out = []
    for c in credential_store.list_public(conn):
        r = refs.get(c["name"], {"refs": [], "ref_count": 0})
        u = used.get(c["name"])
        out.append({**c, "refs": r["refs"], "ref_count": r["ref_count"],
                    "last_used_at": u["at"] if u else None, "last_used_ok": (bool(u["ok"]) if u else None)})
    return out
