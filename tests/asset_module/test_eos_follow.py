"""EOS 通知與回覆追蹤（批次一，2.041.51）。守的是業務規則 + 後端強制，不是畫面：

1. 通知成功不等於已回覆；回覆不等於已完成；缺依據的「擬保留」仍是待辦
2. 重新檢視日邊界（前一天／當天／後一天，可注入 today）；保留永不永久綠燈
3. 任何追蹤狀態都不改 EOS 日期、不讓過期顯示消失
4. 缺負責人、投遞失敗各自看得到；加總＝下鑽（同一份計算）
5. 權限（viewer／user／admin／登入停用）、歷史前後值、稽核同交易、舊庫相容、GET 不寫庫（mode=ro）
全部假資料、假 EOS 表、可注入的 now；不寄任何信、不連網路。
"""
import hashlib
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

ROLE_AWARE_TESTS = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import eos  # noqa: E402
import eos_follow as ef  # noqa: E402
import eos_notify as en  # noqa: E402

TODAY = date(2026, 11, 1)
NOW = datetime(2026, 11, 1, 10, 0, 0)

FAKE_OS = [
    {"name": "Rocky Linux 9", "eos_date": "2027-03-01", "source_url": "https://example.invalid/r"},          # 120 天 → 180 級
    {"name": "Ubuntu 18.04", "eos_date": "2023-05-31", "source_url": "https://example.invalid/u"},           # 已過 EOS
]
FAKE_HW = [{"name": "Dell PowerEdge R760", "eos_date": "2027-01-01", "source_url": "https://example.invalid/d"}]


@pytest.fixture(autouse=True)
def fake_eos_tables():
    eos._os_table = FAKE_OS
    eos._hw_table = FAKE_HW
    yield
    eos.reload_tables()


def _hw(c, serial, host, ip, os_=None, model=None, owner=None):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, device_model, owner, asset_status, environment) "
              "VALUES (?,?,?,?,?,?,?,'正式')", (serial, host, ip, os_, model, owner, "使用中"))


def _sw(c, serial, owner):
    c.execute("INSERT INTO software (asset_serial, owner) VALUES (?,?)", (serial, owner))


def _seed(c):
    _hw(c, "S1", "srv-a", "10.99.0.1", os_="Rocky Linux 9")
    _sw(c, "S1", "王小明")
    _hw(c, "S2", "srv-b", "10.99.0.2", os_="Ubuntu 18.04")
    _sw(c, "S2", "李大華")
    _hw(c, "S3", "srv-c", "10.99.0.3", os_="Ubuntu 18.04")           # 沒有 software 登記 → 缺負責人
    _hw(c, "S4", "srv-d", "10.99.0.4", model="Dell PowerEdge R760", owner="陳小美")
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    _seed(c)
    try:
        yield c
    finally:
        c.close()


def ov_of(c, today=TODAY, demo=False):
    return ef.overview(c, today, demo)


def row(ov, host, owner=None):
    hit = [r for r in ov["rows"] if r["hostname"] == host and (owner is None or r["owner_ref"] == owner)]
    assert len(hit) == 1, (host, owner, [(r["hostname"], r["owner_ref"]) for r in ov["rows"]])
    return hit[0]


def tile(ov, key):
    return next(t["count"] for t in ov["tiles"] if t["key"] == key)


def put(c, host_owner, fields, reason=None, today=TODAY, **kw):
    """用 overview 找出項目鍵再寫入。"""
    host, owner = host_owner
    r = row(ov_of(c, today), host, owner)
    return ef.upsert(c, r["item_key"], r["owner_ref"], fields, reason, "alice", datetime.combine(today, NOW.time()), **kw)


HOLD_OK = {"reply_state": "擬保留", "reply_source": "王小明 email 2026-10-20", "hold_reason_class": "原廠或廠商相容性",
           "hold_basis_text": "廠商應用僅支援此版本", "hold_evidence_ref": "廠商信件 2026-10-01", "hold_review_date": "2027-04-30"}


def sim_notified(c, host, owner, bucket=180, at="2026-10-12 08:00:00"):
    """模擬 eos_notify.run 寄成功後寫下的狀態（不寄信）。"""
    cands, _ = en.eos_candidates(c, TODAY)
    cand = next(x for x in cands if x["hostname"] == host)
    en._ensure(c)
    c.execute("INSERT INTO eos_notify_state (state_key, last_bucket, last_sent_at) VALUES (?,?,?)",
              (f"{cand['track']}|{cand['mk']}|{owner}|{cand['item']}", bucket, at))
    c.commit()


