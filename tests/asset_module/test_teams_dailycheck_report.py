"""1.5 每日檢查日報（Teams，kind=summary_dailycheck）。

不對外送任何訊息：只組卡片、寫本機佇列表。釘住：
  卡片內容（結論、異常列表、另有 N 項、已確認／未確認、沒有 run、夜間資料讀不到不寫 0）、
  排程（08:30 前不排、之後只排一次、開關關閉不排、重複 tick 不重排）、卡片大小、設定驗證、預覽不寫表、
  資料來源狀態的 Python 移植跟 frontend/utils/dailycheckSources.ts 逐案對得上（漂移就紅）。
"""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_check as dc  # noqa: E402
import dailycheck_report as dr  # noqa: E402
import db  # noqa: E402
import teams_notify as tn  # noqa: E402
from test_v2_dailycheck_layers import _node  # noqa: E402

NOW = datetime(2026, 11, 16, 8, 31, 0)
DAY = "2026-11-16"
REAL = "https://prod-12.japaneast.logic.azure.com:443/workflows/abc123SECRETtoken/triggers/manual/paths/invoke?sig=SECRETSIG"


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        import credential_store
        monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(Path(tmp) / "k.key"))
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        dc.ensure(c)
        tn.ensure(c)
        yield c
        c.close()


def _cfg(conn, **kw):
    tn.set_webhook(conn, REAL)
    cfg = {**tn.DEFAULTS, "enabled": True, "live_confirmed": True, "summary_dailycheck": True, "base_url": "http://10.99.0.10:3000", **kw}
    tn.save_settings(conn, cfg)
    conn.commit()
    return tn.load_settings(conn)


def _signoff(conn, on=True):
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (dc.SETTING, json.dumps({"signoff": on})))
    conn.commit()


def _run(conn, rows, day=DAY, status="open"):
    rid = conn.execute("INSERT INTO daily_check_run (day, created_at, trigger, created_by, status) VALUES (?,?,?,?,?)",
                       (day, f"{day} 06:30:00", "schedule", "t", status)).lastrowid
    for i, r in enumerate(rows, 1):
        conn.execute("INSERT INTO daily_check_row (run_id, no, code, section, name, kind, verdict, value_text, reason, data_at, source, result) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, i, r.get("code", f"R-{i}"), "區", r.get("name", f"項目{i}"), "auto", r.get("verdict", "ok"), r.get("value_text"),
                      r.get("reason"), r.get("data_at"), r.get("source", "WhatsUp 告警快照"), r.get("result")))
    if status == "submitted":
        conn.execute("UPDATE daily_check_run SET submitted_by='t', submitted_at=? WHERE id=?", (f"{day} 08:00:00", rid))
    conn.commit()
    return rid


def _texts(card):
    return [b["text"] for b in card["attachments"][0]["content"]["body"] if b["type"] == "TextBlock"]


def _bad_rows(n):
    return [{"verdict": "bad", "name": f"設備{i}", "code": f"B-{i}", "value_text": f"值{i}"} for i in range(1, n + 1)]


# ---------------------------------------------------------------- 卡片內容
def test_結論第一行_數字取自今天最新一次(conn):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}] * 3, day=DAY)
    _run(conn, [{"verdict": "ok"}, {"verdict": "bad", "name": "磁碟"}, {"verdict": "nodata", "reason": "沒資料"}, {"verdict": "ok"}])   # 最新
    title, card, nums = dr.build(conn, cfg, NOW)
    t = _texts(card)
    assert "今天 4 項檢查：異常 1、無資料 1" in t[0] and "資料來源" in t[0]
    assert nums["total"] == 4 and nums["bad"] == 1 and nums["nodata"] == 1
    assert title.endswith(DAY)
    assert "/v2/dailycheck" in card["attachments"][0]["content"]["actions"][0]["url"]
    assert "10.99.0.10" in card["attachments"][0]["content"]["actions"][0]["url"]


