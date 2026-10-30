"""排程盤點（schedule_inventory）：cron／systemd／Windows 觸發的白話與排序。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import schedule_inventory as si  # noqa: E402


def _t(spec):
    return si.describe_cron(spec)


def test_cron_白話():
    assert _t("* * * * *")["text"] == "每分鐘"
    assert _t("*/5 * * * *")["text"] == "每 5 分"
    assert _t("*/5 * * * *")["interval_min"] == 5
    assert _t("15 * * * *")["text"] == "每小時（第 15 分）"
    assert _t("30 2 * * *")["text"] == "每天 02:30"
    assert _t("0 3 * * 1")["text"] == "每週一 03:00"
    assert _t("0 3 * * mon")["text"] == "每週一 03:00"
    assert _t("0 1 1 * *")["text"] == "每月1日 01:00"
    assert _t("0 0 1 1 *")["kind"] == "yearly"
    assert _t("0 */6 * * *")["interval_min"] == 360
    assert _t("*/10 8-17 * * 1-5")["text"] == "週一至週五 08–17 點 每 10 分"
    assert _t("0 2,14 * * *")["text"] == "每天 02:00、14:00"
    assert _t("@reboot")["kind"] == "boot"
    assert _t("@daily")["text"] == "每天 00:00"
    assert _t("0 3 * * 7")["text"] == "每週日 03:00"
    assert _t("bogus")["kind"] == "unknown"


def test_排序_週期短到長_再_00到23_最後事件():
    specs = ["@reboot", "30 23 * * *", "0 3 * * 1", "15 * * * *", "*/5 * * * *",
             "* * * * *", "0 0 * * *", "30 2 * * *", "0 */2 * * *"]
    rows = [{"source": "t", "account": "root", "raw": s, "command": s, "sched": _t(s)} for s in specs]
    out = si.finalize(rows)
    assert [r["raw"] for r in out] == ["* * * * *", "*/5 * * * *", "15 * * * *", "0 */2 * * *",
                                       "0 0 * * *", "30 2 * * *", "0 3 * * 1", "30 23 * * *",
                                       "@reboot"]
    assert [r["no"] for r in out] == list(range(1, 10))
    hist = si.hour_histogram(out)
    assert hist[2] == 1 and hist[23] == 1 and hist[0] == 1 and sum(hist) == 4


def test_crontab_解析_系統與使用者():
    sys_tab = """SHELL=/bin/bash