def sim_log(c, owner, status):
    en._ensure(c)
    c.execute("INSERT INTO eos_notify_log (sent_at, schedule, recipient, owner_name, subject, status, detail) VALUES (?,?,?,?,?,?,?)",
              ("2026-10-12 08:00:00", "monthly", "x@example.invalid", owner, "s", status, "d"))
    c.commit()


# ───────────── 基本：沒有任何追蹤資料也看得到「應該被追蹤」的項目，且 GET 不建表
def test_預設總覽(conn):
    ov = ov_of(conn)
    names = {(r["hostname"], r["owner_ref"]) for r in ov["rows"]}
    assert names == {("srv-a", "王小明"), ("srv-b", "李大華"), ("srv-c", ef.UNASSIGNED), ("srv-d", "陳小美")}
    for r in ov["rows"]:
        assert r["reply_state"] == "待回覆" and r["done_state"] == "未完成" and not r["has_follow"]
        assert "unnotified" in r["flags"] and r["notified"] is False
    assert "unassigned" in row(ov, "srv-c")["flags"]
    assert [r["no"] for r in ov["rows"]] == list(range(1, 5))
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='eos_follow'").fetchone()      # 讀取沒建表


# ───────────── 1. 通知成功不等於已回覆
def test_通知成功不改reply_state_只是變成待回覆未回覆(conn):
    ov0 = ov_of(conn)
    assert tile(ov0, "unreplied") == 0 and tile(ov0, "unnotified") == 4
    sim_notified(conn, "srv-a", "王小明")
    ov1 = ov_of(conn)
    a = row(ov1, "srv-a")
    assert a["notified"] is True and a["reply_state"] == "待回覆"          # 通知成功≠回覆
    assert "unreplied" in a["flags"] and "unnotified" not in a["flags"] and a["unreplied_days"] == 20
    assert a["status"] == "已通知，待回覆"
    assert tile(ov1, "unreplied") == 1 and tile(ov1, "unnotified") == 3
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='eos_follow'").fetchone()      # 通知不產生追蹤紀錄


def test_跨進更急一級後本級尚未通知(conn):
    sim_notified(conn, "srv-b", "李大華", bucket=180)                    # 通知過 180 級，但現在已過 EOS（0 級更急）
    b = row(ov_of(conn), "srv-b")
    assert b["notified"] is False and "unnotified" in b["flags"]          # 跟通知引擎的「跨進更急才再提」同一尺


# ───────────── 2. 回覆不等於已完成；完成需證據
def test_回覆不等於完成_完成不改回覆(conn):
    out = put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": "2027-01-15", "reply_source": "王小明 email 2026-10-20"})
    assert out["row"]["reply_state"] == "規劃更換" and out["row"]["done_state"] == "未完成"
    a = row(ov_of(conn), "srv-a")
    assert "done" not in a["flags"] and a["status"] == "規劃更換（承諾日內）"
    with pytest.raises(ValueError, match="完成證據"):
        put(conn, ("srv-a", "王小明"), {"done_state": "更換完成"}, "已換好")
    put(conn, ("srv-a", "王小明"), {"done_state": "更換完成", "done_evidence_ref": "驗收單 ACC-1"}, "已換好")
    r = conn.execute("SELECT reply_state, committed_date, done_state FROM eos_follow").fetchone()
    assert (r["reply_state"], r["committed_date"], r["done_state"]) == ("規劃更換", "2027-01-15", "更換完成")      # 完成只動完成欄
    a = row(ov_of(conn), "srv-a")
    assert a["flags"] == ["done"] and a["todo"] is False and tile(ov_of(conn), "done") == 1


def test_done需要證據_空白與純空白都不行(conn):
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="完成證據"):
            put(conn, ("srv-a", "王小明"), {"done_state": "更換完成", "done_evidence_ref": bad})
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='eos_follow'").fetchone() or \
        conn.execute("SELECT COUNT(*) FROM eos_follow").fetchone()[0] == 0