def test_異常最多5行_其餘另有N項(conn):
    cfg = _cfg(conn)
    _run(conn, _bad_rows(8))
    _, card, _ = dr.build(conn, cfg, NOW)
    t = _texts(card)
    assert sum(1 for x in t if x.startswith("B-")) == 5
    assert "另有 3 項" in t
    _run(conn, _bad_rows(5), day="2026-11-15")      # 別天的不影響
    conn.execute("DELETE FROM daily_check_row WHERE run_id = (SELECT MAX(id) FROM daily_check_run)")


def test_恰好5項異常不出現另有(conn):
    cfg = _cfg(conn)
    _run(conn, _bad_rows(5))
    _, card, _ = dr.build(conn, cfg, NOW)
    assert not any("另有" in x for x in _texts(card))


def test_未送出_第一行有警語_已送出寫已確認(conn):
    cfg = _cfg(conn)
    _signoff(conn)
    rid = _run(conn, [{"verdict": "ok"}])
    t = _texts(dr.build(conn, cfg, NOW)[1])
    assert t[0].startswith(dr.CONFIRM_WARN) and "已確認" not in t[0]
    conn.execute("UPDATE daily_check_run SET status='submitted', submitted_by='u', submitted_at=? WHERE id=?", (f"{DAY} 08:10:00", rid))
    conn.commit()
    t = _texts(dr.build(conn, cfg, NOW)[1])
    assert t[0].startswith("已確認｜") and "尚未確認" not in t[0]


def test_看板模式_不需送出_不寫未確認警語(conn):
    cfg = _cfg(conn)
    _signoff(conn, False)
    _run(conn, [{"verdict": "ok"}])
    t0 = _texts(dr.build(conn, cfg, NOW)[1])[0]
    assert "尚未確認" not in t0 and "已確認" not in t0


def test_今天沒有run_發短卡不沉默(conn):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "bad"}], day="2026-11-15")      # 昨天有、今天沒有
    title, card, nums = dr.build(conn, cfg, NOW)
    assert _texts(card) == ["今天還沒有產生每日檢查"] and nums["run"] is None
    # 連每日檢查的表都沒有也一樣（不丟例外）
    conn.execute("DROP TABLE daily_check_row")
    conn.execute("DROP TABLE daily_check_run")
    assert _texts(dr.build(conn, cfg, NOW)[1]) == ["今天還沒有產生每日檢查"]


def test_夜間資料讀不到_寫讀不到_不寫0(conn, monkeypatch):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}])
    import night_desk

    def boom(*a, **k):
        raise sqlite3.OperationalError("no such table")
    monkeypatch.setattr(night_desk, "notified", boom)
    t = _texts(dr.build(conn, cfg, NOW)[1])
    night = next(x for x in t if x.startswith("昨晚夜間通知"))
    assert "讀不到" in night and "OperationalError" in night and "0 件" not in night


def test_夜間通知件數與已處理(conn, monkeypatch):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}])
    import night_desk
    monkeypatch.setattr(night_desk, "notified", lambda *a, **k: {"summary": {"total": 3}, "items": [{"handled": True}, {"handled": False}, {"handled": True}]})
    t = _texts(dr.build(conn, cfg, NOW)[1])
    assert "昨晚夜間通知：3 件，已有人處理 2 件" in t


def test_要你處理_資料來源不正常與未確認(conn):
    cfg = _cfg(conn)
    _signoff(conn)
    _run(conn, [{"verdict": "nodata", "reason": "Unity 資料太舊", "source": "Unity 中繼（unity_collect）", "data_at": "2026-11-12 07:00:00"},
                {"verdict": "ok"}])
    t = _texts(dr.build(conn, cfg, NOW)[1])
    todo = next(x for x in t if x.startswith("要你處理"))
    assert "Unity 儲存：過期" in todo and "未確認 2 列" in todo
    _run(conn, [{"verdict": "ok", "result": "ok", "source": "WhatsUp 告警快照"}], day="2026-11-17")
    c2 = dr.build(conn, cfg, datetime(2026, 11, 17, 8, 31))[1]
    assert any(x.startswith("要你處理：資料來源") for x in _texts(c2))


