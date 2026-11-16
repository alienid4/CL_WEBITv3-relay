"""EOS 追蹤 審查修正輪（2.041.52）：用語、日期上限、整數版本鎖、消失列保留待辦與作廢、通知對應、台北時區、
零寬字元、未知欄位 400、長度上限、設定錯誤不吞、verify_numbers 擴充。全部假資料、可注入時間。"""
import json
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROLE_AWARE_TESTS = True
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(BACKEND))

import ad_import  # noqa: E402
import db  # noqa: E402
import eos  # noqa: E402
import eos_follow as ef  # noqa: E402
import eos_notify as en  # noqa: E402

TODAY = date(2026, 11, 1)
NOW = datetime(2026, 11, 1, 10, 0, 0)       # 台北時間（naive）
FAKE_OS = [{"name": "Rocky Linux 9", "eos_date": "2027-03-01", "source_url": "https://example.invalid/r"},
           {"name": "Ubuntu 18.04", "eos_date": "2023-05-31", "source_url": "https://example.invalid/u"}]
HOLD_OK = {"reply_state": "擬保留", "reply_source": "王小明 email 2026-10-20", "hold_reason_class": "原廠或廠商相容性",
           "hold_basis_text": "廠商應用僅支援此版本", "hold_evidence_ref": "廠商信件 2026-10-01", "hold_review_date": "2027-04-30"}


@pytest.fixture(autouse=True)
def fake_eos_tables():
    eos._os_table = FAKE_OS
    eos._hw_table = []
    yield
    eos.reload_tables()


def _hw(c, serial, host, ip, os_=None):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, asset_status, environment) VALUES (?,?,?,?,?,'正式')",
              (serial, host, ip, os_, "使用中"))


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    _hw(c, "S1", "srv-a", "10.99.0.1", "Rocky Linux 9")
    c.execute("INSERT INTO software (asset_serial, owner) VALUES ('S1','王小明')")
    _hw(c, "S2", "srv-b", "10.99.0.2", "Ubuntu 18.04")
    c.execute("INSERT INTO software (asset_serial, owner) VALUES ('S2','李大華')")
    _hw(c, "S3", "srv-c", "10.99.0.3", "Ubuntu 18.04")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def ov_of(c, today=TODAY):
    return ef.overview(c, today)


def row(ov, host, owner=None):
    hit = [r for r in ov["rows"] if r["hostname"] == host and (owner is None or r["owner_ref"] == owner)]
    assert len(hit) == 1, (host, owner, [(r["hostname"], r["owner_ref"]) for r in ov["rows"]])
    return hit[0]


def tile(ov, key):
    return next(t["count"] for t in ov["tiles"] if t["key"] == key)


def put(c, ho, fields, reason=None, now=NOW, **kw):
    r = row(ov_of(c, now.date()), *ho)
    return ef.upsert(c, r["item_key"], r["owner_ref"], fields, reason, "alice", now, **kw)


def sim_state(c, host, owner, bucket=180, at="2026-10-12 08:00:00", recipient=None):
    cands, _ = en.eos_candidates(c, TODAY)
    cand = next(x for x in cands if x["hostname"] == host)
    en._ensure(c)
    c.execute("INSERT OR REPLACE INTO eos_notify_state (state_key, last_bucket, last_sent_at, last_recipient) VALUES (?,?,?,?)",
              (f"{cand['track']}|{cand['mk']}|{owner}|{cand['item']}", bucket, at, recipient))
    c.commit()


def sim_log(c, owner, status, recipient="x@example.invalid", at="2026-10-20 08:00:00"):
    en._ensure(c)
    c.execute("INSERT INTO eos_notify_log (sent_at, schedule, recipient, owner_name, subject, status, detail) VALUES (?,?,?,?,?,?,?)",
              (at, "monthly", recipient, owner, "s", status, "d"))
    c.commit()


def add_ad(c, eid, name, mail):
    c.execute(ad_import.TABLE_SQL)
    c.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, manager_mail, dept_name) VALUES (?,?,?,?,?,?)",
              (eid, name, mail, 1, None, "架構部"))
    c.commit()