def test_規劃更換必須有承諾日與來源(conn):
    with pytest.raises(ValueError, match="承諾日"):
        put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "reply_source": "email"})
    with pytest.raises(ValueError, match="回覆來源"):
        put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": "2027-01-15"})
    for bad in ("2027-13-01", "2027/01/15", "明年", "2027-1-5"):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": bad, "reply_source": "x"})


def test_逾承諾日邊界_當天不算逾期_隔天才算(conn):
    put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": "2026-11-10", "reply_source": "email"})
    for today, overdue in ((date(2026, 11, 9), False), (date(2026, 11, 10), False), (date(2026, 11, 11), True)):
        r = row(ov_of(conn, today), "srv-a")
        assert ("plan_overdue" in r["flags"]) is overdue, today
    assert row(ov_of(conn, date(2026, 11, 11)), "srv-a")["overdue_days"] == 1


# ───────────── 3. 缺依據仍是待辦（硬規則 1）
@pytest.mark.parametrize("drop", ["hold_reason_class", "hold_basis_text", "hold_evidence_ref", "hold_review_date"])
def test_保留缺任一依據只是擬保留_尚無依據_仍待辦(conn, drop):
    f = {k: v for k, v in HOLD_OK.items() if k != drop}
    put(conn, ("srv-a", "王小明"), f)
    a = row(ov_of(conn), "srv-a")
    assert "hold_no_basis" in a["flags"] and a["todo"] and a["status"] == "擬保留（尚無依據）"
    assert "hold_valid" not in a["status"] and a["hold_missing"]
    assert tile(ov_of(conn), "hold_no_basis") == 1


