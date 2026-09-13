"""資產生命週期：申請單轉錄 → 上線前檢查 → 基線回檢（drift）。

這串的核心價值是「知道某個設定本來是刻意的、現在失效了」，所以測試重點放在：
  1. 沒有資料時一律 unknown，不能假裝通過（最危險的失敗模式：綠燈但根本沒人看過那台）
  2. 全部處理完才准上線，且上線那一刻才產生基線
  3. 基線變了要開 drift、恢復了要自動關、已處理過的不要每天重開
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import golive  # noqa: E402
import api  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402


def _add_service(db_path, ip, port, proto="tcp", gone=False):
    conn = db.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO host_service (ip, proto, port, bind_addr, source, gone_at) "
            "VALUES (?, ?, ?, '0.0.0.0', 'test', ?)",
            (ip, proto, port, "2026-08-15 00:00:00" if gone else None),
        )
        conn.commit()
    finally:
        conn.close()


def _conn(db_path):
    return db.get_connection(db_path)


# ===== 申請單轉錄 =====

def test_申請單轉錄_建立資產同時留下單據與檢查表():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/manual", json={
                "fields": {"asset_serial": "PR-001", "hostname": "web01", "ip": "10.1.1.1",
                           "os": "RHEL 9.6"},
                "provision": {
                    "source": "form", "request_no": "ES800011504075",
                    "applicant_unit": "資料工程部", "applicant": "王小明",
                    "form_date": "2026-05-05", "change_kind": "一般",
                    "raw_fields": {"CPU Core": "16", "記憶體(GB)": "128"},
                },
            })
            assert resp.status_code == 200, resp.text
            # 還沒過上線檢查，不能是「使用中」
            assert resp.json()["hardware"]["asset_status"] == "待上線"

            p = client.get("/api/assets/PR-001/provision").json()["provision"]
            assert p["request_no"] == "ES800011504075"
            assert p["applicant"] == "王小明"
            # 申請單當初填的內容原樣保留，之後才比得出「當初申請 128G、現在 256G」
            assert p["raw_fields"]["記憶體(GB)"] == "128"

            detail = client.get("/api/golive/PR-001").json()
            assert detail["status"] == "open"
            assert detail["total"] > 0
        finally:
            api.app.dependency_overrides.clear()


def test_申請單轉錄_單據編號必填而直接新增不用():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            bad = client.post("/api/assets/manual", json={
                "fields": {"asset_serial": "PR-002"},
                "provision": {"source": "form"},
            })
            assert bad.status_code == 400

            ok = client.post("/api/assets/manual", json={
                "fields": {"asset_serial": "PR-003"},
                "provision": {"source": "direct"},
            })
            assert ok.status_code == 200
        finally:
            api.app.dependency_overrides.clear()


def test_手動新增_沒帶provision也不會壞_相容舊呼叫():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = client.post("/api/assets/manual", json={
                "fields": {"asset_serial": "PR-004", "asset_status": "使用中"},
            })
            assert resp.status_code == 200
            # 使用者自己指定狀態時要尊重他（補登記早就在跑的機器）
            assert resp.json()["hardware"]["asset_status"] == "使用中"
        finally:
            api.app.dependency_overrides.clear()


# ===== auto 項判定 =====

def test_沒有服務採集資料時一律unknown不是通過():
    """最危險的失敗模式：畫面顯示「Telnet 已停用」的綠燈，實際上根本沒收過那台的資料。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "AU-001", "h1", "10.2.0.1", os="RHEL 9.6")
            conn = _conn(db_path)
            try:
                r = golive.evaluate_auto_items(conn, "AU-001")
            finally:
                conn.close()
            assert r["telnet_disabled"]["verdict"] == "unknown"
            assert r["ftp_disabled"]["verdict"] == "unknown"
        finally:
            api.app.dependency_overrides.clear()


def test_有服務資料時_該關的埠在聽就是fail該開的沒聽也是fail():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "AU-002", "h2", "10.2.0.2", os="RHEL 9.6")
            _add_service(db_path, "10.2.0.2", 23)   # Telnet 開著＝不該有
            _add_service(db_path, "10.2.0.2", 22)   # SSH 有＝正常
            conn = _conn(db_path)
            try:
                r = golive.evaluate_auto_items(conn, "AU-002")
            finally:
                conn.close()
            assert r["telnet_disabled"]["verdict"] == "fail"
            assert r["ssh_available"]["verdict"] == "pass"
            assert r["ftp_disabled"]["verdict"] == "pass"     # 有資料且沒在聽 21
            assert r["snmp_agent"]["verdict"] == "fail"       # 該有 SNMP 卻沒收到
        finally:
            api.app.dependency_overrides.clear()