# ───────────── 1. 用語：使用者看得到的字不得出現「核准」「approve」
def test_使用者可見文字沒有核准一詞():
    for f in (BACKEND / "eos_follow.py", FE / "components" / "v2" / "EosFollow.vue", FE / "utils" / "eosFollow.ts"):
        t = f.read_text(encoding="utf-8")
        assert "核准" not in t and not re.search(r"approv", t, re.I), f.name
    v = (BACKEND / "routers" / "vulns.py").read_text(encoding="utf-8")
    seg = v[v.index("# ===== EOS 通知與回覆追蹤"):]
    assert "核准" not in seg and not re.search(r"approv", seg, re.I)
    assert "核准" not in ef.DISCLAIMER
    man = (FE / "content" / "manual.ts").read_text(encoding="utf-8")
    life = man[man.index("id: 'lifecycle'"):man.index("id: 'data'")]
    assert "核准" not in life


def test_API回應沒有核准一詞(conn):
    ef.seed_demo(conn, "admin", TODAY)
    blob = json.dumps(ov_of(conn), ensure_ascii=False) + json.dumps(ef.overview(conn, TODAY, True), ensure_ascii=False)
    blob += json.dumps(ef.reminders(ov_of(conn)), ensure_ascii=False) + json.dumps(ef.detail(conn, "x", "y"), ensure_ascii=False)
    blob += json.dumps(ef.load_settings(conn), ensure_ascii=False)
    assert "核准" not in blob and not re.search(r"approv", blob, re.I)


# ───────────── 2. 日期上限
def test_日期上限邊界(conn):
    limit = ef.add_months(TODAY, 24)                         # 2028-11-01
    assert limit == date(2028, 11, 1)
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": limit.isoformat()})            # 剛好 24 個月：可以
    with pytest.raises(ValueError, match="不能晚於 2028-11-01"):
        put(conn, ("srv-a", "王小明"), {"hold_review_date": "2028-11-02"}, "太遠")                 # 多一天：拒絕
    with pytest.raises(ValueError, match="今天起 24 個月內"):
        put(conn, ("srv-a", "王小明"), {"hold_review_date": "9999-12-31"}, "永久綠燈")
    with pytest.raises(ValueError, match="承諾日"):
        put(conn, ("srv-b", "李大華"), {"reply_state": "規劃更換", "committed_date": "2028-11-02", "reply_source": "x"})
    put(conn, ("srv-b", "李大華"), {"reply_state": "規劃更換", "committed_date": "2028-11-01", "reply_source": "x"})
    for bad in ("0000-00-00", "0001-13-01"):
        with pytest.raises(ValueError, match="有效日期"):
            put(conn, ("srv-a", "王小明"), {"hold_review_date": bad}, "x")


def test_日期上限可調_範圍1到60(conn):
    cfg = ef.load_settings(conn)
    cfg["max_horizon_months"] = 12
    ef.save_settings(conn, cfg, "admin")
    with pytest.raises(ValueError, match="2027-11-01"):
        put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2027-11-02"})
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2027-11-01"})
    for bad in (0, 61, "x"):
        with pytest.raises(ValueError):
            ef.save_settings(conn, {**cfg, "max_horizon_months": bad}, "admin")
    assert ef.DEFAULTS["max_horizon_months"] == 24


# ───────────── 3. 整數版本鎖
def test_版本號每次寫入加一_同一秒兩次更新也擋得住(conn):
    r1 = put(conn, ("srv-a", "王小明"), HOLD_OK)["row"]
    assert r1["version"] == 1
    r2 = put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-05-30"}, "甲改", expected_version=1)["row"]
    assert r2["version"] == 2 and r2["updated_at"] == r1["updated_at"]          # 同一秒：時間戳分不出來，版本號分得出來
    with pytest.raises(ef.Conflict):
        put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-06-30"}, "乙改（拿舊版本）", expected_version=1)
    assert ef.get_follow(conn, row(ov_of(conn), "srv-a")["item_key"], "王小明")["hold_review_date"] == "2027-05-30"
    r3 = put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-06-30"}, "乙重新整理後改", expected_version=2)["row"]
    assert r3["version"] == 3
    for bad in (-1, True, "1", 3_000_000_000):
        with pytest.raises(ValueError, match="版本號"):
            put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-07-30"}, "x", expected_version=bad)


def test_沒帶版本號時UPDATE本身仍以版本為條件(conn, monkeypatch):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    real = ef.get_follow
    stale = real(conn, row(ov_of(conn), "srv-a")["item_key"], "王小明")
    conn.execute("UPDATE eos_follow SET version = version + 1")          # 別人在我讀完之後寫了
    conn.commit()
    monkeypatch.setattr(ef, "get_follow", lambda *a, **k: stale)
    with pytest.raises(ef.Conflict):
        put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-05-30"}, "我改")