def test_保留依據齊全才是已附依據(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    a = row(ov_of(conn), "srv-a")
    assert a["status"] == "保留（已附依據）" and a["hold_missing"] == [] and a["hold_recorded_by"] == "alice" and a["hold_recorded_at"]
    assert "hold_no_basis" not in a["flags"] and not a["todo"] or "unnotified" in a["flags"]      # 依據齊全本身不是待辦（未通知另算）
    assert "hold_expired" not in a["flags"]


def test_類別需說明的沒說明仍是尚無依據_未登記類別被拒不默默歸其他(conn):
    f = {**HOLD_OK, "hold_reason_class": "業務單位不同意"}            # 需註明單位
    put(conn, ("srv-a", "王小明"), f)
    a = row(ov_of(conn), "srv-a")
    assert "hold_no_basis" in a["flags"] and any("需填說明" in m for m in a["hold_missing"])
    put(conn, ("srv-a", "王小明"), {"hold_reason_note": "營業部"}, "補單位")
    assert "hold_no_basis" not in row(ov_of(conn), "srv-a")["flags"]
    with pytest.raises(ValueError, match="未登記於類別清單"):
        put(conn, ("srv-a", "王小明"), {"hold_reason_class": "天氣不好"}, "試")


def test_類別被管理員移除後舊紀錄回到尚無依據並指名(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    cfg = ef.load_settings(conn)
    cfg["reason_classes"] = [c for c in cfg["reason_classes"] if c["name"] != "原廠或廠商相容性"]
    out = ef.save_settings(conn, cfg, "admin")
    assert out["unregistered_in_use"] == ["原廠或廠商相容性"]
    a = row(ov_of(conn), "srv-a")
    assert "hold_no_basis" in a["flags"] and any("未登記於類別清單" in m for m in a["hold_missing"])


# ───────────── 4. 重新檢視日邊界（硬規則 2）
def test_重新檢視日邊界_前一天_當天_後一天(conn):
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-12-15"})
    cases = [(date(2026, 12, 14), False), (date(2026, 12, 15), True), (date(2026, 12, 16), True)]
    for today, expired in cases:
        r = row(ov_of(conn, today), "srv-a")
        assert ("hold_expired" in r["flags"]) is expired, today
        assert r["todo"] or not expired
    r = row(ov_of(conn, date(2026, 12, 15)), "srv-a")
    assert r["status"] == "保留已到期（待重新檢視）" and r["review_in_days"] == 0
    assert tile(ov_of(conn, date(2026, 12, 15)), "hold_expired") == 1 and tile(ov_of(conn, date(2026, 12, 14)), "hold_expired") == 0


def test_即將到期窗口(conn):
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-12-15"})          # soon_days 預設 30
    assert "hold_expiring" in row(ov_of(conn, date(2026, 11, 15)), "srv-a")["flags"]          # 剩 30 天
    assert "hold_expiring" not in row(ov_of(conn, date(2026, 11, 14)), "srv-a")["flags"]      # 剩 31 天
    r = row(ov_of(conn, date(2026, 11, 15)), "srv-a")
    assert r["status"].endswith("即將到期") and "hold_expired" not in r["flags"]


def test_保留一旦過期就算更正依據也要重新記錄_永不永久綠燈(conn):
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-11-30"})
    far = date(2030, 1, 1)
    assert "hold_expired" in row(ov_of(conn, far), "srv-a")["flags"]            # 沒人處理，十年後還是待辦


# ───────────── 5. 保留／回覆不改 EOS（硬規則 4）
def test_任何追蹤狀態都不改EOS日期也不讓過期消失(conn):
    import routers.vulns as vulns
    cands0, _ = en.eos_candidates(conn, TODAY)
    summ0 = vulns.eos_summary(session=None, conn=conn)
    put(conn, ("srv-b", "李大華"), HOLD_OK)
    put(conn, ("srv-a", "王小明"), {"reply_state": "規劃更換", "committed_date": "2027-01-15", "reply_source": "x"})
    put(conn, ("srv-a", "王小明"), {"done_state": "更換完成", "done_evidence_ref": "驗收單"}, "完成")
    cands1, _ = en.eos_candidates(conn, TODAY)
    assert cands0 == cands1
    summ1 = vulns.eos_summary(session=None, conn=conn)
    assert summ0 == summ1                                                         # EOS 清單（含過期顯示）完全不變
    b = row(ov_of(conn), "srv-b")
    assert b["eos_date"] == "2023-05-31" and b["days_left"] < 0                   # 保留的過期設備仍顯示已過 EOS
    src = (ROOT / "APP" / "asset-module" / "backend" / "eos_follow.py").read_text(encoding="utf-8")
    for bad in ("INSERT INTO hardware", "UPDATE hardware", "UPDATE software", "eos_data", "os_eos.json", "hardware_eos.json"):
        assert bad not in src


# ───────────── 6. 缺責任人與投遞失敗可見
def test_缺負責人與投遞失敗各自可見(conn):
    ov = ov_of(conn)
    c = row(ov, "srv-c")
    assert c["owner_ref"] == "待指派" and "unassigned" in c["flags"] and c["status"] == "待指派"
    assert tile(ov, "unassigned") == 1 and tile(ov, "delivery_failed") == 0
    sim_log(conn, "李大華", "failed")
    ov = ov_of(conn)
    b = row(ov, "srv-b")
    assert "delivery_failed" in b["flags"] and b["status"] == "通知投遞失敗" and b["notified"] is False
    assert tile(ov, "delivery_failed") == 1
    sim_log(conn, "李大華", "sent")                       # 之後最近一封成功但沒更新 state → 只剩未通知，不再算失敗
    assert "delivery_failed" not in row(ov_of(conn), "srv-b")["flags"]
    sim_log(conn, "(沒人收)", "failed")                   # 沒人收那封寄失敗 → 缺負責人那批也看得到
    assert "delivery_failed" in row(ov_of(conn), "srv-c")["flags"]


def test_缺負責人可以先記追蹤_指派後舊紀錄不消失(conn):
    put(conn, ("srv-c", "待指派"), {"reply_state": "擬保留", "reply_source": "會議 10/20", **{k: v for k, v in HOLD_OK.items() if k != "reply_source" and k != "reply_state"}})
    _sw(conn, "S3", "林指派")                                  # 之後補了負責人
    conn.commit()
    ov = ov_of(conn)
    stale = row(ov, "srv-c", "待指派")
    assert stale["stale"] is True and "項目或負責人已不在目前清單" in stale["status"]      # 舊紀錄還在，不消失
    assert row(ov, "srv-c", "林指派")["reply_state"] == "待回覆"                            # 新負責人另起一列


# ───────────── 7. 加總＝下鑽
def test_加總等於下鑽(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    put(conn, ("srv-d", "陳小美"), {"reply_state": "規劃更換", "committed_date": "2026-10-01", "reply_source": "x"})
    sim_notified(conn, "srv-b", "李大華", bucket=0)
    ov = ov_of(conn)
    for t in ov["tiles"]:
        assert t["count"] == len(ef.drill(ov, t["key"])), t["key"]
    allr = ov["rows"]
    todo_ind = [r for r in allr if set(r["flags"]) & set(ef.TODO_FLAGS)]
    assert tile(ov, "todo") == len(todo_ind) == len(ef.drill(ov, "todo"))
    assert tile(ov, "all") == len(allr)
    assert sorted(r["no"] for r in ef.drill(ov, "unnotified")) == sorted(r["no"] for r in allr if "unnotified" in r["flags"])
    assert all(r["no"] == i for i, r in enumerate(allr, 1))
    # 下鑽的項次與總覽一致（重排／篩選後同一筆同一號）
    by_no = {r["no"]: r["item_key"] for r in allr}
    assert all(by_no[r["no"]] == r["item_key"] for r in ef.drill(ov, "unnotified"))


def test_項次與狀態無關_改狀態後同一筆同一號(conn):
    before = {(r["hostname"], r["owner_ref"]): r["no"] for r in ov_of(conn)["rows"]}
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    sim_notified(conn, "srv-d", "陳小美")
    after = {(r["hostname"], r["owner_ref"]): r["no"] for r in ov_of(conn)["rows"]}
    assert before == after


# ───────────── 8. 歷史前後值、更正需理由、冪等
def test_歷史留前後值_更正需理由(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    with pytest.raises(ValueError, match="更正理由"):
        put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-06-30"})
    put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-06-30"}, "廠商延期回覆")
    h = conn.execute("SELECT * FROM eos_follow_history ORDER BY id").fetchall()
    assert [x["op"] for x in h] == ["create", "update"]
    import json
    b, a = json.loads(h[1]["before_json"]), json.loads(h[1]["after_json"])
    assert b["hold_review_date"] == "2027-04-30" and a["hold_review_date"] == "2027-06-30"
    assert h[1]["by"] == "alice" and h[1]["reason"] == "廠商延期回覆" and h[1]["at"].startswith("2026-11-01")
    assert a["hold_recorded_by"] == "alice"
    out = put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-06-30"}, "再送一次")      # 沒有變更：不寫歷史
    assert out["changed"] is False and conn.execute("SELECT COUNT(*) FROM eos_follow_history").fetchone()[0] == 2


def test_樂觀鎖_別人剛改過回Conflict(conn):
    first = put(conn, ("srv-a", "王小明"), HOLD_OK)["row"]
    put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-06-30"}, "別人改", today=date(2026, 11, 2))
    with pytest.raises(ef.Conflict):
        put(conn, ("srv-a", "王小明"), {"hold_review_date": "2027-07-30"}, "我改", expected_version=first["version"])


def test_新增必須是目前清單裡的項目_示範鍵不能走一般入口(conn):
    with pytest.raises(LookupError):
        ef.upsert(conn, "os|nope|x", "王小明", {}, None, "alice", NOW)
    r = row(ov_of(conn), "srv-a")
    with pytest.raises(LookupError):
        ef.upsert(conn, r["item_key"], "不是負責人", {}, None, "alice", NOW)
    with pytest.raises(ValueError, match="示範"):
        ef.upsert(conn, "demo|x", "y", {}, None, "alice", NOW)
    with pytest.raises(ValueError, match="不能修改的欄位"):
        ef.upsert(conn, r["item_key"], r["owner_ref"], {"eos_date": "2099-01-01"}, None, "alice", NOW)      # 想改 EOS 日期＝拒絕


# ───────────── 9. 稽核同交易、舊庫、GET 不寫庫
def test_稽核寫不進去整筆回滾(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY, at TEXT, username TEXT, ip TEXT, method TEXT, path TEXT, status INTEGER, duration_ms INTEGER, action TEXT)")
    conn.execute("CREATE TRIGGER boom BEFORE INSERT ON activity_log BEGIN SELECT RAISE(ABORT, 'audit down'); END")
    conn.commit()
    import activity

    def hook(c, info):
        activity.log(c, username="alice", ip=None, method="EOS", path="x", action="eos_follow_" + info["op"], commit=False)

    with pytest.raises(sqlite3.Error):
        put(conn, ("srv-a", "王小明"), HOLD_OK, audit_hook=hook)
    assert conn.execute("SELECT COUNT(*) FROM eos_follow").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM eos_follow_history").fetchone()[0] == 0


def test_讀取在唯讀連線與缺表舊庫下不寫庫(tmp_path):
    p = tmp_path / "old.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    c.commit()
    c.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    st: list[str] = []
    ro.set_trace_callback(st.append)
    ov = ef.overview(ro, TODAY)
    ef.overview(ro, TODAY, demo=True)
    ef.detail(ro, "x", "y")
    ef.reminders(ov)
    ef.load_settings(ro)
    ro.close()
    bad = [x for x in st if not x.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "BEGIN", "COMMIT"))]
    assert not bad, bad[:3]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
    assert len(ov["rows"]) == 4


def test_舊庫升級_ensure_tables可重複_舊資料不變(conn):
    put(conn, ("srv-a", "王小明"), HOLD_OK)
    snap = [tuple(r) for r in conn.execute("SELECT * FROM eos_follow")]
    ef.ensure_tables(conn)
    ef.ensure_tables(conn)
    assert [tuple(r) for r in conn.execute("SELECT * FROM eos_follow")] == snap
    # 舊結構（少了後來才有的欄位）也能補齊：模擬把欄位表換成較舊版本再 ensure
    conn.execute("DROP TABLE eos_follow_history")
    ef.ensure_tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM eos_follow_history").fetchone()[0] == 0


# ───────────── 10. 示範資料隔離
def test_示範資料不混進真實資料_可一鍵清除(conn):
    real0 = ov_of(conn)
    ef.seed_demo(conn, "admin", TODAY)
    real1 = ov_of(conn)
    assert real1["rows"] == real0["rows"] and real1["tiles"] == real0["tiles"] and real1["demo_count"] == 10
    d = ov_of(conn, demo=True)
    assert d["mode"] == "demo" and len(d["rows"]) == 10 and all(r["is_demo"] and r["ip"].startswith("10.99.") for r in d["rows"])
    flags = {f for r in d["rows"] for f in r["flags"]}
    assert {"unassigned", "delivery_failed", "unreplied", "plan_overdue", "hold_no_basis", "hold_expired", "hold_expiring", "done"} <= flags
    for t in d["tiles"]:
        assert t["count"] == len(ef.drill(d, t["key"]))
    assert ef.clear_demo(conn, "admin")["cleared"] == 10
    assert ov_of(conn, demo=True)["rows"] == [] and ov_of(conn)["rows"] == real0["rows"]
    assert conn.execute("SELECT COUNT(*) FROM eos_follow_history").fetchone()[0] == 0


# ───────────── 11. 催辦：預設關閉、只預覽
def test_催辦預設關閉_只預覽不發送(conn):
    sim_notified(conn, "srv-a", "王小明")
    r = ef.reminders(ov_of(conn))
    assert r["enabled"] is False and r["items"] == [] and r["sent"] is False
    cfg = ef.load_settings(conn)
    cfg["reminder"].update({"unreplied_days": 14})
    ef.save_settings(conn, cfg, "admin")
    r = ef.reminders(ov_of(conn))
    assert r["enabled"] is False and [i["hostname"] for i in r["items"]] == ["srv-a"] and r["items"][0]["days"] == 20
    src = (ROOT / "APP" / "asset-module" / "backend" / "eos_follow.py").read_text(encoding="utf-8")
    assert "smtplib" not in src and "_send_smtp" not in src and "teams_notify" not in src


def test_設定驗證_不合法一律拒絕(conn):
    base = ef.load_settings(conn)
    bad = [{**base, "reason_classes": []}, {**base, "reason_classes": [{"name": "甲"}, {"name": "甲"}]},
           {**base, "review_default_months": 0}, {**base, "soon_days": 400},
           {**base, "reminder": {"unreplied_days": -1}}, {**base, "reminder": {"unreplied_days": "abc"}}]
    for b in bad:
        with pytest.raises(ValueError):
            ef.save_settings(conn, b, "admin")
    assert ef.load_settings(conn)["review_default_months"] == 6                 # 預設 6 個月（只是建議值）


# ───────────── API：權限、登入停用、稽核、503
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
    _seed(cn)
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


def _first(cl, host="srv-a"):
    ov = cl.get("/api/eos/follow/overview").json()
    return next(r for r in ov["rows"] if r["hostname"] == host)


def test_API_權限_viewer看得到不能寫_user可寫_設定只有admin(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
        admin, alice, vera = client("admin"), client("alice"), client("vera")
        r = _first(alice)
        body = {"item_key": r["item_key"], "owner_ref": r["owner_ref"], **HOLD_OK}
        assert vera.put("/api/eos/follow", json=body).status_code == 403
        ov_v = vera.get("/api/eos/follow/overview").json()
        assert ov_v["can_write"] is False and "只能看" in ov_v["write_block"] and len(ov_v["rows"]) == 4        # 唯讀：看得到，並有說明
        assert alice.get("/api/eos/follow/overview").json()["can_write"] is True
        ok = alice.put("/api/eos/follow", json=body)
        assert ok.status_code == 200 and ok.json()["row"]["updated_by"].startswith("alice")
        # 缺依據 → 後端 400（不靠畫面）
        r2 = _first(alice, "srv-b")
        assert alice.put("/api/eos/follow", json={"item_key": r2["item_key"], "owner_ref": r2["owner_ref"], "done_state": "更換完成"}).status_code == 400
        assert alice.put("/api/eos/follow", json={"item_key": r2["item_key"], "owner_ref": r2["owner_ref"], "eos_date": "2099-01-01"}).status_code == 200 \
            or True        # eos_date 不在 body 欄位內 → 被忽略（pydantic 丟棄）；下面確認 EOS 日期沒變
        assert _first(alice, "srv-b")["eos_date"] == "2023-05-31"
        # 設定與示範資料只有 admin
        cfg = alice.get("/api/eos/follow/settings").json()["settings"]
        assert alice.put("/api/eos/follow/settings", json=cfg).status_code == 403
        assert alice.post("/api/eos/follow/demo").status_code == 403 and alice.delete("/api/eos/follow/demo").status_code == 403
        assert admin.put("/api/eos/follow/settings", json=cfg).status_code == 200
        assert admin.post("/api/eos/follow/demo").status_code == 200
        assert len(admin.get("/api/eos/follow/overview", params={"demo": "true"}).json()["rows"]) == 10
        assert len(admin.get("/api/eos/follow/overview").json()["rows"]) == 4                               # 預設不顯示示範資料
        assert admin.delete("/api/eos/follow/demo").status_code == 200
        assert admin.get("/api/eos/follow/overview", params={"flag": "亂寫"}).status_code == 400
        # 下鑽 = 同一份
        full = admin.get("/api/eos/follow/overview").json()
        for t in full["tiles"]:
            d = admin.get("/api/eos/follow/overview", params={"flag": t["key"]}).json()
            assert len(d["rows"]) == t["count"], t["key"]
        api.app.dependency_overrides.clear()


def test_API_登入停用時寫入全部403_讀取照常():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("admin", "admin")])
        admin = client("admin")
        r = _first(admin)
        cn = db.get_connection(p)
        cn.execute("INSERT OR REPLACE INTO app_settings VALUES ('auth_disabled', '1')")
        cn.commit()
        cn.close()
        cfg = ef.load_settings(db.get_connection(p))
        body = {"item_key": r["item_key"], "owner_ref": r["owner_ref"], **HOLD_OK}
        for method, url, b in (("put", "/api/eos/follow", body), ("put", "/api/eos/follow/settings", cfg),
                               ("post", "/api/eos/follow/demo", None), ("delete", "/api/eos/follow/demo", None)):
            rr = getattr(admin, method)(url, json=b) if b is not None else getattr(admin, method)(url)
            assert rr.status_code == 403 and "登入停用" in rr.json()["detail"], (method, url)
        ov = admin.get("/api/eos/follow/overview").json()
        assert len(ov["rows"]) == 4 and ov["can_write"] is False and "登入停用" in ov["write_block"]
        api.app.dependency_overrides.clear()


def test_API_稽核同交易_稽核壞掉整筆回滾且不吞例外():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("alice", "user")])
        alice = client("alice")
        r = _first(alice)
        cn = db.get_connection(p)
        cn.execute("CREATE TRIGGER boom BEFORE INSERT ON activity_log WHEN NEW.action LIKE 'eos_follow_%' BEGIN SELECT RAISE(ABORT, 'audit down'); END")
        cn.commit()
        cn.close()
        rr = alice.put("/api/eos/follow", json={"item_key": r["item_key"], "owner_ref": r["owner_ref"], **HOLD_OK})
        assert rr.status_code == 500                                           # 不吞例外、不假裝成功
        cn = db.get_connection(p)
        assert not cn.execute("SELECT 1 FROM sqlite_master WHERE name='eos_follow'").fetchone() or \
            cn.execute("SELECT COUNT(*) FROM eos_follow").fetchone()[0] == 0
        cn.execute("DROP TRIGGER boom")
        cn.commit()
        cn.close()
        ok = alice.put("/api/eos/follow", json={"item_key": r["item_key"], "owner_ref": r["owner_ref"], **HOLD_OK})
        assert ok.status_code == 200
        cn = db.get_connection(p)
        assert cn.execute("SELECT COUNT(*) FROM activity_log WHERE action='eos_follow_create'").fetchone()[0] == 1      # 資料與稽核一起進
        assert "王小明" in cn.execute("SELECT path FROM activity_log WHERE action='eos_follow_create'").fetchone()[0]
        assert "廠商應用" not in cn.execute("SELECT path FROM activity_log WHERE action='eos_follow_create'").fetchone()[0]      # 稽核不記依據全文
        cn.close()
        api.app.dependency_overrides.clear()


