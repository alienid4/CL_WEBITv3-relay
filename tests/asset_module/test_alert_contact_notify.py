"""R15 告警聯絡資訊（2.044.2）＋R16 告警一鍵通知（2.044.2）。全部假資料（IP 192.0.2.x、email example.test）。

R15 驗收：五欄都有值或明示「未提供」；欄位來源可追溯；匯入與手動覆寫優先序固定（人工＞匯入＞業務系統對照表）。
R16 驗收：未按不送；送前顯示收件人與內容；成功／失敗／不確定都留事件 ID；重送可辨識；只能看的帳號後端擋。
"""
import io
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import alert_contact as ac  # noqa: E402
import alert_notify as an  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import import_templates  # noqa: E402
import wug_alert as wa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"          # 測試用假密碼（不是真實憑證）


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    c.execute("INSERT INTO business_system (api_id, name, ap_department, ap_owner, sys_class) VALUES "
              "('N-001', '甲系統', '資訊一部', '王小明', 'S1-第一類系統軟體（核心）'), ('N-002', '乙系統', NULL, NULL, NULL)")
    c.commit()
    yield c
    c.close()


def _xlsx(rows, head=("APID", "AP團隊", "單位", "系統名稱", "分級", "email")):
    wb = Workbook()
    ws = wb.active
    ws.append(list(head))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _apply(conn, name, data, user="admin"):
    return ac.apply(conn, name, data, user, ac.plan(conn, name, data)["plan_digest"])


def _send(conn, eid, user, resend=False):
    return an.send(conn, eid, user, resend=resend, preview_hash=an.preview(conn, eid, user)["preview_hash"])


def test_A1_有錯誤整份不能匯入_一筆都不動(conn):
    _apply(conn, "好.xlsx", _xlsx([("N-001", "A組", "部", "甲", "第一類", "a@example.test"), ("N-002", "B組", "部", "乙", "第二類", None)]))
    conn.commit()
    before = [tuple(r) for r in conn.execute("SELECT api_id, ap_team FROM alert_contact ORDER BY api_id")]
    assert len(before) == 2
    for bad in ([("N-001", "A2", "部", "甲", "第九類", None)],                       # 格式錯
                [("N-001", "A2", None, None, None, None), ("N-001", "A3", None, None, None, None)],   # APID 重複
                [(None, "沒有APID", "部", None, None, None), ("N-002", "B2", None, None, None, None)]):  # 缺必填
        data = _xlsx(bad)
        p = ac.plan(conn, "壞.xlsx", data)
        assert p["can_confirm"] is False and p["blockers"]
        with pytest.raises(ac.ImportBlocked):
            ac.apply(conn, "壞.xlsx", data, "admin", p["plan_digest"])
        conn.rollback()
        assert [tuple(r) for r in conn.execute("SELECT api_id, ap_team FROM alert_contact ORDER BY api_id")] == before
    # 缺欄位（整份取代會清空那幾欄）也擋
    data = _xlsx([("N-001", "A")], head=("APID", "AP團隊"))
    assert ac.plan(conn, "少欄.xlsx", data)["can_confirm"] is False


def test_A1_預覽過期就拒絕_預覽筆數等於寫入_可以還原(conn):
    d1 = _xlsx([("N-001", "A組", "部", "甲", "第一類", None)])
    p = ac.plan(conn, "1.xlsx", d1)
    with pytest.raises(ac.ImportBlocked, match="過期"):
        ac.apply(conn, "1.xlsx", d1, "admin", "")
    out = ac.apply(conn, "1.xlsx", d1, "admin", p["plan_digest"])
    conn.commit()
    assert out["written"] == p["total"] == 1
    d2 = _xlsx([("N-002", "B組", "部", "乙", "第二類", None)])
    p2 = ac.plan(conn, "2.xlsx", d2)
    _apply(conn, "x.xlsx", _xlsx([("N-009", "X", "部", "丙", "第三類", None)]))     # 預覽之後資料庫被改了
    conn.commit()
    with pytest.raises(ac.ImportBlocked, match="過期"):
        ac.apply(conn, "2.xlsx", d2, "admin", p2["plan_digest"])
    conn.rollback()
    snaps = ac.snapshots(conn)
    first = next(s for s in snaps if s["source_file"] == "x.xlsx")           # x.xlsx 匯入前＝只有 N-001
    r = ac.restore(conn, first["id"], "admin")
    conn.commit()
    assert r["restored"] == 1 and [x[0] for x in conn.execute("SELECT api_id FROM alert_contact")] == ["N-001"]
    assert ac.snapshots(conn)[0]["reason"].startswith("還原快照")                  # 還原前也存了一份


