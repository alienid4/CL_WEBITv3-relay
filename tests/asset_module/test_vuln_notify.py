"""B-19 弱點到期寄信。

守的是「做錯會比沒做更糟」的那幾件，不是解析細節：

1. 同一筆不可以 14/30/60 各寄一次——重複通知會讓人把整個寄件者設成已讀
2. **寄失敗不可以算寄過**——「以為寄了其實沒寄」是這功能最壞的結果
3. 週一撞 1 號只寄一封
4. 找不到收件人的四種情況都要**看得見**，不可以靜靜跳過
5. 同名疑慮**不可以亂猜**寄給其中一個
6. 設定缺值要**指名**缺哪一項；AD 沒匯入要**指路**；兩者都不准「先寄能寄的」
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ad_import  # noqa: E402
import db  # noqa: E402
import vuln_notify as vn  # noqa: E402

TODAY = date(2026, 10, 5)          # 週一
CFG = {"smtp_host": "192.0.2.25", "smtp_port": 25,
       "mail_from": "webit3@example.invalid", "fallback_to": "soc@example.invalid"}


@pytest.fixture()
def conn(tmp_path):
    c = db.get_connection(tmp_path / "t.db")
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    c.execute(ad_import.TABLE_SQL)
    c.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
    import vuln_import
    c.execute(vuln_import.BATCH_SQL)
    c.execute(vuln_import.FINDING_SQL)
    vn._ensure(c)
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _person(c, eid, name, mail, enabled=1, mgr_mail=None):
    c.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, manager_mail) "
              "VALUES (?,?,?,?,?)", (eid, name, mail, enabled, mgr_mail))


def _vuln(c, batch_id, owner, due, fp, host="h1", name="CVE-x"):
    c.execute("INSERT INTO vuln_finding (batch_id, fingerprint, host, name, severity, "
              "due_date, owner, closed) VALUES (?,?,?,?,?,?,?,0)",
              (batch_id, fp, host, name, "High", due, owner))


def _batch(c):
    cur = c.execute("INSERT INTO vuln_batch (file_name, imported_at, status) "
                    "VALUES ('v.xlsx', '2026-10-01 09:00:00', 'ready')")
    return cur.lastrowid


# ── 3 排程 ─────────────────────────────────────────────────────────────
def test_週一撞一號只寄一封():
    """月報是週報的超集，寄兩封只會讓人覺得系統在洗版。"""
    assert vn.schedules_due(date(2026, 6, 1)) == ["monthly"]      # 週一且 1 號
    assert vn.schedules_due(date(2026, 10, 5)) == ["weekly"]      # 只是週一
    assert vn.schedules_due(date(2026, 10, 1)) == ["monthly"]     # 只是 1 號
    assert vn.schedules_due(date(2026, 10, 7)) == []              # 都不是


def test_門檻取最急的那一級():
    b = (14, 30, 60)
    assert vn.bucket_for(-3, b) == 0        # 逾期
    assert vn.bucket_for(12, b) == 14
    assert vn.bucket_for(25, b) == 30
    assert vn.bucket_for(59, b) == 60
    assert vn.bucket_for(90, b) is None     # 還很遠，這期不用提
    assert vn.bucket_for(None, b) is None   # 沒有到期日


# ── 1 不重複通知 ───────────────────────────────────────────────────────
def test_同一級不重複提_跨進更急的才再出現(conn):
    vn.save_settings(conn, CFG)
    _person(conn, "E1", "王小明", "ming@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-11-20", "fp1")     # 距 10/05 還有 46 天 → 60
    conn.commit()

    d1 = vn.collect(conn, TODAY, "monthly")
    assert len(d1["by_owner"]["E1"]["items"]) == 1

    # 假裝 60 那一級已經寄過
    conn.execute("INSERT INTO vuln_notify_state (fingerprint, last_bucket, last_sent_at) "
                 "VALUES ('fp1', 60, '2026-10-05 08:00:00')")
    conn.commit()
    d2 = vn.collect(conn, TODAY, "monthly")
    assert not d2["by_owner"], "同一級不可以再提一次——會讓人把寄件者設成已讀"

    # 剩 25 天：跨進更急的 30，要再出現
    d3 = vn.collect(conn, date(2026, 10, 26), "monthly")
    assert len(d3["by_owner"]["E1"]["items"]) == 1


# ── 4／5 找不到收件人的四種 ────────────────────────────────────────────
def test_找不到收件人的四種都要進沒人收那一籃(conn):
    vn.save_settings(conn, CFG)
    _person(conn, "E1", "有信箱", "ok@example.invalid")
    _person(conn, "E2", "離職者", "gone@example.invalid", enabled=0)
    _person(conn, "E3", "沒信箱", None)
    _person(conn, "E4", "菜市仔名", "a@example.invalid")
    _person(conn, "E5", "菜市仔名", "b@example.invalid")      # 同名
    b = _batch(conn)
    for i, owner in enumerate(["有信箱", "", "查無此人", "離職者", "沒信箱", "菜市仔名"]):
        _vuln(conn, b, owner, "2026-10-10", f"fp{i}")
    conn.commit()

    d = vn.collect(conn, TODAY, "monthly")
    assert list(d["by_owner"]) == ["E1"], "只有 E1 該收到"
    whys = sorted(o["_why"] for o in d["orphans"])
    assert whys == sorted([vn.NO_OWNER, vn.NOT_IN_AD, vn.DISABLED, vn.NO_MAIL, vn.AMBIGUOUS])


def test_同名疑慮不可以亂猜(conn):
    _person(conn, "E4", "菜市仔名", "a@example.invalid")
    _person(conn, "E5", "菜市仔名", "b@example.invalid")
    conn.commit()
    p, why = vn.resolve_recipient(conn, "菜市仔名")
    assert p is None and why == vn.AMBIGUOUS, "寄錯人＝把弱點清單送給不相干的人"


def test_停用帳號不可當收件人但弱點不可以消失(conn):
    _person(conn, "E2", "離職者", "gone@example.invalid", enabled=0)
    conn.commit()
    p, why = vn.resolve_recipient(conn, "離職者")
    assert p is None and why == vn.DISABLED


# ── 6 設定與前提 ───────────────────────────────────────────────────────
def test_設定缺值要指名缺哪一項():
    assert vn.check_settings({}) == ["SMTP 主機", "SMTP 埠", "寄件者"]
    assert vn.check_settings({"smtp_host": "h", "smtp_port": 25}) == ["寄件者"]
    assert vn.check_settings(CFG) == []


def test_沒有預設SMTP主機(conn):
    """使用者：不准有預設主機、不准悄悄退回去用別的。寄錯地方比沒寄更糟。

    這裡刻意**不去比對公司網域字串**——那會把公司識別字寫進 tests/，而 tests/ 跟
    APP/ 一樣會走去識別化外送的路徑。改成驗真正的不變式：沒設定過就是空的，而且
    原始碼裡連一個 IP、一個信箱都不准出現。不指名任何一家公司也成立，而且管得更寬。
    """
    import re

    assert vn.load_settings(conn) == {}, "沒設定過就該是空的，不可以有內建預設"

    src = (ROOT / "APP" / "asset-module" / "backend" / "vuln_notify.py").read_text(
        encoding="utf-8")
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert not re.search(r"\d{1,3}(\.\d{1,3}){3}", code), "程式碼裡不可以出現 IP 當預設"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", code), \
        "程式碼裡不可以出現信箱當預設寄件者"


def test_設定不齊就整個不動_並指名(conn):
    vn.save_settings(conn, {"smtp_host": "h"})
    r = vn.run(conn, TODAY)
    assert r["ok"] is False and "SMTP 埠" in r["reason"] and "寄件者" in r["reason"]
    assert r["sent"] == 0


def test_AD沒匯入要指路而不是只說找不到收件人(conn):
    vn.save_settings(conn, CFG)
    r = vn.run(conn, TODAY)
    assert r["ok"] is False
    assert "資料匯入" in r["reason"], "要告訴人去哪裡補，不是只說查不到"


# ── 2 寄失敗不可以算寄過 ───────────────────────────────────────────────
def test_寄失敗不可以更新bucket(conn, monkeypatch):
    """寧可重複寄也不可以假裝寄過。"""
    vn.save_settings(conn, CFG)
    _person(conn, "E1", "王小明", "ming@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-10-10", "fp1")
    conn.commit()

    monkeypatch.setattr(vn, "_send_smtp", lambda *a, **k: (False, "Connection refused"))
    r = vn.run(conn, TODAY, "monthly")
    assert r["failed"] >= 1 and r["sent"] == 0
    assert conn.execute("SELECT COUNT(*) FROM vuln_notify_state").fetchone()[0] == 0, \
        "寄失敗卻更新了 bucket——下次就不會再寄，那筆弱點永遠沒人提醒"
    # 失敗要留紀錄，不可以靜靜消失
    rows = vn.logs(conn)
    assert rows and rows[0]["status"] == "failed" and "refused" in rows[0]["detail"]


def test_寄成功才更新bucket並留紀錄(conn, monkeypatch):
    vn.save_settings(conn, CFG)
    _person(conn, "E1", "王小明", "ming@example.invalid", mgr_mail="boss@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-10-10", "fp1")
    conn.commit()

    seen = {}

    def fake(cfg, to, cc, subject, body):
        seen.update(to=to, cc=cc, subject=subject, body=body)
        return True, "250 OK"

    monkeypatch.setattr(vn, "_send_smtp", fake)
    r = vn.run(conn, TODAY, "monthly")
    assert r["sent"] >= 1
    assert conn.execute(
        "SELECT last_bucket FROM vuln_notify_state WHERE fingerprint='fp1'").fetchone()[0] == 14
    # 月報主管收副本
    assert seen["cc"] == ["boss@example.invalid"]
    log = vn.logs(conn)[0]
    assert log["status"] == "sent" and log["smtp_host"] == CFG["smtp_host"]
    assert log["mail_from"] == CFG["mail_from"], "出事時要答得出那天寄到哪去了"


def test_週報不給主管副本(conn, monkeypatch):
    """使用者只說月報主管收副本。週報是行動清單，每週抄送主管會變成監視。"""
    vn.save_settings(conn, CFG)
    _person(conn, "E1", "王小明", "ming@example.invalid", mgr_mail="boss@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-10-10", "fp1")
    conn.commit()
    seen = {}
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda cfg, to, cc, s, bd: (seen.update(cc=cc), (True, "250"))[1])
    vn.run(conn, TODAY, "weekly")
    assert seen["cc"] == []


def test_沒設fallback時沒人收的那批不可以靜靜吞掉(conn, monkeypatch):
    cfg = dict(CFG); cfg.pop("fallback_to")
    vn.save_settings(conn, cfg)
    _person(conn, "E1", "有信箱", "ok@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "", "2026-10-10", "fp1")          # 沒有負責人
    conn.commit()
    monkeypatch.setattr(vn, "_send_smtp", lambda *a, **k: (True, "250"))
    r = vn.run(conn, TODAY, "monthly")
    bad = [d for d in r["details"] if d["status"] == "failed"]
    assert bad and "沒有設定" in bad[0]["detail"], "要講出來，那批弱點會永遠沒人知道"


def test_設定壞掉跟沒設定要分得開(conn):
    """壞掉回 {} 的話，畫面會說「設定還缺 SMTP 主機…」——
    使用者會重填一次已經填過的東西，而真正的原因（誰把它寫壞了）沒人看到。

    2026-09-23：這是閘門的 test_no_silent_catch 抓到的，不是我自己想到的。
    諷刺的是這個功能通篇在防「把異常吞成看起來正常」，我自己在讀設定那裡犯了同一件。
    """
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                 (vn.SETTING_KEY, "{這不是合法 JSON"))
    conn.commit()
    with pytest.raises(vn.SettingsCorrupt) as e:
        vn.load_settings(conn)
    assert "壞掉" in str(e.value) and "不是沒設定" in str(e.value)

    r = vn.run(conn, TODAY)
    assert r["ok"] is False and "壞掉" in r["reason"], "run() 要把原因帶出來"
    assert "還缺" not in r["reason"], "不可以講成「沒設定」"


# ── 排程：到點自己跑 ───────────────────────────────────────────────────
# 這一段守的是「排程做錯會比沒排程更糟」的四件：
#   關著卻在寄／同一期寄好幾次／失敗後整週不補／跑過沒留痕跡分不出「0 封」與「沒跑」
MON_8 = datetime(2026, 10, 5, 8, 0)          # 週一 08:00


def _ready(c, monkeypatch, ok=True):
    """設定填好、AD 有人、有一筆快到期的弱點；SMTP 用假的。"""
    vn.save_settings(c, {**CFG, "enabled": True})
    _person(c, "E1", "王小明", "ming@example.invalid")
    b = _batch(c)
    _vuln(c, b, "王小明", "2026-10-10", "fp1")
    c.commit()
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda *a, **k: (ok, "250 OK" if ok else "Connection refused"))


def test_沒到點不跑(conn, monkeypatch):
    _ready(conn, monkeypatch)
    assert vn.is_due(conn, datetime(2026, 10, 5, 7, 59)) == []
    assert vn.tick(conn, datetime(2026, 10, 5, 7, 59)) == []


def test_沒開就不准自己寄(conn, monkeypatch):
    """沒設定過＝關著。剛匯完 AD 的系統不可以自己開始對全公司寄信。"""
    _ready(conn, monkeypatch)
    vn.save_settings(conn, {**CFG})           # 沒有 enabled
    assert vn.is_due(conn, MON_8) == []


def test_同一期不會寄兩次(conn, monkeypatch):
    """排程器每分鐘叫一次。少了這條，週一 08:00 到 18:00 會寄六百封。"""
    _ready(conn, monkeypatch)
    first = vn.tick(conn, MON_8)
    assert len(first) == 1 and first[0]["sent"] >= 1
    assert vn.tick(conn, MON_8 + timedelta(minutes=1)) == []
    assert vn.tick(conn, MON_8 + timedelta(hours=5)) == []


def test_失敗要補寄而不是整週不寄(conn, monkeypatch):
    """08:00 剛好 SMTP 在維護就整週不寄，是「靜靜跳過」的另一種面貌。"""
    _ready(conn, monkeypatch, ok=False)
    assert len(vn.tick(conn, MON_8)) == 1              # 跑了，但寄失敗
    assert vn.tick(conn, MON_8 + timedelta(minutes=30)) == [], "冷卻時間內就重試＝洗版"
    assert len(vn.tick(conn, MON_8 + timedelta(minutes=61))) == 1, "過了冷卻要補寄"


def test_過了下班就不再補(conn, monkeypatch):
    """晚上才寄出的到期通知當天沒人處理，還會讓人以為系統壞了。下一期會再進同一封。"""
    _ready(conn, monkeypatch, ok=False)
    vn.tick(conn, MON_8)
    assert vn.is_due(conn, datetime(2026, 10, 5, 18, 0)) == []


def test_每一次嘗試都要留痕跡(conn, monkeypatch):
    """出事當下「今天寄了嗎」要答得出來。沒紀錄會被讀成「沒到點」，
    而真相可能是「跑了好幾次都被 SMTP 拒絕」。"""
    _ready(conn, monkeypatch, ok=False)
    vn.tick(conn, MON_8)
    vn.tick(conn, MON_8 + timedelta(minutes=61))
    runs = vn.last_runs(conn)
    assert len(runs) == 2, "失敗那次沒被記下來"
    # config_ok=1（設定沒問題、跑得完）但 failed>0（一封都沒寄到）。
    # 這兩欄分開存就是為了不要再把「跑完了」讀成「寄到了」——
    # 2026-09-23 我自己就是這樣寫錯，害失敗那期整週不補寄。
    assert all(r["config_ok"] == 1 and r["failed"] >= 1 for r in runs)
    assert all(r["reason"] or r["failed"] for r in runs), "失敗卻沒留原因"


def test_本期0筆也要寄一封(conn, monkeypatch):
    """需求單必守 1。沉默沒有辦法證明系統還活著——收信的人分不出
    「這週真的沒事」與「排程死了／設定被改壞了」。

    順便守住另一件：跑過但 0 筆，跟根本沒跑，在紀錄上要分得出來。
    """
    vn.save_settings(conn, {**CFG, "enabled": True})
    _person(conn, "E1", "王小明", "ming@example.invalid")
    _batch(conn)                                  # 有批次但沒有任何弱點
    conn.commit()
    sent = []
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda cfg, to, cc, subj, body: (sent.append((to, subj, body)), (True, "250 OK"))[1])

    assert vn.last_runs(conn) == [], "還沒跑就不該有紀錄"
    vn.tick(conn, MON_8)

    runs = vn.last_runs(conn)
    assert len(runs) == 1, "跑了寄 0 筆，也要留下『跑過』"
    assert len(sent) == 1, "0 筆就整個不寄——收信的人會以為排程死了"
    to, subj, body = sent[0]
    assert to == CFG["fallback_to"], "0 筆的通知要寄給代收信箱，不是去吵沒事的負責人"
    assert "0 筆" in subj and "0 筆" in body


def test_0筆但沒設代收信箱不可以靜靜跳過(conn, monkeypatch):
    """沒有這一條，畫面會顯示「這期跑完、寄了 0 封」，看起來跟正常沒兩樣。"""
    vn.save_settings(conn, {k: v for k, v in CFG.items() if k != "fallback_to"} | {"enabled": True})
    _person(conn, "E1", "王小明", "ming@example.invalid")   # AD 要有人，否則會卡在前一關
    _batch(conn)
    conn.commit()
    monkeypatch.setattr(vn, "_send_smtp", lambda *a, **k: (True, "250 OK"))

    r = vn.run(conn, TODAY, "weekly")
    assert r["failed"] >= 1
    assert any("沒有寄出去" in (d.get("detail") or "") for d in r["details"])


def test_沒有期限的不可以靜默略過(conn, monkeypatch):
    """需求單必守 2。沒填期限的永遠不會觸發任何門檻，等於系統替它們決定
    「不用管」——而真相是「沒有人填期限」，那是要有人去補的事。"""
    vn.save_settings(conn, {**CFG, "enabled": True})
    _person(conn, "E1", "王小明", "ming@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-10-10", "fp1")          # 會提醒的
    _vuln(conn, b, "王小明", None, "fp2", host="h2")       # 沒有期限
    _vuln(conn, b, "王小明", "", "fp3", host="h3")         # 空字串也算沒有
    conn.commit()
    sent = []
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda cfg, to, cc, subj, body: (sent.append(body), (True, "250 OK"))[1])

    r = vn.run(conn, TODAY, "weekly")
    assert r["no_due_items"] == 2
    assert sent and "沒有期限、無法提醒：2 筆" in sent[0], "信裡沒寫出來＝等於沒查過"


def test_每封信都要帶資料時間(conn, monkeypatch):
    """排程照樣在寄，但資料可能是三個月前那一份。沒寫出來，收信的人
    會把舊報告當成今天的現況。"""
    vn.save_settings(conn, {**CFG, "enabled": True})
    _person(conn, "E1", "王小明", "ming@example.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-10-10", "fp1")
    conn.commit()
    sent = []
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda cfg, to, cc, subj, body: (sent.append(body), (True, "250 OK"))[1])

    vn.run(conn, TODAY, "weekly")
    assert sent and "資料來源：" in sent[0] and "匯入時間" in sent[0]


def test_週一撞1號只跑月報(conn, monkeypatch):
    """2026-06-01 是週一。排程層也要只有一期，不能週報月報各跑一次。"""
    _ready(conn, monkeypatch)
    assert vn.is_due(conn, datetime(2026, 6, 1, 8, 0)) == ["monthly"]


def test_設定壞掉不會被當成到點(conn):
    """壞掉要用手動觸發時講清楚，不是讓排程器每小時拿壞設定去試。"""
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                 (vn.SETTING_KEY, "{壞掉的 JSON"))
    conn.commit()
    assert vn.is_due(conn, MON_8) == []
    r = vn.run(conn, TODAY)
    assert r["ok"] is False and "壞掉" in r["reason"]


def test_一律副本給_跟月報主管副本是兩件事(conn, monkeypatch):
    """全域窗口用 always_cc（改一次就好），主管副本走 AD 的 manager_mail（依人不同）。
    混成同一欄的話，換窗口要去改每個人的 AD 資料。

    而且同一個信箱不可以出現兩次——收信的人會以為系統壞了。
    """
    assert vn.always_cc({"always_cc": "a@x.invalid, b@x.invalid;a@X.invalid"}) ==         ["a@x.invalid", "b@x.invalid"]
    assert vn.always_cc({}) == []

    vn.save_settings(conn, {**CFG, "enabled": True, "always_cc": "soc@x.invalid"})
    _person(conn, "E1", "王小明", "ming@example.invalid", mgr_mail="soc@x.invalid")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-10-10", "fp1")
    conn.commit()
    got = []
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda cfg, to, cc, subj, body: (got.append(cc), (True, "250 OK"))[1])

    vn.run(conn, TODAY, "monthly")
    assert got[0] == ["soc@x.invalid"], f"主管跟固定窗口同一個信箱，被寄了兩次：{got[0]}"


# ── 收件範圍：先選單位、再挑人（使用者：「我是架構部，我想選只要通知架構部」）──
def _p(eid, name, mail, dept):
    return {"employee_id": eid, "display_name": name, "mail": mail, "dept_name": dept}


def test_沒設範圍就是全部通知():
    """預設做成「沒有人」的話，剛啟用的系統會安安靜靜一封都不寄，
    而畫面上看起來一切正常——那比沒有這個功能更糟。"""
    assert vn.in_scope({}, _p("E1", "甲", "a@x.invalid", "架構部"))
    assert vn.in_scope({"only_depts": [], "only_people": []},
                       _p("E1", "甲", "a@x.invalid", "架構部"))


def test_選了單位就只通知那個單位():
    cfg = {"only_depts": ["架構部"]}
    assert vn.in_scope(cfg, _p("E1", "甲", "a@x.invalid", "架構部"))
    assert not vn.in_scope(cfg, _p("E2", "乙", "b@x.invalid", "網路部"))
    # 沒填單位的人要歸進 (未填單位)，不可以因為欄位空白就變成「誰選都算他」
    assert not vn.in_scope(cfg, _p("E3", "丙", "c@x.invalid", ""))
    assert vn.in_scope({"only_depts": ["(未填單位)"]}, _p("E3", "丙", "c@x.invalid", ""))


def test_挑了人就以人為準():
    """選了人＝使用者挑了特定幾位，單位設定不該再把別人放進來。"""
    cfg = {"only_depts": ["架構部"], "only_people": ["E1"]}
    assert vn.in_scope(cfg, _p("E1", "甲", "a@x.invalid", "架構部"))
    assert not vn.in_scope(cfg, _p("E9", "丁", "d@x.invalid", "架構部"))


def test_範圍外的弱點不可以就這樣消失(conn, monkeypatch):
    """那批弱點還是存在，只是這個系統不提醒了。歸進「沒人收」那一籃，
    代收信箱看得到、畫面也算得出來——不然就是靜靜當作沒事。"""
    vn.save_settings(conn, {**CFG, "enabled": True, "only_depts": ["架構部"]})
    conn.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) "
                 "VALUES (?,?,?,1,?)", ("E2", "乙", "b@example.invalid", "網路部"))
    b = _batch(conn)
    _vuln(conn, b, "乙", "2026-10-10", "fp1")
    conn.commit()
    monkeypatch.setattr(vn, "_send_smtp", lambda *a, **k: (True, "250 OK"))

    r = vn.run(conn, TODAY, "weekly")
    assert r["orphan_items"] == 1, "範圍外的弱點消失了"
    log = vn.logs(conn)
    assert any(x["owner_name"] == "(沒人收)" for x in log)


def test_單位清單看得到人數(conn):
    """畫面要挑單位，就要知道每個單位有幾個人；停用的帳號不算。"""
    conn.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) "
                 "VALUES (?,?,?,1,?)", ("E1", "甲", "a@example.invalid", "架構部"))
    conn.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) "
                 "VALUES (?,?,?,0,?)", ("E8", "離職", "z@example.invalid", "架構部"))
    conn.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) "
                 "VALUES (?,?,?,1,?)", ("E3", "丙", "c@example.invalid", ""))
    conn.commit()
    got = {d["dept"]: d["n"] for d in vn.departments(conn)}
    assert got == {"架構部": 1, "(未填單位)": 1}
    assert [x["employee_id"] for x in vn.people_of(conn, ["架構部"])] == ["E1"]


def test_月報要有各單位統計_逾期另外算(conn, monkeypatch):
    """使用者要的是「哪個單位在失控」。總數與逾期混成一個數字就看不出來：
    20 筆跟「20 筆其中 11 筆已逾期」是兩件完全不同的事。週報不附（週報是行動清單）。"""
    vn.save_settings(conn, {**CFG, "enabled": True})
    conn.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) "
                 "VALUES ('E1','王小明','ming@example.invalid',1,'架構部')")
    conn.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) "
                 "VALUES ('E3','陳大同','tong@example.invalid',1,'網路部')")
    b = _batch(conn)
    _vuln(conn, b, "王小明", "2026-09-01", "f1")      # 已逾期
    _vuln(conn, b, "王小明", "2026-10-10", "f2", host="h2")
    _vuln(conn, b, "陳大同", "2026-10-20", "f3", host="h3")
    conn.commit()
    bodies = []
    monkeypatch.setattr(vn, "_send_smtp",
                        lambda cfg, to, cc, subj, body: (bodies.append(body), (True, "250 OK"))[1])

    data = vn.collect(conn, TODAY, "monthly")
    stats = {r["dept"]: (r["total"], r["overdue"]) for r in data["dept_stats"]}
    assert stats == {"架構部": (2, 1), "網路部": (1, 0)}
    assert data["dept_stats"][0]["dept"] == "架構部", "逾期多的要排前面——要看的是哪裡在失控"

    vn.run(conn, TODAY, "monthly")
    assert any("各單位統計" in b for b in bodies)

    bodies.clear()
    conn.execute("DELETE FROM vuln_notify_state")
    conn.commit()
    vn.run(conn, TODAY, "weekly")
    assert bodies and not any("各單位統計" in b for b in bodies), "週報是行動清單，不該塞全景統計"