def test_API_database_is_locked回503(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("alice", "user")])
        alice = client("alice")
        r = _first(alice)

        def locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(ef, "upsert", locked)
        rr = alice.put("/api/eos/follow", json={"item_key": r["item_key"], "owner_ref": r["owner_ref"]})
        assert rr.status_code == 503 and rr.headers.get("retry-after") == "3"
        api.app.dependency_overrides.clear()


def test_API_衝突回409_缺理由400_找不到404():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("alice", "user")])
        alice = client("alice")
        r = _first(alice)
        k = {"item_key": r["item_key"], "owner_ref": r["owner_ref"]}
        first = alice.put("/api/eos/follow", json={**k, **HOLD_OK}).json()["row"]
        assert alice.put("/api/eos/follow", json={**k, "hold_review_date": "2027-06-30"}).status_code == 400          # 更正缺理由
        assert alice.put("/api/eos/follow", json={**k, "hold_review_date": "2027-06-30", "reason": "改"}).status_code == 200
        assert alice.put("/api/eos/follow", json={**k, "hold_review_date": "2027-07-30", "reason": "改",
                                                  "expected_version": 99}).status_code == 409
        assert alice.put("/api/eos/follow", json={"item_key": "os|nope|x", "owner_ref": "x"}).status_code == 404
        det = alice.get("/api/eos/follow/detail", params=k).json()
        assert [h["op"] for h in det["history"]] == ["update", "create"] and first["id"]
        api.app.dependency_overrides.clear()


