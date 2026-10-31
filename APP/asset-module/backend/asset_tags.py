"""機器標籤（2026-09-21 使用者：「我覺得用 tag，而且可以變更」）。

起因：一台機器在 CIA 上有 5 筆登記（期貨中台 DB、CMail DB、經紀週邊支援 DB…、跨 3 個 AP ID），
使用者問「這可以看得出來為什麼放一起嗎? 共用 DB?」——**系統目前沒有任何欄位在記「為什麼放一起」**。
那是人才知道的事，所以給機器掛標籤，人可以隨時改。

三條規矩：
1. **標籤掛在「台」上**（正典 system_stats.machine_key），不是掛在某一筆登記——
   問題本來就是「這台為什麼有 5 筆」，掛在其中一筆沒有意義。
2. **人說的跟系統猜的分開**。系統只能「建議」（suggest()，附依據），採不採用是人決定；
   採用後就是人工標籤，來源記成 `manual`。畫面要看得出差別，不可以混成一種顏色。
3. **每個標籤都要能回答「誰貼的、什麼時候、為什麼」**——備註欄不是裝飾，是稽核時唯一講得出口的東西。

標籤不覆蓋任何事實：它不改資產狀態、不影響納管漏斗的判定，純粹是給人看的註記與分類。
"""
from __future__ import annotations

import datetime
import re

import system_stats
from lifecycle import RETIRED_STATUS

# 系統會建議的標籤（人也可以自己打別的）
SHARED_DB = "共用DB主機"
SHARED_AP = "共用主機"
SUGGESTABLE = (SHARED_DB, SHARED_AP)

_DDL = """
CREATE TABLE IF NOT EXISTS asset_tag (
    machine_key TEXT NOT NULL,        -- 正典 machine_key（同一台只有一把尺）
    tag         TEXT NOT NULL,
    note        TEXT,                 -- 為什麼貼這個標籤
    source      TEXT DEFAULT 'manual',-- manual＝人貼的；suggested＝採用系統建議
    created_by  TEXT,
    created_at  TEXT,
    PRIMARY KEY (machine_key, tag)
)
"""

# 用 \b 會漏：Python 的 \w 含中文，「經紀週邊支援DB」在「援」和 D 之間沒有邊界。
# 改成前後不是英文字母（中文字也算「不是英文字母」），這樣 dbserver／sdb 不會誤中。
_DB_WORD = re.compile(r"(?<![a-z])(db|database)(?![a-z])|資料庫|ＤＢ", re.I)


def _ensure(conn) -> None:
    conn.execute(_DDL)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def machine_of(conn, asset_serial: str) -> str | None:
    """這個資產序號屬於哪一台（machine_key）。查無此序號回 None。"""
    r = conn.execute("SELECT hostname, ip, asset_serial FROM hardware WHERE asset_serial = ?",
                     (asset_serial,)).fetchone()
    if r is None:
        return None
    return system_stats.machine_key(r[0], r[1], r[2])


def rows_of_machine(conn, asset_serial: str) -> list[dict]:
    """同一台的所有登記（含用途、AP ID）——建議與畫面都吃這份。"""
    mk = machine_of(conn, asset_serial)
    if mk is None:
        return []
    out = []
    for r in conn.execute(
            "SELECT asset_serial, hostname, ip, asset_name, asset_purpose, api_id, asset_status FROM hardware"):
        if system_stats.machine_key(r[1], r[2], r[0]) == mk:
            out.append({"asset_serial": r[0], "hostname": r[1], "ip": r[2], "asset_name": r[3],
                        "asset_purpose": r[4], "api_id": r[5], "asset_status": r[6]})
    return out


def _keys_of_machine(conn, asset_serial: str) -> list[str]:
    """這台的標籤可能掛在哪些 key：目前口徑的 key＋這台每筆登記在兩種口徑下的 key。

    2026-10-17（審查 #3／#9）：台數口徑可以只靠系統設定 machine_key_mode 來回切（不改資料），
    標籤存的是 key 字串，所以讀與刪都要看兩種口徑的 key，切來切去標籤都不會孤兒。
    """
    import machine_identity

    mk = machine_of(conn, asset_serial)
    if mk is None:
        return []
    keys = {mk}
    for r in rows_of_machine(conn, asset_serial):
        keys.add(system_stats.legacy_machine_key(r["hostname"], r["ip"], r["asset_serial"]))
        nk = machine_identity.norm_name(r["hostname"])
        if nk and not machine_identity.is_generic(nk):
            keys.add("h:" + nk)
    return sorted(keys)


