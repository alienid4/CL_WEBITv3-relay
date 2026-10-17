"""偵測存活＋非納管設備豁免（2026-09-16）。

使用者：「多一個 PING 按鈕動作，我可確認是否存活」→ 接著自己更正
「其他 port 也去測，所以不能說 PING，應該是『偵測存活』」。
以及：「如果我發現這個沒辦法納管、也不是下線，譬如客製化系統或者是 Oracle」。

要守的：
1. ICMP 不通但 TCP 通 → 還是「活著」，而且要講明是 ICMP 被擋（不然一整批 Windows 會被判死）
2. 兩個都不通 → 「沒有回應」，**不可以**寫成「機器不存在」；證據等級是推論
3. ping 跑不起來（沒裝／沒權限）→ 「無法判斷」，等級未驗證，不可混成沒回應
4. 只收 IP，不收主機名（子行程參數不可能被塞東西）
5. 偵測**不改任何資料**
6. 豁免：原因必填、不算有問題、不碰資產狀態、可取消
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import host_ping  # noqa: E402
import manage_state as ms  # noqa: E402
import onboard_exempt  # noqa: E402
import pipeline  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OK_OUT = ("3 packets transmitted, 3 received, 0% packet loss\n"
          "rtt min/avg/max/mdev = 0.4/1.2/2.0/0.5 ms")
DEAD_OUT = "3 packets transmitted, 0 received, 100% packet loss"


def runner_ok(cmd, timeout):
    return 0, OK_OUT


def runner_dead(cmd, timeout):
    return 1, DEAD_OUT


def runner_missing(cmd, timeout):
    return None, "這台主機上沒有 ping 這個指令"


def tcp_open(ip, ports=None, timeout=0.6):
    return [22]


def tcp_dead(ip, ports=None, timeout=0.6):
    return None


def test_ping通就是活著():
    r = host_ping.check("192.0.2.10", runner=runner_ok, prober=tcp_dead)
    assert r["alive"] is True and r["evidence"] == "證據"
    assert "回 3 個" in r["verdict"] and "1.2 ms" in r["verdict"]


def test_ICMP被擋但TCP通_仍算活著_且要講明白():
    r = host_ping.check("192.0.2.10", runner=runner_dead, prober=tcp_open)
    assert r["alive"] is True and r["evidence"] == "證據"
    assert "ICMP" in r["verdict"] and "22" in r["verdict"]
    assert "沒開機" in r["verdict"], "要明講這不是關機，不然人會誤判"


def test_都不通只能說沒有回應_不可以說機器不存在():
    r = host_ping.check("192.0.2.10", runner=runner_dead, prober=tcp_dead)
    assert r["alive"] is False and r["evidence"] == "推論"
    assert "沒有回應" in r["verdict"] and "分不出來" in r["verdict"]
    assert "不存在" not in r["verdict"]


def test_ping跑不起來是無法判斷_不是沒回應():
    r = host_ping.check("192.0.2.10", runner=runner_missing, prober=tcp_dead)
    assert r["alive"] is None and r["evidence"] == "未驗證"
    assert "沒跑成" in r["verdict"]
    # 但 ping 跑不起來、TCP 通，照樣是活著
    r2 = host_ping.check("192.0.2.10", runner=runner_missing, prober=tcp_open)
    assert r2["alive"] is True


def test_只收IP():
    with pytest.raises(ValueError):
        host_ping.check("switch01.example", runner=runner_ok, prober=tcp_open)


@pytest.fixture()
def env(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    for i in range(3):
        db.insert_hardware(conn, asset_serial=f"A-{i}", hostname=f"h{i}", ip=f"192.0.2.{i + 1}",
                           asset_status="使用中", os="Oracle Linux 8")
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
        yield TestClient(api.app, raise_server_exceptions=False), p
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


def test_偵測存活端點_有記錄且不改資料(env, monkeypatch):
    c, p = env
    monkeypatch.setattr(host_ping, "_run", runner_dead)
    monkeypatch.setattr(host_ping.net_scan, "_probe_host", tcp_dead)
    before = c.get("/api/assets/A-0").json()["hardware"]
    r = c.post("/api/tools/alive-check", json={"ip": "192.0.2.1"})
    assert r.status_code == 200 and r.json()["alive"] is False
    assert c.get("/api/assets/A-0").json()["hardware"]["asset_status"] == before["asset_status"]
    log = c.get("/api/system/collect-log", params={"kind": "alive_check"}).json()["items"][0]
    assert "沒有回應" in log["message"] and log["kind_label"] == "偵測存活"


def test_偵測存活_主機名被擋(env):
    c, _ = env
    assert c.post("/api/tools/alive-check", json={"ip": "sw01"}).status_code == 400


def test_豁免_原因必填_不算有問題_可取消(env):
    c, p = env
    assert c.post("/api/onboard-exempt", json={"serials": ["A-0"], "reason": " "}).status_code == 400
    r = c.post("/api/onboard-exempt",
               json={"serials": ["A-0", "A-0", "NOPE"], "reason": "客製化系統，廠商不准建帳號",
                     "kind": "客製化系統"})
    assert r.status_code == 200
    assert r.json()["added"] == ["A-0"] and r.json()["not_found"] == ["NOPE"]

    conn = db.get_connection(p)
    try:
        s = ms.summarize(conn)
        assert s["counts"][ms.EXEMPT] == 1 and s["exempt_total"] == 1
        assert s["needs_action_total"] == 2, "豁免的那台不算有問題"
        # 不碰資產狀態
        assert conn.execute("SELECT asset_status FROM hardware WHERE asset_serial='A-0'").fetchone()[0] == "使用中"
    finally:
        conn.close()

    assert c.post("/api/onboard-exempt/remove", json={"serials": ["A-0"]}).json()["removed"] == 1
    conn = db.get_connection(p)
    try:
        assert ms.summarize(conn)["needs_action_total"] == 3, "取消豁免後回到要處理"
    finally:
        conn.close()


def test_豁免_收得到就不再顯示非納管():
    assert ms.classify(True, True, 1, exempt=True) == ms.ONBOARDED
    assert ms.classify(True, True, 0, exempt=True) == ms.EXEMPT
    assert ms.EXEMPT not in ms.NEEDS_ACTION_STATES and ms.NEXT_ACTION[ms.EXEMPT]
    assert "exempt" in pipeline.STAGE_INDEX and "exempt" not in pipeline.TODO_STAGES


def test_豁免清單有原因可查(env):
    c, _ = env
    c.post("/api/onboard-exempt", json={"serials": ["A-1"], "reason": "Oracle RAC，DBA 不准動",
                                       "kind": "資料庫主機（Oracle 等）"})
    items = c.get("/api/onboard-exempt").json()["items"]
    assert len(items) == 1
    assert items[0]["reason"] == "Oracle RAC，DBA 不准動" and items[0]["created_by"] == "tester"
    assert items[0]["hostname"] == "h1"
    assert c.get("/api/assets/A-1").json()["hardware"]["onboard_exempt"]["kind"] == "資料庫主機（Oracle 等）"


# ===== 批次偵測與報告（2026-09-16 使用者：「偵測後要 PO 報告」）=====


def test_批次偵測_產生報告_可查可匯出(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(host_ping, "_run", runner_dead)
    monkeypatch.setattr(host_ping.net_scan, "_probe_host", tcp_dead)
    r = c.post("/api/tools/alive-check/batch", json={"serials": ["A-0", "A-1"], "scope": "測試"})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total"] == 2 and body["summary"]["no_response"] == 2
    run_id = body["run_id"]

    got = c.get(f"/api/tools/alive-check/runs/{run_id}").json()
    assert got["total"] == 2 and got["scope"] == "測試"
    assert {i["ip"] for i in got["items"]} == {"192.0.2.1", "192.0.2.2"}
    assert got["items"][0]["result_label"] == "沒有回應"

    assert c.get("/api/tools/alive-check/runs").json()["items"][0]["id"] == run_id
    x = c.get(f"/api/tools/alive-check/runs/{run_id}/export")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert c.get("/api/tools/alive-check/runs/99999").status_code == 404


def test_批次_沒有IP的不算對象(env):
    c, _ = env
    assert c.post("/api/tools/alive-check/batch", json={"serials": ["NOPE"]}).status_code == 400


def test_批次_超過上限要擋下並說還剩幾台(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(host_ping, "MAX_BATCH", 1)
    r = c.post("/api/tools/alive-check/batch", json={"serials": ["A-0", "A-1"]})
    assert r.status_code == 400 and "還有 1 台" in r.json()["detail"]


def test_報告摘要_要點名ICMP被擋的那批():
    alive_tcp = host_ping.check("192.0.2.1", runner=runner_dead, prober=tcp_open)
    alive_tcp["result"] = host_ping.result_key(alive_tcp["alive"])
    alive_icmp = host_ping.check("192.0.2.2", runner=runner_ok, prober=tcp_dead)
    alive_icmp["result"] = host_ping.result_key(alive_icmp["alive"])
    s = host_ping.summarize_batch([alive_tcp, alive_icmp])
    assert s["alive"] == 2 and s["alive_by_tcp_only"] == 1, "要分得出哪些是靠 TCP 才認出來的"


# ===== 環境三分類（2026-09-16 使用者：「環境 選正式 非正式 oa 3總」）=====


def test_環境三分類_備援算正式_空白獨立一類():
    import env_group

    assert env_group.classify("正式") == env_group.PROD
    assert env_group.classify("備援") == env_group.PROD, "備援是正式的待命機，不是測試機"
    assert env_group.classify("測試") == env_group.NONPROD
    assert env_group.classify("使用者測試(UAT)") == env_group.NONPROD
    assert env_group.classify("開發環境(DEV)") == env_group.NONPROD
    assert env_group.classify("OA") == env_group.OA
    assert env_group.classify("") == env_group.UNSET, "沒填就是沒填，不可以塞進任一類"
    assert env_group.classify(None) == env_group.UNSET
    # [B-06]：認不出來的不猜，獨立成「未知環境」。以前歸非正式，「正式二區」會從正式篩選裡消失
    assert env_group.classify("以後新增的怪值") == env_group.UNKNOWN
    assert env_group.classify("正式二區") == env_group.UNKNOWN, "不含測試字樣的新值不可歸非正式"
    assert env_group.classify("PROD-DR") == env_group.UNKNOWN
    assert env_group.UNKNOWN in env_group.CHOICES, "未知也要出現在選單，不能靜默消失"
    assert env_group.UNSET in env_group.CHOICES, "未填也要出現在選單，不能靜默消失"


def test_漏斗回傳待處理關卡與環境分類_退役不在待處理裡(env):
    c, p = env
    import pipeline

    body = c.get("/api/pipeline").json()
    assert "todo_stages" in body, "「還需要處理」有哪幾關要由後端說了算，前端不可自己寫死"
    assert "retired" not in body["todo_stages"] and "exempt" not in body["todo_stages"]
    assert [g["key"] for g in body["env_groups"]][-1] == "unset"
    if body["items"]:
        assert "physical_location" in body["items"][0] and "env_group" in body["items"][0]
        assert "onboard_block" in body["items"][0] and "onboard_exempt" in body["items"][0]
