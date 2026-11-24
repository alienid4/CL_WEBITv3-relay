"""EOS 追蹤 最後一輪小修（2.041.53）：消失列未通知仍是待辦、收件人解析失敗／信箱清空不沿用舊成功（引擎與追蹤）、
舊資料 NULL 收件地址不重寄、通知引擎用台北今日、版本鎖並發、verify_numbers 補強。"""
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROLE_AWARE_TESTS = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ad_import  # noqa: E402
import db  # noqa: E402
import eos  # noqa: E402
import eos_follow as ef  # noqa: E402
import eos_notify as en  # noqa: E402
import vuln_notify as vn  # noqa: E402

TODAY = date(2026, 11, 1)
NOW = datetime(2026, 11, 1, 10, 0, 0)
FAKE_OS = [{"name": "Rocky Linux 9", "eos_date": "2027-03-01", "source_url": "https://example.invalid/r"}]
SMTP = {"smtp_host": "192.0.2.25", "smtp_port": 25, "mail_from": "webit3@example.invalid"}
HOLD_OK = {"reply_state": "擬保留", "reply_source": "email", "hold_reason_class": "預算",
           "hold_basis_text": "預算未核定", "hold_evidence_ref": "會議紀錄", "hold_review_date": "2027-04-30"}


@pytest.fixture(autouse=True)
def fake_eos_tables():
    eos._os_table = FAKE_OS
    eos._hw_table = []
    yield
    eos.reload_tables()


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, asset_status, environment) VALUES ('S1','srv-a','10.99.0.1','Rocky Linux 9','使用中','正式')")
    c.execute("INSERT INTO software (asset_serial, owner) VALUES ('S1','王小明')")
    c.execute(ad_import.TABLE_SQL)
    vn._ensure(c)
    en._ensure(c)
    vn.save_settings(c, SMTP)
    en.save_settings(c, {"enabled": True, "fallback_to": "soc@example.invalid"})
    c.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, manager_mail, dept_name) VALUES ('E1','王小明','ming@example.invalid',1,NULL,'架構部')")
    c.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, manager_mail, dept_name) VALUES ('E2','別人','other@example.invalid',1,NULL,'架構部')")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def ov_of(c, today=TODAY):
    return ef.overview(c, today)


def row(ov, host="srv-a", owner=None):
    hit = [r for r in ov["rows"] if r["hostname"] == host and (owner is None or r["owner_ref"] == owner)]
    assert len(hit) == 1, [(r["hostname"], r["owner_ref"]) for r in ov["rows"]]
    return hit[0]


def put(c, fields, reason=None, now=NOW, **kw):
    r = row(ov_of(c, now.date()))
    return ef.upsert(c, r["item_key"], r["owner_ref"], fields, reason, "alice", now, **kw)


def sim_state(c, recipient, bucket=180, owner="王小明"):
    cands, _ = en.eos_candidates(c, TODAY)
    cand = next(x for x in cands if x["hostname"] == "srv-a")
    c.execute("INSERT OR REPLACE INTO eos_notify_state (state_key, last_bucket, last_sent_at, last_recipient) VALUES (?,?,?,?)",
              (f"{cand['track']}|{cand['mk']}|{owner}|{cand['item']}", bucket, "2026-10-12 08:00:00", recipient))
    c.commit()


def retire(c):
    c.execute("UPDATE hardware SET asset_status='報廢'")
    c.commit()


def in_collect(c, **kw):
    d = en.collect(c, TODAY, **kw)
    return (any(i["hostname"] == "srv-a" for g in d["by_owner"].values() for i in g["items"]),
            [o for o in d["orphans"] if o["hostname"] == "srv-a"])


class Sender:
    def __init__(self):
        self.mails = []

    def __call__(self, cfg, to, cc, subject, body):
        self.mails.append({"to": to, "body": body})
        return True, "250 OK"


# ───────────── 1. 消失列：尚未通知／未回覆不掉出待辦
def test_stale未通知仍有unnotified旗標與待辦(conn):
    put(conn, {})                                                    # 已有負責人、待回覆、尚未通知
    assert row(ov_of(conn))["stale"] is False
    retire(conn)
    r = row(ov_of(conn))
    assert r["stale"] and "unnotified" in r["flags"] and r["todo"] is True
    assert "項目或負責人已不在目前清單" in r["status"]
    ov = ov_of(conn)
    assert next(t["count"] for t in ov["tiles"] if t["key"] == "unnotified") == 1
    assert next(t["count"] for t in ov["tiles"] if t["key"] == "todo") == 1