def test_A6_預覽後收件人或聯絡資訊變了_送出被拒(conn, monkeypatch):
    monkeypatch.setattr(an, "_target", lambda c: ({"channel": "webhook"}, "Teams 頻道（Workflow：example.test）", None))
    eid = _event(conn)
    pv = an.preview(conn, eid, "alice")
    assert pv["recipient"].startswith("會送到：Teams 頻道") and "不會寄給 AP" in pv["recipient"]     # B4
    assert any(l.startswith("請處理：") for l in pv["lines"])                                       # B5
    ac.save_override(conn, "N-001", {"email": "new@example.test"}, "admin")                        # 聯絡資訊變了
    conn.commit()
    with pytest.raises(an.NotifyRefused, match="重新"):
        an.send(conn, eid, "alice", preview_hash=pv["preview_hash"])
    with pytest.raises(an.NotifyRefused):
        an.send(conn, eid, "alice", preview_hash="")
    monkeypatch.setattr(an, "_target", lambda c: ({"channel": "email"}, "信箱 other@example.test", None))   # 收件人變了
    pv2 = an.preview(conn, eid, "alice")
    monkeypatch.setattr(an, "_target", lambda c: ({"channel": "webhook"}, "Teams 頻道（Workflow：example.test）", None))
    with pytest.raises(an.NotifyRefused):
        an.send(conn, eid, "alice", preview_hash=pv2["preview_hash"])


def test_B5_磁碟類告警的請處理():
    assert "housekeeping" in an.action_text({"monitor": "Disk Utilization", "status_text": "", "open": True})
    assert "確認設備" in an.action_text({"monitor": "Ping", "status_text": "Down", "open": True})


def test_沒匯入時_退回業務系統對照表_缺的寫未提供(conn):
    c = ac.Resolver(conn).get("N-001")
    assert c["system_name"] == "甲系統" and c["system_name_src"] == ac.SRC_BIZ
    assert c["unit"] == "資訊一部" and c["sys_class"] == "第一類"
    assert c["ap_team"] is None and c["ap_team_text"] == "未提供" and c["email_text"] == "未提供"
    assert ac.Resolver(conn).get("N-002")["sys_class_text"] == "未提供"      # 未分級不猜


def test_優先序_人工大於匯入大於對照表(conn):
    ac.ensure(conn)
    _apply(conn, "聯絡.xlsx", _xlsx([("N-001", "交易AP組", "資訊二部", "甲系統（新名）", "第二類", "ap1@example.test")]))
    c = ac.Resolver(conn).get("N-001")
    assert c["unit"] == "資訊二部" and c["unit_src"].startswith(ac.SRC_IMPORT) and c["sys_class"] == "第二類"
    ac.save_override(conn, "N-001", {"unit": "資訊三部"}, "admin")
    c = ac.Resolver(conn).get("N-001")
    assert c["unit"] == "資訊三部" and c["unit_src"] == ac.SRC_OVERRIDE
    assert c["ap_team"] == "交易AP組"                                          # 其他欄位照匯入
    # 重新匯入（沒有 N-001）：匯入值消失，人工覆寫留著，其餘退回對照表
    _apply(conn, "聯絡2.xlsx", _xlsx([("N-002", "報表組", None, None, "3", None)]))
    c = ac.Resolver(conn).get("N-001")
    assert c["unit"] == "資訊三部" and c["system_name"] == "甲系統" and c["ap_team"] is None
    # 清掉覆寫 → 回到對照表
    ac.save_override(conn, "N-001", {"unit": ""}, "admin")
    assert ac.Resolver(conn).get("N-001")["unit"] == "資訊一部"
    assert conn.execute("SELECT COUNT(*) FROM contact_audit WHERE kind = 'alert_contact'").fetchone()[0] == 4