# ───────────── 4. 消失列保留待辦；作廢
def _retire(conn, serial):
    conn.execute("UPDATE hardware SET asset_status='報廢' WHERE asset_serial=?", (serial,))
    conn.commit()


def test_項目消失的列仍保留待辦旗標_進磚與催辦預覽(conn):
    sim_state(conn, "srv-a", "王小明", bucket=180)
    put(conn, ("srv-b", "李大華"), {"reply_state": "規劃更換", "committed_date": "2026-10-01", "reply_source": "x"})
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-10-15"})
    sim_state(conn, "srv-b", "李大華", bucket=0)
    _retire(conn, "S1")
    _retire(conn, "S2")
    ov = ov_of(conn)
    a, b = row(ov, "srv-a"), row(ov, "srv-b")
    assert a["stale"] and b["stale"]
    assert "hold_expired" in a["flags"] and "plan_overdue" in b["flags"] and a["todo"] and b["todo"]
    assert "項目或負責人已不在目前清單" in a["status"] and "項目或負責人已不在目前清單" in b["status"]
    assert tile(ov, "hold_expired") == 1 and tile(ov, "plan_overdue") == 1
    cfg = ef.load_settings(conn)
    cfg["reminder"].update({"plan_overdue_days": 1, "hold_expired_days": 1})
    ef.save_settings(conn, cfg, "admin")
    rem = ef.reminders(ov_of(conn))
    assert {i["hostname"] for i in rem["items"]} == {"srv-a", "srv-b"} and all(i["stale"] for i in rem["items"])
    for t in ov["tiles"]:
        assert t["count"] == len(ef.drill(ov, t["key"]))


def test_消失列保留最後已知的通知狀態_未回覆仍算(conn):
    put(conn, ("srv-b", "李大華"), {})                                  # 先建立一筆預設追蹤（待回覆）
    sim_state(conn, "srv-b", "李大華", bucket=0)
    _retire(conn, "S2")
    b = row(ov_of(conn), "srv-b")
    assert b["stale"] and b["notified"] and "unreplied" in b["flags"] and "unnotified" not in b["flags"]


def test_作廢追蹤_需理由_留歷史_不可再編輯_不進待辦_全部仍看得到(conn):
    put(conn, ("srv-b", "李大華"), {"reply_state": "規劃更換", "committed_date": "2026-10-01", "reply_source": "x"})
    r = row(ov_of(conn), "srv-b")
    with pytest.raises(ValueError, match="仍在目前 EOS 清單"):
        ef.void(conn, r["item_key"], "李大華", "想作廢", "alice", NOW)                       # 還在清單：不能作廢，請更正
    _retire(conn, "S2")
    with pytest.raises(ValueError, match="作廢必須填理由"):
        ef.void(conn, r["item_key"], "李大華", "  ", "alice", NOW)
    out = ef.void(conn, r["item_key"], "李大華", "機器已汰換，由新主機接替", "alice", NOW, succession_note="由 srv-new / 王大明 接替")["row"]
    assert out["voided_at"].endswith("+08:00") and out["succession_note"] == "由 srv-new / 王大明 接替" and out["version"] == 2
    h = conn.execute("SELECT op, reason FROM eos_follow_history ORDER BY id").fetchall()
    assert [x["op"] for x in h] == ["create", "void"] and h[1]["reason"] == "機器已汰換，由新主機接替"
    with pytest.raises(ValueError, match="已作廢"):
        put_by_key = ef.upsert(conn, r["item_key"], "李大華", {"reply_source": "改"}, "x", "alice", NOW)
    with pytest.raises(ValueError, match="已經作廢"):
        ef.void(conn, r["item_key"], "李大華", "again", "alice", NOW)
    ov = ov_of(conn)
    v = row(ov, "srv-b")
    assert v["flags"] == ["voided"] and v["todo"] is False and v["status"] == "已作廢追蹤"
    assert tile(ov, "plan_overdue") == 0 and tile(ov, "voided") == 1 and v in ef.drill(ov, "all") and v not in ef.drill(ov, "todo")


