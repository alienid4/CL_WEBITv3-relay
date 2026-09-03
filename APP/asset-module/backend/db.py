"""資產盤點模組 — SQLite 連線與初始化。

D13：SQLite。正式環境路徑預期為 /opt/webit3/data/asset.db（D34：與 app/ 分開）；
本機開發預設用相對路徑 data/asset.db，透過環境變數 ASSET_DB_PATH 可覆蓋。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "asset.db"


def get_db_path() -> Path:
    override = os.environ.get("ASSET_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """建立一條 SQLite 連線。

    check_same_thread=False 的理由（這是實際線上故障的修正，不是為了方便）：
    FastAPI 把 sync 的 get_db 依賴與 sync 的路由函式都丟進 anyio threadpool，
    但**不保證兩者落在同一條 worker 執行緒**。單一請求時通常剛好同一條，看起來沒事；
    一旦頁面同時發多個請求（資產查詢頁一次打 5 支 API），執行緒被打散就會炸出
    `SQLite objects created in a thread can only be used in that same thread`，
    前端顯示成「資產資料載入失敗，請稍後再試」——這就是先前被誤判為「暫時性快取問題、
    硬重整就好」的偶發故障的真正病根。

    關掉這個檢查是安全的：每個請求各自建立、使用、關閉自己的連線，
    連線不會被兩條執行緒「同時」使用，只是可能在 worker 之間交手。
    （這也代表不可以把連線改成跨請求共用的全域單例——那才會真的資料競爭。）
    """
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL：讀不會被寫擋住（儀表板一次並行打 5 支 API，預設 rollback journal 下
    # 讀寫互卡會出現 database is locked），而且備份用的 VACUUM INTO 可以在有人讀寫時
    # 安全進行。這是資料庫層設定，只要有人開過一次就永久生效，之後每次連線讀到的都是 wal。
    conn.execute("PRAGMA journal_mode = WAL")
    # 併發寫入時等一下再放棄，不要一碰到鎖就直接丟 database is locked 給使用者
    conn.execute("PRAGMA busy_timeout = 5000")
    _register_ip_int(conn)
    return conn


def _register_ip_int(conn: sqlite3.Connection) -> None:
    """SQL 函式 `_ip_int('10.99.1.5')` → 整數，供網段範圍查詢用。

    註冊在這裡（每條連線都有）而不是各模組自己註冊：漏註冊的症狀是
    `no such function: _ip_int`，會在某支查詢上偶發爆掉，很難查。
    IPv4 以外或格式不對回 NULL，讓 BETWEEN 自然不成立，不要讓整句查詢失敗。
    """
    import ipaddress as _ipa

    def _ip_int(s):
        try:
            addr = _ipa.ip_address(str(s).strip())
        except (ValueError, AttributeError):
            return None
        # 只認 IPv4——網段的 net_start/net_end 是拿 IPv4 位址灌的整數（SQLite INTEGER
        # 64位元有號範圍內）；IPv6 的整數值可以到 2^128，讓 SQLite 收這個回傳值會炸
        # OverflowError，而且不是「NULL 讓 BETWEEN 自然不成立」那種安全失敗，是直接
        # 整條查詢／整批批次處理中斷。2026-08-19 正式機真的踩到（vCenter 收到 VM 的
        # IPv6 link-local 位址 fe80::... 當 hardware.ip，害 CI 圖譜整批重建中途炸掉）。
        if addr.version != 4:
            return None
        return int(addr)

    conn.create_function("_ip_int", 1, _ip_int)


def init_db(db_path: Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


# 時間欄位改存本地時間後，既有資料要一次性換算。用 app_settings 記已完成，避免重複換算
# （跑第二次會再加一次時差，資料就毀了）。
_TZ_MIGRATION_KEY = "tz_localtime_migrated_v1"
_TZ_MIGRATION_TARGETS = [
    ("scan_history", "scan_time"),
    ("comparison_result", "detected_at"),
    ("comparison_result", "handled_at"),
    ("import_log", "imported_at"),
    ("connections", "last_tested_at"),
    ("connections", "updated_at"),
    ("connections", "created_at"),
    ("hardware", "created_at"),
    ("hardware", "updated_at"),
    ("personnel", "created_at"),
    ("software", "created_at"),
    ("systems", "created_at"),
    ("systems", "updated_at"),
    ("system_deps", "created_at"),
    ("feature_flags", "updated_at"),
    ("users", "created_at"),
]


# 2026-08-26：分類名稱改以系統管理員那份盤點表為準。原本白名單是照部門簡報抄的，
# 有 8 個名字抄歪了（嘉寶/嘉實、電子/雷影、版控/監控…）。使用者拍板「管理員那份是源頭」。
#
# ⚠️ 為什麼要 migration 而不是只改 JSON：APID→分類 對照表存在 app_settings 裡，
# **值就是分類名稱字串**。只改白名單的話，DB 裡那些舊名稱不會報錯，只會在
# `_category_defs()` 查不到而靜靜地從報表上消失（歸不進核心/非核心，變成未分類）。
# 「不會報錯但數字悄悄變了」正是報表最不能接受的失敗方式。
_SYSTEM_CATEGORY_RENAMES = {
    "嘉寶新樹精靈AP": "嘉實新樹精靈AP",
    "嘉寶新樹精靈Web": "嘉實新樹精靈Web",
    "金融交易處理服務": "金融交易服務",
    "財務/服務/法遵系統": "財務/股務/法遵系統",
    "開發版控平台": "開發監控平台",
    "電子密碼中心": "雷影密碼中心",
    # ⚠️ 這張表**不可以出現含公司識別字的名稱**。原本還有一條
    # 「<公司>證券中心 → <公司>證券中台」，2026-08-26 移除：`APP/` 底下的檔案一定會
    # 走去識別化，那條的兩端在打包後都會變成假名字，等於永遠對不到任何東西——
    # 而且不會報錯。含公司字的改名交給 _prefix_categories()，它拿 DB 裡那份
    # 清單（app_settings，不進版控）去比對，程式碼裡不需要出現真實名稱。
    "全景通路系統": "全景憑證系統",
}


def _prefix_categories(conn: sqlite3.Connection) -> int:
    """把 DB 裡沒有字母編號的舊分類值，補成清單裡對應的完整名稱。

    例：存的是 `資安管理系統`，清單裡是 `X.資安管理系統` → 改成後者。

    ## 為什麼用「比對」而不是寫死一張對應表

    2026-08-26 打 patch 時踩到：分類名稱含公司識別字，而 `APP/` 底下的檔案
    **一定會走去識別化**。寫死一張 `"<公司>證券App": "C.<公司>證券App"` 的表，
    打包後兩邊都會變成假名字，這條 migration 就等於沒作用——而且不會報錯。

    改成拿**DB 裡那份清單**（app_settings，不進版控、不被去識別化）去比對，
    程式碼裡就完全不需要出現真實名稱。

    只在「去掉前綴後唯一對上一個」時才改；對到多個或對不到就不動，
    留著讓人在分類頁上看得到那台是未分類，自己決定要歸哪一類——
    猜錯會把機器歸到錯的分類，代價比留著未分類高。
    """
    import json as _json

    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'report_system_categories'"
    ).fetchone()
    if not row or not row[0]:
        return 0
    try:
        defs = _json.loads(row[0])
    except (ValueError, TypeError):
        return 0
    if not isinstance(defs, list):
        return 0

    strip = lambda v: re.sub(r"^[A-Za-z]{1,3}[.．、]\s*", "", str(v or "").strip()).strip()  # noqa: E731
    valid = {c.get("name") for c in defs if isinstance(c, dict)}
    by_bare: dict[str, list[str]] = {}
    for n in valid:
        by_bare.setdefault(strip(n), []).append(n)

    changed = 0
    stored = {r[0] for r in conn.execute(
        "SELECT DISTINCT system_category FROM hardware "
        "WHERE system_category IS NOT NULL AND TRIM(system_category) <> ''")}
    for cur in stored:
        if cur in valid:
            continue
        hits = by_bare.get(strip(cur), [])
        if len(hits) == 1:
            changed += conn.execute(
                "UPDATE hardware SET system_category = ? WHERE system_category = ?",
                (hits[0], cur)).rowcount
    if changed:
        conn.commit()
    return changed


def _resolve_renames(raw: dict[str, str]) -> dict[str, str]:
    """把鏈式改名收斂成「一步到位」的對應。

    ⚠️ 這裡有一個很容易漏的坑：上面的表同時有
        「電子密碼中心 → 雷影密碼中心」（第一輪改錯字）
        「雷影密碼中心 → A.雷影密碼中心」（第二輪加回字母編號）
    如果只套一次，`電子密碼中心` 會停在中間的 `雷影密碼中心`——那個值**不在白名單裡**，
    於是那批機器會靜靜地變成未分類（就是這個 migration 當初要防的那個症狀）。

    hardware 那邊因為是逐條 UPDATE 跑完整張表，剛好會被下一條接著改到，
    **但那是靠 dict 的插入順序，重排一下就壞**；app_settings 那份 JSON 更是只套一次。
    與其依賴順序，不如在這裡把鏈走完。
    """
    out: dict[str, str] = {}
    for old_name in raw:
        seen = {old_name}
        cur = raw[old_name]
        while cur in raw and cur not in seen:
            seen.add(cur)
            cur = raw[cur]
        if cur != old_name:
            out[old_name] = cur
    return out


def _rename_system_categories(conn: sqlite3.Connection) -> int:
    """把 DB 裡存的舊分類名稱換成新的。兩處都要換：逐台的 hardware.system_category，
    以及 app_settings 裡那份 APID→分類 對照表（JSON）。

    冪等：換過之後舊名稱就不存在，再跑一次是 no-op。"""
    renames = _resolve_renames(_SYSTEM_CATEGORY_RENAMES)
    changed = 0
    for old, new in renames.items():
        changed += conn.execute(
            "UPDATE hardware SET system_category = ? WHERE system_category = ?",
            (new, old),
        ).rowcount

    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", ("report_system_category",)
    ).fetchone()
    if row and row[0]:
        try:
            mapping = json.loads(row[0])
        except (ValueError, TypeError):
            mapping = None
        if isinstance(mapping, dict):
            fixed = {k: renames.get(v, v) for k, v in mapping.items()}
            if fixed != mapping:
                changed += sum(1 for k in fixed if fixed[k] != mapping[k])
                conn.execute(
                    "UPDATE app_settings SET value = ? WHERE key = ?",
                    (json.dumps(fixed, ensure_ascii=False), "report_system_category"),
                )
    if changed:
        conn.commit()
    return changed


def _migrate(conn: sqlite3.Connection) -> None:
    """既有 DB 的欄位遷移（CREATE TABLE IF NOT EXISTS 不會替既有表加欄位，故手動補）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    for col in ("subnet", "mac", "hw_serial"):
        if col not in cols:
            conn.execute(f"ALTER TABLE hardware ADD COLUMN {col} TEXT")
    # 最強識別碼：vCenter 的 instanceUuid。換 IP、換主機名都不變，
    # 是多來源合併時唯一能單獨定案的東西（見 identity.py 的強度階梯）。
    if "vm_uuid" not in cols:
        conn.execute("ALTER TABLE hardware ADD COLUMN vm_uuid TEXT")
    # 納管狀態（與 asset_status 不同軸，見 schema.sql 註解）
    for col, coltype in (("collect_ok", "INTEGER"), ("collect_checked_at", "TEXT"),
                         ("collect_error", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE hardware ADD COLUMN {col} {coltype}")
    # 「人」最後一次修改這筆的時間。跟 updated_at 分開是必要的：updated_at 每次自動
    # 匯入都會被刷新，拿它當「有沒有人在維護」的新鮮度指標，匯一次 Excel 全部變新鮮，
    # 那個數字是假的（2026-08-15 自我檢查抓到）。只有編輯 API 會寫這欄。
    if "manual_updated_at" not in cols:
        conn.execute("ALTER TABLE hardware ADD COLUMN manual_updated_at TEXT")
    # 業務用途分類。掛每一台不是掛 api_id——2026-08-26 驗證：155 個 api_id 有 88 個
    # 橫跨多種分類，分類是逐台的人工判斷。詳見 schema.sql 該欄位的說明。
    if "system_category" not in cols:
        conn.execute("ALTER TABLE hardware ADD COLUMN system_category TEXT")
    # 「這台在哪個 vCenter 上」是很常追的問題，但原本一律寫死 source='vcenter'，
    # 多座 VC 的匯出混成一池就查不出來了。欄位名用 vi_sdk_server 而不是 vcenter——
    # RVTools 也能直連單台 ESXi，這欄只保證是「RVTools 連的那個管理端」。
    # 既有列一律 NULL，畫面要講清楚是「匯入早於此功能」不是「查過沒有」。
    if "vi_sdk_server" not in cols:
        conn.execute("ALTER TABLE hardware ADD COLUMN vi_sdk_server TEXT")
    _rename_system_categories(conn)
    _prefix_categories(conn)

    # 網段配置表：使用者 2026-08-26 提供的版本多了 環境別／註解／VLAN 三欄。
    seg_cols = {r[1] for r in conn.execute("PRAGMA table_info(network_segment)")}
    for col in ("environment_raw", "vlan", "remark", "expanded_from"):
        if col not in seg_cols:
            conn.execute(f"ALTER TABLE network_segment ADD COLUMN {col} TEXT")

    # 「誰在線上」用的心跳欄位。既有 DB 的 sessions 表要手動補。
    sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col in ("last_seen_at", "last_ip", "user_agent"):
        if col not in sess_cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")

    # 連線來源開關：既有 DB 已經有 connections 表，光改 schema.sql 不會補欄位
    conn_cols = {r[1] for r in conn.execute("PRAGMA table_info(connections)")}
    if "enabled" not in conn_cols:
        conn.execute("ALTER TABLE connections ADD COLUMN enabled INTEGER DEFAULT 1")

    # S16 未知主機指紋：既有 DB（含 221 正式庫）已經有 scan_history，光改 schema.sql 沒用
    scan_cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_history)")}
    for col, coltype in (
        ("mac", "TEXT"), ("mac_vendor", "TEXT"), ("open_ports", "TEXT"),
        ("ttl", "INTEGER"), ("os_guess", "TEXT"),
    ):
        if col not in scan_cols:
            conn.execute(f"ALTER TABLE scan_history ADD COLUMN {col} {coltype}")
    # 帳號盤點：既有 DB（含 221 正式庫）已經建過 host_account，
    # 改 schema.sql 不會替既有表補欄位——這個坑本檔開頭就寫過，這裡照辦。
    acct_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='host_account'")}
    if acct_tables:
        acct_cols = {r[1] for r in conn.execute("PRAGMA table_info(host_account)")}
        # note＝稽核人員「手動輸入的備註」（跟 gecos 自動備註是兩回事）。
        for col in ("login_source", "os_family", "os_id", "os_version", "note"):
            if col not in acct_cols:
                conn.execute(f"ALTER TABLE host_account ADD COLUMN {col} TEXT")

    # 單據檔案室：既有 DB 已經建過 doc_archive 時，改 schema.sql 不會補欄位
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='doc_archive'"
    ).fetchone():
        doc_cols = {r[1] for r in conn.execute("PRAGMA table_info(doc_archive)")}
        for col in ("full_text", "checkboxes", "values_json", "sections_json",
                    "checklist_json", "review_status", "reviewed_by", "reviewed_at"):
            if col not in doc_cols:
                conn.execute(f"ALTER TABLE doc_archive ADD COLUMN {col} TEXT")
        for col in ("is_current", "has_secrets", "is_decommission"):
            if col not in doc_cols:
                default = 1 if col == "is_current" else 0
                conn.execute(
                    f"ALTER TABLE doc_archive ADD COLUMN {col} INTEGER DEFAULT {default}")

    # 匯入紀錄要能區分「這是哪一種匯入」跟「哪個檔案」——2026-08-19 使用者反映：
    # 一次要匯6份RVTools檔案，需要看得出「已經匯過哪些」，原本 import_log 只有
    # 數字統計，看不出來源跟檔名，等於每次都要憑記憶判斷匯過沒。
    #
    # exported_at 是**哪天從 vCenter 匯出的**，跟 imported_at（哪天匯進系統）是兩件事。
    # 2026-08-20 實際踩到：8/20 匯進來的五個檔，全部是 7/30 匯出的，中間隔了三週。
    # 拿三週前的快照算爆炸半徑，會漏掉搬過來的 VM、也會多算搬走的——查 8/18 事故那台
    # ESXI169-220 只列出 16 台，當天手抄是 30 台，差一半。
    # 畫面只寫「最後匯入 8/20」會讓人以為資料是新的，那比沒有這個資訊更糟。
    log_cols = {r[1] for r in conn.execute("PRAGMA table_info(import_log)")}
    for col in ("source", "file_name", "exported_at"):
        if col not in log_cols:
            conn.execute(f"ALTER TABLE import_log ADD COLUMN {col} TEXT")

    # 既有的匯入紀錄補上 exported_at——檔名本來就存著，資訊一直都在，只是沒解析。
    # 不補的話正式庫要等下次匯入才看得到匯出日期，而「現在這批多舊」正是當下最想知道的。
    # 解析用 rvtools_import 那支唯一的實作，不在這裡再寫一份正則（規則有兩份必然漂走）。
    try:
        import rvtools_import  # noqa: PLC0415 - 延遲匯入，避免模組載入順序相依

        rows = conn.execute(
            "SELECT id, file_name FROM import_log "
            "WHERE source = 'rvtools' AND exported_at IS NULL AND file_name IS NOT NULL"
        ).fetchall()
        for r in rows:
            got = rvtools_import.export_time_from_filename(r[1])
            if got:
                conn.execute("UPDATE import_log SET exported_at = ? WHERE id = ?", (got, r[0]))
    except Exception:  # noqa: BLE001 - 回填失敗不該擋住服務啟動，下次匯入自然會有
        pass

    # 檢查清單從「人」改成「機器」為單位：2026-08-20 使用者反映事故當下要看的是
    # 「這台機器多重要、聯絡不到怎麼辦」，不是姓名/部門主管這種通訊錄欄位——補上
    # 機器身分（環境/位置）跟排序依據（severity/sort_depth，只給系統用不對人顯示）。
    checklist_cols = {r[1] for r in conn.execute("PRAGMA table_info(checklist_item)")}
    for col in ("environment", "physical_location", "severity", "sort_depth"):
        if col not in checklist_cols:
            coltype = "INTEGER" if col == "sort_depth" else "TEXT"
            conn.execute(f"ALTER TABLE checklist_item ADD COLUMN {col} {coltype}")

    # 新模組的功能開關：feature_flags 用 INSERT OR IGNORE 建在 schema.sql 裡，
    # 但既有 DB（含 221 正式庫）已經有這張表，executescript 不會補新的列。
    for key, label in (
        ("golive", "上線前檢查與基線"), ("documents", "單據檔案室"),
        ("segments", "網段配置表"), ("data_quality", "資料品質"),
        ("eos", "EOS 生命週期"), ("adopt", "納入管理"), ("pipeline", "納管漏斗"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO feature_flags (module_key, label, enabled) VALUES (?, ?, 1)",
            (key, label),
        )

    # 2026-08-21 起的新模組**預設關閉**，跟上面那批（既有功能，補登記用）不同。
    # 理由：這個開關是路由層真攔截，關著的功能連網址直接打都進不去。預設關比較安全，
    # 也讓使用者自己決定什麼時候讓同事看到——不要一部署就出現在別人的選單上。
    for key, label in (
        ("report_system", "系統組報告"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO feature_flags (module_key, label, enabled) VALUES (?, ?, 0)",
            (key, label),
        )

    conn.commit()
    _migrate_timestamps_to_localtime(conn)


def _migrate_timestamps_to_localtime(conn: sqlite3.Connection) -> None:
    """把既有的 UTC 時間欄位換算成本地時間。

    背景：schema 原本用 `datetime('now')`，那是 **UTC**；但 scan_runs 是 Python
    `datetime.now()` 寫的本地時間。同一次掃描在兩張表差了一個時差（台灣是 8 小時），
    畫面上的「最後掃描」因此顯示成 8 小時前的時間。已把 schema 全面改成
    `datetime('now','localtime')`，這裡負責把舊資料一起換過來，不然新舊混在一起更難查。

    sessions 表刻意不動：它的 expires_at 由 auth.py 用 UTC 寫、也用 UTC 比對，
    自成一套是正確的；動它會讓所有人的登入立刻失效或提早過期。
    """
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (_TZ_MIGRATION_KEY,)
    ).fetchone()
    if row is not None:
        return  # 已換算過，再跑一次會重複加時差

    existing_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, col in _TZ_MIGRATION_TARGETS:
        if table not in existing_tables:
            continue
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            continue
        # datetime(col,'localtime') 把值當 UTC 換算成本地；NULL 保持 NULL
        conn.execute(
            f"UPDATE {table} SET {col} = datetime({col}, 'localtime') WHERE {col} IS NOT NULL"
        )
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, '1')", (_TZ_MIGRATION_KEY,)
    )
    conn.commit()