def test_卡片大小與不含真實IP(conn):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "bad", "name": "很長" * 80, "value_text": "值" * 900, "code": f"B-{i}"} for i in range(60)])
    title, card, _ = dr.build(conn, cfg, NOW)
    raw = json.dumps(tn._with_event_id(card, "sum:dc:x"), ensure_ascii=False)
    assert len(raw.encode()) <= tn.CARD_LIMIT_BYTES
    import re
    assert not re.search(r"\b10\.9[0-8]\.\d+\.\d+", raw)        # 只允許示範 10.99.x


def test_預覽走唯讀連線_不寫任何表(conn):
    _cfg(conn)
    _run(conn, _bad_rows(2))
    before = conn.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0]
    with tn.read_only(conn):
        pv = tn.preview(conn, "dailycheck")
    assert pv["card"] and "預覽" in json.dumps(pv["card"], ensure_ascii=False)
    assert conn.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == before


# ---------------------------------------------------------------- 排程
def _at(h, m):
    return datetime(2026, 11, 16, h, m, 0)


def _dc_keys(conn):
    return [r[0] for r in conn.execute("SELECT event_key FROM teams_notify_delivery WHERE kind='summary_dailycheck'")]


def test_08_30前不排_之後只排一次(conn):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}])
    assert tn.enqueue_summaries(conn, cfg, _at(8, 29)) == []
    assert _dc_keys(conn) == []
    assert tn.enqueue_summaries(conn, cfg, _at(8, 30)) == [f"sum:dc:{DAY}"]
    assert tn.enqueue_summaries(conn, cfg, _at(8, 31)) == []
    assert tn.enqueue_summaries(conn, cfg, _at(15, 0)) == []
    assert _dc_keys(conn) == [f"sum:dc:{DAY}"]
    row = conn.execute("SELECT * FROM teams_notify_delivery WHERE kind='summary_dailycheck'").fetchone()
    assert row["status"] == "pending"
    exp = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    assert exp.total_seconds() == tn.SUMMARY_EXPIRE_HOURS * 3600


def test_重複tick不重排_次日再排(conn):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}])
    for _ in range(3):
        tn.tick(conn, _at(8, 40), transport=lambda u, b: (202, ""))
    assert len(_dc_keys(conn)) == 1
    tn.tick(conn, datetime(2026, 11, 17, 8, 40), transport=lambda u, b: (202, ""))
    assert len(_dc_keys(conn)) == 2


def test_已排過_不再重組卡片(conn, monkeypatch):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}])
    n = []
    real = dr.build
    monkeypatch.setattr(dr, "build", lambda *a, **k: n.append(1) or real(*a, **k))
    tn.enqueue_summaries(conn, cfg, _at(8, 30))
    tn.enqueue_summaries(conn, cfg, _at(8, 31))
    assert len(n) == 1


def test_開關關閉不排(conn):
    cfg = _cfg(conn, summary_dailycheck=False)
    _run(conn, [{"verdict": "ok"}])
    assert tn.enqueue_summaries(conn, cfg, _at(9, 0)) == []
    assert _dc_keys(conn) == []


def test_總開關關閉不排(conn):
    cfg = _cfg(conn, enabled=False)
    tn.tick(conn, _at(9, 0))
    assert _dc_keys(conn) == []


def test_自訂時間(conn):
    cfg = _cfg(conn, dailycheck_report_time="09:15")
    assert tn.enqueue_summaries(conn, cfg, _at(9, 14)) == []
    assert len(tn.enqueue_summaries(conn, cfg, _at(9, 15))) == 1


def test_沒有run也排短卡(conn):
    cfg = _cfg(conn)
    assert tn.enqueue_summaries(conn, cfg, _at(8, 30)) == [f"sum:dc:{DAY}"]
    payload = conn.execute("SELECT payload_json FROM teams_notify_delivery WHERE kind='summary_dailycheck'").fetchone()[0]
    assert "今天還沒有產生每日檢查" in payload


