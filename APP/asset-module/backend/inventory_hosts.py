"""盤點主機清單：每種盤點都先回答「是哪幾台」（2026-09-16）。

## 為什麼要有

使用者看到帳號盤點只有彙總卡片時說：

> 「我只知道四台? 不知道哪四台，應該是先列出哪幾台，我再進去點後才是那一台的全部帳號資訊」

接著補了一句適用範圍：**「每個盤點都是這概念」**。

彙總數字（129 個帳號、321 個發現）對得起來也沒用——人要做的事是「去那台上面改」，
所以每一種盤點都要先給主機清單，點進去才是那台的明細。

## 為什麼集中寫在這裡

服務、軟體、EOS 三種盤點的資料表不同，但「列出哪幾台」的形狀完全一樣：
IP、主機名、資產序號、機房、環境、OS、幾筆、最後盤點時間。
各頁各寫一份，遲早會有一頁漏掉機房、另一頁的「最後盤點」取到不同欄位。

⚠️ 這裡只回「**實際收到資料的主機**」。沒出現在清單上代表這一輪沒收到
（還沒納管、收不到、或被排除），那是另一件事——不要用 hardware 全表 LEFT JOIN
假裝每台都盤點過，那會讓「盤點了 4 台」變成「盤點了 4789 台其中 4785 台是 0 筆」。
"""
from __future__ import annotations

#: 每種盤點：資料表、計數欄位、時間欄位、額外統計
KINDS: dict[str, dict] = {
    "service": {
        "table": "host_service",
        "time_col": "last_seen",
        "label": "服務",
        # 對外曝露的（bind 在 0.0.0.0）是看的人最在意的
        "extra": {
            "exposed": "SUM(CASE WHEN exposure = 'all' THEN 1 ELSE 0 END)",
            "infra": "SUM(CASE WHEN is_infra = 1 THEN 1 ELSE 0 END)",
            "guessed": "SUM(CASE WHEN guess_source = 'port' THEN 1 ELSE 0 END)",
        },
    },
    "software": {
        "table": "host_package",
        "time_col": "last_seen",
        "label": "軟體",
        "extra": {},
    },
}

_EXTRA_LABEL = {
    "exposed": "對外曝露",
    "infra": "基礎服務",
    "guessed": "靠埠號猜的",
}


def _time_col(conn, table: str, want: str) -> str | None:
    """舊資料庫可能沒有 last_seen。取不到就回 None，畫面顯示「—」而不是整支爆掉。"""
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:  # noqa: BLE001
        return None
    for c in (want, "collected_at", "last_seen", "first_seen"):
        if c in cols:
            return c
    return None


def hosts(conn, kind: str) -> list[dict]:
    """某一種盤點實際收到資料的主機清單。"""
    spec = KINDS[kind]
    table = spec["table"]
    tcol = _time_col(conn, table, spec["time_col"])
    extra_sql = "".join(f", {expr} AS {name}" for name, expr in spec["extra"].items())
    time_sql = f", MAX({tcol}) AS collected_at" if tcol else ", NULL AS collected_at"
    try:
        rows = conn.execute(
            f"SELECT ip, MAX(asset_serial) AS asset_serial, COUNT(*) AS items{extra_sql}{time_sql} "
            f"FROM {table} WHERE ip IS NOT NULL AND ip != '' GROUP BY ip ORDER BY ip"
        ).fetchall()
    except Exception:  # noqa: BLE001 - 表還沒建（沒收過）就當沒有
        return []

    hw = {r["ip"]: r for r in conn.execute(
        "SELECT ip, hostname, os, environment, physical_location FROM hardware "
        "WHERE ip IS NOT NULL AND ip != ''")}

    out = []
    for r in rows:
        h = hw.get(r["ip"])
        item = {
            "ip": r["ip"],
            "asset_serial": r["asset_serial"],
            "hostname": h["hostname"] if h else None,
            "os": h["os"] if h else None,
            "environment": h["environment"] if h else None,
            "physical_location": h["physical_location"] if h else None,
            "items": r["items"],
            "collected_at": r["collected_at"],
            # 收到資料但資產庫查不到這個 IP——要看得見，不要靜默當成已登記
            "registered": h is not None,
        }
        for name in spec["extra"]:
            item[name] = r[name] or 0
        out.append(item)
    return out