def insert_hardware(conn: sqlite3.Connection, **fields) -> int:
    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO hardware ({columns}) VALUES ({placeholders})",
        tuple(fields.values()),
    )
    conn.commit()
    return cur.lastrowid


def update_hardware(conn: sqlite3.Connection, asset_serial: str, fields: dict) -> int:
    """更新一台資產的欄位。回傳實際被改的列數（0＝查無此序號）。

    只接受 hardware 表真實存在的欄位（呼叫端已擋過一次，這裡是第二道），
    欄位名不參數化但來源受限於白名單，值一律參數化。
    asset_serial 是主鍵不允許改（要改序號等於換一台，走刪除＋新增才不會弄丟關聯）。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    safe = {k: v for k, v in fields.items() if k in cols and k not in ("id", "asset_serial")}
    if not safe:
        return 0
    assignments = ", ".join(f"{k} = ?" for k in safe)
    cur = conn.execute(
        f"UPDATE hardware SET {assignments}, updated_at = datetime('now','localtime') "
        "WHERE asset_serial = ?",
        (*safe.values(), asset_serial),
    )
    conn.commit()
    return cur.rowcount


def get_hardware_by_serial(conn: sqlite3.Connection, asset_serial: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()


def list_hardware(conn: sqlite3.Connection, environment: str | None = None) -> list[sqlite3.Row]:
    if environment:
        return conn.execute(
            "SELECT * FROM hardware WHERE environment = ? ORDER BY hostname", (environment,)
        ).fetchall()
    return conn.execute("SELECT * FROM hardware ORDER BY hostname").fetchall()


# ⚠️ 時間欄位一律由 Python 明確寫入本地時間，不倚賴資料表的 DEFAULT。
# 理由（實際踩到）：`CREATE TABLE IF NOT EXISTS` 不會修改既有資料表的欄位預設值，
# 所以就算 schema.sql 改成 datetime('now','localtime')，既有資料庫上的新資料
# 仍然會用舊的 UTC 預設寫入——改了 schema 卻只對全新資料庫生效，是最容易漏掉的那種。
def insert_comparison_result(conn: sqlite3.Connection, hostname: str, ip: str, issue_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO comparison_result (hostname, ip, issue_type, detected_at) VALUES (?, ?, ?, ?)",
        (hostname, ip, issue_type, _now_local()),
    )
    conn.commit()
    return cur.lastrowid


def mark_comparison_read(conn: sqlite3.Connection, result_id: int) -> None:
    conn.execute(
        "UPDATE comparison_result SET is_read = 1, handled_at = datetime('now','localtime') WHERE id = ?",
        (result_id,),
    )
    conn.commit()


def create_user(conn: sqlite3.Connection, username: str, password_hash: str) -> int:
    cur = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    conn.commit()
    return cur.lastrowid


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def create_session(conn: sqlite3.Connection, token: str, user_id: int, expires_at: str) -> None:
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires_at),
    )
    conn.commit()


def get_valid_session(
    conn: sqlite3.Connection, token: str, now: str
) -> sqlite3.Row | None:
    """回傳未過期的 session（含關聯的 user 欄位），過期或不存在都回傳 None。"""
    return conn.execute(
        "SELECT sessions.*, users.username AS username FROM sessions "
        "JOIN users ON users.id = sessions.user_id "
        "WHERE sessions.token = ? AND sessions.expires_at > ?",
        (token, now),
    ).fetchone()


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def create_import_log(
    conn: sqlite3.Connection,
    imported_by: str | None,
    hardware_count: int,
    personnel_count: int,
    software_count: int,
    error_count: int,
    source: str | None = None,
    file_name: str | None = None,
    exported_at: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO import_log (imported_by, hardware_count, personnel_count, software_count, "
        "error_count, imported_at, source, file_name, exported_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (imported_by, hardware_count, personnel_count, software_count, error_count, _now_local(),
         source, file_name, exported_at),
    )
    conn.commit()
    return cur.lastrowid


def list_import_log(conn: sqlite3.Connection, source: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
    """最近的匯入紀錄——2026-08-19 使用者原話「要有紀錄表讓我查」：一次要匯好幾份
    RVTools檔案時，需要看得出哪些已經匯過（檔名+時間），不用憑記憶判斷。"""
    if source:
        return conn.execute(
            "SELECT * FROM import_log WHERE source = ? ORDER BY id DESC LIMIT ?", (source, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_latest_import_log(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM import_log ORDER BY imported_at DESC, id DESC LIMIT 1"
    ).fetchone()


def list_connections(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM connections ORDER BY name").fetchall()


def get_connection_by_id(conn: sqlite3.Connection, connection_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()


def create_connection_record(
    conn: sqlite3.Connection,
    name: str,
    connection_type: str | None,
    target: str,
    port: int | None,
    username: str | None,
    password: str | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO connections (name, connection_type, target, port, username, password) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, connection_type, target, port, username, password),
    )
    conn.commit()
    return cur.lastrowid


def update_connection_record(
    conn: sqlite3.Connection,
    connection_id: int,
    name: str,
    connection_type: str | None,
    target: str,
    port: int | None,
    username: str | None,
    password: str | None,
) -> None:
    """password=None代表這次不更動密碼（write-only的「留空=不變更」UX）。"""
    if password is None:
        conn.execute(
            "UPDATE connections SET name=?, connection_type=?, target=?, port=?, "
            "username=?, updated_at=datetime('now','localtime') WHERE id=?",
            (name, connection_type, target, port, username, connection_id),
        )
    else:
        conn.execute(
            "UPDATE connections SET name=?, connection_type=?, target=?, port=?, "
            "username=?, password=?, updated_at=datetime('now','localtime') WHERE id=?",
            (name, connection_type, target, port, username, password, connection_id),
        )
    conn.commit()


def set_connection_enabled(conn: sqlite3.Connection, connection_id: int, enabled: bool) -> None:
    """來源開關。停用＝排程掃描直接跳過，不會被算成「掃描失敗」。

    需要這個是因為：有些來源現階段本來就連不到（例如公司內網的 CMDB Gateway，
    在家裡開發時碰不到）。沒有開關的話它每次都失敗、每次都把「掃描不完整」點亮，
    久了那個警示就沒人看了——真正的掃描問題會被這種常態假警報淹掉。
    """
    conn.execute(
        "UPDATE connections SET enabled = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (1 if enabled else 0, connection_id),
    )
    conn.commit()


def delete_connection_record(conn: sqlite3.Connection, connection_id: int) -> None:
    conn.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
    conn.commit()


def update_connection_status(
    conn: sqlite3.Connection, connection_id: int, status: str
) -> None:
    conn.execute(
        "UPDATE connections SET last_status = ?, last_tested_at = datetime('now','localtime') WHERE id = ?",
        (status, connection_id),
    )
    conn.commit()


def list_feature_flags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM feature_flags ORDER BY module_key").fetchall()


def get_feature_flag(conn: sqlite3.Connection, module_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM feature_flags WHERE module_key = ?", (module_key,)
    ).fetchone()


def set_feature_flag(conn: sqlite3.Connection, module_key: str, enabled: bool) -> None:
    conn.execute(
        "UPDATE feature_flags SET enabled = ?, updated_at = datetime('now','localtime') WHERE module_key = ?",
        (int(enabled), module_key),
    )
    conn.commit()


# ---- app_settings（鍵值設定）----
def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---- scan_runs（掃描執行紀錄）----
def _now_local() -> str:
    """本地時間字串——掃描排程判定要跟 datetime.now() 一致，故不用 SQLite 的 UTC datetime('now')。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_scan_run(conn: sqlite3.Connection, trigger: str) -> int:
    cur = conn.execute(
        "INSERT INTO scan_runs (trigger, status, started_at) VALUES (?, 'running', ?)",
        (trigger, _now_local()),
    )
    conn.commit()
    return cur.lastrowid


def finish_scan_run(
    conn: sqlite3.Connection, run_id: int, status: str, found_count: int, error: str | None
) -> None:
    conn.execute(
        "UPDATE scan_runs SET status = ?, found_count = ?, error = ?, finished_at = ? WHERE id = ?",
        (status, found_count, error, _now_local(), run_id),
    )
    conn.commit()


def get_latest_scan_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()


def get_last_schedule_run_time(conn: sqlite3.Connection):
    """最近一次「排程觸發」掃描的開始時間（datetime，本地）；沒有回 None。"""
    row = conn.execute(
        "SELECT started_at FROM scan_runs WHERE trigger = 'schedule' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["started_at"]:
        return None
    from datetime import datetime

    return datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {get_db_path()}")
