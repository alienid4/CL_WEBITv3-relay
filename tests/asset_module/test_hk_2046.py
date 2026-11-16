"""2.046.0 目錄清理：R18 清理候選＋接告警、R19 夜間自動（預設關閉）。危險區 D2（送到目標機的指令不變，只加選誰、何時掃）。
規格 docs/ui-v2/磁碟清理候選_規格_2026-11-04.md。全部假資料、IP 用 10.99.x、不連任何主機（runner 注入）。"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import dir_housekeeping as hk  # noqa: E402
from test_dir_housekeeping import LINUX_OUT, _client  # noqa: E402

NIGHT = dt.datetime(2026, 10, 28, 22, 0)          # 週三晚上
EARLY = dt.datetime(2026, 10, 29, 6, 59)          # 同一晚
SEVEN = dt.datetime(2026, 10, 29, 7, 0)
NEXT = dt.datetime(2026, 10, 29, 19, 0)           # 下一晚
DAY = dt.datetime(2026, 10, 28, 10, 0)            # 盤中
ROLE_AWARE_TESTS = True                            # 權限要照正式行為（tester＝一般帳號）


def _detail(denied=0, partial=0):
    dirs = [
        {"mount": "/data", "path": "/data/app", "kb": 5_400_000, "old_kb": 100, "old_files": 1, "newest": "2026-10-01"},
        {"mount": "/data", "path": "/data/app/logs", "kb": 5_000_000, "old_kb": 3_000_000, "old_files": 40, "newest": "2024-01-01"},
        {"mount": "/data", "path": "/data/tmp", "kb": 3_000_000, "old_kb": 100, "old_files": 1, "newest": "2026-10-27"},      # 舊檔很少：不標「超過 2 年」
        {"mount": "/data", "path": "/data/arch", "kb": 2_000_000, "old_kb": 0, "old_files": 0, "newest": "2026-09-01"},
        {"mount": "/data", "path": "/data/arch/2023", "kb": 1_000_000, "old_kb": 0, "old_files": 0, "newest": "2024-01-01"},  # 子目錄比大檔小
        {"mount": "/data", "path": "/data/catalog", "kb": 1_200_000, "old_kb": 0, "old_files": 0, "newest": "2026-10-20"},
    ]
    files = [{"mount": "/data", "kb": 3_100_000, "mtime": "2023-05-01", "path": "/data/app/logs/server.log"},
             {"mount": "/data", "kb": 1_500_000, "mtime": "2025-01-01", "path": "/data/arch/2024.tar.gz"}]
    return {"fs": [{"mount": "/data", "pct": 91}], "dirs": dirs, "files": files, "stats": {"/data": {"denied": denied, "partial": partial}}}


def test_候選_前3個目錄_往下鑽_最大一項_原因():
    cs = hk.candidates(_detail())
    assert len(cs) == 1 and cs[0]["mount"] == "/data" and cs[0]["pct"] == 91
    items = cs[0]["items"]
    assert [x["path"] for x in items] == ["/data/app/logs", "/data/tmp", "/data/arch"], "子目錄佔 ≥80% 往下鑽；前 3；不重複同一串"
    logs, tmp, arch = items
    assert logs["biggest"]["path"] == "/data/app/logs/server.log" and logs["biggest"]["kind"] == "file"
    assert logs["reasons"][0] == "log" and any("超過 2 年沒動（佔 60%）" == r for r in logs["reasons"])
    assert tmp["reasons"] == ["tmp"]
    assert "舊封存" in arch["reasons"] and arch["biggest"]["path"].endswith(".tar.gz")
    assert cs[0]["flags"] == []


def test_候選_判不出原因不硬猜_權限與逾時照實標():
    d = _detail(denied=12, partial=1)
    d["dirs"] = [x for x in d["dirs"] if x["path"] == "/data/catalog"]
    d["files"] = []
    cs = hk.candidates(d)
    assert cs[0]["items"][0]["reasons"] == [hk.REASON_NONE], "catalog 不是 log（整段比對，不做子字串）"
    assert any("權限不足" in f and "12" in f for f in cs[0]["flags"]) and any("逾時" in f for f in cs[0]["flags"])
    assert hk.candidates({"summary": "Windows 報告"}) == [], "Windows 只有全文：不從文字猜"
    assert hk.reasons_of("/var/crash/core.1234") == ["core dump"]
    assert hk.reasons_of("/u01/backup") == ["舊封存"]


def test_結果頁與摘要都帶候選(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        assert hk.brief(c, ip="10.99.8.1") == "目錄清理：尚未掃描"
        r = hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: LINUX_OUT)
        res = hk.result(c, r["ids"][0])
        assert res["candidates"] and res["candidates"][0]["items"][0]["path"] == "/data/app"
        b = hk.brief(c, ip="10.99.8.1")
        assert b.startswith("容量占用候選（10-28 22:00 掃，請資料擁有者判斷處理方式）：/data/app") and "權限不足" in b, b
        assert "；/data/tmp" in b and b.index("/data/app") < b.index("/data/tmp"), "放得下的照順序都塞進去"
        hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: "@@NOFS\t80\n")
        assert "沒有檔案系統超過門檻" in hk.brief(c, serial="L1")
        c.close()


def test_告警通知_磁碟類附housekeeping摘要(monkeypatch):
    import alert_contact
    import alert_notify
    import wug_alert as wa
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: LINUX_OUT)
        c.execute("CREATE TABLE IF NOT EXISTS alert_event (id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO alert_event (id) VALUES (1)")
        c.execute("INSERT INTO alert_event (id) VALUES (2)")
        rows = {1: {"id": 1, "ip": "10.99.8.1", "monitor": "Disk Utilization", "status_text": "/data 91%", "open": True},
                2: {"id": 2, "ip": "10.99.8.1", "monitor": "Ping", "status_text": "down", "open": True}}
        monkeypatch.setattr(wa, "_has", lambda conn, t: True)
        monkeypatch.setattr(wa, "_row", lambda r: dict(rows[r["id"]]))
        monkeypatch.setattr(alert_contact, "attach", lambda conn, rs, key="api_id": rs)
        ev = alert_notify._event(c, 1)
        assert ev["hk_brief"].startswith("容量占用候選") and "/data/app" in alert_notify.action_text(ev)
        assert "hk_brief" not in alert_notify._event(c, 2), "不是磁碟告警不查"
        assert alert_notify.action_text({"monitor": "disk", "open": True}).endswith("目錄清理：尚未掃描")
        c.close()


def _withto(cmd, out):
    """假目標機：有 timeout 指令（夜間自動要先確認有遠端逾時保護）。"""
    return "hk_to_ok\n" if cmd.startswith("command -v timeout") else out


def _night_db(tmp, monkeypatch):
    from test_dir_housekeeping import _conn
    c, p = _conn(tmp)
    db.insert_hardware(c, asset_serial="L3", hostname="lx3", ip="10.99.8.3", os="Red Hat Enterprise Linux 8", asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'L3'")
    for serial, ip, free in (("L1", "10.99.8.1", 9.0), ("L2", "10.99.8.2", 5.0), ("L3", "10.99.8.3", 20.1)):
        c.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?, ?, ?, '2026-10-28')",
                  (serial, ip, json.dumps({"by": "mount", "items": [{"name": "/", "size_gb": 50, "free_gb": 40},
                                                                    {"name": "/data", "size_gb": 100, "free_gb": free}]})))
    c.commit()
    return c


def test_夜間自動_預設關閉_時段_一次一台_每晚一次(monkeypatch):
    clock = {"t": NIGHT}
    monkeypatch.setattr(hk, "_now", lambda: clock["t"])
    with tempfile.TemporaryDirectory() as tmp:
        c = _night_db(tmp, monkeypatch)
        sent = []
        run = lambda h, cmd: sent.append((h, cmd)) or _withto(cmd, LINUX_OUT)        # noqa: E731
        assert hk.night_tick(c, runner=run, sync=True) == {"skip": "off"}, "預設關閉"
        assert hk.night_targets(c) == [("L2", 95.0), ("L1", 91.0)], "只挑 ≥80%（L3 79.9 不挑），使用率高的先"
        hk.set_night(c, True, "admin")
        r = hk.night_tick(c, runner=run, sync=True)
        assert r["queued"] == "L1", "L2 沒納管，閘門擋下；排 L1"
        assert hk.night_tick(c, runner=run, sync=True) == {"skip": "none"}, "每台每晚最多一次"
        clock["t"] = EARLY
        assert hk.night_tick(c, runner=run, sync=True) == {"skip": "none"}, "00:00～06:59 還是同一晚"
        clock["t"] = SEVEN
        assert hk.night_tick(c, runner=run, sync=True) == {"skip": "window"}, "07:00 之後不開新的一台"
        clock["t"] = NEXT
        assert hk.night_tick(c, runner=run, sync=True)["queued"] == "L1", "下一晚再掃"
        assert {h for h, _ in sent} == {"10.99.8.1"} and sum("-printf" in cmd for _, cmd in sent) == 2, "只掃了 L1、兩晚各一次"
        scans = hk.night_status(c, NEXT)["scans"]
        assert [s["serial"] for s in scans] == ["L1"] and hk.night_status(c, NEXT)["enabled"] is True
        c.close()


def test_夜間自動_盤中守門_有掃描在跑就等_忙碌算一次_VMware不掃(monkeypatch):
    import manage_state
    import quiet_window as qw
    clock = {"t": NIGHT}
    monkeypatch.setattr(hk, "_now", lambda: clock["t"])
    with tempfile.TemporaryDirectory() as tmp:
        c = _night_db(tmp, monkeypatch)
        hk.set_night(c, True, "admin")
        assert qw.ENTRIES["hk_night_tick"]["cls"] == "A"
        monkeypatch.setattr(qw, "in_quiet_window", lambda conn, now=None: True)
        assert hk.night_tick(c, runner=lambda h, cmd: pytest.fail("盤中不可連線"), sync=True) == {"skip": "quiet"}
        monkeypatch.undo()
        monkeypatch.setattr(hk, "_now", lambda: clock["t"])
        hk._active["running"] = True
        try:
            assert hk.night_tick(c, runner=lambda h, cmd: pytest.fail("一次一台"), sync=True) == {"skip": "running"}
        finally:
            hk._active["running"] = False
        r = hk.night_tick(c, runner=lambda h, cmd: _withto(cmd, "@@BUSY\tload=9\tcpus=4\n"), sync=True)
        assert r["queued"] == "L1" and hk.result(c, r["ids"][0])["status"] == "busy", hk.result(c, r["ids"][0])
        assert hk.night_tick(c, runner=lambda h, cmd: pytest.fail("忙就跳過，這晚不再敲"), sync=True) == {"skip": "none"}
        clock["t"] = NEXT
        monkeypatch.setattr(manage_state, "drop_vmware_targets", lambda conn, rows: ([], [{"reason": "VMware 不可納管"}]))
        assert hk.night_tick(c, runner=lambda h, cmd: pytest.fail("VMware 不掃"), sync=True) == {"skip": "none"}
        c.close()


def test_夜間自動_開關只給管理員_設定壞了當關閉(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp, "tester")
        assert cl.put("/api/housekeeping/night", json={"enabled": True}).status_code == 403
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp, "admin")
        assert cl.get("/api/housekeeping/overview").json()["night"]["enabled"] is False
        r = cl.put("/api/housekeeping/night", json={"enabled": True})
        assert r.status_code == 200 and r.json()["night"]["enabled"] is True and r.json()["night"]["changed_by"]
        ov = cl.get("/api/housekeeping/overview").json()["night"]
        assert ov["window"] == "19:00～07:00" and ov["changed_at"]
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        c.execute("INSERT INTO app_settings (key, value) VALUES ('hk_night_enabled', 'yes')")
        assert hk.night_enabled(c) is False, "不是明確的 1 一律當關閉"
        c.close()


def test_夜間自動_排程與規則都接上():
    src = (ROOT / "APP" / "asset-module" / "backend" / "scan_service.py").read_text(encoding="utf-8")
    assert "_hk_night_tick(conn)" in src and "dir_housekeeping.night_tick(conn)" in src
    doc = (ROOT / "APP" / "asset-module" / "backend" / "dir_housekeeping.py").read_text(encoding="utf-8")
    assert "手動＋預設關閉的夜間自動" in doc and "沒有排程" not in doc, "鐵律第 2 條要跟著改，不留矛盾的舊字"


def test_候選_沒有第一層目錄時_去掉祖先子孫重複():
    d = {"fs": [{"mount": "/data", "pct": 88}], "files": [], "stats": {},
         "dirs": [{"mount": "/data", "path": "/data/x/y", "kb": 5_000_000, "old_kb": 0},
                  {"mount": "/data", "path": "/data/x/y/z", "kb": 3_000_000, "old_kb": 0},
                  {"mount": "/data", "path": "/data/w/v", "kb": 2_000_000, "old_kb": 0}]}
    assert [x["path"] for x in hk.candidates(d)[0]["items"]] == ["/data/x/y", "/data/w/v"], "z 在 y 底下，不重複列"


def test_摘要_不拿掃描中的那一筆(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: LINUX_OUT)
        c.execute("INSERT INTO hk_scan (asset_serial, ip, status, queued_at) VALUES ('L1', '10.99.8.1', 'running', '2026-10-28 23:00:00')")
        c.commit()
        assert hk.brief(c, ip="10.99.8.1").startswith("容量占用候選"), "掃描中的那一筆還沒結果，用上一次完成的"
        c.close()


def test_夜間自動_離0830不到15分鐘不開新的一台(monkeypatch):
    """07:00 之後本來就不開；這條是第二道：就算時段設定被改到 08:20，離 08:30 太近也不開。"""
    t = dt.datetime(2026, 10, 29, 8, 20)
    monkeypatch.setattr(hk, "_now", lambda: t)
    monkeypatch.setattr(hk, "NIGHT_END", dt.time(8, 25))
    with tempfile.TemporaryDirectory() as tmp:
        c = _night_db(tmp, monkeypatch)
        hk.set_night(c, True, "admin")
        assert hk.night_tick(c, runner=lambda h, cmd: pytest.fail("離 08:30 太近不可開始"), sync=True) == {"skip": "runway"}
        c.close()


def test_畫面與手冊都講清楚():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    risk = (fe / "pages" / "v2" / "risk.vue").read_text(encoding="utf-8")
    assert 'data-testid="hk-night"' in risk and "candRows(hkResult.value?.candidates)" in risk and "容量占用候選" in risk
    assert "nightConfirmText(on)" in risk, "開關前要確認"
    man = (fe / "content" / "manual.ts").read_text(encoding="utf-8")
    assert "容量占用候選" in man and "夜間自動掃描" in man and "請資料擁有者判斷處理方式" in man and "19:00～07:00" in man
    spec_f = ROOT / "docs" / "ui-v2" / "磁碟清理候選_規格_2026-11-04.md"
    if not spec_f.exists():
        return                                        # docs/ 不進公開中繼（決策 G1），中繼產出物上沒有規格檔
    spec = spec_f.read_text(encoding="utf-8")
    assert "## 附錄 A　UI 設計流程" in spec and "### A1　想過的十種做法" in spec and "### A4　挑剔自己" in spec


# ============================================================ 2.046.1 依 GPT 審查（gpt_hk）修 4 條 P1
BANNED = ("可刪", "建議刪除", "可以刪", "可不可以刪", "是否可刪", "可清除", "建議清除", "應刪除")
HK_FILES = ("APP/asset-module/backend/dir_housekeeping.py", "APP/asset-module/backend/alert_notify.py",
            "APP/asset-module/frontend/pages/v2/risk.vue", "APP/asset-module/frontend/utils/hkCandidates.ts",
            "APP/asset-module/frontend/content/manual.ts", "docs/ui-v2/磁碟清理候選_規格_2026-11-04.md")


def test_守門_禁用處置導向的字():
    """「可刪除」「建議刪除」「是否可刪除」這類字不准出現（畫面、summary、通知、手冊、規格）；一律寫「容量占用候選，請資料擁有者判斷處理方式」。"""
    hits = [(f, w) for f in HK_FILES if (ROOT / f).exists() for w in BANNED if w in (ROOT / f).read_text(encoding="utf-8")]   # docs/ 不在中繼產出物裡
    assert not hits, hits
    # Windows 工具（使用者自己的 CL_DiskHK，原樣不改）報告最後那句是處置導向：送進畫面／summary 前一定換掉
    ps1 = (ROOT / "APP" / "asset-module" / "backend" / "hk_scripts" / "Get-DiskHousekeeping.ps1").read_text(encoding="utf-8-sig")
    lines = [ln for ln in ps1.splitlines() if any(w in ln for w in BANNED)]
    assert lines, "前提：工具裡有那句"
    for ln in lines:
        sent = ln.split("'")[1] if "'" in ln else ln
        out = hk.neutral_text("報告開頭\n" + sent + "\n")
        assert not any(w in out for w in BANNED) and "請資料擁有者判斷處理方式" in out, out


def test_Windows報告_送進畫面前換成中性說法(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    import win_onboard
    monkeypatch.setattr(win_onboard, "collect_block", lambda conn, ip, serial: None)
    monkeypatch.setattr(hk, "_platform", lambda conn, host: "windows")
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        r = hk.scan_host(c, "L1", win_runner=lambda ip, s: "■ D:\\ 92%\n以上項目請 AP 確認是否可刪除、壓縮或移出，處理後請回覆，謝謝。")
        assert r["status"] == "ok" and not any(w in r["summary"] for w in BANNED) and "請資料擁有者判斷處理方式" in r["summary"]
        c.close()


def test_告警摘要_先用事件的資產_IP唯一才退回(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: LINUX_OUT)
        db.insert_hardware(c, asset_serial="L9", hostname="lx9", ip="10.99.8.1", os="Red Hat Enterprise Linux 8", asset_status="使用中")
        c.commit()
        # IP 重複（L1、L9 都是 10.99.8.1）：事件對應到 L9 → 用 L9（沒掃過），不可以拿 L1 的目錄
        assert hk.brief_for_event(c, "L9", "10.99.8.1") == "目錄清理：尚未掃描"
        assert hk.brief_for_event(c, "L1", "10.99.8.1").startswith("容量占用候選")
        assert "對不到唯一一台" in hk.brief_for_event(c, None, "10.99.8.1"), "沒對應資產而且 IP 不唯一：不猜"
        assert hk.brief_for_event(c, None, "10.99.8.2") == "目錄清理：尚未掃描", "IP 唯一（L2）才退回用 IP"
        assert "沒有對應的主機" in hk.brief_for_event(c, None, None)
        c.close()


def test_告警通知_事件對應到資產就用資產(monkeypatch):
    import alert_contact
    import alert_notify
    import wug_alert as wa
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: LINUX_OUT)
        db.insert_hardware(c, asset_serial="L9", hostname="lx9", ip="10.99.8.1", os="Red Hat Enterprise Linux 8", asset_status="使用中")
        c.execute("CREATE TABLE IF NOT EXISTS alert_event (id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO alert_event (id) VALUES (1)")
        c.commit()
        row = {"id": 1, "ip": "10.99.8.1", "asset_serial": "L9", "match": "matched", "monitor": "Disk", "status_text": "92%", "open": True}
        monkeypatch.setattr(wa, "_has", lambda conn, t: True)
        monkeypatch.setattr(wa, "_row", lambda r: dict(row))
        monkeypatch.setattr(alert_contact, "attach", lambda conn, rs, key="api_id": rs)
        assert alert_notify._event(c, 1)["hk_brief"] == "目錄清理：尚未掃描", "對到 L9 就用 L9，不拿同 IP 的 L1"
        c.close()


def _many_detail(n_fs=3):
    fs, dirs = [], []
    for i in range(n_fs):
        m = f"/very/long/mount/point/number/{i}"
        fs.append({"mount": m, "pct": 95})
        for j in range(3):
            dirs.append({"mount": m, "path": f"{m}/application/archive/logs/segment_{j}", "kb": 9_000_000 - j, "old_kb": 0})
    return {"fs": fs, "dirs": dirs, "files": [], "stats": {fs[0]["mount"]: {"denied": 4, "partial": 1}}}


def test_告警摘要_限長_超過寫另N項_註記一定留(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        c.execute(hk.SCAN_SQL)
        c.execute("INSERT INTO hk_scan (asset_serial, ip, status, queued_at, finished_at, detail) VALUES ('L1','10.99.8.1','ok','2026-10-28 22:00:00','2026-10-28 22:10:00',?)",
                  (json.dumps(_many_detail()),))
        c.commit()
        b = hk.brief(c, serial="L1")
        assert len(b) <= hk.BRIEF_MAX, len(b)
        assert "另 " in b and "項見 1.3 目錄清理" in b, b
        assert "權限不足" in b and "逾時" in b, "數字是下限的註記不能被擠掉"
        c.close()


def test_告警hash綁定實際送出的卡片_長行不會被偷偷截掉():
    import alert_notify as an
    import teams_notify as tn
    import wug_alert as wa
    long = "請處理：" + "／very/long/path/" * 60
    t = an._fit(long, an.LINE_MAX)
    assert len(tn.md_escape(t)) <= an.LINE_MAX and t.endswith("…"), "修到跳脫後也放得下，被修掉的寫「…」"
    card = wa.build_card("標題", [t])
    assert card["attachments"][0]["content"]["body"][1]["text"] == tn.md_escape(t), "build_card 不會再截：預覽＝實際送出"
    ev = {"id": 1, "monitor": "Disk", "status_text": "92%", "open": True, "who": "lx1", "ip": "10.99.8.1", "wug_state": "Down",
          "ended_at": None, "duration_min": 5, "api_id": "AP1", "c_system_name": "網銀", "c_sys_class": "第一類", "c_ap_team": "A",
          "c_unit": "U", "c_email": "a@example.com", "started_at": "2026-10-28 21:00", "tier_label": "紅", "market": "", "device_name": "d",
          "hk_brief": "容量占用候選（短）"}
    h1 = an._hash(ev, "頻道", "teams", 1, "admin")
    h2 = an._hash({**ev, "hk_brief": "容量占用候選（短）" + "x" * 2000}, "頻道", "teams", 1, "admin")
    assert h1 != h2
    title, lines = an._message({**ev, "hk_brief": "x" * 2000}, 1, "admin")
    assert all(len(tn.md_escape(x)) <= an.LINE_MAX for x in lines) and len(tn.md_escape(title)) <= an.TITLE_MAX


def test_夜間自動_目標機沒有timeout就跳過_手動照舊(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        c = _night_db(tmp, monkeypatch)
        hk.set_night(c, True, "admin")
        sent = []
        no_to = lambda h, cmd: sent.append(cmd) or ("" if cmd.startswith("command -v timeout") else LINUX_OUT)   # noqa: E731
        r = hk.night_tick(c, runner=no_to, sync=True)
        res = hk.result(c, r["ids"][0])
        assert res["status"] == "notimeout" and "只能手動" in res["status_label"], res
        assert not any("-printf" in x for x in sent), "沒有遠端逾時保護：不送掃描腳本"
        assert hk.night_tick(c, runner=no_to, sync=True) == {"skip": "none"}, "這晚算掃過了，不重敲"
        sent.clear()
        m = hk.queue_scan(c, ["L1"], "admin", sync=True, runner=no_to)
        assert hk.result(c, m["ids"][0])["status"] == "ok" and any("-printf" in x for x in sent), "手動掃描照原規則"
        c.close()


# ============================================================ 2.046.2 依 GPT 複核（gpt_hk2）
def test_看門狗_只結束這次掃描自己的子程序_同一台其他連線不受影響():
    import subprocess
    import time
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])     # 同一台的另一條連線（別的收集）
    try:
        t0 = time.time()
        out, killed = hk.run_with_deadline([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
        assert killed and out == "" and time.time() - t0 < 10, "到時間就結束自己的子程序"
        assert other.poll() is None, "其他連線不受影響（沒被殺、沒被關）"
        out, killed = hk.run_with_deadline([sys.executable, "-c", "print('@@DONE')"], 20)
        assert not killed and "@@DONE" in out, "沒到時間不動"
    finally:
        other.kill()
        other.wait()


def test_看門狗時間早於08_30_跟逾時對齊():
    assert hk.scan_deadline(3600 * 6, 2400) == 2460, "腳本預算＋60 秒收尾"
    for runway in (900, 1200, 3000, 6000):
        assert hk.scan_deadline(runway, 2400) <= runway - hk.WATCHDOG_MARGIN, "一定在 08:30 前 2 分鐘以前收掉"


def test_正式runner掃描走獨立連線_到時間只kill自己(monkeypatch):
    import subprocess
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)

    class FakeSSH:                         # 長得像 manage_state.SSHRunner：短指令照舊走它（共用連線）
        key_path, account, timeout = "/k/collector", "webit3scan", 10

        def __call__(self, ip, cmd):
            return "hk_ok" if cmd == "echo hk_ok" else ""

    class Proc:
        made = []

        def __init__(self, argv, **kw):
            self.argv, self.killed, self.calls = argv, False, 0
            Proc.made.append(self)

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            return "", ""

        def kill(self):
            self.killed = True
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        r = hk.scan_host(c, "L1", runner=FakeSSH(), popen=Proc)
        assert r["status"] == "stopped" and "看門狗" in r["note"], r
        p0 = Proc.made[0]
        assert p0.killed, "到時間 kill 自己啟動的 ssh"
        assert "ControlMaster=no" in p0.argv and "ControlPath=none" in p0.argv and "-O" not in p0.argv, "獨立連線，不碰共用 master"
        assert p0.argv[-2] == "webit3scan@10.99.8.1" and "-printf" in p0.argv[-1]
        c.close()
    src = (ROOT / "APP" / "asset-module" / "backend" / "dir_housekeeping.py").read_text(encoding="utf-8")
    assert '"-O", "exit"' not in src, "不可以用 ssh -O exit 關共用 master（會連同一台的其他連線一起關）"


def test_告警摘要_首條路徑超長_另N項與警告一定在(monkeypatch):
    long_path = "/data/" + "very_long_directory_name_" * 30 + "logs"
    d = {"fs": [{"mount": "/data", "pct": 97}], "files": [],
         "stats": {"/data": {"denied": 9, "partial": 1}},
         "dirs": [{"mount": "/data", "path": long_path, "kb": 9_000_000, "old_kb": 0},
                  {"mount": "/data", "path": "/data/b", "kb": 3_000_000, "old_kb": 0},
                  {"mount": "/data", "path": "/data/c", "kb": 2_000_000, "old_kb": 0}]}
    with tempfile.TemporaryDirectory() as tmp:
        from test_dir_housekeeping import _conn
        c, _ = _conn(tmp)
        c.execute(hk.SCAN_SQL)
        c.execute("INSERT INTO hk_scan (asset_serial, ip, status, queued_at, finished_at, detail) VALUES ('L1','10.99.8.1','ok','2026-10-28 22:00:00','2026-10-28 22:10:00',?)",
                  (json.dumps(d),))
        c.commit()
        b = hk.brief(c, serial="L1")
        assert len(b) <= hk.BRIEF_MAX, len(b)
        assert "另 2 項見 1.3 目錄清理" in b and "權限不足" in b and "逾時" in b, b
        assert "…" in b and b.count("/data/very_long_directory_name") == 1 and "logs" in b, "路徑截中段、頭尾都留"
        c.close()
    assert hk._mid("abcdefghij", 5) == "ab…ij" and hk._mid("abc", 5) == "abc"