def test_stale已通知待回覆_是未回覆待辦(conn):
    sim_state(conn, None)
    put(conn, {})
    retire(conn)
    r = row(ov_of(conn))
    assert r["stale"] and "unreplied" in r["flags"] and "unnotified" not in r["flags"] and r["todo"]


def test_stale很遠的EOS不算尚未通知(conn):
    put(conn, {})
    conn.execute("UPDATE eos_follow SET eos_date='2035-01-01'")
    conn.commit()
    retire(conn)
    r = row(ov_of(conn))
    assert r["stale"] and "unnotified" not in r["flags"] and r["todo"] is False


# ───────────── 2. 收件人解析失敗／信箱清空
def test_信箱被清空_舊地址的成功不算_追蹤待核對_引擎進沒人收(conn):
    sim_state(conn, "ming@example.invalid")
    assert row(ov_of(conn))["notified"] is True
    assert in_collect(conn) == (False, [])                           # 地址沒變：不重寄
    conn.execute("UPDATE ad_person SET mail='' WHERE employee_id='E1'")
    conn.commit()
    r = row(ov_of(conn))
    assert r["notified"] is False and r["notify_map"] == "recipient_unverified" and r["status"] == "收件地址無法確認（待核對）"
    assert "unnotified" in r["flags"] and r["todo"]
    sent, orphans = in_collect(conn)
    assert sent is False and len(orphans) == 1                       # 進既有「沒人收」，不是沿用舊 state 跳過


def test_收件人解析失敗_查無此人_同樣處理(conn):
    sim_state(conn, "ming@example.invalid")
    conn.execute("DELETE FROM ad_person WHERE employee_id='E1'")
    conn.commit()
    r = row(ov_of(conn))
    assert r["notified"] is False and r["status"] == "收件地址無法確認（待核對）"
    sent, orphans = in_collect(conn)
    assert sent is False and len(orphans) == 1
    d = en.preview(conn, TODAY)
    assert any(o["hostname"] == "srv-a" for o in d["orphans"])


def test_沒記收件地址的舊資料_解析失敗仍沿用舊行為_不新增噪音(conn):
    sim_state(conn, None)
    conn.execute("DELETE FROM ad_person WHERE employee_id='E1'")
    conn.commit()
    assert in_collect(conn) == (False, [])                           # NULL 不重寄、也不突然進沒人收


# ───────────── 4. NULL 相容：不得因為 NULL 重寄
def test_舊資料NULL收件地址_算已通知_引擎不重寄_畫面提示舊資料(conn):
    sim_state(conn, None)
    r = row(ov_of(conn))
    assert r["notified"] is True and r["recipient_unrecorded"] is True and "unnotified" not in r["flags"]
    assert in_collect(conn) == (False, [])
    snd = Sender()
    en.run(conn, TODAY, "monthly", sender=snd)
    assert not any("srv-a" in m["body"] for m in snd.mails)           # 實際跑一期也沒有重寄這一筆
    assert conn.execute("SELECT last_recipient FROM eos_notify_state").fetchone()[0] is None      # 沒寄就不動它


def test_下次真實寄送成功才寫入收件地址(conn):
    sim_state(conn, "old@example.invalid")                            # 地址換了才會真的寄
    snd = Sender()
    res = en.run(conn, TODAY, "monthly", sender=snd)
    assert res["sent"] >= 1 and any(m["to"] == "ming@example.invalid" and "srv-a" in m["body"] for m in snd.mails)
    assert conn.execute("SELECT last_recipient FROM eos_notify_state").fetchone()[0] == "ming@example.invalid"
    r = row(ov_of(conn))
    assert r["notified"] is True and r["recipient_unrecorded"] is False


# ───────────── 3. 通知引擎用台北今天
def test_引擎的今天是台北_跨日(conn):
    utc = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)         # UTC 還是 10/31，台北 11/01 00:30
    assert en.today_tpe(utc) == date(2026, 11, 1)
    d = en.collect(conn, None, "monthly", ignore_state=True, now_utc=utc)
    assert d["today"] == "2026-11-01"
    d2 = en.collect(conn, None, "monthly", ignore_state=True, now_utc=datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc))
    assert d2["today"] == "2026-10-31"
    src = (ROOT / "APP" / "asset-module" / "backend" / "eos_notify.py").read_text(encoding="utf-8")
    assert "date.today()" not in src
    assert ef.today_tpe is en.today_tpe                              # 追蹤與引擎共用同一個 helper