PATH=/sbin:/bin
MAILTO=root
# comment
01 * * * * root run-parts /etc/cron.hourly
25 6 * * * root test -x /usr/sbin/anacron || run-parts --report /etc/cron.daily
@reboot root /opt/app/start.sh
"""
    rows = si.parse_crontab(sys_tab, source="/etc/crontab", has_user=True)
    assert [r["account"] for r in rows] == ["root", "root", "root"]
    assert rows[1]["command"].startswith("test -x")
    usr = "# DO NOT EDIT\n*/5 * * * * /home/app/bin/poll.sh >/dev/null 2>&1\n"
    rows = si.parse_crontab(usr, source="u", has_user=False, user="app")
    assert rows[0]["account"] == "app" and rows[0]["sched"]["text"] == "每 5 分"


def test_systemd_calendar_與_list_timers():
    assert si.calendar_to_cron("*:00/10") == "*/10 * * * *"
    assert si.describe_cron(si.calendar_to_cron("Mon..Fri *-*-* 08:30:00"))["text"] == "週一至週五 08:30"
    assert si.describe_cron(si.calendar_to_cron("daily"))["text"] == "每天 00:00"
    assert si.describe_cron(si.calendar_to_cron("Sun *-*-* 03:10:00"))["text"] == "每週日 03:10"
    assert si.parse_timespan("1h 30min") == 90
    assert si.describe_timer_unit({"OnBootSec": ["15min"]})["text"] == "開機後 15 分"
    lt = ("Tue 2026-10-20 23:00:00 CST 52min left Tue 2026-10-20 22:00:01 CST 7min ago "
          "dnf-makecache.timer dnf-makecache.service\n"
          "n/a                         n/a          n/a                         n/a     "
          "backup.timer backup.service\n")
    t = si.parse_list_timers(lt)
    assert t[0] == {"unit": "dnf-makecache.timer", "activates": "dnf-makecache.service",
                    "next": "2026-10-20 23:00:00", "last": "2026-10-20 22:00:01"}
    assert t[1]["next"] is None and t[1]["last"] is None


def _rebuild_data():
    def item(iid, **kw):
        return {"id": iid, "title": iid, "state": "collected", **kw}
    return {"groups": [{"group": "排程", "items": [
        item("cron.system", files=[{"path": "/etc/cron.d/0hourly",
                                    "content": "01 * * * * root run-parts /etc/cron.hourly"}]),
        item("cron.user", files=[{"path": "/var/spool/cron/oracle",
                                  "content": "30 2 * * * /u01/backup.sh"}]),
        item("cron.periodic", files=[{"path": "/etc/cron.hourly/0anacron", "content": "#!/bin/sh"},
                                     {"path": "/etc/cron.daily/logrotate", "content": "#!/bin/sh"}]),
        item("cron.timers", text=(
            "Wed 2026-10-21 00:00:00 CST 1h left Tue 2026-10-20 00:00:00 CST 22h ago "
            "logrotate.timer logrotate.service\n"
            "Wed 2026-10-21 04:00:00 CST 5h left n/a n/a backup.timer backup.service\n")),
    ]}, {"group": "開機／服務", "items": [
        item("boot.systemd_custom", files=[{"path": "/etc/systemd/system/backup.timer",
                                            "content": "[Unit]\nDescription=x\n[Timer]\nOnCalendar=*-*-* 04:00:00\n"}]),
    ]}]}


def test_linux_由_OS_重建資料組出排程():
    rows, notes = si.linux_rows(_rebuild_data())
    out = si.finalize(rows)
    by = {r["command"]: r for r in out}
    assert by["/u01/backup.sh"]["account"] == "oracle"
    assert by["/u01/backup.sh"]["schedule"] == "每天 02:30"
    assert by["/etc/cron.hourly/0anacron"]["schedule"] == "每小時（第 01 分）"
    assert "anacron" in by["/etc/cron.daily/logrotate"]["schedule"]
    assert by["backup.service"]["schedule"] == "每天 04:00" and by["backup.service"]["builtin"] is False
    assert by["logrotate.service"]["builtin"] is True and "推定" in by["logrotate.service"]["schedule"]
    assert by["backup.service"]["next_run"] == "2026-10-21 04:00:00"
    kinds = [r["kind"] for r in out]
    assert kinds.index("interval") < kinds.index("daily")
    assert notes == []


WIN_SAMPLE = "\r\n".join([
    "SRC=schtasks",
    "TASK\t\\Backup\\Nightly\t\\Backup\\\tNightly\tReady\tSYSTEM\tServiceAccount\tHighest\tops"
    "\t2026-10-20 02:00:01\t0\t2026-10-21 02:00:00\tnightly backup",
    "ACT\t\\Backup\\Nightly\tC:\\tools\\bk.exe\t/all\t\t",
    "TRG\t\\Backup\\Nightly\tMSFT_TaskDailyTrigger\tTrue\t2026-01-01T02:00:00+08:00\t1\t\t\t\t\t\t",
    "TASK\t\\Poll\tPoll\tPoll\tReady\tsvc_app\tPassword\tLimited\t\t1999-11-30 00:00:00\t267011\t2026-10-20 22:05:00\t",
    "ACT\t\\Poll\tpowershell.exe\t-NoProfile -Command Get-Date\t\t",
    "TRG\t\\Poll\tMSFT_TaskTimeTrigger\tTrue\t2026-01-01T00:00:00\t\t\t\tPT5M\tP1D\t\t",
    "TASK\t\\Weekly\tWeekly\tWeekly\tDisabled\tSYSTEM\tServiceAccount\tHighest\t\t\t1\t\t",
    "TRG\t\\Weekly\tMSFT_TaskWeeklyTrigger\tTrue\t2026-01-05T03:00:00\t\t1\t2\t\t\t\t",
    "TASK\t\\Microsoft\\Windows\\Defrag\\ScheduledDefrag\t\\Microsoft\\Windows\\Defrag\\\tScheduledDefrag"
    "\tReady\tSYSTEM\tServiceAccount\tHighest\t\t\t0\t\t",
    "TRG\t\\Microsoft\\Windows\\Defrag\\ScheduledDefrag\tMSFT_TaskBootTrigger\tTrue\t\t\t\t\t\t\tPT10M\t",
    "ERR\ttaskinfo\tDENIED\t2 tasks: Access is denied",
])


def test_windows_觸發白話與排序():
    parsed = si.parse_windows_schedule(WIN_SAMPLE)
    assert len(parsed["tasks"]) == 4
    out = si.finalize(si.windows_rows(parsed))
    assert [r["name"] for r in out] == ["Poll", "Nightly", "Weekly", "ScheduledDefrag"]
    poll, nightly, weekly, defrag = out
    assert poll["schedule"] == "自 00:00 起每 5 分" and poll["last_run"] is None
    assert poll["last_result"] == "尚未執行過"
    assert nightly["schedule"] == "每天 02:00" and nightly["command"] == "C:\\tools\\bk.exe /all"
    assert nightly["account"] == "SYSTEM" and nightly["next_run"] == "2026-10-21 02:00:00"
    assert weekly["schedule"] == "每週一 03:00" and weekly["state"] == "停用"
    assert weekly["last_result"].startswith("失敗")
    assert defrag["builtin"] is True and defrag["schedule"] == "開機時（延遲 10 分）"
    assert [si.explain_win_error(e) for e in parsed["errors"]][0].startswith("排程的上次／下次執行時間：權限不足")


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE hardware (asset_serial TEXT, ip TEXT, os TEXT)")
    return conn


def test_detail_linux_沒收過重建要講先收_OS_重建(monkeypatch):
    import manage_state
    conn = _db()
    conn.execute("INSERT INTO hardware VALUES ('S1','10.99.0.1','Red Hat Enterprise Linux 9')")
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
    monkeypatch.setattr(manage_state, "expand_to_machines", lambda c, s: s)
    conn.execute("CREATE TABLE host_rebuild (asset_serial TEXT PRIMARY KEY, ip TEXT, platform TEXT,"
                 " data_json TEXT, raw_json TEXT, collected_at TEXT)")
    d = si.detail_for(conn, "S1")
    assert d["rows"] == [] and "先收 OS 重建" in d["empty_reason"]
    import json
    conn.execute("INSERT INTO host_rebuild VALUES ('S1','10.99.0.1','linux',?,'{}','2026-10-20 10:00:00')",
                 (json.dumps(_rebuild_data()),))
    d = si.detail_for(conn, "S1")
    assert d["source"] == "OS 重建資料" and len(d["rows"]) == 6 and d["rows"][0]["no"] == 1


def test_detail_windows_收集後讀得到(monkeypatch):
    import manage_state
    import onboard_eligibility
    conn = _db()
    conn.execute("ALTER TABLE hardware ADD COLUMN collect_ok INTEGER")
    for c in ("is_vm", "device_model", "hostname", "asset_name", "asset_purpose"):
        conn.execute(f"ALTER TABLE hardware ADD COLUMN {c} TEXT")
    conn.execute("INSERT INTO hardware (asset_serial, ip, os, collect_ok) "
                 "VALUES ('W1','10.99.0.2','Windows Server 2019',1)")
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    monkeypatch.setattr(manage_state, "expand_to_machines", lambda c, s: s)
    monkeypatch.setattr(onboard_eligibility, "judge_for_asset", lambda *a, **k: None)
    assert "還沒收過排程" in si.detail_for(conn, "W1")["empty_reason"]
    r = si.collect_windows(conn, only_serial="W1", runner=lambda ip, s: WIN_SAMPLE)
    assert r["ok"] == 1 and r["failed"] == 0
    d = si.detail_for(conn, "W1")
    assert d["platform"] == "windows" and len(d["rows"]) == 4
    assert any("權限不足" in n for n in d["notes"])
    # 空清單＝收集失敗，不是 0 個排程
    r = si.collect_windows(conn, only_serial="W1", runner=lambda ip, s: "SRC=schtasks\n")
    assert r["ok"] == 0 and r["failed"] == 1