def test_完成的列不用作廢_API作廢權限(conn):
    put(conn, ("srv-b", "李大華"), {"reply_state": "規劃更換", "committed_date": "2026-12-01", "reply_source": "x",
                                    "done_state": "更換完成", "done_evidence_ref": "驗收單"})
    _retire(conn, "S2")
    r = row(ov_of(conn), "srv-b")
    with pytest.raises(ValueError, match="已結案"):
        ef.void(conn, r["item_key"], "李大華", "x", "alice", NOW)


# ───────────── 5. 通知對應
def test_失敗紀錄只套給同一收件地址_不套給別的地址或別人(conn):
    add_ad(conn, "E1", "王小明", "ming@example.invalid")
    add_ad(conn, "E2", "李大華", "lee@example.invalid")
    sim_log(conn, "李大華", "failed", "lee@example.invalid", "2026-10-25 08:00:00")
    sim_log(conn, "王小明", "failed", "other-address@example.invalid", "2026-10-25 08:00:00")      # 王小明的別的收件地址
    ov = ov_of(conn)
    assert "delivery_failed" in row(ov, "srv-b")["flags"]
    assert "delivery_failed" not in row(ov, "srv-a")["flags"]         # 王小明現行信箱 ming@ 沒有失敗；別的地址的失敗不套過來


def test_最新失敗優先_與已通知並存(conn):
    sim_state(conn, "srv-a", "王小明", bucket=180, at="2026-10-12 08:00:00")
    sim_log(conn, "王小明", "failed", "ming@example.invalid", "2026-10-30 08:00:00")             # 之後那一封失敗
    a = row(ov_of(conn), "srv-a")
    assert a["notified"] is True and "delivery_failed" in a["flags"] and "unreplied" in a["flags"]
    assert "最近一次寄送失敗" in a["status"]
    sim_log(conn, "王小明", "sent", "ming@example.invalid", "2026-10-31 08:00:00")                 # 再之後成功 → 失敗旗標消失
    assert "delivery_failed" not in row(ov_of(conn), "srv-a")["flags"]


def test_失敗早於最後成功不算_超過時間窗不算(conn):
    sim_state(conn, "srv-a", "王小明", bucket=180, at="2026-10-28 08:00:00")
    sim_log(conn, "王小明", "failed", "x@example.invalid", "2026-10-20 08:00:00")                  # 早於最後成功
    assert "delivery_failed" not in row(ov_of(conn), "srv-a")["flags"]
    sim_log(conn, "李大華", "failed", "y@example.invalid", "2026-08-01 08:00:00")                  # 三個月前
    assert "delivery_failed" not in row(ov_of(conn), "srv-b")["flags"]


def test_收件地址變更_舊地址的成功不能把新地址判成已通知(conn):
    add_ad(conn, "E1", "王小明", "new-ming@example.invalid")
    sim_state(conn, "srv-a", "王小明", bucket=180, recipient="old-ming@example.invalid")
    a = row(ov_of(conn), "srv-a")
    assert a["notified"] is False and "unnotified" in a["flags"]
    sim_state(conn, "srv-a", "王小明", bucket=180, recipient="NEW-ming@example.invalid")          # 大小寫不同仍是同一地址
    assert row(ov_of(conn), "srv-a")["notified"] is True
    sim_state(conn, "srv-a", "王小明", bucket=180, recipient=None)                                  # 舊資料沒記收件地址：視為相容、算已通知
    assert row(ov_of(conn), "srv-a")["notified"] is True


def test_通知引擎同樣不把舊地址的成功當新地址已通知(conn):
    add_ad(conn, "E1", "王小明", "new-ming@example.invalid")
    en.save_settings(conn, {"enabled": True, "fallback_to": "soc@example.invalid"})
    sim_state(conn, "srv-a", "王小明", bucket=180, recipient="old-ming@example.invalid")
    data = en.collect(conn, TODAY)
    assert any(i["hostname"] == "srv-a" for g in data["by_owner"].values() for i in g["items"])       # 引擎會再寄給新地址
    sim_state(conn, "srv-a", "王小明", bucket=180, recipient="new-ming@example.invalid")
    data = en.collect(conn, TODAY)
    assert not any(i["hostname"] == "srv-a" for g in data["by_owner"].values() for i in g["items"])