def test_靜默時段內不送(conn):
    cfg = _cfg(conn, quiet_start="08:00", quiet_end="09:00")
    _run(conn, [{"verdict": "ok"}])
    sent = []
    tn.tick(conn, _at(8, 40), transport=lambda u, b: sent.append(b) or (202, ""))
    assert sent == []                                    # info 級在靜默時段內留在待送
    assert conn.execute("SELECT status FROM teams_notify_delivery WHERE kind='summary_dailycheck'").fetchone()[0] == "pending"


def test_組卡片失敗不拖垮投遞_晚30分鐘仍失敗才排短卡讓使用者看到(conn, monkeypatch):
    cfg = _cfg(conn)
    monkeypatch.setattr(dr, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert tn.enqueue_summaries(conn, cfg, _at(8, 40)) == []          # 預定 08:30，還沒到 +30 分：只寫日誌、下一輪再試
    assert _dc_keys(conn) == []
    assert tn.enqueue_summaries(conn, cfg, _at(9, 5)) == [f"sum:dc:{DAY}"]   # 過了 09:00 仍失敗：排一張短卡，不靜默
    row = conn.execute("SELECT payload_json FROM teams_notify_delivery WHERE kind='summary_dailycheck'").fetchone()[0]
    assert "組不出來" in row and "RuntimeError" in row
    assert tn.enqueue_summaries(conn, cfg, _at(9, 6)) == []           # 同一個 key，一天只一則


def test_lastpoll與vmware的age_min缺值_不丟例外_當很舊():
    """獨立審查 P2：age_min 缺值曾讓整張日報組不出來。"""
    lp = dr.lastpoll_status({"latest": {"collected_at": "2026-11-16 06:00:00", "complete": True},
                             "latest_complete": {"collected_at": "2026-11-16 06:00:00"}, "groups": []},
                            lp_alert=None, fresh_min=35, now_hm="09:00") if hasattr(dr, "lastpoll_status") else None
    assert lp is not None and lp["status"] in ("過期", "正常", "只收到一半", "失敗", "沒收到", "待設定")
    assert dr._age({"age_min": None}) == float("inf") and dr._age({}) == float("inf") and dr._age({"age_min": "12"}) == 12.0


def test_關掉日報_取消未送出的(conn):
    cfg = _cfg(conn)
    _run(conn, [{"verdict": "ok"}])
    tn.enqueue_summaries(conn, cfg, _at(9, 0))
    tn.save_settings(conn, {**cfg, "summary_dailycheck": False})
    conn.commit()
    assert conn.execute("SELECT status FROM teams_notify_delivery WHERE kind='summary_dailycheck'").fetchone()[0] == "cancelled"


# ---------------------------------------------------------------- 設定驗證
def test_預設關閉_時間0830():
    assert tn.DEFAULTS["summary_dailycheck"] is False and tn.DEFAULTS["dailycheck_report_time"] == "08:30"


@pytest.mark.parametrize("bad", ["25:00", "8:5", "abc", "08:60"])
def test_日報時間格式錯誤被拒(bad):
    with pytest.raises(ValueError):
        tn.validate({**tn.DEFAULTS, "dailycheck_report_time": bad})


def test_日報時間空白回預設():
    assert tn.validate({**tn.DEFAULTS, "dailycheck_report_time": ""})["dailycheck_report_time"] == "08:30"


def test_舊設定沒有新欄位_讀出來是預設(conn):
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (tn.SETTING_KEY, json.dumps({"enabled": True, "summary_daily": True})))
    cfg = tn.load_settings(conn)
    assert cfg["summary_dailycheck"] is False and cfg["dailycheck_report_time"] == "08:30"