def list_tags(conn, asset_serial: str) -> list[dict]:
    keys = _keys_of_machine(conn, asset_serial)
    if not keys:
        return []
    try:
        _ensure(conn)
        ph = ",".join("?" for _ in keys)
        seen, out = set(), []
        for r in conn.execute(
                "SELECT tag, note, source, created_by, created_at FROM asset_tag "
                f"WHERE machine_key IN ({ph}) ORDER BY created_at, tag", keys):
            if r["tag"] not in seen:
                seen.add(r["tag"])
                out.append(dict(r))
        return out
    except Exception:  # noqa: BLE001 - 唯讀庫：當作沒有標籤，不擋整頁
        return []


def add_tag(conn, asset_serial: str, tag: str, note: str = "", by: str | None = None,
            source: str = "manual") -> dict:
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("標籤不可以是空的")
    if len(tag) > 20:
        raise ValueError("標籤最多 20 個字（太長就寫進備註）")
    mk = machine_of(conn, asset_serial)
    if mk is None:
        raise ValueError(f"查無資產序號 {asset_serial}")
    _ensure(conn)
    now = _now()
    conn.execute(
        "INSERT INTO asset_tag (machine_key, tag, note, source, created_by, created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(machine_key, tag) DO UPDATE SET "
        "note = excluded.note, source = excluded.source, created_by = excluded.created_by, "
        "created_at = excluded.created_at",
        (mk, tag, (note or "").strip(), source, by, now))
    conn.commit()
    return {"machine_key": mk, "tag": tag, "note": (note or "").strip(),
            "source": source, "created_by": by, "created_at": now}


def remove_tag(conn, asset_serial: str, tag: str) -> dict:
    mk = machine_of(conn, asset_serial)
    if mk is None:
        raise ValueError(f"查無資產序號 {asset_serial}")
    _ensure(conn)
    keys = _keys_of_machine(conn, asset_serial)
    ph = ",".join("?" for _ in keys)
    cur = conn.execute(f"DELETE FROM asset_tag WHERE machine_key IN ({ph}) AND tag = ?", (*keys, tag))
    conn.commit()
    return {"removed": cur.rowcount}


def all_tags(conn) -> list[dict]:
    """全站有哪些標籤、各幾台——給篩選與自動完成用。"""
    try:
        _ensure(conn)
        return [{"tag": r[0], "machines": r[1]} for r in conn.execute(
            "SELECT tag, COUNT(*) FROM asset_tag GROUP BY tag ORDER BY COUNT(*) DESC, tag")]
    except Exception:  # noqa: BLE001
        return []


def suggest(conn, asset_serial: str) -> list[dict]:
    """系統建議的標籤，**一定附依據**。人沒採用之前，它只是建議，不會存進資料。

    現在只有一條規則（使用者 2026-09-21 問「共用 DB?」那個情境）：
    同一台有兩筆以上登記、用途都提到 DB／資料庫 → 疑似共用資料庫主機。
    ⚠️ 這只是推論：用途是人填的字串，掃描與服務盤點才是證據（下一步寫在 next 裡）。
    """
    rows = [r for r in rows_of_machine(conn, asset_serial)
            if (r["asset_status"] or "").strip() not in RETIRED_STATUS]
    if len(rows) < 2:
        return []
    have = {t["tag"] for t in list_tags(conn, asset_serial)}
    out = []
    dbish = [r for r in rows if _DB_WORD.search(str(r["asset_purpose"] or "") + " " + str(r["asset_name"] or ""))]
    apis = sorted({(r["api_id"] or "").strip() for r in rows if (r["api_id"] or "").strip()})
    if len(dbish) >= 2 and len(dbish) == len(rows) and SHARED_DB not in have:
        out.append({
            "tag": SHARED_DB,
            "why": f"這台 {len(rows)} 筆登記的用途都提到 DB／資料庫"
                   + (f"，橫跨 {len(apis)} 個 AP ID（{'、'.join(apis)}）" if len(apis) > 1 else ""),
            "confidence": "推論",
            "next": "到 3-9 服務盤點看這台在聽哪些埠：只有一個資料庫埠＝一套 DB 多系統共用；"
                    "好幾個實例埠＝一台機器擺多套。收不到服務資料就只能停在推論",
        })
    # 已經貼了（或剛建議了）「共用DB主機」就不要再建議「共用主機」——
    # 後者是前者的上位概念，兩個一起出現只會讓人以為系統在亂猜
    elif (len(dbish) >= 2 and len(dbish) == len(rows)) or SHARED_DB in have:
        pass
    elif len(rows) >= 2 and len(apis) > 1 and SHARED_AP not in have:
        out.append({
            "tag": SHARED_AP,
            "why": f"這台 {len(rows)} 筆登記橫跨 {len(apis)} 個 AP ID（{'、'.join(apis)}）",
            "confidence": "推論",
            "next": "確認是真的多個系統共用一台，還是 CIA 上重複登記（重複登記請在資產查詢處理）",
        })
    return out