def test_顯示名與登記名不一致_正規化比對對得上(conn):
    assert ef.norm_name("王小明 (Ming)") == ef.norm_name("王小明") == ef.norm_name(" 王小明（ming）")
    sim_log(conn, "王小明 (Ming)", "failed", "ming@example.invalid", "2026-10-30 08:00:00")        # AD 沒匯入：用姓名正規化比對
    assert "delivery_failed" in row(ov_of(conn), "srv-a")["flags"]


def test_對不上通知紀錄顯示無法對應而不是尚未通知(conn):
    sim_log(conn, "別人", "sent", "z@example.invalid", "2026-10-20 08:00:00")                      # 系統有寄送紀錄，但沒有這兩位的
    a = row(ov_of(conn), "srv-a")
    assert a["notify_map"] == "unmapped" and a["status"] == "無法對應通知紀錄"
    assert "unnotified" in a["flags"]                                    # 仍是待辦（沒有已通知的證據），只是講實話
    en._ensure(conn)
    fresh = row(ov_of(conn), "srv-c")
    assert fresh["status"] == "待指派"


def test_完全沒有任何寄送紀錄時仍是尚未通知(tmp_path):
    db.init_db(tmp_path / "e.db")
    c = db.get_connection(tmp_path / "e.db")
    _hw(c, "S1", "srv-a", "10.99.0.1", "Rocky Linux 9")
    c.execute("INSERT INTO software (asset_serial, owner) VALUES ('S1','王小明')")
    c.commit()
    assert row(ov_of(c), "srv-a")["status"] == "尚未通知"
    c.close()


# ───────────── 6. 時區：一律台北
def test_今天用台北日期_不看伺服器時區():
    utc_prev_day = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)       # UTC 還是 10/31，台北已是 11/01 00:30
    assert ef.today_tpe(utc_prev_day) == date(2026, 11, 1)
    assert ef.today_tpe(datetime(2026, 10, 31, 15, 59, 59, tzinfo=timezone.utc)) == date(2026, 10, 31)
    assert ef.today_tpe(datetime(2026, 10, 31, 16, 0, 0)) == date(2026, 11, 1)           # naive 視為 UTC
    assert ef.now_tpe(utc_prev_day).strftime("%H:%M") == "00:30"


def test_台北00點30_重新檢視日當天已到期(conn):
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-11-01"}, now=datetime(2026, 10, 30, 9, 0))
    utc = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)
    ov = ef.overview(conn, now_utc=utc)
    assert ov["today"] == "2026-11-01" and "hold_expired" in row(ov, "srv-a")["flags"]
    ov = ef.overview(conn, now_utc=datetime(2026, 10, 31, 15, 30, tzinfo=timezone.utc))          # 台北 10/31 23:30：前一天，尚未到期
    assert ov["today"] == "2026-10-31" and "hold_expired" not in row(ov, "srv-a")["flags"]


def test_稽核時間格式統一並標示時區(conn):
    r = put(conn, ("srv-a", "王小明"), HOLD_OK, now=datetime(2026, 11, 1, 0, 30, 5))["row"]
    assert r["created_at"] == "2026-11-01 00:30:05+08:00" and r["hold_recorded_at"] == "2026-11-01 00:30:05+08:00"
    assert ef.overview(conn, TODAY)["tz"].startswith("Asia/Taipei")
    h = conn.execute("SELECT at FROM eos_follow_history").fetchone()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+08:00", h)


# ───────────── 8. 零寬字元
@pytest.mark.parametrize("field", ["hold_basis_text", "hold_evidence_ref"])
@pytest.mark.parametrize("zw", ["​", "‌‍", "﻿", " ⁠ "])
def test_零寬字元不能當依據(conn, field, zw):
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, field: zw})
    a = row(ov_of(conn), "srv-a")
    assert "hold_no_basis" in a["flags"]
    assert (ef.get_follow(conn, a["item_key"], "王小明")[field]) is None            # 存進去的是空，不是隱形字元


def test_零寬字元不能當回覆來源或完成證據(conn):
    with pytest.raises(ValueError, match="回覆來源"):
        put(conn, ("srv-a", "王小明"), {**HOLD_OK, "reply_source": "​​"})
    put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": "2026-12-01", "reply_source": "x"})
    with pytest.raises(ValueError, match="完成證據"):
        put(conn, ("srv-a", "王小明"), {"done_state": "更換完成", "done_evidence_ref": "​"}, "x")