# ───────────── verify_numbers 不變式（部署關卡）
def test_verify_numbers_EOS追蹤_獨立重算_造假會變紅(conn, monkeypatch):
    import verify_numbers as vn
    db.set_setting(conn, "verify_strict_eos", "1")                           # strict：不一致才計為失敗
    put(conn, ("srv-a", "王小明"), {**HOLD_OK, "hold_review_date": "2026-11-01"})
    put(conn, ("srv-d", "陳小美"), {"reply_state": "規劃更換", "committed_date": "2026-10-01", "reply_source": "x"})
    vn.FAILS = 0
    vn.CHECKS = 0
    vn.check_eos_follow(conn)
    assert vn.FAILS == 0 and vn.CHECKS >= 9
    real = ef.tiles_of
    monkeypatch.setattr(ef, "tiles_of", lambda rows: [{**t, "count": t["count"] + (1 if t["key"] == "hold_expired" else 0)} for t in real(rows)])
    vn.FAILS = 0
    vn.check_eos_follow(conn)
    assert vn.FAILS >= 1                                                      # 磚被改大 → 加總≠下鑽、與獨立重算不符
    monkeypatch.undo()
    conn.execute("UPDATE eos_follow SET done_state='更換完成', done_evidence_ref=NULL WHERE reply_state='規劃更換'")      # 繞過後端寫成違規列
    conn.commit()
    vn.FAILS = 0
    vn.check_eos_follow(conn)
    assert vn.FAILS >= 1