def test_匯入預覽_範例略過_重複不猜_格式錯誤列出(conn):
    data = _xlsx([("範例-001", "x", "x", "x", "第一類", "a@example.test"),
                  ("N-001", "A組", "部", "甲", "第一類", "ok@example.test; b@example.test"),
                  ("N-002", "B組", "部", "乙", "第九類", "not-an-email"),
                  ("N-009", "C組", None, None, None, None), ("N-009", "D組", None, None, None, None)])
    p = ac.plan(conn, "x.xlsx", data)
    assert p["skipped_example"] == 1 and p["duplicates"] == ["N-009"]
    assert p["total"] == 2 and p["new"] == 2 and p["removed"] == 0
    msgs = " ".join(e["message"] for e in p["errors"])
    assert "第九類" in msgs and "not-an-email" in msgs and "N-009" in " ".join(e["api_id"] for e in p["errors"])
    rows = p["_rows"]
    assert rows["N-001"]["email"] == "ok@example.test; b@example.test" and rows["N-002"]["sys_class"] is None


def test_範例檔原封匯回_一筆都不寫(conn):
    fname, data = import_templates.build("alert_contact")
    assert fname.endswith(".xlsx")
    p = ac.plan(conn, fname, data)
    assert p["total"] == 0 and p["skipped_example"] == 2 and not p["missing_columns"]


def test_告警列帶五欄_沒有APID的寫未提供(conn):
    rows = [{"api_id": "N-001"}, {"api_id": None}, {"api_ids": ["N-001", "N-002"]}]
    ac.attach(conn, rows)
    assert rows[0]["c_system_name"] == "甲系統" and rows[0]["c_email"] == "未提供"
    assert all(rows[1]["c_" + f] == "未提供" for f in ac.FIELDS) and rows[1]["c_note"]
    assert rows[2]["c_system_name"] == "甲系統／乙系統"


# ── R16 ────────────────────────────────────────────────────────────────
def _event(c, ip="192.0.2.10", api_id="N-001"):
    wa.ensure(c)
    cur = c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, match, api_id, system_name, tier, started_at, last_seen, "
                    "duration_min, created_at) VALUES ('7', 'Ping', 'dev7', ?, 'matched', ?, '甲系統', 'red', '2026-11-04 08:00:00', "
                    "'2026-11-04 08:10:00', 10, '2026-11-04 08:00:00')", (ip, api_id))
    c.commit()
    return cur.lastrowid


def test_Teams沒啟用_預覽講原因_送出被拒_什麼都不寫(conn):
    eid = _event(conn)
    pv = an.preview(conn, eid, "alice")
    assert pv["can_send"] is False and "沒有啟用" in pv["hold_reason"]
    assert "甲系統" in pv["lines"][0] and pv["contact"]["email"] == "未提供"
    with pytest.raises(an.NotifyRefused):
        an.send(conn, eid, "alice")
    assert an.history(conn, eid) == []


