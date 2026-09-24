"""每個 GET 端點都不能回 500（2026-09-17）。

## 為什麼要有這支

使用者 2026-09-17：「功能越多越多 BUG」。他是對的。看當天實際發生的幾個：

| Bug | 類型 |
|---|---|
| 「非納管」405 | 端點改名、前端漏改 |
| 帳號主機清單顯示「0 台」 | **SQL 欄位名打錯（collected_at 實為 last_seen）→ 500** |
| 儀表板長出 189 列帳號 | 裸的 `v-else` |

共通點：**幾乎都不是邏輯錯，是接線錯**。而且最傷的那個是被前端的
`.catch(() => ({items: []}))` 吞掉，畫面顯示成「沒有資料」——
使用者以為是資料沒收到，其實是程式壞了。

單元測試擋不到這一類：每支模組自己的測試都綠的，但「API 這條線真的通不通」沒人驗。
`test_auth_coverage` 已經走過所有端點驗 401，這支用**同一個模式**驗「不會 500」。

那個欄位名打錯的 bug，在這支測試下會當場紅。

## 這支測什麼、不測什麼

- **測**：每個 GET 端點，在一個有資料的資料庫上呼叫，不可以 500
- **不測**：回傳內容對不對（那是各模組自己的測試該管的）
- 404／400／422 都**算過**——那是「這個 id 不存在」「參數不合法」，是正常回應；
  500 才代表程式炸了

只走 GET：POST／PUT／DELETE 會改資料，不適合這種掃描式冒煙測試。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

#: path 參數的替代值。值本身不重要——回 404 算過，我們只在意有沒有炸。
PATH_STUB = {
    "{asset_serial}": "A-1",
    "{issue_id}": "1",
    "{connection_id}": "1",
    "{module_key}": "topology",
    "{system_id}": "S-1",
    "{dep_id}": "1",
    "{snapshot_id}": "1",
    "{source}": "dynassets",
    "{api_id}": "N-107",
    "{archive_id}": "1",
    "{run_id}": "1",
    "{kind}": "service",
    "{ip}": "192.0.2.1",
    "{node_id}": "hw:A-1",
    "{username}": "root",
    "{canonical}": "x",
    "{name}": "x",
}

#: 需要 query 參數才有意義的端點（少了會 422，那也算過，但給了才測得到真正的路徑）
QUERY_STUB = {
    "/api/accounts/by-username": {"username": "root"},
    "/api/accounts/by-username/export": {"username": "root"},
}

#: 刻意跳過：會真的連出去打網路或跑很久的端點。
#: ⚠️ 加東西進這份清單＝承認「這支沒被冒煙測到」，要想清楚再加。
SKIP = {
    "/api/connections/{connection_id}/test",   # 真的去連目標
    "/api/accounts/diagnose",                  # 逐台 SSH
}


def _endpoints() -> list[str]:
    out = []
    for r in api.app.routes:
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", set()) or set()
        if not path.startswith("/api/") or "GET" not in methods:
            continue
        if path in SKIP:
            continue
        out.append(path)
    return sorted(set(out))


def _fill(path: str) -> str:
    for k, v in PATH_STUB.items():
        path = path.replace(k, v)
    return path


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """有資料的庫——空庫測不到「有資料才會走到的那段 SQL」，
    而欄位名打錯正是只有在真的查下去才會炸。"""
    p = tmp_path_factory.mktemp("smoke") / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="192.0.2.1",
                       os="Red Hat Enterprise Linux 9.4", device_model="HP DL380",
                       environment="正式", physical_location="內湖機房",
                       asset_status="使用中", usage_unit="資訊部", user_name="王小明",
                       custodian="李小華", collect_ok=1)
    db.insert_hardware(conn, asset_serial="A-2", hostname="h2", ip="192.0.2.2",
                       os="Microsoft Windows Server 2019", environment="測試",
                       asset_status="使用中")
    conn.execute("INSERT INTO host_account (ip, asset_serial, username, uid, kind, "
                 "is_sudoer, never_logged_in, source, first_seen, last_seen) "
                 "VALUES ('192.0.2.1','A-1','root',0,'default',1,0,'ssh',"
                 "'2026-09-17','2026-09-17 01:20:17')")
    conn.execute("INSERT INTO host_service (ip, asset_serial, proto, port, exposure, "
                 "guess_source, is_infra, source, first_seen, last_seen) "
                 "VALUES ('192.0.2.1','A-1','tcp',22,'all','process',1,'ssh_ss',"
                 "'2026-09-17','2026-09-17 01:20:17')")
    conn.execute("INSERT INTO host_package (ip, asset_serial, name, version, source, "
                 "first_seen, last_seen) VALUES "
                 "('192.0.2.1','A-1','openssl','3.0.7','rpm','2026-09-17','2026-09-17')")
    conn.execute("INSERT INTO scan_history (hostname, ip, scan_ok, scan_time, open_ports) "
                 "VALUES ('h1','192.0.2.1',1,'2026-09-17 01:00:00','22,445')")
    conn.execute("INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
                 "triggered_by, ok, stage, message) VALUES "
                 "('192.0.2.2','linux','sysinfra','manual','tester',0,'connect',"
                 "'Permission denied (publickey,password).')")
    conn.commit()
    conn.close()

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
    try:
        yield TestClient(api.app, raise_server_exceptions=False)
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


@pytest.mark.parametrize("path", _endpoints())
def test_GET端點不可以回500(client, path):
    url = _fill(path)
    r = client.get(url, params=QUERY_STUB.get(path))
    # 只擋 500。502／503 是「外部系統沒設定或連不上」——例如 /api/cmdb/pull 會回
    # 502 並附「還沒設定 CMDB Gateway 連線」，那是**處理過的誠實回應**，不是炸掉。
    assert r.status_code != 500, (
        f"{url} 回 500（\n{r.text[:400]}\n）"
        "　500 代表程式炸了（欄位名打錯、import 漏掉、None 沒處理…）。"
        "404／400／422／502 都算過——那些是正常回應。"
    )


def test_這支測試本身有涵蓋到東西():
    """避免路由抓不到而「零端點、永遠通過」——那比沒測還糟。"""
    eps = _endpoints()
    assert len(eps) > 80, f"只抓到 {len(eps)} 個 GET 端點，路由列舉多半壞了"
    # 今天出事的那幾支一定要在涵蓋範圍內
    for must in ("/api/accounts/inventoried-hosts", "/api/inventory/{kind}/hosts",
                 "/api/onboard/failures", "/api/stats/onboard-matrix"):
        assert must in eps, f"{must} 沒被涵蓋到"