def test_直接SQL塞零寬字元也不算有依據(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    conn.execute("UPDATE eos_follow SET hold_basis_text = ?", ("​‌",))
    conn.commit()
    assert "hold_no_basis" in row(ov_of(conn), "srv-a")["flags"]


# ───────────── 10. 記錄人
def test_直接SQL清空記錄人_就是尚無依據(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    assert "hold_no_basis" not in row(ov_of(conn), "srv-a")["flags"]
    conn.execute("UPDATE eos_follow SET hold_recorded_by = NULL")
    conn.commit()
    a = row(ov_of(conn), "srv-a")
    assert "hold_no_basis" in a["flags"] and "記錄人／日期" in a["hold_missing"]
    conn.execute("UPDATE eos_follow SET hold_recorded_by = 'x', hold_recorded_at = ''")
    conn.commit()
    assert "記錄人／日期" in row(ov_of(conn), "srv-a")["hold_missing"]


def test_切回擬保留時記錄人更新成本次操作者(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": "2026-12-01"}, "改規劃更換")
    r = ef.upsert(conn, row(ov_of(conn), "srv-a")["item_key"], "王小明", {"reply_state": "擬保留"}, "又改回保留", "bob",
                  datetime(2026, 11, 2, 9, 0))["row"]
    assert r["hold_recorded_by"] == "bob" and r["hold_recorded_at"].startswith("2026-11-02")


# ───────────── 11. 上限與設定錯誤
def test_類別數量與長度上限(conn):
    base = ef.load_settings(conn)
    many = [{"name": f"類別{i}", "need_note": False} for i in range(51)]
    with pytest.raises(ValueError, match="最多 50"):
        ef.save_settings(conn, {**base, "reason_classes": many}, "admin")
    ef.save_settings(conn, {**base, "reason_classes": many[:50]}, "admin")
    with pytest.raises(ValueError, match="太長"):
        ef.upsert(conn, "x" * 301, "王小明", {}, None, "alice", NOW)
    with pytest.raises(ValueError, match="太長"):
        ef.upsert(conn, "os|a|b", "y" * 201, {}, None, "alice", NOW)
    with pytest.raises(ValueError, match="太長"):
        ef.detail(conn, "x" * 301, "y")


def test_設定讀取只容忍表不存在_其他資料庫錯誤要報(tmp_path):
    class Boom:
        def __init__(self, msg):
            self.msg = msg

        def execute(self, *a, **k):
            raise sqlite3.OperationalError(self.msg)
    assert ef.load_settings(Boom("no such table: app_settings"))["max_horizon_months"] == 24
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        ef.load_settings(Boom("disk I/O error"))
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        ef.load_settings(Boom("database is locked"))


def test_舊結構升級_補上版本與作廢欄位(tmp_path):
    p = tmp_path / "o.db"
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE eos_follow (id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT NOT NULL, owner_ref TEXT NOT NULL, "
              "reply_state TEXT NOT NULL DEFAULT '待回覆', done_state TEXT NOT NULL DEFAULT '未完成', is_demo INTEGER NOT NULL DEFAULT 0)")
    c.execute("INSERT INTO eos_follow (item_key, owner_ref) VALUES ('os|a|b','x')")
    c.commit()
    ef.ensure_tables(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(eos_follow)")}
    assert {"version", "voided_at", "voided_by", "void_reason", "succession_note"} <= cols
    assert c.execute("SELECT version FROM eos_follow").fetchone()[0] == 1
    c.close()


# ───────────── API
def _api(tmp, users):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(p)
    _hw(cn, "S1", "srv-a", "10.99.0.1", "Rocky Linux 9")
    cn.execute("INSERT INTO software (asset_serial, owner) VALUES ('S1','王小明')")
    for name, role in users:
        db.create_user(cn, name, auth.hash_password("test-password-123"))
        cn.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    cn.commit()
    cn.close()

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        return cl
    return client, p, api


def test_API_未知欄位400並列出_版本鎖409_作廢權限_回應無核准():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("alice", "user"), ("vera", "viewer"), ("admin", "admin")])
        alice, vera, admin = client("alice"), client("vera"), client("admin")
        r = alice.get("/api/eos/follow/overview").json()["rows"][0]
        k = {"item_key": r["item_key"], "owner_ref": r["owner_ref"]}
        bad = alice.put("/api/eos/follow", json={**k, "eos_date": "2099-01-01", "foo": 1})
        assert bad.status_code == 400 and "eos_date" in bad.json()["detail"] and "foo" in bad.json()["detail"]
        ok = alice.put("/api/eos/follow", json={**k, **HOLD_OK})
        assert ok.status_code == 200 and ok.json()["row"]["version"] == 1
        assert alice.put("/api/eos/follow", json={**k, "hold_review_date": "2027-05-30", "reason": "改", "expected_version": 1}).status_code == 200
        c409 = alice.put("/api/eos/follow", json={**k, "hold_review_date": "2027-06-30", "reason": "改", "expected_version": 1})
        assert c409.status_code == 409
        assert alice.put("/api/eos/follow", json={**k, "reason": "x", "expected_version": -5}).status_code == 422
        far = alice.put("/api/eos/follow", json={**k, "hold_review_date": "9999-12-31", "reason": "x"})
        assert far.status_code == 400 and "今天起" in far.json()["detail"]
        long = alice.put("/api/eos/follow", json={"item_key": "x" * 400, "owner_ref": "y"})
        assert long.status_code == 422
        assert alice.get("/api/eos/follow/detail", params={"item_key": "x" * 400, "owner_ref": "y"}).status_code == 422
        # 作廢：viewer 403；未消失的列 400
        assert vera.post("/api/eos/follow/void", json={**k, "reason": "x"}).status_code == 403
        assert alice.post("/api/eos/follow/void", json={**k, "reason": "x"}).status_code == 400
        cn = db.get_connection(p)
        cn.execute("UPDATE hardware SET asset_status='報廢'")
        cn.commit()
        cn.close()
        v = alice.post("/api/eos/follow/void", json={**k, "reason": "已汰換", "succession_note": "由新列接替"})
        assert v.status_code == 200 and v.json()["row"]["voided_at"]
        assert alice.put("/api/eos/follow", json={**k, "reply_source": "x", "reason": "x"}).status_code == 400
        ov = admin.get("/api/eos/follow/overview").json()
        assert any(t["key"] == "voided" and t["count"] == 1 for t in ov["tiles"])
        assert "核准" not in json.dumps(ov, ensure_ascii=False) + json.dumps(admin.get("/api/eos/follow/settings").json(), ensure_ascii=False)
        api.app.dependency_overrides.clear()


# ───────────── 7. verify_numbers 擴充
def test_verify_numbers_擴充硬規則與獨立重算(conn, monkeypatch):
    import verify_numbers as vn
    db.set_setting(conn, "verify_strict_eos", "1")
    sim_state(conn, "srv-a", "王小明", bucket=180)
    put(conn, ("srv-b", "李大華"), {"reply_state": "規劃更換", "committed_date": "2026-10-01", "reply_source": "x"})
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-11-01"})
    sim_log(conn, "李大華", "failed", "x@example.invalid", "2026-10-30 08:00:00")
    vn.FAILS = vn.CHECKS = 0
    vn.check_eos_follow(conn, today=TODAY)
    assert vn.FAILS == 0 and vn.CHECKS >= 16
    # 繞過後端寫成違規列，逐一應紅
    for sql in ("UPDATE eos_follow SET committed_date='2026-13-45' WHERE reply_state='規劃更換'",
                "UPDATE eos_follow SET committed_date='0000-00-00' WHERE reply_state='規劃更換'",
                "UPDATE eos_follow SET reply_source=NULL WHERE reply_state='規劃更換'",
                "UPDATE eos_follow SET hold_review_date='2026-02-30' WHERE reply_state='擬保留'",
                "UPDATE eos_follow SET done_state='更換完成', done_evidence_ref=' ' WHERE reply_state='規劃更換'"):
        c2 = sqlite3.connect(":memory:")
        conn.backup(c2)
        c2.row_factory = sqlite3.Row
        c2.execute(sql)
        c2.commit()
        vn.FAILS = 0
        vn.check_eos_follow(c2, today=TODAY)
        assert vn.FAILS >= 1, sql
        c2.close()
    # 磚被改壞（每一個獨立重算的旗標）
    real = ef.tiles_of
    for key in ("unreplied", "plan_overdue", "delivery_failed", "unnotified", "hold_expired"):
        monkeypatch.setattr(ef, "tiles_of", lambda rows, key=key: [{**t, "count": t["count"] + (1 if t["key"] == key else 0)} for t in real(rows)])
        vn.FAILS = 0
        vn.check_eos_follow(conn, today=TODAY)
        assert vn.FAILS >= 1, key
        monkeypatch.undo()