def headers(kind: str) -> list[str]:
    spec = KINDS[kind]
    base = ["IP", "主機名稱", "資產序號", "機房", "環境", "作業系統", f"{spec['label']}筆數"]
    base += [_EXTRA_LABEL.get(n, n) for n in spec["extra"]]
    base += ["最後盤點"]
    return base


def export_rows(conn, kind: str) -> list[list]:
    spec = KINDS[kind]
    out = []
    for h in hosts(conn, kind):
        row = [h["ip"], h["hostname"], h["asset_serial"], h["physical_location"],
               h["environment"], h["os"], h["items"]]
        row += [h.get(n, 0) for n in spec["extra"]]
        row.append(h["collected_at"])
        out.append(row)
    return out


# ===== EOS：不是「收集」出來的，是比對出來的 =====
# EOS 沒有自己的資料表——它是拿資產登記的 OS／型號去查 EOS 對照表算出來的。
# 所以「哪幾台」＝**查得到 EOS 日期的那幾台**，跟服務／軟體的「收到幾筆」不同，
# 這裡分開寫而不是硬塞進上面的 KINDS，免得欄位語意被扭曲。

EOS_HEADERS = ["IP", "主機名稱", "資產序號", "機房", "環境", "作業系統", "OS EOS",
               "OS 狀態", "設備型號", "硬體 EOS", "硬體狀態"]


def eos_hosts(conn) -> list[dict]:
    """查得到 EOS 日期的主機。查不到的不列——「沒有資料」不等於「還在支援」。"""
    import eos
    import normalize

    # 退役狀態集合走正典 manage_state.RETIRED_STATUS，不寫死（未來改集合會漏這處）。
    import manage_state as _ms
    _ret = sorted(_ms.RETIRED_STATUS)
    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, os, device_model, environment, physical_location, "
        f"asset_status FROM hardware WHERE COALESCE(asset_status,'') NOT IN ({','.join('?' * len(_ret))})",
        _ret,
    ).fetchall()
    out = []
    for r in rows:
        os_hit = hw_hit = None
        if r["os"]:
            try:
                info = normalize.normalize_os(r["os"], conn, r["device_model"])
                os_hit = eos.lookup_os_eos(info["canonical"])
            except Exception:  # noqa: BLE001 - 正規化失敗不該讓整份清單消失
                os_hit = None
        if r["device_model"]:
            try:
                hw_hit = eos.lookup_hardware_eos(r["device_model"])
            except Exception:  # noqa: BLE001
                hw_hit = None
        # 要有**日期**才算查得到。對照表有這個型號但日期欄空白（官方還沒公佈）
        # 不能算成「查過了」——那會讓人以為已經確認過，其實還是未知。
        # ⚠️ 2026-09-20 公司驗收抓到：這裡原本讀 "eos"，但 eos.lookup_* 回的欄位叫 **eos_date**，
        # 於是每一台都被當成「查不到日期」跳過 → 這塊永遠顯示「盤點到的主機 0 台」，
        # 同一頁下面卻列著「Windows Server 2012 … 221 台」。匯出的 Excel 也一直是空的。
        if not (os_hit or {}).get("eos_date") and not (hw_hit or {}).get("eos_date"):
            continue
        out.append({
            "asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"],
            "os": r["os"], "device_model": r["device_model"],
            "environment": r["environment"], "physical_location": r["physical_location"],
            "os_eos": (os_hit or {}).get("eos_date"),
            "os_status": eos.eos_status((os_hit or {}).get("eos_date")) if os_hit else None,
            "hw_eos": (hw_hit or {}).get("eos_date"),
            "hw_status": eos.eos_status((hw_hit or {}).get("eos_date")) if hw_hit else None,
        })
    return out


def eos_export_rows(conn) -> list[list]:
    return [[h["ip"], h["hostname"], h["asset_serial"], h["physical_location"], h["environment"],
             h["os"], h["os_eos"], h["os_status"], h["device_model"], h["hw_eos"], h["hw_status"]]
            for h in eos_hosts(conn)]
