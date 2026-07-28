"""S5：後端 REST API 層（D29 API優先設計）。

只做資料存取／整合既有模組（db／scanner／comparison_engine），不重複實作比對或掃描邏輯——
這層單純把既有函式包成 HTTP 介面，供 Nuxt3 前端（S7-S9）與之後其他模組重用（D29 note：
系統拓撲模組可直接呼叫這裡的 API 重用資料，不用重新收集）。

sort_by／order 一律經白名單檢查才拼進 SQL ORDER BY，避免使用者輸入直接串進查詢字串。
"""
from __future__ import annotations

import io
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

from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel

import auth
import backup
import cmdb_gateway
import scan_service
from db import (
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
    list_feature_flags,
    mark_comparison_read,
    set_connection_enabled,
    set_feature_flag,
    update_connection_record,
    update_connection_status,
    update_hardware,
)
from excel_import import MAPPING_PATH, SHEET_CONFIG, import_excel, load_mapping

app = FastAPI(title="資產盤點模組 API")

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,  # 帶cookie（session_token）必須開這個，前端fetch才要用credentials:"include"
    allow_methods=["*"],
    allow_headers=["*"],
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


def require_auth(
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
        raise HTTPException(401, "未登入或登入已過期")
    return session


class MarkReadBody(BaseModel):
    is_read: bool = True


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginBody, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    user = auth.authenticate(conn, body.username, body.password)
    if user is None:
        # 帳號不存在／密碼錯誤都回同一句話，不透露「帳號不存在」讓人拿去枚舉帳號
        raise HTTPException(401, "帳號或密碼錯誤")
    token, _expires_at = auth.issue_session(conn, user["id"])
    response.set_cookie(
        key=auth.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 部署目標是內網IP走http（D11/D18已定案的內網簡化精神），改https要記得打開
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    return {"username": user["username"]}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session_token: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
):
    if session_token:
        from db import delete_session

        delete_session(conn, session_token)
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/auth/me")
def me(session: sqlite3.Row = Depends(require_auth)):
    return {"username": session["username"]}


# ===== ICA(登記) × 掃描 的比對：儀表板磚塊與各個下鑽清單共用同一套判定 =====
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


@app.get("/api/dashboard/stats")
def dashboard_stats(
    environment: str = "正式",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """D25：環境篩選預設「正式」。重疊圖三區塊：僅ICA登記／重疊／僅掃描到（=漏登記候選）。"""
    if environment not in ENV_FILTER_PRESETS:
        raise HTTPException(400, f"不支援的環境篩選：{environment}")
    env_list = ENV_FILTER_PRESETS[environment]

    if env_list is None:
        ica_rows = conn.execute("SELECT ip, hostname FROM hardware").fetchall()
    else:
        placeholders = ",".join("?" for _ in env_list)
        ica_rows = conn.execute(
            f"SELECT ip, hostname FROM hardware WHERE environment IN ({placeholders})",
            env_list,
        ).fetchall()
    ica_ips = {r["ip"] for r in ica_rows if r["ip"]}
    ica_hostnames = {r["hostname"] for r in ica_rows if r["hostname"]}

    last_scan_time = _latest_scan_time(conn)
    scanned_rows = _scanned_alive_rows(conn, last_scan_time)
    failed_segments: list[str] = []
    if last_scan_time:
        failed_segments = [
            r["segment"]
            for r in conn.execute(
                "SELECT DISTINCT segment FROM scan_history WHERE scan_time = ? AND scan_ok = 0",
                (last_scan_time,),
            ).fetchall()
        ]

    scan_ips, scan_hostnames = _scan_keys(scanned_rows)

    # 「相符」從**資產側**數：有幾台登記的資產這次掃得到。
    # 原本是從掃描側數（幾筆掃描結果對得上 ICA），再用 ica_count - overlap 反推「登記卻掃不到」——
    # 一旦一台資產被多筆掃描結果對到（同機多 IP／IP 與主機名分別命中），那個減法就會少算甚至變負數。
    # 現在每個數字都可以點進去看清單，磚塊上的數字必須跟清單筆數一致，所以改成各自獨立算：
    #   overlap   = 登記且掃得到（資產側）      -> /assets?scan_status=overlap
    #   ica_only  = 登記但掃不到（資產側）      -> /assets?scan_status=ica_only
    #   scan_only = 掃到但沒登記（掃描側）      -> /adopt（與 /api/scan/unregistered 同一套判定）
    overlap_count = sum(1 for r in ica_rows if _row_in_keys(r, scan_ips, scan_hostnames))
    ica_only_count = len(ica_rows) - overlap_count

    # ⚠️ scan_only 要對「全部」資產比，不能只對環境篩選後的子集：
    # 「掃到卻沒登記」的意思是「這台機器在網路上，但整個資產清單裡都沒有它」。
    # 一台登記為「測試」環境的機器是有登記的，只是不屬於目前檢視的環境——
    # 拿環境子集去比，會把它算成未登記，磚塊顯示 4 但點進去的 /adopt（不分環境）只有 3。
    # 數字對不上的下鑽比不能點更糟，實測踩到過。
    all_ica_rows = conn.execute("SELECT ip, hostname FROM hardware").fetchall()
    all_ica_ips = {r["ip"] for r in all_ica_rows if r["ip"]}
    all_ica_hostnames = {r["hostname"] for r in all_ica_rows if r["hostname"]}
    scan_only_count = sum(
        1 for r in scanned_rows if not _row_in_keys(r, all_ica_ips, all_ica_hostnames)
    )

    # 一致率專用的「全站」數字：不受環境下拉影響。
    # 理由：一致率的分母要含「掃到卻沒登記」，而那些機器**不屬於任何環境**（沒登記過，
    # 哪來的環境欄位）。拿環境篩選過的登記數去配全站的未登記數，兩邊尺規不同，
    # 換個環境分數就跳動，但實際盤點狀況根本沒變。頭條數字要能一眼比較，所以固定全站。
    total_overlap_count = sum(
        1 for r in all_ica_rows if _row_in_keys(r, scan_ips, scan_hostnames)
    )

    issue_counts = {"異常新增": 0, "異常消失": 0, "漏登記": 0}
    for row in conn.execute(
        "SELECT issue_type, COUNT(*) AS c FROM comparison_result WHERE is_read = 0 GROUP BY issue_type"
    ).fetchall():
        issue_counts[row["issue_type"]] = row["c"]

    return {
        "environment": environment,
        "ica_count": len(ica_rows),
        "scanned_count": len(scanned_rows),
        "overlap_count": overlap_count,
        "ica_only_count": ica_only_count,
        "scan_only_count": scan_only_count,
        # 一致率用這兩個（全站、不隨環境變動），別拿上面那組環境篩選過的去算
        "total_ica_count": len(all_ica_rows),
        "total_overlap_count": total_overlap_count,
        "last_scan_time": last_scan_time,
        "last_scan_ok": len(failed_segments) == 0,
        "failed_segments": failed_segments,
        "issue_counts": issue_counts,
    }


@app.get("/api/issues")
def list_issues(
    issue_type: str | None = None,
    is_read: bool | None = None,
    sort_by: str = "detected_at",
    order: str = "desc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_sort(sort_by, order, ALLOWED_ISSUE_SORT)

    query = "SELECT * FROM comparison_result WHERE 1=1"
    params: list = []
    if issue_type is not None:
        query += " AND issue_type = ?"
        params.append(issue_type)
    if is_read is not None:
        query += " AND is_read = ?"
        params.append(int(is_read))
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@app.patch("/api/issues/{issue_id}")
def mark_issue_read(
    issue_id: int,
    body: MarkReadBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """標記已處理（契約已定案功能）。目前只支援標記為已讀，不支援改回未讀。"""
    if not body.is_read:
        raise HTTPException(400, "目前只支援標記已處理（is_read=true）")
    existing = conn.execute(
        "SELECT id FROM comparison_result WHERE id = ?", (issue_id,)
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "查無此問題紀錄")
    mark_comparison_read(conn, issue_id)
    row = conn.execute("SELECT * FROM comparison_result WHERE id = ?", (issue_id,)).fetchone()
    return dict(row)


@app.get("/api/assets/field-groups")
def asset_field_groups(session: sqlite3.Row = Depends(require_auth)):
    """D31精神延伸：常用/進階分層來自設定檔，前端不用自己硬編欄位清單。"""
    return json.loads(FIELD_GROUPS_PATH.read_text(encoding="utf-8"))


@app.get("/api/assets")
def list_assets(
    response: Response,
    environment: str | None = None,
    q: str | None = None,
    scan_status: str | None = None,
    filter_field: str | None = None,
    filter_value: str | None = None,
    virtual: str | None = None,
    platform: str | None = None,
    role: str | None = None,
    sort_by: str = "hostname",
    order: str = "asc",
    limit: int | None = None,
    offset: int = 0,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """scan_status：讓儀表板的「兩邊相符 / 登記卻掃不到」磚塊可以點進來看是哪幾台。
    判定與 /api/dashboard/stats 共用 _row_in_keys，數字才會跟磚塊對得起來。

    environment 這裡吃單一環境值；儀表板用的是 ENV_FILTER_PRESETS（正式／正式+測試／全部），
    前端下鑽時會把 preset 展開成對應的單一環境或不帶此參數。
    """
    _check_sort(sort_by, order, ALLOWED_ASSET_SORT | _sortable_columns(conn, "hardware"))

    if scan_status is not None and scan_status not in ("overlap", "ica_only"):
        raise HTTPException(400, "scan_status 只接受 overlap（登記且掃得到）或 ica_only（登記但掃不到）")

    query = "SELECT * FROM hardware WHERE 1=1"
    params: list = []
    if environment is not None:
        # 同時吃「單一環境值」與儀表板用的 preset（正式／正式+測試／全部）。
        # 儀表板磚塊要能原封不動把目前的環境選擇帶進下鑽，否則點進來的筆數會跟磚塊對不上。
        if environment in ENV_FILTER_PRESETS:
            env_list = ENV_FILTER_PRESETS[environment]
            if env_list is not None:  # None＝全部，不加條件
                placeholders = ",".join("?" for _ in env_list)
                query += f" AND environment IN ({placeholders})"
                params.extend(env_list)
        else:
            query += " AND environment = ?"
            params.append(environment)
    # .strip()：使用者常從 Excel/終端機複製貼上，尾隨空白會讓搜尋直接 0 筆，
    # 看起來像「查無此主機」，其實只是多了一個空格——這是實測踩到的體感問題。
    if q and q.strip():
        query += " AND (hostname LIKE ? OR ip LIKE ? OR asset_serial LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    # 「同值篩選」：畫面上任何一個類別型的值（設備機型／群組名稱／保管者…）都要能點進來
    # 看「還有誰跟它一樣」。欄位名走真實欄位白名單，值走參數化，不會有字串拼進 SQL。
    if filter_field is not None:
        if filter_field not in _sortable_columns(conn, "hardware"):
            raise HTTPException(400, f"不支援的篩選欄位：{filter_field}")
        if filter_value in (None, ""):
            # 點「空值」也要能看——「哪些機器這欄是空的」本身就是有用的盤點問題
            query += f" AND ({filter_field} IS NULL OR {filter_field} = '')"
        else:
            query += f" AND {filter_field} = ?"
            params.append(filter_value)
    # 虛擬／實體篩選：is_vm 在資料裡混了 0/1 與 'VM' 字串（納管表單存字串），
    # 不能直接用 filter_field 比值。這裡統一判定：非空且不是 0/none 就算虛擬機。
    if virtual in ("yes", "no"):
        want_vm = virtual == "yes"
        vm_true = "(is_vm IS NOT NULL AND LOWER(CAST(is_vm AS TEXT)) NOT IN ('0','','none','false','否'))"
        query += f" AND {vm_true}" if want_vm else f" AND NOT {vm_true}"
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()

    if scan_status is not None:
        # 掃描比對用 Python 做而不是塞進 SQL：判定是「IP 或主機名稱任一命中」，
        # 跟 /api/scan/unregistered、儀表板同一套；寫成 SQL 反而會分岔成兩套規則。
        scanned = _scanned_alive_rows(conn, _latest_scan_time(conn))
        ips, hostnames = _scan_keys(scanned)
        want_matched = scan_status == "overlap"
        rows = [r for r in rows if _row_in_keys(r, ips, hostnames) == want_matched]

    # 平台／角色篩選。兩者都在 Python 端做，理由跟 scan_status 一樣：
    # 平台判定是「真 OS > 掃描推測 > 機型」的優先序邏輯，角色來自 host_service 的
    # 監聽埠——都不是 hardware 表上的單一欄位，硬塞進 SQL 會分岔成兩套規則。
    if platform or role:
        import asset_classify

        cls = asset_classify.classify_all(conn)
        if platform:
            wanted = {p.strip() for p in platform.split(",") if p.strip()}
            rows = [r for r in rows if cls.get(r["asset_serial"], {}).get("platform") in wanted]
        if role:
            wanted_r = {x.strip() for x in role.split(",") if x.strip()}
            rows = [r for r in rows
                    if wanted_r & set(cls.get(r["asset_serial"], {}).get("roles", ["unknown"]))]

    # 分頁：不帶 limit 時行為與以前完全相同（回全部、純陣列），既有呼叫端不受影響。
    # 總筆數放在 X-Total-Count 標頭而不是包成 {items, total}：改回應結構會弄壞所有
    # 現有前端；標頭是加法，不是改法。
    total = len(rows)
    if limit is not None:
        if limit < 1 or limit > 1000:
            raise HTTPException(400, "limit 只接受 1–1000")
        if offset < 0:
            raise HTTPException(400, "offset 不能是負數")
        rows = rows[offset:offset + limit]
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    # 平台／角色一併回傳（加欄位是加法，不動既有結構）。分類在分頁之後才算，
    # 只算這一頁的那幾十筆，不用為了顯示兩個標籤把全表都跑一遍。
    import asset_classify

    cls = asset_classify.classify_all(conn) if rows else {}
    out = []
    for r in rows:
        d = dict(r)
        c = cls.get(r["asset_serial"], {})
        d["platform"] = c.get("platform", "未知")
        d["roles"] = c.get("roles", ["unknown"])
        out.append(d)
    return out


class BatchLookupBody(BaseModel):
    terms: list[str]


@app.post("/api/assets/batch-lookup")
def batch_lookup_assets(
    body: BatchLookupBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """值班用批次查詢（貼上多筆主機名稱/IP，一行一筆）：精確比對，不是like模糊搜尋。"""
    terms = [t.strip() for t in body.terms if t.strip()]
    if not terms:
        return []
    placeholders = ",".join("?" for _ in terms)
    rows = conn.execute(
        f"SELECT * FROM hardware WHERE hostname IN ({placeholders}) OR ip IN ({placeholders})",
        terms + terms,
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/personnel")
def list_personnel(
    q: str | None = None,
    sort_by: str = "person_name",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_sort(sort_by, order, ALLOWED_PERSONNEL_SORT | _sortable_columns(conn, "personnel"))

    query = "SELECT * FROM personnel WHERE 1=1"
    params: list = []
    if q and q.strip():
        query += " AND (person_name LIKE ? OR asset_serial LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like])
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/software")
def list_software(
    q: str | None = None,
    sort_by: str = "asset_name",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_sort(sort_by, order, ALLOWED_SOFTWARE_SORT | _sortable_columns(conn, "software"))

    query = "SELECT * FROM software WHERE 1=1"
    params: list = []
    if q and q.strip():
        query += " AND (asset_name LIKE ? OR hostname LIKE ? OR ip LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/assets/{asset_serial}")
def asset_detail(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """主機詳細頁：D24分層邏輯不外露給使用者（見D31）——這裡回傳全部欄位，
    常用/進階顯示由前端依 field-groups 設定檔決定，不是後端替使用者過濾資料。
    """
    hardware = conn.execute(
        "SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    if hardware is None:
        raise HTTPException(404, "查無此資產")

    personnel = conn.execute(
        "SELECT * FROM personnel WHERE asset_serial = ?", (asset_serial,)
    ).fetchall()
    software = conn.execute(
        "SELECT * FROM software WHERE asset_serial = ?", (asset_serial,)
    ).fetchall()
    history: list[sqlite3.Row] = []
    if hardware["hostname"]:
        history = conn.execute(
            "SELECT * FROM comparison_result WHERE hostname = ? ORDER BY detected_at DESC",
            (hardware["hostname"],),
        ).fetchall()

    return {
        "hardware": dict(hardware),
        "personnel": [dict(r) for r in personnel],
        "software": [dict(r) for r in software],
        "history": [dict(r) for r in history],
    }


class OnboardBody(BaseModel):
    ip: str
    platform: str          # linux / windows
    username: str
    password: str          # ⚠️ 只用於這一次納管，處理完即丟，絕不寫任何地方


@app.post("/api/onboard")
def onboard_endpoint(
    body: OnboardBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """A：UI 一鍵納管。使用者在畫面輸入 sysctl 帳密 → 系統用它去目標機建 webit3scan。

    ⚠️ 憑證不落地是寫死的底線：body.password 只傳給引擎、用完即丟——
    不寫 DB、不寫 log、不進稽核紀錄。稽核只記帳號名、平台、成敗，不記密碼。
    """
    import onboard_engine

    collector_ip = os.environ.get("ASSET_COLLECTOR_IP", "YOUR_SERVER_IP")
    result = onboard_engine.onboard(
        host=body.ip, platform=body.platform,
        username=body.username, password=body.password,   # 用完即丟，下面不再引用
        collector_ip=collector_ip,
    )
    # 稽核：明確只寫無憑證的欄位。body.password 到這裡就結束生命週期，不進 SQL。
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
        "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (body.ip, body.platform, body.username, "manual", session["username"],
         1 if result.ok else 0, result.stage, result.message, result.output),
    )
    conn.commit()

    # 納管成功後：
    # 1) 「發現但沒登記」的主機要先補一筆最小資產，否則收到的 facts 無處可落。
    #    只填系統知道的（IP／掃到的主機名／預設環境），業務欄位留空給人補。
    # 2) 試連＋收 facts，讓使用者立刻看到狀態變「已納管」＋真 OS/序號進來。
    if result.ok:
        try:
            import manage_state
            exists = conn.execute("SELECT 1 FROM hardware WHERE ip = ?", (body.ip,)).fetchone()
            if not exists:
                scan_hn = conn.execute(
                    "SELECT hostname FROM scan_history WHERE ip = ? AND scan_ok = 1 "
                    "ORDER BY scan_time DESC LIMIT 1", (body.ip,)).fetchone()
                insert_hardware(
                    conn, asset_serial=f"AUTO-{body.ip}", ip=body.ip,
                    hostname=(scan_hn["hostname"] if scan_hn and scan_hn["hostname"] else None),
                    environment="正式", asset_status="使用中",
                )
            manage_state.refresh_collect_status(conn)
            manage_state.collect_facts_into_assets(conn)
        except Exception:  # noqa: BLE001 - 收集失敗不影響納管本身已完成的事實
            pass

    return {"ok": result.ok, "stage": result.stage, "message": result.message,
            "output": result.output}


@app.get("/api/onboard/hint")
def onboard_hint(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """納管前的平台研判——**即時探測，不信登記值**。

    為什麼不用 hardware.os：登記的 OS 可能過時、可能是假資料（實例：.101 登記成
    Ubuntu，實際 banner 是 OpenSSH_for_Windows）。拿錯平台的腳本去打，一定失敗。
    這裡即時抓 SSH banner（不需帳密），banner 自報最準；並回報跟登記值是否衝突，
    讓使用者看得到「畫面說 Ubuntu 但實測是 Windows」這種矛盾。
    """
    import fingerprint

    banner = fingerprint.grab_banner(ip, 22)
    scan = conn.execute(
        "SELECT open_ports, os_guess FROM scan_history WHERE ip = ? AND scan_ok = 1 "
        "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
    ports = [int(p) for p in (scan["open_ports"] or "").split(",") if p.strip().isdigit()] \
        if scan else []
    os_guess = scan["os_guess"] if scan else None
    method = fingerprint.onboard_method(os_guess=os_guess, open_ports=ports, banner=banner)

    registered = conn.execute("SELECT os FROM hardware WHERE ip = ?", (ip,)).fetchone()
    registered_os = registered["os"] if registered else None
    # 衝突：登記說 Linux 但實測 Windows（或反過來）
    conflict = None
    if registered_os and method["method"] in ("linux", "windows"):
        reg_is_win = any(k in registered_os.lower() for k in ("windows", "microsoft"))
        detected_win = method["method"] == "windows"
        if reg_is_win != detected_win:
            conflict = f"登記為「{registered_os}」，但實測是 {'Windows' if detected_win else 'Linux'}"

    return {
        "ip": ip,
        "platform": method["method"] if method["method"] in ("linux", "windows") else "",
        "confidence": method["confidence"],
        "evidence": method["evidence"],
        "banner": banner,
        "registered_os": registered_os,
        "conflict": conflict,
    }


class CredentialBody(BaseModel):
    name: str
    kind: str = "winrm"
    username: str
    password: str          # ⚠️ 加密後才進 DB，回應永不含它
    scope: str | None = None
    note: str | None = None


@app.get("/api/credentials")
def list_credentials(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """列出收集用憑證——**永不含密碼，連密文都不給**。"""
    import credential_store

    return credential_store.list_public(conn)


@app.post("/api/credentials")
def save_credential(
    body: CredentialBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """新增／更新收集用憑證（如 Windows WinRM 服務帳號）。

    密碼加密後才進 DB，加密金鑰另存 0600 檔案（不在 DB 裡——DB 會被備份、被複製，
    金鑰跟著走就等於沒加密）。回應只回 metadata。
    """
    import credential_store

    credential_store.save(conn, body.name, body.kind, body.username, body.password,
                          scope=body.scope, note=body.note)
    return {"ok": True, "credentials": credential_store.list_public(conn)}


@app.delete("/api/credentials/{name}")
def delete_credential(
    name: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import credential_store

    n = credential_store.delete(conn, name)
    if not n:
        raise HTTPException(404, "查無此憑證")
    return {"ok": True}


@app.get("/api/onboard/script")
def onboard_script(
    platform: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """產生「在目標機本機自己跑」的一行指令。

    為什麼需要這條路：遠端納管要目標機的管理員帳密，但很多情況拿不到——
    Windows 沒有維運帳號慣例、單機環境沒網域、或人就坐在那台機器前面（根本不用遠端）。
    這時給他一行可貼的指令最實際，且**完全不需要任何密碼**。
    腳本內容與遠端用的完全同一份（同一個引擎產出），不會有兩套行為分岔。
    """
    import base64

    import onboard_engine

    if platform not in ("linux", "windows"):
        raise HTTPException(400, "platform 只接受 linux 或 windows")
    collector_ip = os.environ.get("ASSET_COLLECTOR_IP", "YOUR_SERVER_IP")
    script = onboard_engine.build_script(platform, onboard_engine.collector_pubkey(), collector_ip)
    b64 = base64.b64encode(script.encode()).decode()

    if platform == "linux":
        cmd = f"echo '{b64}' | base64 -d | sudo bash"
        note = "在該機器以 root（或可 sudo 的帳號）執行"
    else:
        cmd = (f"$s='{b64}'; $f=\"$env:TEMP\\wb.ps1\"; "
               f"[IO.File]::WriteAllText($f,"
               f"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)),"
               f"(New-Object Text.UTF8Encoding $true)); "
               f"powershell -ExecutionPolicy Bypass -File $f")
        note = "在該機器開「系統管理員 PowerShell」執行"
    return {"platform": platform, "command": cmd, "note": note}


@app.get("/api/onboard/audit")
def onboard_audit_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """納管稽核紀錄（不含任何憑證）。"""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM onboard_audit ORDER BY id DESC LIMIT 100")]


class SegmentBody(BaseModel):
    prefix: str
    enabled: bool = True
    note: str | None = None


class SegmentEnabledBody(BaseModel):
    enabled: bool


class AutoOnboardEnabledBody(BaseModel):
    enabled: bool


@app.get("/api/auto-onboard")
def auto_onboard_state(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """B 排程自動納管的現況：總開關、授權網段、最近的自動納管稽核。

    授權網段是這功能的安全閘門——排程只碰列在這裡且啟用的網段前綴。稽核只含帳號名/成敗，
    永不含密碼。
    """
    import auto_onboard

    return {
        "enabled": auto_onboard.is_enabled(conn),
        "segments": auto_onboard.list_segments(conn),
        "recent": auto_onboard.recent_auto_audit(conn, 50),
    }


@app.patch("/api/auto-onboard/enabled")
def auto_onboard_set_enabled(
    body: AutoOnboardEnabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """總開關。關閉＝排程不會自動納管任何機器（預設關閉）。

    這只擋「無人在場的排程」；就算開著，也只碰授權網段內的主機。
    """
    import auto_onboard

    auto_onboard.set_enabled(conn, body.enabled)
    return {"ok": True, "enabled": auto_onboard.is_enabled(conn)}


@app.post("/api/auto-onboard/segments")
def auto_onboard_save_segment(
    body: SegmentBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """新增／更新一個授權網段（以前綴為唯一鍵）。加進來才代表「授權排程自動納管這段」。"""
    import auto_onboard

    try:
        auto_onboard.save_segment(conn, body.prefix, body.enabled, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "segments": auto_onboard.list_segments(conn)}


@app.patch("/api/auto-onboard/segments/{seg_id}/enabled")
def auto_onboard_toggle_segment(
    seg_id: int,
    body: SegmentEnabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """啟用／停用一個授權網段。停用＝排程跳過但保留設定。"""
    import auto_onboard

    if not auto_onboard.set_segment_enabled(conn, seg_id, body.enabled):
        raise HTTPException(404, "查無此授權網段")
    return {"ok": True, "segments": auto_onboard.list_segments(conn)}


@app.delete("/api/auto-onboard/segments/{seg_id}")
def auto_onboard_delete_segment(
    seg_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import auto_onboard

    if not auto_onboard.delete_segment(conn, seg_id):
        raise HTTPException(404, "查無此授權網段")
    return {"ok": True, "segments": auto_onboard.list_segments(conn)}


@app.post("/api/auto-onboard/run")
def auto_onboard_run_now(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動「立即執行」一輪自動納管：試連 → 納管授權網段內未納管的 Linux → 收 facts。

    操作者在場的手動觸發，不受總開關約束，但仍受授權網段約束（沒授權就沒候選）。
    會花數秒到數十秒（每台一次試連／SSH），前端要顯示執行中三態。
    """
    import auto_onboard

    collector_ip = os.environ.get("ASSET_COLLECTOR_IP", "YOUR_SERVER_IP")
    return auto_onboard.scheduled_cycle(collector_ip=collector_ip, conn=conn)


@app.get("/api/manage-state")
def manage_state_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """納管四態：未登記／未納管／已納管／失聯。互斥且窮盡，加總＝你知道的所有機器。

    跟 asset_status 是兩條獨立的軸——一台可以同時「使用中」而且「連不進去」。
    儀表板四格與資產清單的狀態欄共用這一份，數字才不會跟清單對不上。
    """
    import manage_state

    return manage_state.summarize(conn)


@app.get("/api/diagnostics")
def diagnostics_endpoint(
    note: str = "",
    desensitize: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """萬用診斷包：出問題時一個動作產出「足以判斷問題在哪」的去識別化資料。

    不綁任何單一功能——核心負責環境快照/去識別化/打包，各功能自己註冊一段
    （見 diagnostics.register）。新功能加一個函式就會自動被收進來。

    desensitize 預設 True：公司資料不出這台機器是硬規則，要關掉必須明確指定。
    """
    import diagnostics as dg
    # 確保各功能的診斷外掛都已註冊（模組載入時才會 register）
    import auto_onboard  # noqa: F401
    import identity  # noqa: F401
    import manage_state  # noqa: F401
    import normalize  # noqa: F401
    import vcenter_autoimport  # noqa: F401

    return dg.collect(conn, note=note, desensitize=desensitize)


@app.get("/api/dashboard/composition")
def dashboard_composition(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """全站組成統計：我的機器長什麼樣子（各平台幾台、虛實、環境、狀態）。

    這是儀表板該回答的問題——「有幾台 Windows」是統計；
    「兩邊相符／登記卻掃不到」是對帳細節，屬於小功能，不該佔戰情室頭條。
    """
    import manage_state

    return manage_state.composition(conn)


@app.post("/api/manage-state/collect")
def manage_state_collect(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """對已納管的機器收 facts 並寫回資產（真 OS／序號／機型）。"""
    import manage_state

    return manage_state.collect_facts_into_assets(conn)


@app.post("/api/manage-state/refresh")
def manage_state_refresh(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動觸發「試連所有已登記主機」。會花數秒到數十秒（每台一次 SSH）。"""
    import manage_state

    return manage_state.refresh_collect_status(conn)


class AssetUpdateBody(BaseModel):
    fields: dict


@app.put("/api/assets/{asset_serial}")
def update_asset(
    asset_serial: str,
    body: AssetUpdateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """修改一台資產的欄位。

    在這之前系統**完全沒有編輯功能**——資料一旦進來就只能靠重新匯入 Excel 覆蓋，
    連打錯一個字都要重跑匯入。盤點系統的資料本來就會被持續修正，這是必要缺口。

    只接受 hardware 真實存在的欄位（擋掉亂塞）；asset_serial 是主鍵不給改
    （改序號等於換一台，會弄丟 personnel/software 的關聯）。空字串一律存成 NULL，
    否則「空」會有 '' 和 NULL 兩種寫法，篩選和排序就會分岔。
    """
    if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone() is None:
        raise HTTPException(404, "查無此資產")

    cols = _sortable_columns(conn, "hardware")
    unknown = [k for k in body.fields if k not in cols]
    if unknown:
        raise HTTPException(400, f"不支援的欄位：{', '.join(sorted(unknown))}")

    cleaned = {k: (None if v == "" else v) for k, v in body.fields.items()}
    changed = update_hardware(conn, asset_serial, cleaned)
    row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    return {"updated_fields": changed and len(cleaned) or 0, "hardware": dict(row)}


NO_IMPORT_SENTINEL = "不匯入"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@app.get("/api/import/last")
def import_last(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = get_latest_import_log(conn)
    return dict(row) if row else None


@app.get("/api/import/field-mapping")
def import_field_mapping(session: sqlite3.Row = Depends(require_auth)):
    """D14精神：欄位對應可調整、不寫死。回傳目前的field_mapping.json內容。"""
    return load_mapping()


class FieldMappingBody(BaseModel):
    mapping: dict[str, dict[str, str]]


@app.put("/api/import/field-mapping")
def update_import_field_mapping(
    body: FieldMappingBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """value為「不匯入」的項目不寫入設定檔（等於移除對應）；其餘value要對得上目標資料表
    的實際欄位名稱（PRAGMA table_info動態查，不寫死欄位清單，schema改了這裡自動跟上）。

    _comment說明欄位：load_mapping()讀取時會把它拿掉（不是真的分頁對應資料），這裡存檔
    要把原本檔案裡的_comment原封放回去，不然存一次設定檔裡給後人看的說明文字就消失了。
    """
    cleaned: dict[str, dict[str, str]] = {}
    for sheet, columns in body.mapping.items():
        if sheet not in SHEET_CONFIG:
            raise HTTPException(400, f"不支援的分頁：{sheet}")
        valid_columns = _table_columns(conn, SHEET_CONFIG[sheet]["table"])
        cleaned[sheet] = {}
        for excel_header, system_field in columns.items():
            if system_field == NO_IMPORT_SENTINEL:
                continue
            if system_field not in valid_columns:
                raise HTTPException(
                    400, f"{sheet}分頁「{excel_header}」對應到不存在的欄位：{system_field}"
                )
            cleaned[sheet][excel_header] = system_field

    existing_raw = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    to_write = {}
    if "_comment" in existing_raw:
        to_write["_comment"] = existing_raw["_comment"]
    to_write.update(cleaned)

    MAPPING_PATH.write_text(
        json.dumps(to_write, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return cleaned


@app.post("/api/import/excel")
def import_excel_upload(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """故意用sync def（不是async def）：UploadFile.file是同步可讀的SpooledTemporaryFile，
    sync路由可以直接讀，不需要await。

    ⚠️ 原本這裡寫著「FastAPI 會把 get_db 依賴跟 sync 路由排進同一個 threadpool worker
    執行緒」——那個假設是錯的，並行時兩者可能落在不同 worker，會炸
    sqlite3「不同執行緒不能共用連線」。真正的修正在 db.get_connection()
    （check_same_thread=False），不是靠 sync/async 的寫法去賭執行緒。
    """
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")

    contents = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        try:
            summary = import_excel(tmp_path, conn)
        except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報
            raise HTTPException(400, f"匯入失敗，請確認檔案格式：{exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn,
        imported_by=session["username"],
        hardware_count=summary["sheets"].get("硬體", {}).get("inserted", 0)
        + summary["sheets"].get("硬體", {}).get("updated", 0),
        personnel_count=summary["sheets"].get("人員", {}).get("inserted", 0)
        + summary["sheets"].get("人員", {}).get("updated", 0),
        software_count=summary["sheets"].get("軟體", {}).get("inserted", 0)
        + summary["sheets"].get("軟體", {}).get("updated", 0),
        error_count=len(summary["errors"]),
    )
    return summary


@app.post("/api/import/rvtools")
def import_rvtools_upload(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """S19 VC 採集器：吃一份 RVTools 匯出的 vCenter 盤點（vInfo 分頁）。

    每台 VM 走身分解析：對到既有資產就更新機器事實（不碰業務欄位），新的建成 VC- 資產，
    判不準的進人工審核佇列（不自動合併）。真實 vCenter 資料，非假資料。
    """
    import rvtools_import

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 RVTools 匯出的 .xlsx 檔案")

    contents = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            summary = rvtools_import.import_rvtools(tmp_path, conn)
        except Exception as exc:  # noqa: BLE001 - 壞檔/非RVTools格式統一如實回報
            raise HTTPException(400, f"匯入失敗，請確認是 RVTools 匯出的檔：{exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn, imported_by=session["username"],
        hardware_count=summary["inserted"] + summary["updated"],
        personnel_count=0, software_count=0, error_count=len(summary["errors"]),
    )
    return summary


@app.get("/api/merge-reviews")
def list_merge_reviews(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """待人工判定的合併案（身分解析判不準的）。空＝沒有懸而未決的。

    這些是「同 IP 但不同 UUID」這種不敢自動合併的案子——攤開來讓人決定，
    不要藏著，否則等於資料默默對不上卻沒人知道。
    """
    rows = conn.execute(
        "SELECT mr.id, mr.reason, mr.candidates, mr.status, mr.created_at, "
        "sr.source, sr.payload FROM merge_review mr "
        "JOIN source_record sr ON sr.id = mr.source_record_id "
        "WHERE mr.status = 'open' ORDER BY mr.id DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["candidates"] = json.loads(d["candidates"]) if d["candidates"] else []
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        except (ValueError, TypeError):
            pass
        out.append(d)
    return out


class VcAutoImportBody(BaseModel):
    enabled: bool
    dir: str = ""
    max_age_hours: int = 36


@app.get("/api/vcenter-autoimport")
def vcenter_autoimport_status(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """VC 自動匯入（方案 B）現況：設定＋鮮度燈（今晚的匯出到底有沒有進來）。"""
    import vcenter_autoimport

    return vcenter_autoimport.health(conn)


@app.put("/api/vcenter-autoimport")
def vcenter_autoimport_save(
    body: VcAutoImportBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定監看資料夾＋開關＋逾時門檻。開了之後排程器每輪會看資料夾有沒有新檔。"""
    import vcenter_autoimport

    vcenter_autoimport.set_config(conn, body.enabled, body.dir, body.max_age_hours)
    return vcenter_autoimport.health(conn)


@app.post("/api/vcenter-autoimport/run")
def vcenter_autoimport_run(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動「立即抓一次」：找監看資料夾最新且寫完的 RVTools 檔，比上次新才匯。"""
    import vcenter_autoimport

    return vcenter_autoimport.pickup(conn)


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


@app.get("/api/connections")
def list_connections_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    return [_serialize_connection(r) for r in list_connections(conn)]


@app.post("/api/connections")
def create_connection_endpoint(
    body: ConnectionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    new_id = create_connection_record(
        conn, body.name, body.connection_type, body.target, body.port, body.username, body.password
    )
    return _serialize_connection(get_connection_by_id(conn, new_id))


@app.put("/api/connections/{connection_id}")
def update_connection_endpoint(
    connection_id: int,
    body: ConnectionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    # 密碼write-only的另一半：留空（None或空字串）代表「這次不變更密碼」，不是「清空密碼」。
    password_to_set = body.password if body.password else None
    update_connection_record(
        conn,
        connection_id,
        body.name,
        body.connection_type,
        body.target,
        body.port,
        body.username,
        password_to_set,
    )
    return _serialize_connection(get_connection_by_id(conn, connection_id))


class ConnectionEnabledBody(BaseModel):
    enabled: bool


@app.patch("/api/connections/{connection_id}/enabled")
def set_connection_enabled_endpoint(
    connection_id: int,
    body: ConnectionEnabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """來源啟用／停用。停用的來源排程掃描會直接跳過，不會被算成「掃描失敗」。

    存在的理由：有些來源現階段本來就連不到（例如 CMDB Gateway 在家裡碰不到公司內網）。
    沒有開關的話它每次掃描都失敗、每次都把「掃描不完整」點亮——常態性的假警報
    會讓真正的掃描問題沒人看見。
    """
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    set_connection_enabled(conn, connection_id, body.enabled)
    return _serialize_connection(get_connection_by_id(conn, connection_id))


@app.delete("/api/connections/{connection_id}")
def delete_connection_endpoint(
    connection_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    delete_connection_record(conn, connection_id)
    return {"ok": True}


@app.post("/api/connections/{connection_id}/test")
def test_connection_endpoint(
    connection_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """D15「重新檢查」按鈕：真的做一次TCP連線測試，不是硬編一個假狀態。這個階段還沒有
    真實vCenter/SNMP協定整合（S3的ScanSource還是mock，接上真實協定是之後的工作），但
    port通不通得起來是真訊號，比起顯示假的「已連線」更誠實，符合D31精神。
    """
    row = get_connection_by_id(conn, connection_id)
    if row is None:
        raise HTTPException(404, "查無此連線設定")
    if not row["port"]:
        raise HTTPException(400, "這筆連線沒有設定Port，無法測試")
    try:
        with socket.create_connection((row["target"], row["port"]), timeout=3):
            status = "綠"
    except OSError:
        status = "紅"
    update_connection_status(conn, connection_id, status)
    return _serialize_connection(get_connection_by_id(conn, connection_id))


# ===== S14 備份健康儀表 =====
# 目的是讓工程師/助理不用進命令列就能看狀態、手動備份。備份這件事最糟的失敗模式是
# 「以為有備份，還原時才發現壞的」，所以這裡不只顯示「有沒有跑過」，而是實際去驗證。

@app.get("/api/backup/status")
def backup_status_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """燈號 + 明細。會實際開檔跑 integrity_check，不是只看檔案存不存在。"""
    return backup.health(conn=conn)


class OffsiteBody(BaseModel):
    dir: str = ""


@app.put("/api/backup/offsite")
def backup_set_offsite(
    body: OffsiteBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定異地備份目錄（畫面可設，不用改 systemd 環境變數再重啟）。

    這裡只存路徑；路徑本身要指到「真正獨立的儲存」才算異地——例如掛載另一台機器
    （222）的 /ai_backup。留空＝清掉異地（回黃燈）。存完立刻回最新健康狀態讓畫面看變化。
    """
    from db import set_setting

    set_setting(conn, backup.OFFSITE_SETTING_KEY, (body.dir or "").strip())
    return backup.health(conn=conn)


@app.post("/api/backup/run")
def backup_run_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動觸發一次備份（安全快照 + 完整性驗證 + 異地複製 + 清理過期）。

    刻意用 sync def：跟 import_excel_upload 同理，備份是 I/O 密集的阻塞操作，
    交給 FastAPI 的 threadpool 跑，不要卡住 event loop。
    """
    db_path = get_db_path()
    result = backup.run_backup(
        db_path, backup.get_backup_dir(db_path), datetime.now(), backup.get_offsite_dir(conn)
    )
    payload = {
        "ok": result.ok,
        "path": str(result.path) if result.path else None,
        "size_bytes": result.size_bytes,
        "integrity_ok": result.integrity_ok,
        "integrity_detail": result.integrity_detail,
        "offsite_path": str(result.offsite_path) if result.offsite_path else None,
        "offsite_error": result.offsite_error,
        "pruned_count": len(result.pruned),
        "took_seconds": result.took_seconds,
        "error": result.error,
    }
    if not result.ok:
        # 用 200 回失敗細節而不是丟 500：前端要能把「為什麼失敗」原樣顯示給使用者，
        # 500 只會變成一句「伺服器錯誤」，等於把診斷資訊丟掉。
        payload["message"] = f"備份失敗：{result.error}"
    return payload


@app.get("/api/backup/list")
def backup_list_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """本地與異地的備份檔清單（新到舊）。"""
    db_path = get_db_path()
    offsite = backup.get_offsite_dir(conn)
    return {
        "local": backup.list_backups(backup.get_backup_dir(db_path)),
        "offsite": backup.list_backups(offsite) if offsite else [],
    }


@app.get("/api/feature-flags")
def list_feature_flags_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """D28：只列出 schema 種好的可切換模組（不含「系統設定」本身，見 schema.sql 註解）。"""
    return [dict(r) for r in list_feature_flags(conn)]


class FeatureFlagBody(BaseModel):
    enabled: bool


@app.put("/api/feature-flags/{module_key}")
def update_feature_flag_endpoint(
    module_key: str,
    body: FeatureFlagBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_feature_flag(conn, module_key) is None:
        raise HTTPException(404, "查無此功能模組")
    set_feature_flag(conn, module_key, body.enabled)
    return dict(get_feature_flag(conn, module_key))


# ============ 掃描：手動重掃 + 排程設定 ============
@app.post("/api/scan/run")
def scan_run_endpoint(session: sqlite3.Row = Depends(require_auth)):
    """手動重掃：背景執行，馬上回覆，前端輪詢 /api/scan/status 取四態。"""
    if not scan_service.start_scan("manual"):
        raise HTTPException(409, "已有一次掃描正在進行，請稍候")
    return {"started": True}


@app.get("/api/scan/status")
def scan_status_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    return scan_service.latest_status(conn)


@app.get("/api/scan/schedule")
def scan_schedule_get_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    return scan_service.get_schedule(conn)


class ScheduleBody(BaseModel):
    enabled: bool
    mode: str            # daily / interval
    time: str            # HH:MM（daily 用）
    interval_hours: int  # interval 用


@app.put("/api/scan/schedule")
def scan_schedule_put_endpoint(
    body: ScheduleBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        scan_service.set_schedule(conn, body.enabled, body.mode, body.time, body.interval_hours)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return scan_service.get_schedule(conn)


# ============ 納入管理（把掃到的主機登記成資產）============
@app.get("/api/cmdb/pull")
def cmdb_pull_endpoint(
    group: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """從 CMDB Gateway 拉資料：回筆數、看到的欄位名（用來對應到我們的欄位）、樣本。
    連不到/認證失敗回 502，錯誤原因如實帶出（不假裝成功）。"""
    try:
        items = cmdb_gateway.fetch_group(conn, group)
    except ConnectionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "group": group,
        "count": len(items),
        "fields": cmdb_gateway.seen_fields(items),
        "sample": items[:20],
    }


@app.get("/api/export")
def export_assets_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """匯出資產（硬體/人員/軟體三分頁 xlsx）——對應資料匯入，有進就有出。

    表頭優先用「匯入對應表(field_mapping.json)的標題」，保證匯出的檔案能原封不動再匯入
    （round-trip）；欄位依 field_meta 分類排序，讓同一類欄位在表上相鄰、好填。
    """
    base = Path(__file__).parent
    meta_all = json.loads((base / "field_meta.json").read_text(encoding="utf-8"))
    fields_meta = meta_all.get("fields", {})
    cat_order = [c["key"] for c in meta_all.get("categories", [])]
    mapping = json.loads((base / "field_mapping.json").read_text(encoding="utf-8"))

    def _cat_rank(col: str) -> int:
        cat = fields_meta.get(col, {}).get("category")
        return cat_order.index(cat) if cat in cat_order else len(cat_order)

    wb = Workbook()
    first = True
    for table, sheet in (("hardware", "硬體"), ("personnel", "人員"), ("software", "軟體")):
        ws = wb.active if first else wb.create_sheet(sheet)
        if first:
            ws.title = sheet
            first = False
        # 反轉匯入對應表：db欄位 -> 匯入期待的標題（確保 round-trip）。
        # 用 setdefault 保留「第一個出現的標題」當正式標題：field_mapping 允許一個db欄位
        # 掛多個標題別名（例如 api_id 同時吃 "API ID" 與 "AP ID"），若用 {v:k} 推導式會
        # last-wins，匯出表頭會被別名蓋掉、隨設定檔行序漂移。
        rev: dict[str, str] = {}
        for header, column in mapping.get(sheet, {}).items():
            if header != "_comment":
                rev.setdefault(column, header)
        orig = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        skip = {"id", "created_at", "updated_at"}
        orig = [c for c in orig if c not in skip]
        cols = sorted(orig, key=lambda c: (_cat_rank(c), orig.index(c)))
        # 表頭：先用匯入標題(可 round-trip)，退回 field_meta 中文 label，再退回欄名
        ws.append([rev.get(c) or fields_meta.get(c, {}).get("label", c) for c in cols])
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table}"):
            ws.append([row[c] for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"assets_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/field-meta")
def field_meta_endpoint(session: sqlite3.Row = Depends(require_auth)):
    """欄位中繼資料（分類×兩層），前端據此把納入管理表單分區、標 tech/biz。
    路徑刻意不放 /api/assets/ 底下——會被 /api/assets/{asset_serial} 這條 path 參數路由吃掉。"""
    return json.loads((Path(__file__).parent / "field_meta.json").read_text(encoding="utf-8"))


@app.get("/api/scan/unregistered")
def unregistered_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """最新一次掃到、但 hardware(ICA) 未登記的主機——納入管理的候選清單。"""
    row = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history WHERE scan_ok = 1").fetchone()
    t = row["t"] if row else None
    if not t:
        return []
    scanned = conn.execute(
        "SELECT ip, hostname, device_model, is_vm, segment, "
        "mac, mac_vendor, open_ports, ttl, os_guess "
        "FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
        (t,),
    ).fetchall()
    out = []
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        matched = conn.execute(
            "SELECT 1 FROM hardware WHERE (ip IS NOT NULL AND ip = ?) OR (hostname IS NOT NULL AND hostname = ?)",
            (r["ip"], r["hostname"]),
        ).fetchone()
        if matched is None:
            out.append(dict(r))
    return out


@app.get("/api/scan/results")
def scan_results_endpoint(
    registered: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """最新一次掃描「掃得到（存活）」的主機，每筆標註是否已在 ICA 登記。

    給儀表板的「本次掃描 / 本次掃到存活」磚塊下鑽用。/api/scan/unregistered 只回未登記的
    那一半（納入管理專用），這支回全部並附 registered 旗標，才能對得上磚塊上的總數。

    registered：不帶=全部；yes=已登記；no=未登記（等同 unregistered 那份）。
    """
    if registered is not None and registered not in ("yes", "no"):
        raise HTTPException(400, "registered 只接受 yes 或 no")

    scan_time = _latest_scan_time(conn)
    scanned = _scanned_alive_rows(conn, scan_time)
    if not scanned:
        return {"scan_time": scan_time, "items": []}

    ica_rows = conn.execute("SELECT ip, hostname FROM hardware").fetchall()
    ica_ips = {r["ip"] for r in ica_rows if r["ip"]}
    ica_hostnames = {r["hostname"] for r in ica_rows if r["hostname"]}

    items = []
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        is_registered = _row_in_keys(r, ica_ips, ica_hostnames)
        if registered == "yes" and not is_registered:
            continue
        if registered == "no" and is_registered:
            continue
        item = dict(r)
        item["registered"] = is_registered
        # 已登記的話一併帶出資產序號，前端才能直接連到那台的詳細頁
        if is_registered:
            hw = conn.execute(
                "SELECT asset_serial FROM hardware WHERE (ip IS NOT NULL AND ip = ?) "
                "OR (hostname IS NOT NULL AND hostname = ?) LIMIT 1",
                (r["ip"], r["hostname"]),
            ).fetchone()
            item["asset_serial"] = hw["asset_serial"] if hw else None
        items.append(item)

    return {"scan_time": scan_time, "items": items}


class AdoptBody(BaseModel):
    fields: dict


@app.post("/api/assets/adopt")
def adopt_endpoint(
    body: AdoptBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把一台掃到的主機登記成受管資產（technical 前端已帶入、business 使用者填）。"""
    f = {k: v for k, v in body.fields.items() if v not in (None, "")}
    ip = f.get("ip")
    if not ip:
        raise HTTPException(400, "缺少 IP，無法納入管理")
    if conn.execute("SELECT 1 FROM hardware WHERE ip = ?", (ip,)).fetchone():
        raise HTTPException(409, f"{ip} 已納入管理")
    if not f.get("asset_serial"):
        f["asset_serial"] = f"ADOPT-{ip}"   # 沒公司資產序號時先給暫用值，之後可改
    # 資產狀態沒填就預設「使用中」，不要留空。
    # 理由：這台會出現在納管候選清單，正是因為**掃描掃到它活著**——我們明明知道它在跑，
    # 畫面卻顯示「未知」是錯的資訊，不是保守。留空還會讓狀態排序整片沉到最後。
    # （使用者實際反映：納管進來的 6 台全顯示「未知」。）
    if not f.get("asset_status"):
        f["asset_status"] = "使用中"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    f = {k: v for k, v in f.items() if k in cols}
    new_id = insert_hardware(conn, **f)

    # 納管當下就撤銷這台的「漏登記」，不要等下一次掃描。
    # 實際踩到（2026-07-19）：使用者納管 5 台之後，儀表板仍顯示「漏登記 6 筆」，
    # 但實際只有 1 台沒登記——因為撤銷只發生在掃描後的重比對。中間這段時間畫面在說謊，
    # 而「剛做完的動作沒有反映在畫面上」是最容易讓人失去信任的一種。
    retracted = conn.execute(
        "UPDATE comparison_result SET is_read = 1, handled_at = datetime('now','localtime') "
        "WHERE issue_type = '漏登記' AND is_read = 0 AND ("
        "  (ip IS NOT NULL AND ip = ?) OR (hostname IS NOT NULL AND hostname != '' AND hostname = ?)"
        ")",
        (ip, f.get("hostname") or ""),
    ).rowcount
    conn.commit()
    return {"id": new_id, "asset_serial": f["asset_serial"], "retracted_issues": retracted}


@app.get("/api/version")
def version_endpoint():
    """版本 + git commit + 服務啟動時間——讓使用者一眼確認「跑的是不是新版」。

    git_commit 直接問 git（服務就跑在 git 工作區上），不再依賴部署時 stamp 的
    build_info.json。理由是實際踩過的坑：stamp 檔由部署腳本寫、版號是每次請求即時讀檔，
    所以「檔案換了但服務沒重啟」時版號照樣跳成新版，看起來部署成功，實際跑的還是舊程式
    （2026-07-18 換 0.6.0 時就是這樣，最後靠 systemd 啟動時間才抓到）。

    started_at 是「本行程的啟動時間」——只有真的重啟過才會變，才是新程式碼有沒有生效的
    可信證據。dirty=true 代表工作區有未提交的改動（就地開發時很常見，不是錯誤）。
    """
    info = {"version": "?", "git_commit": None, "dirty": None,
            "started_at": _PROCESS_STARTED_AT, "built_at": None}
    here = Path(__file__).parent
    try:
        info["version"] = json.loads(
            (here / "version.json").read_text(encoding="utf-8")
        ).get("version", "?")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=here,
                           capture_output=True, encoding="utf-8", timeout=5)
        if r.returncode == 0:
            info["git_commit"] = r.stdout.strip()
            d = subprocess.run(["git", "status", "--porcelain"], cwd=here,
                               capture_output=True, encoding="utf-8", timeout=5)
            info["dirty"] = bool(d.stdout.strip()) if d.returncode == 0 else None
    except Exception:  # noqa: BLE001
        pass
    if info["git_commit"] is None:
        # 沒有 git 的環境（例如打包部署到別台）才退回 stamp 檔
        try:
            info.update(json.loads((here / "build_info.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return info


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


@app.get("/api/topology")
def get_topology(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """M2 圖資料：一次回 systems + deps，前端餵 Cytoscape。全新建、人維護（戰情室重寫模型）。

    改為需登入（原本讀免登入）：這張圖含系統依賴與 SPOF 標記，等於直接告訴人「打哪一台
    全部會倒」，是最不該公開的資料；頁面本來就在登入牆後，公開讀沒有帶來任何好處。
    健康檢查請改用 /api/version（deploy.sh 用的就是它）。
    """
    import manage_state

    systems = [dict(r) for r in conn.execute("SELECT * FROM systems ORDER BY category, label").fetchall()]
    deps = [dict(r) for r in conn.execute("SELECT * FROM system_deps ORDER BY id").fetchall()]

    # M1↔M2 接起來：系統健康度由關聯主機的實際納管狀態推導，取代人手動標。
    # 沒有關聯主機的系統維持人工值，但明確標成 manual——把「某人半年前填的 ok」
    # 跟「剛剛確認過是 ok」混在一起，等於讓人相信一個沒有根據的綠燈。
    health = manage_state.system_health(conn)
    for s in systems:
        h = health.get(s["id"], {})
        s["health"] = h.get("health", s["health"])
        s["health_source"] = h.get("health_source", "manual")
        s["hosts"] = h.get("hosts", [])
    return {"systems": systems, "deps": deps}


@app.post("/api/systems")
def create_system(
    body: SystemBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not _ID_RE.match(body.id):
        raise HTTPException(400, "系統代碼只能是英數字/底線/減號，1-32 字")
    if body.health not in _HEALTH:
        raise HTTPException(400, "健康度必須是 ok/warn/err")
    if conn.execute("SELECT 1 FROM systems WHERE id = ?", (body.id,)).fetchone():
        raise HTTPException(409, f"系統代碼「{body.id}」已存在")
    conn.execute(
        "INSERT INTO systems (id, label, category, domain, health, is_spof, note) VALUES (?,?,?,?,?,?,?)",
        (body.id, body.label, body.category, body.domain, body.health, int(body.is_spof), body.note),
    )
    conn.commit()
    return {"id": body.id}


@app.put("/api/systems/{system_id}")
def update_system(
    system_id: str,
    body: SystemUpdateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone() is None:
        raise HTTPException(404, "查無此系統")
    fields = body.model_dump(exclude_none=True)
    if fields.get("health") and fields["health"] not in _HEALTH:
        raise HTTPException(400, "健康度必須是 ok/warn/err")
    if "is_spof" in fields:
        fields["is_spof"] = int(fields["is_spof"])
    if not fields:
        return {"id": system_id, "updated": 0}
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE systems SET {sets}, updated_at = datetime('now','localtime') WHERE id = ?",
        [*fields.values(), system_id],
    )
    conn.commit()
    return {"id": system_id, "updated": len(fields)}


@app.delete("/api/systems/{system_id}")
def delete_system(
    system_id: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone() is None:
        raise HTTPException(404, "查無此系統")
    # 連帶刪掉相關依賴（schema 有 ON DELETE CASCADE，但 SQLite 預設不強制外鍵，這裡明確刪）
    conn.execute("DELETE FROM system_deps WHERE source = ? OR target = ?", (system_id, system_id))
    conn.execute("DELETE FROM systems WHERE id = ?", (system_id,))
    conn.commit()
    return {"id": system_id, "deleted": True}


@app.post("/api/deps")
def create_dep(
    body: DepBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if body.source == body.target:
        raise HTTPException(400, "系統不能依賴自己")
    for sid in (body.source, body.target):
        if conn.execute("SELECT 1 FROM systems WHERE id = ?", (sid,)).fetchone() is None:
            raise HTTPException(400, f"系統「{sid}」不存在")
    if conn.execute(
        "SELECT 1 FROM system_deps WHERE source = ? AND target = ?", (body.source, body.target)
    ).fetchone():
        raise HTTPException(409, "這條依賴已存在")
    cur = conn.execute(
        "INSERT INTO system_deps (source, target, dep_type) VALUES (?,?,?)",
        (body.source, body.target, body.dep_type),
    )
    conn.commit()
    return {"id": cur.lastrowid}


@app.delete("/api/deps/{dep_id}")
def delete_dep(
    dep_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute("SELECT 1 FROM system_deps WHERE id = ?", (dep_id,)).fetchone() is None:
        raise HTTPException(404, "查無此依賴")
    conn.execute("DELETE FROM system_deps WHERE id = ?", (dep_id,))
    conn.commit()
    return {"id": dep_id, "deleted": True}


@app.get("/api/search")
def global_search(
    q: str,
    limit: int = 8,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """全域搜尋：一個框找遍資產／服務／業務系統／未登記的掃描結果。

    設計取捨：
    - **分組回傳而不是混成一串**：使用者搜「3306」時，「哪台在聽 3306」跟
      「哪台資產叫 3306」是完全不同的東西，混在一起只會讓人多按一次才找到。
    - **每組限量**：搜尋框是導航用的，不是報表。要看全部就點該組的「看全部」，
      帶著同一個關鍵字跳到對應的清單頁（連結由前端組，後端只回關鍵字命中）。
    - **掃到但未登記的主機也要出現**：那正是「這個 IP 是什麼」最常被問的時候，
      如果只搜已登記資產，答案會是「查無此項」——但它明明就在網路上。
    """
    kw = (q or "").strip()
    if not kw:
        return {"query": "", "groups": []}
    if limit < 1 or limit > 50:
        raise HTTPException(400, "limit 只接受 1–50")
    like = f"%{kw}%"

    groups = []

    assets = conn.execute(
        "SELECT asset_serial, hostname, ip, device_model, environment, asset_purpose "
        "FROM hardware WHERE hostname LIKE ? OR ip LIKE ? OR asset_serial LIKE ? "
        "OR device_model LIKE ? OR asset_purpose LIKE ? OR custodian LIKE ? "
        "ORDER BY hostname LIMIT ?",
        (like, like, like, like, like, like, limit + 1),
    ).fetchall()
    if assets:
        groups.append({
            "key": "assets", "label": "資產",
            "items": [{
                "title": r["hostname"] or r["asset_serial"],
                "subtitle": " · ".join(x for x in (r["ip"], r["device_model"],
                                                   r["environment"]) if x),
                "to": f"/assets/{r['asset_serial']}",
            } for r in assets[:limit]],
            "more": len(assets) > limit,
            "more_to": f"/assets?q={kw}",
        })

    # 服務：搜埠號、行程名、服務名都要中。數字就當埠號精準比，
    # 不然搜 "22" 會把 8022、2222 全撈進來，最想要的那筆反而被淹掉。
    if kw.isdigit():
        svc_rows = conn.execute(
            "SELECT hs.ip, hs.port, hs.process, hs.service_guess, hs.exposure, "
            "hs.asset_serial, h.hostname FROM host_service hs "
            "LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
            "WHERE hs.gone_at IS NULL AND hs.port = ? ORDER BY hs.ip LIMIT ?",
            (int(kw), limit + 1),
        ).fetchall()
    else:
        svc_rows = conn.execute(
            "SELECT hs.ip, hs.port, hs.process, hs.service_guess, hs.exposure, "
            "hs.asset_serial, h.hostname FROM host_service hs "
            "LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
            "WHERE hs.gone_at IS NULL AND (hs.process LIKE ? OR hs.service_guess LIKE ?) "
            "ORDER BY hs.ip LIMIT ?",
            (like, like, limit + 1),
        ).fetchall()
    if svc_rows:
        groups.append({
            "key": "services", "label": "服務",
            "items": [{
                "title": f"{r['service_guess'] or '未知服務'} · {r['port']}",
                "subtitle": f"{r['hostname'] or r['ip']}"
                            f"{'（僅本機）' if r['exposure'] == 'localhost' else ''}",
                "to": f"/services?port={r['port']}",
            } for r in svc_rows[:limit]],
            "more": len(svc_rows) > limit,
            "more_to": f"/services?port={kw}" if kw.isdigit() else "/services",
        })

    systems = conn.execute(
        "SELECT id, label, category, domain FROM systems "
        "WHERE id LIKE ? OR label LIKE ? OR category LIKE ? OR domain LIKE ? "
        "ORDER BY label LIMIT ?",
        (like, like, like, like, limit + 1),
    ).fetchall()
    if systems:
        groups.append({
            "key": "systems", "label": "業務系統",
            "items": [{
                "title": r["label"],
                "subtitle": " · ".join(x for x in (r["category"], r["domain"]) if x),
                "to": "/topology",
            } for r in systems[:limit]],
            "more": len(systems) > limit,
            "more_to": "/topology",
        })

    # 掃到但沒登記的主機——「這個 IP 是什麼」最常就是在問這種
    scan_time = _latest_scan_time(conn)
    if scan_time:
        known_ips = {r["ip"] for r in conn.execute(
            "SELECT ip FROM hardware WHERE ip IS NOT NULL AND ip != ''")}
        unreg = [r for r in conn.execute(
            "SELECT ip, hostname, os_guess, mac_vendor FROM scan_history "
            "WHERE scan_time = ? AND scan_ok = 1 AND (ip LIKE ? OR hostname LIKE ?) "
            "ORDER BY ip LIMIT ?", (scan_time, like, like, limit + 10))
            if r["ip"] not in known_ips]
        if unreg:
            groups.append({
                "key": "unregistered", "label": "掃到但未登記",
                "items": [{
                    "title": r["ip"],
                    "subtitle": " · ".join(x for x in (r["hostname"], r["os_guess"],
                                                       r["mac_vendor"]) if x) or "無其他線索",
                    "to": "/adopt",
                } for r in unreg[:limit]],
                "more": len(unreg) > limit,
                "more_to": "/adopt",
            })

    return {"query": kw, "groups": groups}


# ===== M2 第一片：服務盤點（一台主機看得出什麼服務）=====

ALLOWED_SERVICE_SORT = {
    "ip", "hostname", "port", "proto", "exposure", "process", "service_guess",
    "guess_source", "last_seen", "first_seen", "asset_serial",
}


@app.get("/api/services")
def list_services_endpoint(
    ip: str | None = None,
    asset_serial: str | None = None,
    port: int | None = None,
    exposure: str | None = None,
    include_gone: bool = False,
    include_infra: bool = True,
    sort_by: str = "ip",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """服務清單。預設不含已消失的，含基礎服務（SSH/NTP…，前端可一鍵收合）。

    排序在後端做（欄位走白名單、方向只認 asc/desc），跟 /api/assets 同一套；
    這張表可能上千列，前端排序只會排到目前拿到的那批。
    """
    import service_inventory

    rows = service_inventory.list_services(
        conn, ip=ip, asset_serial=asset_serial,
        include_gone=include_gone, include_infra=include_infra,
    )
    if port is not None:
        rows = [r for r in rows if r["port"] == port]
    if exposure:
        rows = [r for r in rows if r["exposure"] == exposure]

    key = sort_by if sort_by in ALLOWED_SERVICE_SORT else "ip"
    reverse = order == "desc"

    def sort_val(r):
        v = r.get(key)
        if v is None or v == "":
            return (1, "")           # 空值一律排最後，不隨 asc/desc 翻面
        if key == "ip" and isinstance(v, str) and v.count(".") == 3:
            try:
                return (0, tuple(int(p) for p in v.split(".")))
            except ValueError:
                return (0, v)
        return (0, v if not isinstance(v, str) else v.lower())

    try:
        rows.sort(key=sort_val, reverse=reverse)
    except TypeError:
        rows.sort(key=lambda r: (r.get(key) is None, str(r.get(key) or "")), reverse=reverse)

    return {"items": rows, "summary": service_inventory.service_summary(conn)}


@app.get("/api/services/summary")
def services_summary_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import service_inventory

    return service_inventory.service_summary(conn)


@app.post("/api/services/collect")
def collect_services_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """對已納管主機收一輪服務（可指定單台）。

    ⚠️ 這是同步執行、會真的連出去——大批主機時前端要顯示執行中三態（async-feedback 規範）。
    收不到行程名（沒 root）不算失敗：埠是拿得到的，那已經是主要價值。
    """
    import service_inventory

    try:
        return service_inventory.collect_services(
            conn, only_serial=asset_serial, trigger="manual"
        )
    except Exception as exc:  # noqa: BLE001 - 失敗原因原樣回給畫面，不吞成一句「失敗」
        raise HTTPException(500, f"服務採集失敗：{exc}") from exc


# ===== 帳號盤點（稽核導向）=====

ALLOWED_ACCOUNT_SORT = {
    "ip", "hostname", "username", "uid", "kind", "gecos", "last_login", "pw_last_change",
    "pw_max_days", "pw_status", "is_sudoer", "authorized_keys", "asset_serial",
}


@app.get("/api/accounts")
def list_accounts_endpoint(
    ip: str | None = None,
    asset_serial: str | None = None,
    kind: str | None = None,
    sudoer_only: bool = False,
    hide_builtin: bool = False,
    include_gone: bool = False,
    sort_by: str = "ip",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import account_inventory

    rows = account_inventory.list_accounts(
        conn, ip=ip, asset_serial=asset_serial, kind=kind,
        include_gone=include_gone, sudoer_only=sudoer_only,
    )

    # 「拉掉內建帳號」：只藏乾淨的系統/內建帳號，被稽核點名的一律留著——
    # 把有 finding 的內建帳號藏起來，正好會蓋掉 UID 0 後門這種最該看的東西。
    # 藏了幾個會回報，不靜默丟（靜默截斷會讓人以為「就這些帳號」）。
    hidden_builtin = 0
    if hide_builtin:
        # 只有「實質違規(fail)」才讓內建帳號留下。不能用全部 finding——
        # 沒 root 時每個帳號都有一堆 unknown(查不到)，那會讓每個帳號都被保護、
        # 篩選器一個都藏不掉（真機 306 條 unknown 就是這情形）。
        # unknown 是「這欄沒查到」不是「這帳號有問題」，不該撐住整列。
        flagged = {(f["ip"], f["username"])
                   for f in account_inventory.latest_findings(conn)
                   if f["verdict"] == "fail"}
        kept = []
        for r in rows:
            if r.get("is_builtin") and (r["ip"], r["username"]) not in flagged:
                hidden_builtin += 1
                continue
            kept.append(r)
        rows = kept

    key = sort_by if sort_by in ALLOWED_ACCOUNT_SORT else "ip"
    reverse = order == "desc"

    def sv(r):
        v = r.get(key)
        if v is None or v == "":
            return (1, "")
        if key == "ip" and isinstance(v, str) and v.count(".") == 3:
            try:
                return (0, tuple(int(p) for p in v.split(".")))
            except ValueError:
                return (0, v)
        return (0, v if not isinstance(v, str) else v.lower())

    try:
        rows.sort(key=sv, reverse=reverse)
    except TypeError:
        rows.sort(key=lambda r: (r.get(key) is None, str(r.get(key) or "")), reverse=reverse)
    return {"items": rows, "summary": account_inventory.audit_summary(conn),
            "hidden_builtin": hidden_builtin}


@app.get("/api/accounts/findings")
def account_findings_endpoint(
    severity: str | None = None,
    rule_id: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """稽核發現。這是這個模組真正的產出——清單只是中間產物。"""
    import account_inventory
    import account_rules

    return {
        "items": account_inventory.latest_findings(conn, severity=severity, rule_id=rule_id),
        "summary": account_inventory.audit_summary(conn),
        "rules": [{"id": r["id"], "label": r["label"], "severity": r["severity"],
                   "law": r["law"]} for r in account_rules.RULES],
        "thresholds": account_rules.get_thresholds(conn),
    }


@app.get("/api/accounts/findings/export")
def export_findings(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把稽核發現匯出成 Excel——稽核當天要交的是可存檔的證據表，不是叫人看畫面。

    每條含：風險/項目/帳號/帳號備註/主機/IP/判定/處置狀態/例外到期/覆核人/覆核時間/
    手動備註/法規依據。含處置狀態＝這份報告本身就是稽核軌跡。
    """
    import account_inventory

    findings = account_inventory.latest_findings(conn)
    sev_label = {"high": "高", "medium": "中", "low": "低"}
    status_label = {"open": "待處理", "ack": "已確認", "exception": "核准例外", "fixed": "已修復"}

    wb = Workbook()
    ws = wb.active
    ws.title = "帳號稽核發現"
    ws.append(["風險", "項目", "帳號", "帳號備註", "主機", "IP", "判定",
               "處置狀態", "例外到期", "覆核人", "覆核時間", "手動備註", "法規依據"])
    for f in findings:
        ws.append([
            "查不到" if f.get("verdict") == "unknown" else sev_label.get(f.get("severity"), ""),
            f.get("label"), f.get("username"), f.get("gecos"),
            f.get("hostname") or "", f.get("ip"), f.get("detail"),
            status_label.get(f.get("status"), f.get("status")),
            f.get("exempt_until") or "", f.get("decided_by") or "", f.get("decided_at") or "",
            f.get("note") or "", f.get("law"),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"帳號稽核發現_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@app.get("/api/accounts/matrix/export")
def export_matrix(
    cols: list[str] = Query(default=[]),
    kind: str | None = None,
    hide_builtin: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """合規表匯出：使用者自選欄位，資料最小化給稽核看。

    只吐勾選的欄位——稽核不需要看到 UID、shell、金鑰把數這些內部細節，
    給越少越好。狀態欄的計算走 account_inventory.MATRIX_EXPORT_COLS（與前端同源）。
    """
    import account_inventory

    valid = account_inventory.MATRIX_EXPORT_COLS
    chosen = [c for c in cols if c in valid]
    if not chosen:
        raise HTTPException(400, "至少要選一個有效欄位")

    rows = account_inventory.list_accounts(conn, kind=kind or None)
    if hide_builtin:
        flagged = {(f["ip"], f["username"])
                   for f in account_inventory.latest_findings(conn) if f["verdict"] == "fail"}
        rows = [r for r in rows
                if not (r.get("is_builtin") and (r["ip"], r["username"]) not in flagged)]

    wb = Workbook()
    ws = wb.active
    ws.title = "帳號合規表"
    ws.append([valid[c][0] for c in chosen])
    for r in rows:
        ws.append([valid[c][1](r) for c in chosen])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"帳號合規表_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@app.post("/api/accounts/collect")
def collect_accounts_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import account_inventory

    try:
        return account_inventory.collect_accounts(
            conn, only_serial=asset_serial, trigger="manual")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"帳號採集失敗：{exc}") from exc


class CollectAccountBody(BaseModel):
    account: str


# 允許的收集身分：唯讀預設 + 已知標準管理帳號。不開放任意字串——
# 收集身分決定拿多大權限，不該讓它變成打字就能指定任意帳號的欄位。
def _allowed_collect_accounts() -> set[str]:
    import account_collector

    return {"webit3scan"} | set(account_collector.STD_MGMT_ACCOUNTS.keys())


@app.get("/api/accounts/collect-config")
def get_collect_config(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """目前的遠端收集身分，以及切成管理帳號時要先做的授權動作。

    webit3 用自己的金鑰、但以設定的身分登入——所以切成 sysinfra 前，要先把
    webit3 收集公鑰授權進各機的 sysinfra/.ssh/authorized_keys（一次性、可撤）。
    這裡回傳那把公鑰與現成指令，讓使用者用既有管道（巡檢 ansible）佈下去。
    """
    import manage_state

    current = manage_state.get_collect_account(conn)
    pubkey = ""
    try:
        with open(manage_state.COLLECTOR_KEY_DEFAULT + ".pub", encoding="utf-8") as f:
            pubkey = f.read().strip()
    except OSError:
        pubkey = "（讀不到收集公鑰，請確認 /opt/webit3/.collector_key.pub 存在）"
    return {
        "account": current,
        "options": [
            {"value": "webit3scan", "label": "webit3scan（唯讀最小權限）",
             "note": "看不到需 root 的欄位（密碼效期、sudo 明細、authorized_keys）"},
            {"value": "sysinfra", "label": "sysinfra（標準管理帳號，完整資料）",
             "note": "NOPASSWD:ALL，sudo -n 全通；需先授權收集公鑰進 sysinfra"},
        ],
        "pubkey": pubkey,
        "provision_hint": (
            "切成 sysinfra 前，先在各主機把上面這把公鑰加進 "
            "~sysinfra/.ssh/authorized_keys（用巡檢 ansible 一次佈完）。"
            "webit3 不持有 sysinfra 私鑰，授權隨時可撤（移掉該行即失效）。"
        ),
    }


@app.put("/api/accounts/collect-config")
def set_collect_config(
    body: CollectAccountBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    from db import set_setting

    if body.account not in _allowed_collect_accounts():
        raise HTTPException(
            400, f"不支援的收集身分：{body.account}（只接受 "
                 f"{', '.join(sorted(_allowed_collect_accounts()))}）")
    set_setting(conn, "collect_ssh_account", body.account)
    return {"account": body.account}


@app.get("/api/accounts/hosts")
def list_account_hosts(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """可收集的主機清單＋是否已排除。給排除管理與健檢選單用。"""
    import account_inventory

    return {"hosts": account_inventory.list_collectable_hosts(conn)}


class DispositionBody(BaseModel):
    ip: str
    username: str
    rule_id: str
    status: str
    note: str | None = None
    exempt_until: str | None = None


@app.put("/api/accounts/findings/disposition")
def set_finding_disposition_endpoint(
    body: DispositionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設一條稽核發現的處置狀態（待處理/已確認/核准例外/已修復）。跨盤點持久。

    decided_by 記登入者——稽核要能查「這條例外是誰、何時核准的」。
    """
    import account_inventory

    try:
        return account_inventory.set_finding_disposition(
            conn, body.ip, body.username, body.rule_id, body.status,
            note=body.note, exempt_until=body.exempt_until,
            decided_by=session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class AccountNoteBody(BaseModel):
    ip: str
    username: str
    note: str = ""


@app.put("/api/accounts/note")
def set_account_note_endpoint(
    body: AccountNoteBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設某帳號的「手動備註」——稽核人員自己輸入的註記（跟 gecos 自動備註是兩回事）。"""
    import account_inventory

    try:
        return account_inventory.set_account_note(conn, body.ip, body.username, body.note)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class ExcludeBody(BaseModel):
    asset_serial: str
    exclude: bool


@app.put("/api/accounts/exclude")
def set_account_exclude(
    body: ExcludeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把一台主機排除／納回帳號稽核。排除時清掉它的舊帳號與稽核資料。"""
    import account_inventory

    if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?",
                    (body.asset_serial,)).fetchone() is None:
        raise HTTPException(404, "查無此資產")
    return account_inventory.set_host_excluded(conn, body.asset_serial, body.exclude)


@app.get("/api/accounts/diagnose")
def diagnose_account_host(
    asset_serial: str,
    desensitize: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """逐主機收集健檢：即時跑每條收集指令，回報為什麼收不到。

    給公司多 OS 上線 debug 用。安全：不含任何原始輸出內容（見 collect_probe），
    再走 diagnostics 的去識別化＋殘留掃描閘門，可安全匯出貼給開發者。
    desensitize 預設 True——真實資料不出這台機器是硬規則。
    """
    import collect_probe
    import diagnostics as dg
    import manage_state

    row = conn.execute(
        "SELECT ip FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    if not row or not row["ip"]:
        raise HTTPException(404, "查無此資產或它沒有 IP")

    account = manage_state.get_collect_account(conn)
    try:
        report = collect_probe.probe(
            row["ip"], account, manage_state.COLLECTOR_KEY_DEFAULT,
            local_ips=manage_state.local_ips())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"健檢執行失敗：{exc}") from exc

    if desensitize:
        d = dg.Desensitizer(enabled=True)
        report = d.walk(report)
        residual = dg.residual_scan(json.dumps(report, ensure_ascii=False))
        report["_desensitized"] = True
        report["_residual_scan"] = residual or "通過（未發現未遮蔽的位址）"
        # 殘留掃描沒過就不給——真實位址不出這台機器
        if residual:
            raise HTTPException(
                500, f"健檢輸出殘留掃描未通過，已擋下不外送：{residual[:3]}")
    return report


@app.get("/api/accounts/sudo-rules")
def account_sudo_rules(session: sqlite3.Row = Depends(require_auth)):
    """收集帳號要拿到密碼/sudo/金鑰資訊所需的 sudo 白名單。

    刻意做成「給你看、你自己決定要不要佈」而不是自動佈署：
    這是擴權動作，且會改動所有已納管主機的 sudoers。
    白名單本身不含 `cat /etc/shadow`——chage -l／passwd -S 拿得到同樣的稽核結論
    卻不吐密碼雜湊，能用小權限達成就不該要大的。
    """
    import account_collector

    return {
        "rules": account_collector.SUDO_RULES,
        "note": "存成 /etc/sudoers.d/webit3scan-audit（chmod 440），"
                "並用 visudo -cf 驗證後才生效。不含 /etc/shadow，不會洩漏密碼雜湊。",
    }


@app.on_event("startup")
def _start_scheduler():
    # 只在正式服務啟動時開排程器（webit3-api.service 設 ASSET_SCHEDULER=1）；
    # 測試/開發匯入 app 時不啟動，避免背景亂掃。
    if os.environ.get("ASSET_SCHEDULER") == "1":
        scan_service.start_scheduler()