# ---------------------------------------------------------------- 資料來源狀態：跟畫面（.ts）逐案對照
def _same(py, js):
    assert (py is None) == (js is None)
    if py is not None:
        assert {k: py[k] for k in ("status", "at", "next")} == {k: js[k] for k in ("status", "at", "next")}


ROW_CASES = {
    "empty": [],
    "manual_only": [{"verdict": "manual"}],
    "ok": [{"code": "W-a", "verdict": "ok", "data_at": "2026-11-14 06:20:00"}],
    "half": [{"code": "S-1", "verdict": "ok", "data_at": "2026-11-14 07:00:00"}, {"code": "S-2", "verdict": "nodata", "reason": "對不到中繼資料"}],
    "stale": [{"code": "S-1", "verdict": "nodata", "reason": "Unity 資料太舊（最後 11-12）", "data_at": "2026-11-12 07:00:00"}],
    "expired": [{"code": "S-1", "verdict": "nodata", "reason": "資料已過期", "data_at": "2026-11-12 07:00:00"}],
    "fail": [{"code": "S-1", "verdict": "nodata", "reason": "讀取失敗", "data_at": "2026-11-12 07:00:00"}],
    "none": [{"code": "S-1", "verdict": "nodata", "reason": "沒有資料"}],
    "unset": [{"code": "S-1", "verdict": "nodata", "reason": "板橋清單未設定"}],
    "mixed": [{"code": "A", "verdict": "nodata", "reason": "太舊", "data_at": "x"}, {"code": "B", "verdict": "nodata", "reason": "失敗", "data_at": "y"}],
    "long": [{"code": "S-1", "verdict": "nodata", "reason": "很長的原因" * 40}],
}


def test_rowsStatus_逐案同畫面():
    js = _node(f"Object.fromEntries(Object.entries({json.dumps(ROW_CASES)}).map(([k, v]) => [k, m.rowsStatus(v)]))")
    for k, rows in ROW_CASES.items():
        _same(dr.rows_status(rows), js[k])


def test_srcKey_與_partialWhy_同畫面():
    srcs = ["WhatsUp 告警快照", "WhatsUp 上次輪循", "vROps（VMware）", "Unity 中繼（unity_collect）", "AD 對時中繼", "WhatsUp 效能中繼", "WhatsUp 磁碟中繼／納管健康檢查", None, "", "手填"]
    assert [dr.src_key_of(s) for s in srcs] == _node(f"{json.dumps(srcs)}.map(m.srcKeyOf)")
    errs = [None, "", "report cap hit", "per-device cpu", "time box", "call budget", "page cap", "HTTP 503", "timeout", "HTTP 403", "weird"]
    assert [dr.partial_why(e) for e in errs] == _node(f"{json.dumps(errs)}.map(m.partialWhy)")


def _lp(**kw):
    base = {"latest": {"collected_at": "2026-11-16 08:00:00", "complete": True, "problems": []},
            "latest_complete": {"collected_at": "2026-11-16 08:00:00", "age_min": 10},
            "groups": [{"path": "A > B", "ok": True, "scope_ok": True, "partial": [], "members": 10}], "fresh_min": 35}
    base.update(kw)
    return base


LP_CASES = {
    "none": (None, "10:00", None),
    "no_latest": ({"latest": None}, "10:00", None),
    "ok": (_lp(), "10:00", None),
    "failed": (_lp(groups=[{"path": "G1", "ok": False, "error": "boom"}, {"path": "G2", "ok": False}]), "10:00", None),
    "alert": (_lp(), "10:00", "上次輪循 60 分鐘沒有新的完整批次"),
    "no_complete": (_lp(latest_complete=None, latest={"collected_at": "x", "complete": False, "problems": ["缺 cpu"]}), "10:00", None),
    "stale_in_window": (_lp(latest_complete={"collected_at": "x", "age_min": 99}), "10:00", None),
    "stale_off_window": (_lp(latest_complete={"collected_at": "x", "age_min": 99}), "20:00", None),
    "incomplete_latest": (_lp(latest={"collected_at": "2026-11-16 08:10:00", "complete": False, "problems": ["少一個"]}), "10:00", None),
    "partial": (_lp(groups=[{"path": "A > 資料庫", "ok": True, "scope_ok": True, "partial": ["cpu"], "members": 120,
                             "partial_why": {"cpu": {"error": "report cap", "devices": 100}}}]), "10:00", None),
    "partial_noinfo": (_lp(groups=[{"path": "A > 資料庫", "ok": True, "scope_ok": True, "partial": ["mem"], "partial_why": {"mem": {"error": "", "devices": None}}}]), "10:00", None),
    "unscoped": (_lp(groups=[{"path": "G", "ok": True, "scope_ok": False}]), "10:00", None),
}