# ───────────── 6. 並發更新／作廢（版本鎖）
def test_兩個連線並發更新_後到的拿舊版本被擋(conn, tmp_path):
    first = put(conn, HOLD_OK)["row"]
    c2 = db.get_connection(tmp_path / "t.db")
    try:
        r = row(ov_of(conn))
        ef.upsert(conn, r["item_key"], r["owner_ref"], {"hold_review_date": "2027-05-30"}, "甲", "alice", NOW, expected_version=first["version"])
        with pytest.raises(ef.Conflict):
            ef.upsert(c2, r["item_key"], r["owner_ref"], {"hold_review_date": "2027-06-30"}, "乙", "bob", NOW, expected_version=first["version"])
        assert ef.get_follow(c2, r["item_key"], r["owner_ref"])["hold_review_date"] == "2027-05-30"
    finally:
        c2.close()


def test_並發作廢與更新_作廢後更新被擋_更新後作廢拿舊版本被擋(conn, tmp_path):
    first = put(conn, HOLD_OK)["row"]
    retire(conn)
    r = row(ov_of(conn))
    c2 = db.get_connection(tmp_path / "t.db")
    try:
        ef.upsert(conn, r["item_key"], r["owner_ref"], {"hold_review_date": "2027-05-30"}, "甲更新", "alice", NOW, expected_version=first["version"])
        with pytest.raises(ef.Conflict):
            ef.void(c2, r["item_key"], r["owner_ref"], "乙作廢（舊版本）", "bob", NOW, expected_version=first["version"])
        ef.void(c2, r["item_key"], r["owner_ref"], "乙重新整理後作廢", "bob", NOW, expected_version=2)
        with pytest.raises(ValueError, match="已作廢"):
            ef.upsert(conn, r["item_key"], r["owner_ref"], {"hold_review_date": "2027-06-30"}, "甲再更新", "alice", NOW, expected_version=3)
    finally:
        c2.close()


# ───────────── 5. verify_numbers 補強
def test_verify_numbers_補強_舊資料違規會警示(conn):
    import verify_numbers as vn_
    db.set_setting(conn, "verify_strict_eos", "1")
    put(conn, HOLD_OK)
    vn_.FAILS = vn_.CHECKS = 0
    vn_.check_eos_follow(conn, today=TODAY)
    assert vn_.FAILS == 0
    assert "[​-‏‪-‮⁠-⁯﻿­᠎ㅤﾠ]" == ef._ZW.pattern
    for sql in ("UPDATE eos_follow SET hold_review_date='2031-01-01'",                      # 超過 24 個月的舊資料
                "UPDATE eos_follow SET version=0",
                "UPDATE eos_follow SET version=NULL",
                "UPDATE eos_follow SET voided_at='2026-11-01 00:00:00+08:00', void_reason=NULL",
                "UPDATE eos_follow SET voided_at='2026-11-01 00:00:00+08:00', void_reason='ㅤﾠ'"):
        c2 = sqlite3.connect(":memory:")
        conn.backup(c2)
        c2.row_factory = sqlite3.Row
        try:
            c2.execute(sql)
        except sqlite3.IntegrityError:
            c2.close()
            continue                                                  # version NOT NULL 本身就擋
        c2.commit()
        vn_.FAILS = 0
        vn_.check_eos_follow(c2, today=TODAY)
        assert vn_.FAILS >= 1, sql
        c2.close()
    conn.execute("UPDATE eos_follow SET hold_basis_text='ㅤﾠ'")           # Hangul filler 不算依據（應用端）
    conn.commit()
    assert "hold_no_basis" in row(ov_of(conn))["flags"]
    vn_.FAILS = 0
    vn_.check_eos_follow(conn, today=TODAY)
    assert vn_.FAILS == 0                                              # 獨立重算同樣視為空 → 與磚一致


def test_verify_numbers_預設是觀察模式不計失敗(conn):
    import verify_numbers as vn_
    put(conn, HOLD_OK)
    conn.execute("UPDATE eos_follow SET version=0")
    conn.commit()
    vn_.FAILS = 0
    vn_.check_eos_follow(conn, today=TODAY)
    assert vn_.FAILS == 0