# ===== 批次（v1.468.6，2026-10-19 使用者：「我納管失敗的，可同時幫它們上備註標籤嗎」）=====
# 同一份標籤（asset_tag，掛在台上），不另開第二個存放處。2-1 表格一次要幾百列的標籤，
# 不可以每列各掃一次 hardware（rows_of_machine 會全表掃），所以這裡整批算 key、整批查。

def _row_keys(hostname, ip, serial) -> set[str]:
    """一筆登記可能的標籤 key（目前口徑＋舊口徑＋主機名口徑），同 _keys_of_machine 的意思。"""
    import machine_identity

    keys = {system_stats.machine_key(hostname, ip, serial),
            system_stats.legacy_machine_key(hostname, ip, serial)}
    nk = machine_identity.norm_name(hostname)
    if nk and not machine_identity.is_generic(nk):
        keys.add("h:" + nk)
    return keys


def tags_by_serial(conn, serials) -> dict[str, list[dict]]:
    """這批資產序號各自的標籤：{serial: [{tag, note, created_by, created_at}]}。沒有的不放。"""
    uniq = [s for s in dict.fromkeys(serials) if s]
    if not uniq:
        return {}
    try:
        _ensure(conn)
    except Exception:  # noqa: BLE001 - 唯讀庫：沒有標籤
        return {}
    keys_of: dict[str, set[str]] = {}
    for i in range(0, len(uniq), 400):
        chunk = uniq[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT asset_serial, hostname, ip FROM hardware WHERE asset_serial IN ({ph})", chunk):
            keys_of.setdefault(r[0], set()).update(_row_keys(r[1], r[2], r[0]))
    all_keys = sorted({k for ks in keys_of.values() for k in ks})
    by_key: dict[str, list[dict]] = {}
    for i in range(0, len(all_keys), 400):
        chunk = all_keys[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
                "SELECT machine_key, tag, note, created_by, created_at FROM asset_tag "
                f"WHERE machine_key IN ({ph}) ORDER BY created_at, tag", chunk):
            by_key.setdefault(r[0], []).append({"tag": r[1], "note": r[2], "created_by": r[3],
                                                "created_at": r[4]})
    out: dict[str, list[dict]] = {}
    for s, ks in keys_of.items():
        seen, lst = set(), []
        for k in sorted(ks):
            for t in by_key.get(k, []):
                if t["tag"] not in seen:
                    seen.add(t["tag"])
                    lst.append(t)
        if lst:
            out[s] = sorted(lst, key=lambda t: (t["created_at"] or "", t["tag"]))
    return out


UNREGISTERED_REASON = "未登記，無法加標籤"


def add_tag_batch(conn, items: list[dict], tag: str, note: str = "", by: str | None = None) -> dict:
    """一次幫很多台貼同一個標籤。items：[{ip, asset_serial?}]——沒給序號就用 IP 找登記。

    每一列都回結果（ok／原因）；**沒登記的不會被靜默略過**，回「未登記，無法加標籤」。
    """
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("標籤不可以是空的")
    if len(tag) > 20:
        raise ValueError("標籤最多 20 個字（太長就寫進備註）")
    results = []
    for it in items or []:
        ip = (it.get("ip") or "").strip() or None
        serial = (it.get("asset_serial") or "").strip() or None
        if not serial and ip:
            r = conn.execute("SELECT asset_serial FROM hardware WHERE ip = ? ORDER BY rowid LIMIT 1",
                             (ip,)).fetchone()
            serial = r[0] if r else None
        if not serial:
            results.append({"ip": ip, "asset_serial": None, "ok": False, "reason": UNREGISTERED_REASON})
            continue
        try:
            add_tag(conn, serial, tag, note, by)
            results.append({"ip": ip, "asset_serial": serial, "ok": True, "reason": None})
        except ValueError as exc:
            results.append({"ip": ip, "asset_serial": serial, "ok": False, "reason": str(exc)})
    return {"tag": tag, "ok": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]), "results": results}