def test_lastpollStatus_逐案同畫面():
    js = _node("Object.fromEntries(Object.entries(" + json.dumps({k: list(v) for k, v in LP_CASES.items()})
               + ").map(([k, [s, hm, al]]) => [k, m.lastpollStatus(s, 35, hm, al)]))")
    for k, (lp, hm, al) in LP_CASES.items():
        _same(dr.lastpoll_status(lp, 35, hm, al), js[k])


_VOK = {"latest": {"collected_at": "2026-11-16 06:20:00", "complete": True, "problems": []}, "latest_complete": {"collected_at": "2026-11-16 06:20:00", "age_min": 60}}
_VYD = {"latest": {"collected_at": "2026-11-15 07:30:00", "complete": True, "problems": []}, "latest_complete": {"collected_at": "2026-11-15 07:30:00", "age_min": 1410}}
VM_CASES = {
    "none": (None, True, "", ""),
    "no_latest": ({"latest": None}, True, "", ""),
    "ok": (_VOK, True, "2026-11-16", "09:00"),
    "unset": (_VOK, False, "2026-11-16", "09:00"),
    "no_complete": ({**_VOK, "latest_complete": None}, True, "", ""),
    "old": ({**_VOK, "latest_complete": {"collected_at": "x", "age_min": 3000}}, True, "", ""),
    "not_today_late": (_VYD, True, "2026-11-16", "07:50"),
    "not_today_early": (_VYD, True, "2026-11-16", "05:00"),
    "incomplete": ({**_VOK, "latest": {"collected_at": "2026-11-16 07:00:00", "complete": False, "problems": ["少"]}}, True, "", ""),
}


def test_vmwareStatus_逐案同畫面():
    js = _node("Object.fromEntries(Object.entries(" + json.dumps({k: list(v) for k, v in VM_CASES.items()})
               + ").map(([k, a]) => [k, m.vmwareStatus(...a)]))")
    for k, a in VM_CASES.items():
        _same(dr.vmware_status(*a), js[k])


def test_buildSources_同畫面():
    rows = [{"code": "W-a", "verdict": "ok", "source": "WhatsUp 告警快照", "data_at": "2026-11-16 06:20:00"},
            {"code": "S-1", "verdict": "nodata", "source": "Unity 中繼（unity_collect）", "reason": "資料太舊", "data_at": "2026-11-12 07:00:00"},
            {"code": "A-1", "verdict": "ok", "source": "AD 對時中繼", "data_at": "2026-11-16 06:00:00"},
            {"code": "O-1", "verdict": "manual", "source": None}]
    for perf in ("lastpoll", "w3"):
        inp = {"rows": rows, "lpst": _lp(), "vmst": _VOK, "perfSource": perf, "vmConfigured": True, "lpFreshMin": 35, "nowHM": "10:00", "nowYMD": "2026-11-16", "lpAlert": None}
        js = _node(f"m.buildSources({json.dumps(inp)})")
        py = dr.build_sources(rows, _lp(), _VOK, perf, True, 35, "10:00", "2026-11-16", None)
        assert [(s["key"], s["__no"], s["status"], s["at"], s["next"]) for s in js] == [(s["key"], s["no"], s["status"], s["at"], s["next"]) for s in py]
