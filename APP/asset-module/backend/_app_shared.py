"""S5：後端 REST API 層（D29 API優先設計）。

只做資料存取／整合既有模組（db／scanner／comparison_engine），不重複實作比對或掃描邏輯——
這層單純把既有函式包成 HTTP 介面，供 Nuxt3 前端（S7-S9）與之後其他模組重用（D29 note：
系統拓撲模組可直接呼叫這裡的 API 重用資料，不用重新收集）。

sort_by／order 一律經白名單檢查才拼進 SQL ORDER BY，避免使用者輸入直接串進查詢字串。
"""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

from fastapi import (
    Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel

import contextlib

import activity
import agent_auth
import auth
# 收集器身分（金鑰與自己的位址）集中在這裡解析——五個端點都要用，
# 各自 import 過一次容易漏掉，漏掉的那個就會走回舊的寫死預設值。
import onboard_engine
import backup
import cmdb_gateway
import golive
import scan_service
from db import (
    _now_local,  # 匯出檔名的日期（v1.70.0 起就漏 import，Excel/dump 匯出一律 500）
    create_connection_record,
    get_db_path,
    create_import_log,
    delete_connection_record,
    get_connection,
    get_connection_by_id,
    get_feature_flag,
    get_latest_import_log,
    insert_hardware,
    list_connections,
    list_import_log,
    list_feature_flags,
    mark_comparison_read,
    set_connection_enabled,
    set_feature_flag,
    update_connection_record,
    update_connection_status,
    update_hardware,
)
from excel_import import MAPPING_PATH, SHEET_CONFIG, import_excel, load_mapping

# 本行程的啟動時間：只有真的重啟過才會變，是「新程式碼有沒有生效」最可信的證據。
# （version.json / build_info.json 都是即時讀檔，服務沒重啟時照樣顯示新版，會騙人。）
_PROCESS_STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 開發環境前端跑在 3000 埠（Nuxt3預設），可用環境變數 ASSET_API_CORS_ORIGINS 覆蓋（部署到
# 內網IP時就是靠它指定實際來源）。
#
# 預設同時放行 localhost 與 127.0.0.1 兩種寫法：這兩個在瀏覽器眼中是不同的 origin，
# 少放一個就會在開發時撞到 CORS 錯誤，而畫面只會顯示「載入失敗」，很難聯想到是位址寫法問題。
# （本機實測：這台 localhost 先解析到 IPv6 ::1，開發時改用 127.0.0.1 才連得到 dev server，
#  結果就踩到只放行 localhost 的 CORS。）
_DEFAULT_DEV_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
_allowed_origins = [
    origin.strip()
    for origin in os.environ.get("ASSET_API_CORS_ORIGINS", _DEFAULT_DEV_ORIGINS).split(",")
    if origin.strip()
]
# 除了上面的明確白名單，再自動放行「內網同主機的前端埠」。
# 為什麼要有：CORS 白名單是靠 deploy.sh 的 API_HOST 算出來的。
# （2026-10-06 起 API_HOST 沒有預設值，沒設就直接失敗——原本預設成某台開發機位址，
#  那個字面值因此被打進交付包；詳見 deploy.sh 裡那段註解。）
# 換一台機器部署（例如 10.99.198.14）若沒記得設 API_HOST/ASSET_API_CORS_ORIGINS，
# 白名單就對不上實際前端來源——後果是「跨源下載默默被擋、畫面只寫『匯出失敗』」，
# 很難聯想到是 CORS（2026-09-09 公司機器實際踩到，一般 API 走 SSR 同源沒事、唯獨
# 瀏覽器直接下載的匯出被擋）。
# 這條 regex 只放行 RFC1918 私有網段 + localhost 的「前端埠」（預設 3000，可用
# ASSET_API_CORS_WEB_PORT 覆蓋），不放行任何公網來源——內網工具，風險可接受，
# 換掉「換 IP 就靜默壞」這個反覆踩的雷。
_CORS_WEB_PORT = (os.environ.get("ASSET_API_CORS_WEB_PORT", "3000").strip() or "3000")
_allowed_origin_regex = (
    r"^http://(?:localhost|127\.0\.0\.1"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
    r":" + re.escape(_CORS_WEB_PORT) + r"$"
)

FIELD_GROUPS_PATH = Path(__file__).parent / "field_groups.json"

ALLOWED_ASSET_SORT = {
    "hostname", "ip", "device_model", "rack_no", "group_name", "api_id",
    "asset_purpose", "custodian", "usage_unit", "asset_status", "environment",
    "asset_serial",
}
ALLOWED_ISSUE_SORT = {"detected_at", "hostname", "issue_type", "is_read"}
ALLOWED_PERSONNEL_SORT = {
    "person_name", "phone", "job_desc", "belong_division", "belong_department",
    "asset_serial",
}
ALLOWED_SOFTWARE_SORT = {
    "asset_name", "hostname", "ip", "db_software", "backup_frequency", "cloud",
    "custodian", "asset_serial",
}


def get_db() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


#: 測試階段整個關掉登入的開關（使用者 2026-09-10：「我現在測試階段，用不到登入」）。
#:
#: ⚠️ 開著 = **沒有存取控制**。任何連得到這個埠的人都能撈走全部資產與人員姓名電話。
#: 在金融業這是稽核會直接開缺失的項目，所以設計上刻意麻煩：
#:   · **預設關閉**
#:   · 只能從伺服器端打開（環境變數 `WEBIT3_DISABLE_AUTH=1`，或已登入的人在系統設定裡開）
#:   · 開著時 `/api/version` 會回 `auth_disabled: true`，前端掛一條永遠在的紅色橫幅
#:   · `deploy.sh`／`patch.sh` 不會自動打開
#:
#: 為什麼不做成登入頁上一顆按鈕：那等於「未登入的人自己就能關掉登入」，
#: 那不是開關，那是後門。
AUTH_DISABLED_ENV = "WEBIT3_DISABLE_AUTH"
AUTH_DISABLED_SETTING = "auth_disabled"


def auth_disabled(conn: sqlite3.Connection | None = None) -> bool:
    """登入是不是被關掉了。環境變數優先，其次看系統設定。"""
    if os.environ.get(AUTH_DISABLED_ENV, "").strip() in ("1", "true", "TRUE", "yes"):
        return True
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (AUTH_DISABLED_SETTING,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row) and str(row["value"]).strip() in ("1", "true", "yes")


def _anonymous_session() -> dict:
    """登入關掉時給的假 session。

    使用者名稱刻意寫成看得懂的字而不是 admin——操作紀錄裡要一眼看得出
    「這筆是在沒有登入的狀態下做的」，事後查稽核軌跡才不會以為真的有人登入過。
    """
    return {"id": 0, "username": "(未登入·登入已停用)", "user_id": 0}


def require_auth(
    request: Request,
    session_token: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:
    """要保護一支端點，在路由函式簽章加上 Depends(require_auth) 即可。

    現況：除了 login／logout／version 這三支（見 test_auth_coverage.PUBLIC 白名單），
    所有 /api 端點都掛了這個依賴。

    歷史教訓：S6 當初只用它保護 /api/auth/me，其餘端點「不在這個切片範圍」而留待後續，
    結果一直沒補——前端每頁都有登入守衛，看起來很安全，但 API 全裸長達數個切片，
    任何碰得到後端埠的人都能撈走全部資產與人員姓名電話，或 PUT 改欄位對應。
    現已全數補上，並由 tests/asset_module/test_auth_coverage.py 自動涵蓋新端點防止再退化。
    新增路由請預設掛上；真要公開必須明確加進該白名單。
    """
    session = auth.resolve_session(conn, session_token)
    if session is None:
        # 測試階段可以整個關掉登入（使用者 2026-09-10：「我現在測試階段，用不到登入」）。
        #
        # ⚠️ 這是**關掉存取控制**，不是介面調整：開著的時候，任何連得到這個埠的人
        # 都能撈走全部資產與人員姓名電話。所以：
        #   · 預設關閉，只能由**伺服器端**打開（環境變數／系統設定），
        #     不是畫面上一顆按鈕——按鈕會變成「未登入的人自己就能關掉登入」
        #   · 開著時 /api/version 回 auth_disabled=true，畫面掛一條永遠在的橫幅，
        #     避免哪天忘記關
        #   · deploy.sh／patch.sh 都不會自動打開它
        if auth_disabled(conn):
            return _anonymous_session()
        raise HTTPException(401, "未登入或登入已過期")
    # 「誰在線上」的心跳。掛在這裡是因為它是**唯一保證覆蓋所有端點**的地方
    # （test_auth_coverage.py 會檢查每支新端點都掛了 require_auth）；掛在別處，
    # 新頁面漏記時不會有任何測試變紅，等到要查的時候才發現那段時間是空白的。
    # 內建 60 秒節流，不是每個 request 都寫 DB（見 activity.touch_session）。
    #
    # 整段吞例外：心跳是附加資訊，為了它讓一支正常的 API 回 500 是本末倒置。
    try:
        activity.touch_session(conn, session, request)
    except Exception:  # noqa: BLE001 - 心跳失敗不可以影響請求本身
        pass
    return session


def require_host_key(
    x_agent_key: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> str:
    """Push agent 端點專用的驗證——跟 require_auth（人的 session cookie）是不同層級，
    這裡驗的是「機器」用 header 帶的一次性核發 key。回傳驗證出的 asset_serial，
    路由函式一定要用這個回傳值，不能信任 request body 裡任何自稱的主機識別欄位
    （否則 agent 就能冒充寫別台的資料，見 agent_auth.resolve_host_key 的說明）。
    """
    asset_serial = agent_auth.resolve_host_key(conn, x_agent_key)
    if asset_serial is None:
        raise HTTPException(401, "agent key 缺失、無效或已撤銷")
    return asset_serial


class MarkReadBody(BaseModel):
    is_read: bool = True


class LoginBody(BaseModel):
    username: str
    password: str


def _log_auth(conn: sqlite3.Connection, request: Request, username: str | None,
              action: str, status: int) -> None:
    """登入相關事件的紀錄。這幾支不走 middleware 的一般路徑（見那裡的說明），
    因為只有端點自己知道「是誰」以及「成功還是失敗」。"""
    try:
        activity.log(conn, username=username, ip=activity.client_ip(request),
                     method=request.method, path=request.url.path,
                     status=status, action=action)
    except Exception:  # noqa: BLE001 - 記錄失敗不可以擋住登入
        pass


# ===== CIA(登記) × 掃描 的比對：儀表板磚塊與各個下鑽清單共用同一套判定 =====
# 儀表板每個數字都可以點進去看細項，所以「數字」跟「清單」必須用同一段邏輯算，
# 否則點進去筆數對不上，比不能點還糟。

def _latest_scan_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    return row["t"] if row else None


def _scanned_alive_rows(conn: sqlite3.Connection, scan_time: str | None) -> list[sqlite3.Row]:
    """最新一次掃描中「掃得到（存活）」的主機。scan_ok=0 是網段掃描失敗，不是主機死掉。"""
    if not scan_time:
        return []
    return conn.execute(
        "SELECT * FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (scan_time,)
    ).fetchall()


def _scan_keys(scanned_rows: list[sqlite3.Row]) -> tuple[set[str], set[str]]:
    return (
        {r["ip"] for r in scanned_rows if r["ip"]},
        {r["hostname"] for r in scanned_rows if r["hostname"]},
    )


def _row_in_keys(row, ips: set[str], hostnames: set[str]) -> bool:
    """IP 或主機名稱任一對得上就算同一台（沿用 /api/scan/unregistered 既有的判定方式）。"""
    return bool(
        (row["ip"] and row["ip"] in ips) or (row["hostname"] and row["hostname"] in hostnames)
    )


def _check_sort(sort_by: str, order: str, allowed: set[str]) -> None:
    if sort_by not in allowed:
        raise HTTPException(400, f"不支援的排序欄位：{sort_by}")
    if order not in ("asc", "desc"):
        raise HTTPException(400, "order 只接受 asc 或 desc")


_COLUMN_CACHE: dict[str, set[str]] = {}


def _sortable_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """回傳該資料表**實際存在的欄位名**當排序白名單。

    為什麼不寫死一份清單：使用者的鐵則是「表格每一欄都要能排」，而畫面上的欄位
    是由 field_groups/field_meta 動態決定的（含數十個進階欄位）。寫死的白名單只涵蓋
    12 欄，點到其他欄就回 400、畫面顯示「資產資料載入失敗」——比不能排更糟。

    安全性不變：來源是 PRAGMA 的真實欄位名，使用者輸入只能「命中或不命中」，
    永遠不會有自由字串被拼進 ORDER BY。新增欄位也自動涵蓋，不用記得回來改。
    """
    if table not in _COLUMN_CACHE:
        _COLUMN_CACHE[table] = {
            r[1] for r in conn.execute(f"PRAGMA table_info({table})")
        }
    return _COLUMN_CACHE[table]


# D25：儀表板環境篩選預設「正式」，可切換包含測試/備援。HTML Mock v4三段式選單，
# key用ASCII"+"避免query string編碼問題，None代表不篩選（=全部含備援）。
ENV_FILTER_PRESETS: dict[str, list[str] | None] = {
    "正式": ["正式"],
    "正式+測試": ["正式", "測試"],
    "全部": None,
}


class MarkReadBatchBody(BaseModel):
    ids: list[int]


def _dismissed_keys(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """人工標記過「這組不是重複」的 (hostname, ip) key 集合。"""
    return {
        (r["hostname"], r["ip"])
        for r in conn.execute("SELECT hostname, ip FROM duplicate_dismiss").fetchall()
    }


def _origin_of(conn: sqlite3.Connection, row: dict) -> dict:
    """這一筆資產是從哪條路進來的——**診斷用，唯讀，不改任何東西**。

    只回四樣，剛好夠定案「是哪兩條路撞的」，不多給：
      * `how`  序號前綴看得出來的來路（掃描收編／批次納管／匯入或手建）
      * `created_at` / `updated_at`  時間差是目前最可能的解釋，要看得到先後
      * `from_import`  有沒有對應的 source_record（匯入來的才有）
      * `strong_ids`   有沒有 vm_uuid／hw_serial／mac

    最後一項最關鍵：**兩筆都沒有強識別碼**的話，身分解析只能靠主機名＋IP，
    那正是「當時 IP 還沒補齊就各自建了一筆」會發生的情境。
    """
    serial = (row.get("asset_serial") or "").upper()
    if serial.startswith("ADOPT-"):
        how = "掃描收編（/api/assets/adopt）"
    elif serial.startswith("AUTO-"):
        how = "批次納管自動補（_post_onboard）"
    else:
        how = "匯入或手動新增"
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM source_record WHERE resolved_hardware_id = ?",
            (row.get("id"),)).fetchone()[0]
    except sqlite3.Error:
        n = None            # 沒有這張表就是沒有，不要假裝查過
    return {
        "how": how,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # None＝查不到（表不在），0＝查過了真的沒有。兩者不可以混。
        "from_import": None if n is None else n > 0,
        "strong_ids": [k for k in ("vm_uuid", "hw_serial", "mac")
                       if str(row.get(k) or "").strip()],
    }


def _duplicate_groups(conn: sqlite3.Connection) -> list[dict]:
    """找出「主機名稱與 IP 都一樣」的多筆資產——幾乎可以確定是同一台被登記了兩次。

    只認兩者都相同，是刻意保守：
      · 只有 IP 相同 → 可能是 DHCP 回收後給了別台，或真的位址衝突，都不是重複登記。
      · 只有主機名相同 → 可能是同一台換過網段，那是搬遷不是重複。
    誤報一筆重複，使用者就得花時間去比對兩台到底哪裡不一樣，寧可少報也不要亂報。

    比對時去空白、主機名不分大小寫：Excel 匯入常常帶進尾端空白或大小寫不一致，
    那種「看起來一模一樣卻被當成兩台」正是最該抓出來的情況。

    停用/報廢/閒置＝退役資產排除在外：不然「停用舊料＋使用中新料同IP」會被誤判成重複登記，
    使用者得花時間去比對兩台根本不是同一回事（見 manage_state.RETIRED_STATUS）。

    人工標記過「這組不是重複」（`duplicate_dismiss`）的組別也排除在外——使用者比機器更清楚
    哪些是同一台合法掛多個業務系統/VIP，不該每次都再跳出來煩他。

    抽成獨立函式讓 release/dismiss 兩個動作也能重跑同一份判準，在伺服器端重新驗證
    使用者操作當下這組資料是不是還跟畫面上看到的一樣（不信任前端傳來的舊快照）。
    """
    import manage_state as ms
    import vip_view

    # 2026-10-17 審查 #5：分組改吃 vip_view.duplicate_candidate_groups（儀表板、健康檢查同一份）
    groups = []
    for key, members in vip_view.duplicate_candidate_groups(conn):
        rows = [dict(m) for m in members]
        # **每一筆「是從哪條路進來的」要看得到。**
        # 使用者 2026-09-23 問「為什麼重複這麼多台」——只看得到兩筆長得一樣，
        # 答不出是哪兩條路撞的，就只能一直手動合併，下個月再長一次。
        # 序號前綴會說話：ADOPT- 是掃描收編、AUTO- 是批次納管自動補、其餘是匯入或手建。
        for r in rows:
            r["origin"] = _origin_of(conn, r)
        # 標出這組成員之間「值不一樣」的欄位：若完全一致，刪掉多的那筆即可；
        # 若有差異，就得由人判斷該留哪一筆，不能盲目合併。
        diff_fields = []
        for col in rows[0].keys():
            if col in ("id", "asset_serial", "created_at", "updated_at"):
                continue
            if col == "big_ip_vip":
                # 「無」跟空白是同一件事，不算欄位不同
                vals = {vip_view.normalize_vip(r.get(col)) for r in rows}
            else:
                vals = {("" if r.get(col) is None else str(r.get(col)).strip()) for r in rows}
            if len(vals) > 1:
                diff_fields.append(col)
        # 分三類（2026-09-14）：CIA 清冊是「每個服務／VIP 一筆」，同主機同 IP 多筆
        # 大多是一台掛多系統／多 VIP，不是重複。只有「疑似真重複」才給可少筆數。
        cls = vip_view.classify_duplicate_group(rows)
        groups.append({
            "key": key,
            "hostname": rows[0].get("hostname"),
            "ip": rows[0].get("ip"),
            "count": len(rows),
            "identical": not diff_fields,
            "diff_fields": diff_fields,
            "kind": cls["kind"],
            "kind_label": cls["kind_label"],
            "extra_rows": cls["extra_rows"],
            "systems": cls["systems"],
            "vips": cls["vips"],
            # 給人工管理介面逐欄比較用：完整欄位，不截斷。
            "members": rows,
        })
    return groups


class DuplicateReleaseBody(BaseModel):
    hostname: str
    ip: str
    release_serials: list[str]
    note: str | None = None


MERGE_AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS merge_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    merged_at       TEXT NOT NULL,
    actor           TEXT,
    hostname        TEXT,
    ip              TEXT,
    released_serial TEXT NOT NULL,   -- 被刪掉的那個序號
    kept_serials    TEXT,            -- 留下來的是哪幾個
    snapshot        TEXT             -- 被刪那一列的完整內容（JSON）
)
"""


def _ensure_merge_audit(conn: sqlite3.Connection) -> None:
    conn.execute(MERGE_AUDIT_SQL)


class DuplicateDismissBody(BaseModel):
    hostname: str
    ip: str
    note: str | None = None


def _onboard_block(hw: dict, conn=None, esxi_names=None) -> dict | None:
    """這台能不能納管；不能的話回原因。判準跟收集分派／批次納管同一支，畫面不另外猜。

    2026-09-15 使用者看到 SAN switch 詳細頁還掛著「一鍵納管」；2026-09-16 使用者要求
    資產查詢頁每列也要有「一鍵納管」，所以清單頁也用這一支，兩邊不會一個給按一個不給。

    2026-10-16：以前這裡只傳 os／hostname／ip 三項，**少了 esxi_names 與資產名稱／
    用途**，而 /adopt 那邊兩項都有。於是同一台 EMC UnityXT480（os 欄 `N/A`、用途
    寫著 NAS）在 /adopt 是「儲存設備」、在資產查詢卻可以按「一鍵納管」。
    改成走 `onboard_eligibility.block_for_asset`——證據由那一支統一蒐集，
    三個呼叫點不再各湊各的（要加證據就加在那裡，三頁一起變）。

    `esxi_names` 在逐列迴圈裡一定要先查好傳進來，不然每列都會全表掃一次。
    """
    try:
        import onboard_eligibility

        return onboard_eligibility.block_for_asset(conn, hw, esxi_names=esxi_names)
    except Exception:  # noqa: BLE001 - 判不出來就照舊顯示，不擋整頁
        return None


class BatchLookupBody(BaseModel):
    terms: list[str]




def _collect_trace_of(conn, asset_serial: str) -> dict | None:
    """這台最近一次收集的 SSH 事實。沒有紀錄回 None（畫面顯示「還沒收過」）。

    ⚠️ 這一塊跟其他「收不到的理由」不同層：其他那些是**收集器的推論**
    （「AIX 不可能零 fileset，所以是收集失敗」——對，但那是推論）；
    這裡是**離開碼與 stderr 的原文**，是事實。兩者畫面上要分開標示。
    """
    import json

    import manage_state as _ms

    try:
        row = conn.execute("SELECT * FROM collect_ssh_trace WHERE asset_serial = ?",
                           (asset_serial,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊 DB 還沒有這張表時不要讓整頁掛掉
        return None
    if row is None:
        return None
    out = dict(row)
    try:
        out["samples"] = json.loads(out.get("samples") or "[]")
    except (ValueError, TypeError):
        out["samples"] = []
    out.update(_ms.trace_verdict(row))
    return out



def _norm_serial(v) -> str:
    """序號比對用的正規化：去掉大小寫、空白、連字號與廠商前綴。

    **不是為了合併成一個值**，是為了判斷「這兩個到底算不算同一個」。
    AIX 的 `uname -u` 給的是 systemid（帶 `IBM,02` 之類的前綴），
    `prtconf` 的 Machine Serial Number 給的是裸序號——
    那**本來就是兩種寫法**，不正規化就會把它們誤判成互相矛盾。
    """
    t = str(v or "").strip().upper().replace(" ", "").replace("-", "")
    if "," in t:
        t = t.split(",", 1)[1]
    return t



def _same_serial(a: str, b: str) -> bool:
    """這兩個序號算不算同一台。

    ⚠️ 不能直接比字串。同一台機器在不同指令下**本來就有不同寫法**：
    AIX 的 `uname -u` 給 systemid（`IBM,0206781A091`——`IBM,` 是廠商、`02` 是型別碼），
    `prtconf` 的 Machine Serial Number 給裸序號（`06781A091`）。
    直接比會把「同一台的兩種寫法」誤報成矛盾——那是假警報，而且會讓真正的矛盾被淹掉。

    判準：正規化後**一個是另一個的結尾**就算同一台（處理各種前綴碼），
    但短字串要完全相同才算——`123` 是一堆序號的結尾，那樣比會把不同機器判成同一台。
    """
    x, y = _norm_serial(a), _norm_serial(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= 6 and long_.endswith(short)


def _serial_view(hw, host_spec) -> dict:
    """把兩個來源的序號攤開給畫面看：值、出處、有沒有互相矛盾。

    ⚠️ **不自動挑一個當正解。** 兩個來源各自有出處，挑一個等於替使用者決定
    要相信誰，而且挑錯的時候畫面上完全看不出來。

    三種下場要分得開：

    ======================  ==================================================
    兩邊都有、正規化後相同   一致，顯示值＋兩個出處
    兩邊都有、正規化後不同   **矛盾**，兩個都列出來給人判（不挑邊）
    只有一邊有              顯示那一邊，並講明另一邊為什麼沒有
    ======================  ==================================================
    """
    facts = (hw.get("hw_serial") or "").strip() if hw else ""
    spec = ""
    if host_spec:
        spec = (host_spec.get("serial") or "").strip()
    sources = []
    if facts:
        sources.append({"value": facts, "from": "主機事實收集（hardware.hw_serial）"})
    if spec:
        sources.append({"value": spec, "from": "硬體規格收集（host_spec.serial）"})

    if not sources:
        return {"value": None, "sources": [], "conflict": False,
                "note": "兩個來源都沒有收到序號——不是這台沒有序號，是沒收到。"}
    conflict = len(sources) == 2 and not _same_serial(facts, spec)
    note = None
    if conflict:
        note = ("兩個收集器拿到「不一樣」的序號，系統不替你挑邊："
                f"主機事實收集拿到 {facts}，硬體規格收集拿到 {spec}。"
                "兩個都列在下面，請以實機標籤為準。")
    elif len(sources) == 1:
        缺的 = "硬體規格收集" if facts else "主機事實收集"
        note = (f"這個值來自{sources[0]['from']}；{缺的}那一邊沒有收到。"
                "兩邊各跑各的指令，少一邊不代表這個值有問題，"
                "但也代表沒有第二個來源可以對帳。")
    return {"value": sources[0]["value"], "sources": sources,
            "conflict": conflict, "note": note}


def _virt_kind_of(hw) -> str | None:
    """這台要顯示 LPAR 還是 VM。uname -L 目前沒有獨立欄位，B-24 收到後從這裡接。"""
    import manage_state as _ms
    return _ms.virt_kind(hw.get("os"), hw.get("is_vm"), hw.get("device_model"))


def _vi_sdk_note(conn: sqlite3.Connection, hw: dict) -> str | None:
    """`vi_sdk_server` 空白時，回一句話說明為什麼空白；有值時回 None。

    判斷靠 source_record（source='vcenter'）——那是匯入時逐列留下的來源紀錄，
    能區分「沒被 RVTools 匯過」與「匯過但那份檔沒那一欄」。
    這兩件事的處理方式不同：前者要去確認這台到底在不在 vCenter 上，
    後者只要拿新版 RVTools 重匯一次就好。
    """
    if hw.get("vi_sdk_server"):
        return None
    row = conn.execute(
        "SELECT payload FROM source_record WHERE source = 'vcenter' "
        "AND resolved_hardware_id = ? ORDER BY id DESC LIMIT 1", (hw["id"],)
    ).fetchone()
    if row is None:
        return "這台不是從 RVTools/vCenter 匯進來的（手動建立、Excel 匯入或掃描發現），所以沒有來源管理端"
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    if "vi_sdk_server" in payload:
        return "這份 RVTools 匯出裡 VI SDK Server 欄是空的"
    return ("匯入時間早於這個功能（2026-08-28），所以沒記到來源管理端。"
            "拿同一份 RVTools 重匯一次就會補上，不需要重新匯出")


class OnboardBody(BaseModel):
    ip: str
    platform: str          # linux / windows
    username: str
    password: str          # ⚠️ 只用於這一次納管，處理完即丟，絕不寫任何地方


class RevokeBody(BaseModel):
    ip: str
    platform: str          # 目前只支援 linux
    username: str
    password: str          # ⚠️ 只用於這一次，處理完即丟，絕不寫任何地方
    confirm: str           # 必須是 "YES"


# ===== Push agent（2026-08-14 設計）：主機自己排程回報 disk/memory 狀態，
# collector 端完全不主動連進主機——防火牆只需要「主機 → collector 單一 port」。
# 詳見 backend/agent_scripts/README.md 的兩段式安裝流程（sysinfra 上傳＋root 排程落地）。

class AgentStageBody(BaseModel):
    asset_serial: str


class AgentFactsBody(BaseModel):
    metrics: list[dict]


class BatchProbeBody(BaseModel):
    ips: list[str]
    username: str
    passwords: list[str]   # ⚠️ 只用於這次探測，處理完即丟，絕不寫任何地方


class CredentialBody(BaseModel):
    name: str
    kind: str = "winrm"
    username: str
    password: str          # ⚠️ 加密後才進 DB，回應永不含它
    scope: str | None = None
    note: str | None = None


class OnboardVerifyBody(BaseModel):
    ip: str
    platform: str          # linux / aix / windows


def _collect_diagnosis(conn: sqlite3.Connection, ip: str, sent_platform: str,
                       os_val: str | None, updated: int, base_error: str | None) -> str:
    """收集失敗時，把「為什麼」直接組成一句人看得懂的話寫進納管紀錄。

    2026-09-22 使用者：「我不希望你提供什麼樣的資料，我會繼續錄影給你看」
    「換到正式區，如果遇到相同的問題才好排除」——**他手上只有畫面**，
    不該被叫去跑 SQL 查 hardware.os 或 onboard_audit.platform。
    所以這裡把四件事一次講完：送出平台、判定平台、實際平台、收到幾個欄位。

    AIX 被判成 Linux 的症狀是「連得上、每個欄位都空」，跟權限不足長得一模一樣。
    把 `uname -s` 的實際結果擺在判定平台旁邊，一眼就看得出是不是判錯。
    """
    import manage_state

    row = conn.execute("SELECT asset_serial FROM hardware WHERE ip = ?", (ip,)).fetchone()
    guessed = manage_state.collect_platform_of(
        conn, ip, os_val, asset_serial=row["asset_serial"] if row else None)
    actual = manage_state.probe_uname(ip)
    parts = [f"送出平台={sent_platform}", f"判定平台={guessed}",
             f"實際 uname -s={actual or '問不到'}", f"收到欄位={updated} 個"]
    msg = "收集失敗：" + "、".join(parts)
    if not (os_val or "").strip():
        msg += "。這台的 OS 欄位是空的，平台是**用開放埠猜的**"
    if actual and guessed and actual.lower() not in guessed.lower():
        msg += (f"。**判定平台與實際不符——拿了 {guessed} 的指令去問 {actual}，"
                f"所以每個欄位都空**。請在資產資料把 OS 填成 {actual}")
    elif updated == 0 and not actual:
        msg += "。連 uname -s 都問不到，可能是收集帳號連不進去（而不是平台判錯）"
    if base_error:
        msg += f"。原始錯誤：{base_error}"
    return msg


def _log_onboard(conn: sqlite3.Connection, *, hardware_ip: str, platform: str,
                 who: str | None, ok: bool, stage: str, message: str,
                 trigger: str = "verify", login_user: str | None = None) -> None:
    """把納管動作寫進 onboard_audit——**成功失敗都寫**。

    2026-09-22 使用者：「AIX 我已經納管多次，每次狀態我看都是未納管」。
    只寫成功的話，「試過三次都失敗」跟「從來沒試過」在畫面上一模一樣，
    人就只能一直重試。失敗要寫得出**原因**，不是只寫「失敗」。

    寫紀錄失敗不可以影響主流程——紀錄是為了事後看得懂，不是業務本身。
    """
    try:
        conn.execute(
            "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
            "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
            (hardware_ip, platform, login_user, trigger, who,
             1 if ok else 0, stage, message[:1000], None))
    except sqlite3.Error:
        pass


class SegmentBody(BaseModel):
    prefix: str
    enabled: bool = True
    note: str | None = None


class SegmentEnabledBody(BaseModel):
    enabled: bool


class AutoOnboardEnabledBody(BaseModel):
    enabled: bool


# ===== 收集入口收斂（決策 C4，2026-08-16）=====
# 使用者只做一個動作：貼網段或 IP 清單、按一下。選路（SSH／WinRM／Agent／只能匯入）
# 由系統依實際探測結果決定，不要人先自己判斷該點哪個按鈕——那個判斷本來就該系統做。

class CollectDispatchBody(BaseModel):
    targets: str          # 網段（10.99.1.0/24）、範圍（10.99.1.10-20）、單一 IP，可混用


class BatchOnboardBody(BaseModel):
    ips: list[str]
    username: str
    passwords: list[str]        # ⚠️ 只用於這次，處理完即丟，絕不寫任何地方
    unify_password: bool = False
    confirm: str = ""           # 勾了 unify_password 時必須是 "YES"（統一密碼會動正式資料）
    include_ips: list[str] = []  # 使用者確認過是測試機、要納入的未登記機器


class DispatchGapBody(BaseModel):
    gap_seconds: int


class AgentPackageBody(BaseModel):
    ip: str


class ScannerVisibilityBody(BaseModel):
    environments: list[str]


class SegmentNoteBody(BaseModel):
    cidr: str
    status: str = ""                 # unused／policy／pending／watch，空＝清除
    expected_hosts: int | None = None
    note: str = ""


def _model_hint(r: sqlite3.Row) -> str | None:
    """給 normalize_model() 的救援線索：device_model 認不出來時，具體型號常常
    躲在資產名稱或資產用途欄位（使用者 2026-08-13 實際發現「EMC XT480」寫在
    資產用途，不是資產名稱——兩欄都不確定會落在哪個，索性都給）。"""
    parts = [r["asset_name"], r["asset_purpose"]]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _identity_guess(r: sqlite3.Row, conn) -> tuple[str | None, str, bool]:
    """使用者 2026-08-13 重新設計：先前把「猜 OS」跟「猜型號」當兩件獨立的事，
    「未分類」清單只用了 OS 規則去猜，涵蓋率很差——實際上這批 os 查不到的資產，
    資產用途欄裡藏的多半根本是**型號**資訊（3PAR／EMC／IBM Storage 這些），
    不是 OS 版本，選錯規則庫，猜不到不是規則不夠多，是問錯規則。

    這裡統一成一支：os 規則猜不到，換型號規則（用同一套已經驗證過、涵蓋率高
    很多的 normalize_model() hint 機制）再猜一次。

    ⚠️ 使用者 2026-08-13 再次要求：一開始只在「靠 hint 猜到」時才顯示，
    device_model 本身規則就直接認得出來（method="rule"）時刻意不顯示，
    理由是「已經看得到了不用重複」——但使用者反應「不顯示就不知道系統到底
    有沒有判斷對，跟完全沒查是同一種空白」。改成不管是規則直接認出來的還是
    靠 hint 猜的都回傳，用第三個回傳值 confirmed 區分：True＝規則/別名字典
    直接對到（可信），False＝靠 hint 猜的（僅供參考）——由前端決定用不同
    樣式呈現，但兩種狀況都要看得到，不能因為「應該已經知道了」就悄悄跳過。

    回傳 (值, 種類, confirmed)，種類是 "os" 或 "model"，都沒有回 (None, "", False)。
    """
    import normalize

    os_val = r["os"]
    os_matched = bool(os_val) and normalize.normalize_os(os_val, conn, r["device_model"])["matched"]
    if os_matched:
        return None, "", False  # os 本身就查得到，不需要猜

    if r["asset_purpose"]:
        os_guess = normalize.suggest_os_canonical(r["asset_purpose"])
        if os_guess:
            return os_guess, "os", False  # suggest_os_canonical 定義上只做「猜」，不算確認

    if r["device_model"]:
        model_info = normalize.normalize_model(r["device_model"], conn, _model_hint(r))
        if model_info["method"] != "unmatched" \
                and model_info["canonical"] not in normalize._HW_VM_PRODUCTS:
            confirmed = model_info["method"] in ("rule", "alias")
            return model_info["canonical"], "model", confirmed

    return None, "", False


class OcpClusterBody(BaseModel):
    asset_serial: str
    cluster: str = ""          # 空＝清除人工指定，回到自動判斷
    reason: str = ""


class OcpRoleBody(BaseModel):
    asset_serial: str
    role: str = ""          # 空字串＝清除指定、回到自動判斷
    reason: str = ""


class ConfigAuditCollectBody(BaseModel):
    asset_serial: str


_VERSION_TAIL = re.compile(r"^(.*\d+\.\d+)\.\d+[A-Za-z0-9()]*$")


def _merge_patch_versions(items_by_canonical: dict) -> dict:
    """把只差小版本號、EOS 查詢結果完全一樣的項目合併成一列，純顯示用途——使用者
    2026-08-13 要求：畫面上一堆「Cisco Network OS 16.12」「16.12.07」看起來很雜，
    但查到的 EOS 日期/來源根本一樣，合併方便看統計。**不動資產本身**，只是把
    eos_summary() 回傳的顯示列合併，device_models/count 都照實加總。

    只有 eos_date／source_url／confidence 三個都相同才合併，避免小版本間 EOS
    其實不一樣時被誤蓋掉（例：某小版本剛好有查到延伸支援公告，其他沒有）。
    """
    groups: dict[tuple, list[dict]] = {}
    for entry in items_by_canonical.values():
        m = _VERSION_TAIL.match(entry["name"])
        base_name = m.group(1) if m else entry["name"]
        key = (base_name, entry["eos_date"], entry.get("source_url"), entry.get("confidence"))
        groups.setdefault(key, []).append(entry)

    merged: dict[str, dict] = {}
    for (base_name, _eos_date, _source_url, _confidence), group in groups.items():
        if len(group) == 1:
            merged[group[0]["name"]] = group[0]
            continue
        combined_models: list[str] = []
        seen_models: set[str] = set()
        for g in group:
            for model in g.get("device_models") or []:
                if model not in seen_models:
                    seen_models.add(model)
                    combined_models.append(model)
        merged[base_name] = {
            **group[0],
            "name": base_name,
            # [2026-09-20] 台數是 machine_key 的集合（見 eos_summary.bump_m）：合併版本時要**取聯集**，
            # 不可以相加——同一台同時有 16.12 和 16.12.07 兩筆登記，相加會把它算兩次
            "_m": set().union(*[g.get("_m") or set() for g in group]),
            "count": sum(g["count"] for g in group),
            "device_models": combined_models,
            "merged_versions": sorted({g["name"] for g in group}),
        }
    return merged


class EosCategoryOverrideBody(BaseModel):
    canonical: str
    category: str


class NormalizeAliasBody(BaseModel):
    kind: str
    raw_value: str
    canonical: str
    note: str | None = None


class CanonicalOverrideBody(BaseModel):
    kind: str
    old_canonical: str
    new_canonical: str


class AssetUpdateBody(BaseModel):
    fields: dict


class ExemptBody(BaseModel):
    serials: list[str]
    reason: str
    kind: str | None = None


class ExemptRemoveBody(BaseModel):
    serials: list[str]


class BatchStatusBody(BaseModel):
    serials: list[str]
    status: str
    reason: str
    scope: str = "single"      # [B-08] single＝只這幾筆（某服務下線）／machine＝整台


class ScopePreviewBody(BaseModel):
    serials: list[str]
    scope: str = "machine"


class CiaSyncedBody(BaseModel):
    ids: list[int]


class ProvisionInfo(BaseModel):
    """申請單來源資訊。source=direct（IT 自建）時整包可以不給。"""
    source: str = "direct"
    request_no: str | None = None
    applicant_unit: str | None = None
    applicant: str | None = None
    unit_manager: str | None = None
    form_date: str | None = None
    change_kind: str | None = None
    raw_fields: dict | None = None


class AssetCreateBody(BaseModel):
    fields: dict
    provision: ProvisionInfo | None = None


# 新資產的預設狀態：還沒過上線檢查前不是「使用中」。
# 刻意選一個不在 manage_state.RETIRED_STATUS 裡的新值——那組是「退役」語意（停用/報廢/
# 閒置），待上線是「還沒開始用」，兩者混在一起會讓報表把新機器算成退役。
PENDING_GOLIVE_STATUS = "待上線"


# 選單值上限：欄位現有值的「種類數」在這個範圍內才當成選單（避免選錯字），超過代表
# 這欄本質上是自由填寫（型號/用途/機櫃號這類），硬做成選單只會塞爆下拉選單。
_MANUAL_OPTIONS_MAX_DISTINCT = 30


# ===== 單據檔案室（既有 Word 表單的歸檔與索引）=====

def _doc_archive_dir() -> Path:
    d = get_db_path().parent / "doc_archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


class DocReviewBody(BaseModel):
    values: dict[str, str] | None = None


class DocBindBody(BaseModel):
    asset_serial: str | None = None


# ===== 機器標籤（2026-09-21 使用者：「我覺得用 tag，而且可以變更」）=====
# 起因：一台在 CIA 上有 5 筆登記、用途都是「…DB」，使用者問「看得出來為什麼放一起嗎?」
# 系統沒有地方記「為什麼放一起」——那是人才知道的事。規則見 asset_tags 模組說明。

class AssetTagBody(BaseModel):
    tag: str
    note: str = ""
    source: str = "manual"      # manual＝人自己打的；suggested＝採用系統建議


class PersonMergeBody(BaseModel):
    from_name: str
    to_name: str


# ===== 資產生命週期：申請單 → 上線前檢查 → 基線回檢 =====
# 模型見 golive.py 檔頭。這區的端點都圍著「一台資產一份檢查表」轉。

def _attachment_dir() -> Path:
    """簽核用的申請單掃描檔落地位置。放 DATA 底下跟 DB 同一層，
    才會被既有的每日備份（backup.py 備 DATA）一起帶走。"""
    d = get_db_path().parent / "provision_attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


class GoliveItemBody(BaseModel):
    item_key: str
    verdict: str


class DriftDispositionBody(BaseModel):
    status: str
    note: str | None = None


NO_IMPORT_SENTINEL = "不匯入"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


class FieldMappingBody(BaseModel):
    mapping: dict[str, dict[str, str]]


RVTOOLS_LAST_DATA_AT = "rvtools_last_data_at"   # 上次成功匯入的那份檔案的「資料時間」


class DumpImportResult(BaseModel):
    source: str
    row_count: int
    headers: list[str]


# ===== 1-4 分佈統計（2026-09-10）=====
# 使用者要一頁「系統(APID) × 環境／機房」：預設前 10 大、其餘展開，
# 點一個系統下鑽到機房，再分 AP／DB。
#
# ⚠️ 三層的證據強度不同，回傳結構刻意讓畫面沒辦法混為一談：
#   系統／環境／機房 = 登記資料
#   AP／DB          = **推論**，而且 221 實測只推得出 7%（4444 台推不出來）
# 所以 roles 一定連「未分類」一起回，role_basis 明寫「推論」。
# 把 93% 藏起來畫成漂亮的圓餅圖，比不做這個功能更糟。


class AuthDisabledBody(BaseModel):
    enabled: bool
    confirm: str | None = None


NAV_LAYOUT_SETTING = "nav_layout"


def _can_edit_nav(session: sqlite3.Row, conn: sqlite3.Connection) -> bool:
    """選單順序限管理員改。判定：登入開著時只有 admin 帳號能改；
    登入停用（測試階段）時沒有真實身分可分辨，放行（本來就是單人在測）。"""
    # 先問「登入是不是停用」再去讀 session：停用時 session 可能是 None，
    # 先讀就直接 TypeError（2026-09-20 寫匯入測試時踩到）
    if auth_disabled(conn):
        return True
    return bool(session is not None and session["username"] == "admin")


class NavLayoutBody(BaseModel):
    layout: list | None = None     # None／空＝清除覆寫、回到內建預設


def _nav_layout_of(conn) -> list | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (NAV_LAYOUT_SETTING,)).fetchone()
    if not row or not row["value"]:
        return None
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return None


def _validate_nav_layout(layout) -> list:
    """匯入前先擋明顯不對的東西——選單壞掉等於整套系統找不到路。

    只驗結構（群組要有名稱、items 是路由字串或 {to,label}），不驗路由存不存在：
    認不得的路由前端本來就會略過，擋掉反而讓跨版本搬設定失敗。
    """
    if not isinstance(layout, list) or not layout:
        raise ValueError("格式不對：最外層要是一個群組陣列")
    for g in layout:
        if not isinstance(g, dict) or not str(g.get("label") or "").strip():
            raise ValueError("格式不對：每個群組要有 label")
        items = g.get("items")
        if not isinstance(items, list):
            raise ValueError(f"格式不對：群組「{g.get('label')}」的 items 要是陣列")
        for it in items:
            if isinstance(it, str):
                continue
            if isinstance(it, dict) and str(it.get("to") or "").strip():
                continue
            raise ValueError(f"格式不對：群組「{g.get('label')}」裡有不認得的項目 {it!r}")
    return layout


class NavLayoutImportBody(BaseModel):
    kind: str | None = None
    version: int | None = None
    layout: list | None = None


class SystemClassBody(BaseModel):
    api_id: str
    sys_class: str = ""     # 第一類/第二類/第三類；空字串或「未分級」＝清除覆寫


# ===== 網段存活掃描（手動新增資產的第二種入口：整段掃、勾選要納入哪些）=====
# 只認 nmap TCP connect（-sT，不用 root）打 22/445——這兩個 port 對得上「這台機器
# 收得到 SSH 或 SMB」，是我們實際會去操作的存活定義，不是任意 ping 存活就算。
# 只掃內網私有網段，避免被拿去對外部位址做連接埠掃描。

_SCAN_PORTS = "22,445"
_SCAN_MAX_HOSTS = 1024


def _assert_private_network(network: "ipaddress.IPv4Network | ipaddress.IPv6Network") -> None:
    if not network.is_private:
        raise HTTPException(400, "只能掃內網私有網段（10/8、172.16/12、192.168/16）")


class ScanDiscoverBody(BaseModel):
    cidr: str


class ScanHostItem(BaseModel):
    ip: str
    hostname: str | None = None


class ScanImportBody(BaseModel):
    hosts: list[ScanHostItem]


# ===== 清空盤點資料（危險操作，打 ClearALL 才執行）=====

# 像 GitHub 刪 repo 那樣，要手動打這個字串才會清——防手滑。
_RESET_CONFIRM = "ClearALL"

# 只清「盤點／採集資料」。明確白名單，絕不誤清帳號/憑證/連線/設定。
# 用 defer_foreign_keys 把外鍵檢查延到 commit，全清後就一致，刪除順序不必講究。
#
# ⚠️ `onboard_audit` **刻意不在這裡**（2026-09-09 使用者問出來的）：
#
#   清空之後目標主機上的收集帳號與金鑰**還在**，是這套系統自己忘了它做過。
#   後果不只是畫面變空——「取消納管」按鈕靠這張表才會出現，紀錄沒了就再也
#   沒辦法從畫面把那些帳號收回來，它們變成沒人認領的孤兒帳號。
#   而且「誰在什麼時候、在哪台建了帳號」是**稽核軌跡**，金融業要留。
#
#   判準就是這段註解自己寫的那句：清的是「盤點／採集資料」，
#   不清「帳號／憑證／連線／設定」。納管紀錄記的是**我們對外做過的動作**，
#   屬於後者。清空再重匯之後，只要 IP 對得回來，納管狀態會自己回來。
#
# ⚠️ **順序有意義**（2026-09-09 公司機實測炸掉才知道）：子表要排在父表前面。
#   原本靠 `PRAGMA defer_foreign_keys=ON` 讓順序不重要，但**那行沒有生效**——
#   defer 只在「交易進行中」有效，而那行 PRAGMA 跑在第一個 DELETE 之前，
#   當下還沒有交易。結果外鍵當場就檢查，`DELETE FROM hardware` 直接爆
#   `FOREIGN KEY constraint failed`，整支端點回 500，畫面只顯示
#   「清空失敗，請稍後再試」——使用者完全看不出是哪裡卡住。
#
#   真正的兇手是 `ci_node`（5529 筆）指向 `hardware`，卻沒被加進這份名單。
#   那張表是後來做 CI 圖譜時新增的，加表的人沒想到清空這件事——
#   **這種漏法一定會再發生**，所以下面那條測試會掃整個 schema 擋住它
#   （`test_reset_inventory_api.py::test_任何指向清空名單的表都必須自己也在名單裡`）。
_RESET_TABLES = (
    # CI 圖譜（衍生資料，指向 hardware）——一定要排在 hardware 前面
    "ci_edge", "ci_node",
    "host_api_key", "host_metric_latest",
    "merge_review", "source_record",
    "finding_disposition", "account_finding", "account_collect_runs", "host_account",
    "service_collect_runs", "host_service",
    "software_collect_runs", "package_change", "host_package",
    "comparison_result", "scan_history", "scan_runs",
    "system_deps", "systems",
    "duplicate_dismiss",
    "software", "personnel", "hardware",
    "import_log",
)

#: 清空**絕不能**碰的表。列出來是為了讓它成為一條會被測試盯著的規則，
#: 而不是「希望以後沒人手滑加回去」。
_RESET_NEVER = ("onboard_audit", "users", "app_settings", "collector_credential")


class ResetConfirm(BaseModel):
    confirm: str


def _short_host(name: str | None) -> str:
    """FQDN 取第一段並轉大寫。

    vCenter 回報的是 FQDN（srv-abc-1234.corp.example.com），CIA 的 Excel 是人填的短名
    且慣例全大寫（SRV-ABC-1234）。兩邊指同一台機器，卻因為一個有網域後綴、一個沒有，
    在身分解析時對不上而全部落到人工審核。切掉網域再統一大小寫才比得出來。
    """
    return (name or "").strip().split(".")[0].upper()


# 可自動合併的判定規則：短名與 IP 同時相符。
# 為什麼這個組合敢自動合併：
#   · 主機名相符 → 指向同一台的可能性高，但主機名可能重複（dev/prod 同名）
#   · IP 相符   → 也高，但 IP 會被回收再指派
#   兩者「同時」相符，還要落在同一筆既有資產上，才算數。任一項不符就不碰，留給人看。
# 這不是最強識別碼（vm_uuid），所以仍然要人按一次確認鈕，只是不必逐筆按 494 次。
def _s_lower(v) -> str:
    return "" if v is None else str(v).strip().lower()


_AUTO_RULE = "short_name_and_ip"
# 第二條規則：vCenter 帶 vm_uuid、資產那邊還沒填 → 合併就是在補身分證號。
# 判定與守衛全部在 merge_autoresolve.plan()，這裡不重寫一份，否則兩邊會走鐘。
_UUID_RULE = "vcenter_uuid_backfill"


def _scan_auto_matchable(conn: sqlite3.Connection) -> list[dict]:
    """掃出「切掉網域後主機名與 IP 同時相符」的待審案，回傳配對結果。

    每次都重新從資料庫算，不信任前端傳來的 id 清單——避免畫面上看到的候選
    與實際執行時的資料已經不同（例如中間有人改了資產）。
    """
    hw = conn.execute(
        "SELECT id, asset_serial, hostname, ip FROM hardware "
        "WHERE hostname IS NOT NULL AND length(trim(hostname)) > 0 "
        "AND ip IS NOT NULL AND length(trim(ip)) > 0"
    ).fetchall()
    # (短名, IP) -> 既有資產。同鍵有多筆就整組排除：那代表既有資料本身重複，
    # 亂挑一筆合併會把資料釘在錯的那台上。
    index: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in hw:
        index.setdefault((_short_host(r["hostname"]), (r["ip"] or "").strip()), []).append(r)

    rows = conn.execute(
        "SELECT mr.id, mr.reason, sr.id AS sr_id, sr.payload FROM merge_review mr "
        "JOIN source_record sr ON sr.id = mr.source_record_id "
        "WHERE mr.status = 'open'"
    ).fetchall()

    out = []
    for r in rows:
        try:
            d = json.loads(r["payload"]) if r["payload"] else {}
        except (ValueError, TypeError):
            continue
        h = (d.get("hostname") or "").strip()
        ip = (d.get("ip") or "").strip()
        if not h or not ip:
            continue
        hits = index.get((_short_host(h), ip)) or []
        if len(hits) != 1:      # 0 筆＝對不上；>1 筆＝既有資料重複，都不自動處理
            continue
        target = hits[0]
        out.append({
            "review_id": r["id"],
            "source_record_id": r["sr_id"],
            "incoming_hostname": h,
            "incoming_ip": ip,
            "target_id": target["id"],
            "target_serial": target["asset_serial"],
            "target_hostname": target["hostname"],
            "payload": d,
        })
    return out


class BatchMergeBody(BaseModel):
    rule: str


class VcAutoImportBody(BaseModel):
    enabled: bool
    dir: str = ""
    max_age_hours: int = 36


def _serialize_connection(row: sqlite3.Row) -> dict:
    """密碼write-only：HTTP回應一律不帶password欄位本身，只給has_password布林值讓畫面
    知道「這筆有沒有設定過密碼」，不洩漏內容（S10 done_when明訂）。
    """
    d = dict(row)
    has_password = bool(d.pop("password", None))
    d["has_password"] = has_password
    return d


class ConnectionBody(BaseModel):
    name: str
    connection_type: str | None = None
    target: str
    port: int | None = None
    username: str | None = None
    password: str | None = None


class SegmentBatchBody(BaseModel):
    cidrs: list[str]


class ConnectionEnabledBody(BaseModel):
    enabled: bool


class ConnectionScanTimeBody(BaseModel):
    scan_time: str | None = None     # "HH:MM"；空／None＝清掉，回到跟全域排程一起掃


class OffsiteBody(BaseModel):
    dir: str = ""


class ExportEncryptBody(BaseModel):
    enabled: bool


class RecipientKeyBody(BaseModel):
    public_key: str


class ReportNoteBody(BaseModel):
    row_key: str
    note: str
    period: str | None = None


class ReportSnapshotBody(BaseModel):
    period: str


class ClassifySetBody(BaseModel):
    asset_serials: list[str]
    category: str | None = None   # None／空字串＝清掉分類


class CiScheduleBody(BaseModel):
    enabled: bool
    time: str


# ===== 計畫性停機評估存證快照（MICS 切片3）=====
# mode=planned 版面按下「存快照」時把當下 impact() 整包結果存證——見 blast_radius.py
# save_snapshot() docstring：圖會變，要拿得出「當初評估說不影響」是哪次算出來的。

class BlastSnapshotBody(BaseModel):
    node_id: str
    reason: str | None = None
    depth: int = 6
    # 2026-08-20 拍板：檢查清單（方案A）要能從事故模式存證，不是只有計畫性停機——
    # mode 只是存證動機的標籤（畫面上顯示用），底層一律算 incident 那份 dependents，
    # 見 save_snapshot()。
    mode: str = "incident"


def _blast_result_to_csv(conn: sqlite3.Connection, node_id: str, label: str, result: dict, header_rows: list) -> str:
    """impact() 結果轉 CSV，快照跟即時查詢共用這支。2026-08-19 使用者原話：
    「拿到這張圖第一件事就是把聯絡資料匯出來，散給每個人去盤點」——所以：
    - 受影響節點要顯示人看得懂的名字（label），不是內部 node_id
    - 應通知要帶部門，不然收到清單的人不知道找哪個單位對口
    - 查不到負責人的資產另外列一段，不能讓「不知道」悄悄從匯出檔裡消失
    """
    import blast_radius

    labels = {node_id: label}
    for h in result["dependents"]:
        labels[h["node_id"]] = blast_radius.label_for_node(conn, h["node_id"])

    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in header_rows:
        writer.writerow(row)
    writer.writerow(["查詢節點", node_id, label])
    writer.writerow([])
    writer.writerow(["受影響節點", "距離", "關係", "可信度"])
    for h in result["dependents"]:
        writer.writerow([labels.get(h["node_id"], h["node_id"]), h["depth"], h["edge_type"] or "", h["confidence"]])
    writer.writerow([])
    writer.writerow(["應通知", "部門", "電話", "角色/代理人"])
    for n in result["summary"]["notify"]:
        writer.writerow([
            n.get("name"), n.get("department") or "", n.get("phone") or "",
            n.get("role") or n.get("proxy") or "",
        ])
    if result["summary"]["unknown_owner"]:
        writer.writerow([])
        writer.writerow(["查不到負責人的資產", "資產序號"])
        for u in result["summary"]["unknown_owner"]:
            writer.writerow([u["label"], u["asset_serial"]])
    return "﻿" + buf.getvalue()  # BOM：Excel 開 UTF-8 CSV 中文不亂碼


# ===== 檢查清單（2026-08-20 拍板方案A）=====
# 事故當下要的是派工清單，不是查詢結果——攤平成「一列一個（主機,聯絡人）配對」，
# 給一個連結，全隊登入都看同一份，改狀態/寫備註就地存。見 blast_radius.py 說明。

class ChecklistCreateBody(BaseModel):
    snapshot_id: int


class ChecklistItemUpdateBody(BaseModel):
    status: str | None = None
    note: str | None = None


class FeatureFlagBody(BaseModel):
    enabled: bool


class ScheduleBody(BaseModel):
    enabled: bool
    mode: str            # daily / interval / weekly
    time: str            # HH:MM（daily／weekly 用）
    interval_hours: int  # interval 用
    weekday: int | None = None   # weekly 用：0=週一 … 6=週日


class ScanScopeBody(BaseModel):
    segment_ids: list[int]


class ScanScopePolicyBody(BaseModel):
    # 全部選填：只送要改的欄位，其餘沿用現有規則（set_policy 會 merge）
    mode: str | None = None                       # rule｜manual
    categories: list[str] | None = None           # 收哪些類別（SERVER/UAT-SERVER/OA/NETWORK…）
    environments: list[str] | None = None         # 收哪些環境（正式/測試）
    respect_recommended_exclude: bool | None = None
    force_in: list[str] | None = None             # 強制納入的 CIDR
    force_out: list[str] | None = None            # 強制排除的 CIDR


# 帳外資產（DYN-/VC-/AUTO- 開頭）的判定跟首頁／部門報告頁同一條規則，
# 這裡不重寫一份，見 system_report.OFF_BOOK_PREFIXES 的說明。
_EXPORT_OFF_BOOK_PREFIXES = {"DYN": "DYN-", "VC": "VC-", "AUTO": "AUTO-"}


def _export_source_of(asset_serial: str | None) -> str:
    s = asset_serial or ""
    for label, prefix in _EXPORT_OFF_BOOK_PREFIXES.items():
        if s.startswith(prefix):
            return label
    return "CIA"


class AdoptBody(BaseModel):
    fields: dict


# ===== M2 系統聯通圖 =====
class SystemBody(BaseModel):
    id: str
    label: str
    category: str | None = None
    domain: str | None = None
    health: str = "ok"
    is_spof: bool = False
    note: str | None = None


class SystemUpdateBody(BaseModel):
    label: str | None = None
    category: str | None = None
    domain: str | None = None
    health: str | None = None
    is_spof: bool | None = None
    note: str | None = None


class DepBody(BaseModel):
    source: str
    target: str
    dep_type: str | None = None


_HEALTH = {"ok", "warn", "err"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


# ===== M2 第一片：服務盤點（一台主機看得出什麼服務）=====

ALLOWED_SERVICE_SORT = {
    "ip", "hostname", "port", "proto", "exposure", "process", "service_guess",
    "guess_source", "last_seen", "first_seen", "asset_serial",
}


# ===== SAN switch 收集（Brocade，2026-09-13）=====
# A 版：帳密由前端當場帶進來、只在記憶體、**不儲存任何憑證**；只跑唯讀白名單指令
# （見 san_collector.READONLY_COMMANDS），不動 switch 設定＝不踩 SOC。只落「結果」供盤點/統計。
class SanCollectBody(BaseModel):
    ip: str
    username: str
    password: str


class SshProbeBody(BaseModel):
    ip: str
    port: int = 22


class AliveCheckBody(BaseModel):
    ip: str


class AliveBatchBody(BaseModel):
    serials: list[str] | None = None
    ips: list[str] | None = None
    scope: str = ""


# ===== 主機自我檢查（批次 1：免提權即時健檢，2026-09-21）=====
# 值班驗證 WhatsUp Gold 告警是誤報還是真故障：輸入 IP → 唯讀收 磁碟/記憶體/負載/
# port/服務 → 紅黃綠＋原始值當證據。只打已知目標、並發有上限、只支援 Linux（批次 1）。
_HEALTH_CONCURRENCY_DEFAULT = 8
_HEALTH_CONCURRENCY_MAX = 16          # 再高就像掃描/暴力，會惹 SOC（沿用批次納管的克制）
_HEALTH_MAX_TARGETS = 200            # 一次上限；整段要跑先由前端二次確認再送這批


class HealthCheckBody(BaseModel):
    ips: list[str]
    concurrency: int | None = None


class SanImportBody(BaseModel):
    ip: str
    transcript: str
    #: IP 防呆被擋下後，使用者確認「就是要匯到這個 IP」時才帶 true
    force: bool = False


# ===== HMC 收集（IBM Power／AIX／IBM i，2026-09-13）=====
# 同 SAN 的 A 版安全模式：帳密當場帶入、只在記憶體、不儲存；只跑唯讀查詢指令（見 hmc_collector）。
class HmcCollectBody(BaseModel):
    ip: str
    username: str
    password: str


# ===== 帳號盤點（稽核導向）=====

ALLOWED_ACCOUNT_SORT = {
    "ip", "hostname", "username", "uid", "kind", "gecos", "last_login", "pw_last_change",
    "pw_max_days", "pw_status", "is_sudoer", "authorized_keys", "asset_serial",
}


# ===== 收集金鑰：檢視與匯入 =====
# 在此之前金鑰只有一條路：deploy.sh 產一把新的。公司若已經有一把佈遍全機隊的
# 金鑰（例如巡檢用的），匯入它就省掉重新納管每一台。

class CollectorKeyBody(BaseModel):
    # 三選一的來源：貼上私鑰，或指定主機上既有的檔案路徑
    private_key: str | None = None
    public_key: str | None = None      # 留空就從私鑰導出
    from_path: str | None = None       # 私鑰不經過網路的那條路
    confirm: str                       # 必須是 "REPLACE"


# ===== 月報匯出（2026-09-07 規格、2026-09-08 動工）=====
# 系統只匯出「raw data ＋提示詞」成一個檔，人拿去給核准的 GPT 打「請分析」。
# **這裡沒有任何 AI 呼叫**——分析要的是判斷力，寫死規則做不到；而把 LLM 接進來
# 會多一條對外連線與一個資安關卡。匯出一個檔給人自己貼，成本最低、責任最清楚。

def _default_ym() -> str:
    return datetime.now().strftime("%Y-%m")


class CollectAccountBody(BaseModel):
    account: str
    collector_ip: str | None = None      # 空字串＝清掉設定、退回自動偵測
    # 收集帳號在 /etc/passwd 的備註欄。空字串＝清掉、退回中性預設。
    # ⚠️ 實際值（員工編號-姓名_部門_系統）存 DB 不進版控——這個檔會進公開 relay。
    account_comment: str | None = None


# 允許的收集身分：唯讀預設 + 已知標準管理帳號。不開放任意字串——
# 收集身分決定拿多大權限，不該讓它變成打字就能指定任意帳號的欄位。
def _allowed_collect_accounts() -> set[str]:
    import account_collector

    return {"webit3scan"} | set(account_collector.STD_MGMT_ACCOUNTS.keys())


class DispositionBody(BaseModel):
    ip: str
    username: str
    rule_id: str
    status: str
    note: str | None = None
    exempt_until: str | None = None


class AccountNoteBody(BaseModel):
    ip: str
    username: str
    note: str = ""


class AccountKindBody(BaseModel):
    ip: str
    username: str
    kind: str = ""     # 空字串＝清掉覆寫、回到程式判定


class ExcludeBody(BaseModel):
    asset_serial: str
    exclude: bool


# ===== B-19 弱點到期自動寄信 =====
# 使用者拍板：每週一、每月 1 號 08:00 寄；門檻 14/30/60 天；收件人＝弱點報告上的
# 負責人（查 AD 名單拿信箱）；月報主管收副本。**SMTP 每一個值都要能在畫面上改。**


class VulnNotifySettings(BaseModel):
    enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int | None = None
    mail_from: str | None = None
    send_time: str | None = None          # "08:00"
    buckets: list[int] | None = None      # 預設 14/30/60
    fallback_to: str | None = None        # 找不到負責人時寄給誰
    manager_cc: bool = True               # 月報給主管副本
    subject_prefix: str | None = None     # 主旨前綴（收信端做規則分類用）
    always_cc: str | None = None          # 一律副本給（窗口，逗號分隔）
    # 收件範圍：先選單位、再挑人。**兩個都空＝全部通知**（見 vuln_notify.in_scope）
    only_depts: list[str] | None = None
    only_people: list[str] | None = None


def _vn_public(cfg: dict) -> dict:
    """回給畫面的設定。**含 pass/secret 的欄位一律不回**——目前公司是免認證
    內部 relay 所以沒有密碼欄，但這種東西要在「還沒有」的時候就擋住，
    不是等有人加了密碼欄之後才想到 API 會把它吐出去。"""
    return {k: v for k, v in cfg.items()
            if not any(w in k.lower() for w in ("pass", "secret", "token"))}


# ===== OS 類型人工指定（2026-09-18）=====
# 使用者：「如果少數錯誤，我可以手動編輯 OS 類型搬移」。另存一張表，重匯 CIA 不會蓋掉
#（改資產的 OS 欄會被 excel_import._upsert 覆寫）。見 os_override.py。

class OsTypeOverrideBody(BaseModel):
    hostname: str | None = None
    ip: str | None = None
    asset_serial: str | None = None
    os_type: str
    auto_os_type: str | None = None
    reason: str | None = None


# ===== /adopt 列上的「改分類」：搬到別的頁簽（2026-10-16）=====
# 使用者在公司機 1.428.0：「我還是沒看到可以移動到其他組別的方式」。
# 資產明細頁 2026-10-13 就有搬移入口了，但他是在 /adopt 看到分類錯的，
# 不該叫他先記下 IP、再去資產明細頁一台一台找。兩邊**寫同一張表**
#（os_type_override），不另做第二套儲存。
#
# 這支只是在 os_override 外面包一層「頁簽 ↔ OS 大類」的翻譯：/adopt 的字彙是頁簽
#（儲存設備／SSH 收集…），os_override 存的是漏斗的 OS 大類。對照在
# collect_dispatch.move_choices()／os_type_for_tab()，前後端共用同一份。
#
# ⚠️ 安全邊界（使用者拍板，不可自行放寬）：搬成「SSH 收集」只影響顯示與試連
#（唯讀登入），**不會讓系統自動去那台建本機帳號**。建帳號維持要人按「⚡ 一鍵納管」，
# 而那條路的白名單 `batch_onboard_service.classify_targets` 讀的是資產登記的
# 作業系統欄，完全不看 os_type_override——tests/asset_module/test_adopt_move.py 釘住。

class AdoptMoveTarget(BaseModel):
    ip: str
    hostname: str | None = None
    asset_serial: str | None = None


class AdoptMoveBody(BaseModel):
    targets: list[AdoptMoveTarget]
    #: 目的頁簽；`None` ＝恢復機器自動判定（軟刪除那筆指定）
    tab: str | None = None
    reason: str | None = None


# ===== 帳號盤點：匯入上次盤點／範例／匯出本次（2026-09-18）=====
# 使用者：「上次盤點的，在匯入的地方做一個帳號盤點匯入／範例／及盤點後的匯出」
# 「帳號盤點格式是用這個為準，其他的類似 sudo 等算內部管理用，匯出可選標準 A-R，跟全部兩種」。
# 見 account_audit.py。

def _xlsx_response(buf, fname: str):
    from urllib.parse import quote
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})

# 拆檔後路由模組在 routers/ 子目錄，module 內 Path(__file__).parent 會指到 routers/，
# 讀同目錄 JSON/腳本會失敗。這個常數固定指向 backend 目錄（本檔就在 backend），
# 路由改用它即與拆檔前一致。
_BACKEND_DIR = Path(__file__).parent


class _RouteCollector:
    """收集 @router.<method>(...) 呼叫，稍後由 api.py 原樣轉成 app.<method>(...)。

    這樣路由就跟拆分前一樣「直接註冊在 app 上」：app.routes 是扁平的 APIRoute 列表
    （列舉 app.routes 的測試照樣數得到），且 dependency override 以 app 為來源
    （api.app.dependency_overrides 生效）。不依賴特定 FastAPI 版本 include_router 的行為。
    """
    def __init__(self):
        self.collected = []
    def __getattr__(self, method):
        def deco(*args, **kwargs):
            def wrap(func):
                self.collected.append((method, args, kwargs, func))
                return func
            return wrap
        return deco


__all__ = [
    '_BACKEND_DIR',
    '_RouteCollector',
    'csv',
    'io',
    'ipaddress',
    'json',
    'os',
    're',
    'socket',
    'sqlite3',
    'subprocess',
    'tempfile',
    'datetime',
    'Path',
    'Iterator',
    'Cookie',
    'Depends',
    'FastAPI',
    'File',
    'Form',
    'Header',
    'HTTPException',
    'Query',
    'Request',
    'Response',
    'UploadFile',
    'CORSMiddleware',
    'FileResponse',
    'JSONResponse',
    'StreamingResponse',
    'Workbook',
    'load_workbook',
    'BaseModel',
    'contextlib',
    'activity',
    'agent_auth',
    'auth',
    'onboard_engine',
    'backup',
    'cmdb_gateway',
    'golive',
    'scan_service',
    '_now_local',
    'create_connection_record',
    'get_db_path',
    'create_import_log',
    'delete_connection_record',
    'get_connection',
    'get_connection_by_id',
    'get_feature_flag',
    'get_latest_import_log',
    'insert_hardware',
    'list_connections',
    'list_import_log',
    'list_feature_flags',
    'mark_comparison_read',
    'set_connection_enabled',
    'set_feature_flag',
    'update_connection_record',
    'update_connection_status',
    'update_hardware',
    'MAPPING_PATH',
    'SHEET_CONFIG',
    'import_excel',
    'load_mapping',
    '_PROCESS_STARTED_AT',
    '_DEFAULT_DEV_ORIGINS',
    '_allowed_origins',
    '_CORS_WEB_PORT',
    '_allowed_origin_regex',
    'FIELD_GROUPS_PATH',
    'ALLOWED_ASSET_SORT',
    'ALLOWED_ISSUE_SORT',
    'ALLOWED_PERSONNEL_SORT',
    'ALLOWED_SOFTWARE_SORT',
    'get_db',
    'AUTH_DISABLED_ENV',
    'AUTH_DISABLED_SETTING',
    'auth_disabled',
    '_anonymous_session',
    'require_auth',
    'require_host_key',
    'MarkReadBody',
    'LoginBody',
    '_log_auth',
    '_latest_scan_time',
    '_scanned_alive_rows',
    '_scan_keys',
    '_row_in_keys',
    '_check_sort',
    '_COLUMN_CACHE',
    '_sortable_columns',
    'ENV_FILTER_PRESETS',
    'MarkReadBatchBody',
    '_dismissed_keys',
    '_origin_of',
    '_duplicate_groups',
    'DuplicateReleaseBody',
    'MERGE_AUDIT_SQL',
    '_ensure_merge_audit',
    'DuplicateDismissBody',
    '_onboard_block',
    'BatchLookupBody',
    '_collect_trace_of',
    '_norm_serial',
    '_same_serial',
    '_serial_view',
    '_virt_kind_of',
    '_vi_sdk_note',
    'OnboardBody',
    'RevokeBody',
    'AgentStageBody',
    'AgentFactsBody',
    'BatchProbeBody',
    'CredentialBody',
    'OnboardVerifyBody',
    '_collect_diagnosis',
    '_log_onboard',
    'SegmentBody',
    'SegmentEnabledBody',
    'AutoOnboardEnabledBody',
    'CollectDispatchBody',
    'BatchOnboardBody',
    'DispatchGapBody',
    'AgentPackageBody',
    'ScannerVisibilityBody',
    'SegmentNoteBody',
    '_model_hint',
    '_identity_guess',
    'OcpClusterBody',
    'OcpRoleBody',
    'ConfigAuditCollectBody',
    '_VERSION_TAIL',
    '_merge_patch_versions',
    'EosCategoryOverrideBody',
    'NormalizeAliasBody',
    'CanonicalOverrideBody',
    'AssetUpdateBody',
    'ExemptBody',
    'ExemptRemoveBody',
    'BatchStatusBody',
    'ScopePreviewBody',
    'CiaSyncedBody',
    'ProvisionInfo',
    'AssetCreateBody',
    'PENDING_GOLIVE_STATUS',
    '_MANUAL_OPTIONS_MAX_DISTINCT',
    '_doc_archive_dir',
    'DocReviewBody',
    'DocBindBody',
    'AssetTagBody',
    'PersonMergeBody',
    '_attachment_dir',
    'GoliveItemBody',
    'DriftDispositionBody',
    'NO_IMPORT_SENTINEL',
    '_table_columns',
    'FieldMappingBody',
    'RVTOOLS_LAST_DATA_AT',
    'DumpImportResult',
    'AuthDisabledBody',
    'NAV_LAYOUT_SETTING',
    '_can_edit_nav',
    'NavLayoutBody',
    '_nav_layout_of',
    '_validate_nav_layout',
    'NavLayoutImportBody',
    'SystemClassBody',
    '_SCAN_PORTS',
    '_SCAN_MAX_HOSTS',
    '_assert_private_network',
    'ScanDiscoverBody',
    'ScanHostItem',
    'ScanImportBody',
    '_RESET_CONFIRM',
    '_RESET_TABLES',
    '_RESET_NEVER',
    'ResetConfirm',
    '_short_host',
    '_s_lower',
    '_AUTO_RULE',
    '_UUID_RULE',
    '_scan_auto_matchable',
    'BatchMergeBody',
    'VcAutoImportBody',
    '_serialize_connection',
    'ConnectionBody',
    'SegmentBatchBody',
    'ConnectionEnabledBody',
    'ConnectionScanTimeBody',
    'OffsiteBody',
    'ExportEncryptBody',
    'RecipientKeyBody',
    'ReportNoteBody',
    'ReportSnapshotBody',
    'ClassifySetBody',
    'CiScheduleBody',
    'BlastSnapshotBody',
    '_blast_result_to_csv',
    'ChecklistCreateBody',
    'ChecklistItemUpdateBody',
    'FeatureFlagBody',
    'ScheduleBody',
    'ScanScopeBody',
    'ScanScopePolicyBody',
    '_EXPORT_OFF_BOOK_PREFIXES',
    '_export_source_of',
    'AdoptBody',
    'SystemBody',
    'SystemUpdateBody',
    'DepBody',
    '_HEALTH',
    '_ID_RE',
    'ALLOWED_SERVICE_SORT',
    'SanCollectBody',
    'SshProbeBody',
    'AliveCheckBody',
    'AliveBatchBody',
    '_HEALTH_CONCURRENCY_DEFAULT',
    '_HEALTH_CONCURRENCY_MAX',
    '_HEALTH_MAX_TARGETS',
    'HealthCheckBody',
    'SanImportBody',
    'HmcCollectBody',
    'ALLOWED_ACCOUNT_SORT',
    'CollectorKeyBody',
    '_default_ym',
    'CollectAccountBody',
    '_allowed_collect_accounts',
    'DispositionBody',
    'AccountNoteBody',
    'AccountKindBody',
    'ExcludeBody',
    'VulnNotifySettings',
    '_vn_public',
    'OsTypeOverrideBody',
    'AdoptMoveTarget',
    'AdoptMoveBody',
    '_xlsx_response',
]