def test_送出排進佇列_重送要確認_結果對到投遞紀錄(conn, monkeypatch):
    import teams_notify as tn
    monkeypatch.setattr(an, "_target", lambda c: ({"channel": "webhook"}, "Teams 頻道（Workflow：example.test）", None))
    eid = _event(conn)
    r1 = _send(conn, eid, "alice")
    conn.commit()
    assert r1["seq"] == 1 and not r1["is_resend"]
    d = conn.execute("SELECT kind, source_ref, status, payload_json FROM teams_notify_delivery WHERE event_key = ?", (r1["delivery_key"],)).fetchone()
    assert d["kind"] == "wug_manual" and d["source_ref"] == f"alert:{eid}" and d["status"] == "pending"
    assert f"#{eid}" in d["payload_json"]
    with pytest.raises(an.NotifyRefused, match="確認重送"):
        _send(conn, eid, "alice")
    r2 = _send(conn, eid, "bob", resend=True)
    conn.commit()
    assert r2["seq"] == 2 and r2["is_resend"] and "重送" in r2["title"]
    for key, st, want in ((r1["delivery_key"], "sent", "成功"), (r2["delivery_key"], "uncertain", "不確定")):
        conn.execute("UPDATE teams_notify_delivery SET status = ? WHERE event_key = ?", (st, key))
    h = {x["seq"]: x for x in an.history(conn, eid)}
    assert h[1]["result"] == "成功" and h[2]["result"] == "不確定" and h[2]["kind_label"] == "第 2 次（重送）"
    assert all(x["event_id"] == eid for x in h.values())
    conn.execute("UPDATE teams_notify_delivery SET status = 'dead' WHERE event_key = ?", (r2["delivery_key"],))
    assert {x["seq"]: x["result"] for x in an.history(conn, eid)}[2] == "失敗"
    rows = [{"id": eid}]
    an.attach(conn, rows)
    assert rows[0]["notified_n"] == 2 and "失敗" in rows[0]["notified_text"]
    assert tn.STATUS_LABEL       # 佇列模組照舊


# ── 權限（API）────────────────────────────────────────────────────────
@pytest.fixture()
def web(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "w.db"
    db.init_db(p)

    def _get():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get
    c = db.get_connection(p)
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, name, auth.hash_password(_PW), role=role)
    c.execute("INSERT INTO business_system (api_id, name) VALUES ('N-001', '甲系統')")
    c.commit()
    eid = _event(c)
    c.close()

    def client(name):
        t = TestClient(api.app)
        assert t.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
        return t

    yield client, eid
    api.app.dependency_overrides.clear()


def test_只能看的帳號_看得到聯絡資訊_但不能改不能通知(web):
    client, eid = web
    v = client("vera")
    r = v.get("/api/v2/alert-contacts")
    assert r.status_code == 200 and r.json()["can_edit"] is False
    assert v.put("/api/v2/alert-contacts/N-001", json={"unit": "x"}).status_code == 403
    assert v.get(f"/api/v2/wug-alerts/events/{eid}/notify").status_code == 403
    assert v.post(f"/api/v2/wug-alerts/events/{eid}/notify", json={}).status_code == 403
    assert v.get("/api/v2/today").json()["can_notify"] is False


def test_一般帳號_可以預覽通知_不能改聯絡資訊_Teams沒開送出409(web):
    client, eid = web
    a = client("alice")
    assert a.put("/api/v2/alert-contacts/N-001", json={"unit": "x"}).status_code == 403
    pv = a.get(f"/api/v2/wug-alerts/events/{eid}/notify")
    assert pv.status_code == 200 and pv.json()["can_send"] is False
    assert a.post(f"/api/v2/wug-alerts/events/{eid}/notify", json={}).status_code == 409


def test_管理員_手動改與匯入預覽(web):
    client, _ = web
    ad = client("admin")
    r = ad.put("/api/v2/alert-contacts/N-001", json={"ap_team": "交易AP組", "email": "bad"})
    assert r.status_code == 400
    r = ad.put("/api/v2/alert-contacts/N-001", json={"ap_team": "交易AP組", "sys_class": "第一類"})
    assert r.status_code == 200 and r.json()["ap_team"] == "交易AP組" and r.json()["ap_team_src"] == "人工"
    data = _xlsx([("N-001", "X", "Y", "Z", "第三類", "z@example.test")])
    pv = ad.post("/api/v2/alert-contacts/import", files={"file": ("c.xlsx", data)}, data={"confirm": "false"})
    assert pv.status_code == 200 and pv.json()["new"] == 1 and "_rows" not in pv.json()
    ok = ad.post("/api/v2/alert-contacts/import", files={"file": ("c.xlsx", data)}, data={"confirm": "true", "plan_digest": pv.json()["plan_digest"]})
    assert ok.status_code == 200
    row = next(x for x in ad.get("/api/v2/alert-contacts").json()["rows"] if x["api_id"] == "N-001")
    assert row["ap_team"] == "交易AP組" and row["unit"] == "Y"           # 人工覆寫贏、其餘用匯入