def test_已消失的服務不算在監聽():
    """gone_at 有值＝服務已經不在了，不能還當成「Telnet 開著」。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "AU-003", "h3", "10.2.0.3", os="RHEL 9.6")
            _add_service(db_path, "10.2.0.3", 22)
            _add_service(db_path, "10.2.0.3", 23, gone=True)
            conn = _conn(db_path)
            try:
                r = golive.evaluate_auto_items(conn, "AU-003")
            finally:
                conn.close()
            assert r["telnet_disabled"]["verdict"] == "pass"
        finally:
            api.app.dependency_overrides.clear()


def test_windows與linux檢查項不一樣():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "OS-WIN", "w1", "10.3.0.1", os="Windows Server 2019")
            _insert_hardware(db_path, "OS-LNX", "l1", "10.3.0.2", os="RHEL 9.6")
            conn = _conn(db_path)
            try:
                win = {i["key"] for i in golive.items_for_asset(conn, "OS-WIN")}
                lnx = {i["key"] for i in golive.items_for_asset(conn, "OS-LNX")}
            finally:
                conn.close()
            assert "print_spooler_off" in win and "print_spooler_off" not in lnx
            assert "ssh_available" in lnx and "ssh_available" not in win
            assert "telnet_disabled" in win and "telnet_disabled" in lnx  # both
        finally:
            api.app.dependency_overrides.clear()


# ===== 上線閘門 =====

def _pass_all_manual(client, serial):
    detail = client.get(f"/api/golive/{serial}").json()
    for item in detail["items"]:
        if item["check_type"] == "manual":
            client.post(f"/api/golive/{serial}/item",
                        json={"item_key": item["key"], "verdict": "pass"})


def test_還有項目沒處理完不准上線():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "GL-001", "h1", "10.4.0.1", os="RHEL 9.6",
                             asset_status="待上線")
            resp = client.post("/api/golive/GL-001/pass")
            assert resp.status_code == 400
            assert "沒處理完" in resp.json()["detail"]
            conn = _conn(db_path)
            try:
                row = conn.execute(
                    "SELECT asset_status FROM hardware WHERE asset_serial='GL-001'"
                ).fetchone()
            finally:
                conn.close()
            assert row["asset_status"] == "待上線"
        finally:
            api.app.dependency_overrides.clear()


def test_auto項測不到就擋著_不能靠人工勾過去():
    """auto 項只能標「不需」，不能人工宣告「已完成」——否則基線就是人講的，不是機器講的。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "GL-002", "h2", "10.4.0.2", os="RHEL 9.6")
            resp = client.post("/api/golive/GL-002/item",
                               json={"item_key": "telnet_disabled", "verdict": "pass"})
            assert resp.status_code == 400
            assert "不能人工勾選" in resp.json()["detail"]

            ok = client.post("/api/golive/GL-002/item",
                             json={"item_key": "telnet_disabled", "verdict": "na"})
            assert ok.status_code == 200
        finally:
            api.app.dependency_overrides.clear()


def test_全部處理完才通過_並轉使用中且產生基線():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "GL-003", "h3", "10.4.0.3", os="RHEL 9.6",
                             asset_status="待上線")
            _add_service(db_path, "10.4.0.3", 22)
            _add_service(db_path, "10.4.0.3", 161, proto="udp")
            _pass_all_manual(client, "GL-003")

            resp = client.post("/api/golive/GL-003/pass")
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "passed"

            conn = _conn(db_path)
            try:
                hw = conn.execute(
                    "SELECT asset_status FROM hardware WHERE asset_serial='GL-003'"
                ).fetchone()
                base = {
                    r["item_key"]: r["baseline"]
                    for r in conn.execute(
                        "SELECT item_key, baseline FROM baseline_drift WHERE asset_serial='GL-003'"
                    )
                }
            finally:
                conn.close()
            assert hw["asset_status"] == "使用中"
            # 「Telnet 沒在聽」這件事被記成刻意的基線，之後才比得出被打開
            assert base["telnet_disabled"] == "absent"
            assert base["ssh_available"] == "present"
            assert base["os_version"] == "RHEL 9.6"
        finally:
            api.app.dependency_overrides.clear()


def test_通過後不能再改檢查項():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "GL-004", "h4", "10.4.0.4", os="RHEL 9.6")
            _add_service(db_path, "10.4.0.4", 22)
            _add_service(db_path, "10.4.0.4", 161, proto="udp")
            _pass_all_manual(client, "GL-004")
            assert client.post("/api/golive/GL-004/pass").status_code == 200

            resp = client.post("/api/golive/GL-004/item",
                               json={"item_key": "admin_pw", "verdict": "fail"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


# ===== 基線回檢 =====

def _golive_ok(client, db_path, serial, ip):
    _insert_hardware(db_path, serial, serial.lower(), ip, os="RHEL 9.6", asset_status="待上線")
    _add_service(db_path, ip, 22)
    _add_service(db_path, ip, 161, proto="udp")
    _pass_all_manual(client, serial)
    assert client.post(f"/api/golive/{serial}/pass").status_code == 200


def test_基線失效_本來刻意停用的服務被打開就開一筆drift():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _golive_ok(client, db_path, "DR-001", "10.5.0.1")
            assert client.get("/api/drift").json()["drifts"] == []

            _add_service(db_path, "10.5.0.1", 23)      # 有人把 Telnet 打開了
            summary = client.post("/api/drift/recheck").json()
            assert summary["opened"] == 1

            drifts = client.get("/api/drift").json()["drifts"]
            assert len(drifts) == 1
            assert drifts[0]["item_key"] == "telnet_disabled"
            assert drifts[0]["baseline"] == "absent"
            assert drifts[0]["current"] == "present"
            # 畫面要講得出「當初是誰放行的」，這是它跟一般掃描告警的差別
            assert drifts[0]["passed_by"]
        finally:
            api.app.dependency_overrides.clear()


def test_基線失效_恢復後自動關閉且不重複開():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _golive_ok(client, db_path, "DR-002", "10.5.0.2")
            _add_service(db_path, "10.5.0.2", 23)
            client.post("/api/drift/recheck")

            # 連跑兩次不該變成兩筆——每天亮同一條紅燈會讓人直接忽略整張表
            again = client.post("/api/drift/recheck").json()
            assert again["opened"] == 0
            assert len(client.get("/api/drift").json()["drifts"]) == 1

            # 服務關掉了（gone_at 標記）→ 回檢應自動恢復
            conn = _conn(db_path)
            try:
                conn.execute(
                    "UPDATE host_service SET gone_at = '2026-08-16 00:00:00' "
                    "WHERE ip='10.5.0.2' AND port=23"
                )
                conn.commit()
            finally:
                conn.close()
            back = client.post("/api/drift/recheck").json()
            assert back["recovered"] == 1
            assert client.get("/api/drift").json()["drifts"] == []
        finally:
            api.app.dependency_overrides.clear()


def test_基線回檢_收不到資料不當成失效():
    """服務採集當天掛掉 → 所有主機看起來都「沒在聽任何埠」。
    這時如果判成 drift，隔天早上會收到滿滿一頁假告警，這張表就沒人看了。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _golive_ok(client, db_path, "DR-003", "10.5.0.3")
            conn = _conn(db_path)
            try:
                conn.execute("DELETE FROM host_service WHERE ip='10.5.0.3'")
                conn.commit()
            finally:
                conn.close()
            summary = client.post("/api/drift/recheck").json()
            assert summary["opened"] == 0
            assert client.get("/api/drift").json()["drifts"] == []
        finally:
            api.app.dependency_overrides.clear()


def test_drift處置_標記已確認後不再列在待處理():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _golive_ok(client, db_path, "DR-004", "10.5.0.4")
            _add_service(db_path, "10.5.0.4", 23)
            client.post("/api/drift/recheck")
            drift_id = client.get("/api/drift").json()["drifts"][0]["id"]

            resp = client.post(f"/api/drift/{drift_id}/disposition",
                               json={"status": "ack", "note": "已開單請 AP 組關閉"})
            assert resp.status_code == 200
            assert client.get("/api/drift").json()["drifts"] == []
            assert len(client.get("/api/drift?status=ack").json()["drifts"]) == 1

            # ack 只是「我知道了」，不是把基線改掉：機器還是不符合基線，回檢仍持續追蹤
            client.post("/api/drift/recheck")
            assert len(client.get("/api/drift?status=ack").json()["drifts"]) == 1
        finally:
            api.app.dependency_overrides.clear()


def test_退役資產不再回檢_不噴假drift():
    """機器報廢關機 → 所有 port 消失 → 每項基線都不符。不擋的話每天一整台份的假告警。
    （2026-08-15 自我檢查抓到的缺陷。）"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _golive_ok(client, db_path, "DR-010", "10.5.1.10")
            conn = _conn(db_path)
            try:
                conn.execute("DELETE FROM host_service WHERE ip='10.5.1.10'")
                conn.execute(
                    "INSERT INTO host_service (ip, proto, port, bind_addr, source) "
                    "VALUES ('10.5.1.10','tcp',9999,'0.0.0.0','test')"  # 有資料但基線的埠都不見了
                )
                conn.execute("UPDATE hardware SET asset_status='報廢' WHERE asset_serial='DR-010'")
                conn.commit()
            finally:
                conn.close()

            s = client.post("/api/drift/recheck").json()
            assert s["checked_assets"] == 0, "報廢資產不該再被回檢"
            assert client.get("/api/drift").json()["drifts"] == []
        finally:
            api.app.dependency_overrides.clear()
