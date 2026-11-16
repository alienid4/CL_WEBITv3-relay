"""Windows 排程的重點欄位（2026-10-21 使用者給工作排程器內容視窗截圖：「這些都是 WINDOWS 排程的重點」）。

一般（名稱／位置／作者／描述）、觸發程序（類型／白話詳細／狀態，可多個）、動作（程式／引數／起始位置，
未加引號／使用者可寫目錄要標）、安全性選項（執行帳戶／登入方式／最高權限／隱藏／設定相容性）、
執行狀態（狀態／上次執行／上次結果／下次執行）。

Get-ScheduledTask（CIM）跟 COM 退路（工作定義 XML）兩條路，同一個工作要得出**一樣**的內容。
全部是假資料；真的 Windows 主機沒測過。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import schedule_inventory as si  # noqa: E402

ADOBE_ID = "\\Adobe Acrobat Update Task"
ADOBE_EXE = "C:\\Program Files (x86)\\Common Files\\Adobe\\ARM\\1.0\\AdobeARM.exe"
ADOBE_DESC = "This task keeps your Adobe Reader and Acrobat applications up to date with the latest enhancements and security fixes"

#: Get-ScheduledTask 路徑的輸出（SCHEDULE_PS 的 TASK／ACT／TRG 行）
ADOBE_CIM = "\r\n".join([
    "SRC=schtasks",
    "\t".join(["TASK", ADOBE_ID, "\\", "Adobe Acrobat Update Task", "Ready", "", "Group", "Limited",
               "Adobe Systems Incorporated", "2026-10-21 09:00:02", "0", "2026-10-21 23:00:00", ADOBE_DESC,
               "False", "True", "Vista", "INTERACTIVE", "cim"]),
    "\t".join(["ACT", ADOBE_ID, ADOBE_EXE, "", "", ""]),
    "\t".join(["TRG", ADOBE_ID, "MSFT_TaskLogonTrigger", "True", "", "", "", "", "PT3H30M", "", "", "",
               "", "False", "", "", ""]),
    "\t".join(["TRG", ADOBE_ID, "MSFT_TaskDailyTrigger", "True", "2018-05-02T23:00:00", "1", "", "", "", "",
               "", "", "2032-05-02T12:05:00", "", "", "", ""]),
])

#: COM 退路：工作定義 XML（照 Adobe 實際匯出的樣子，攤成一行前的原文）
ADOBE_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>Adobe Systems Incorporated</Author>
    <Description>%s</Description>
    <URI>\\Adobe Acrobat Update Task</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Repetition>
        <Interval>PT3H30M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>2018-05-02T23:00:00</StartBoundary>
      <EndBoundary>2032-05-02T12:05:00</EndBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <GroupId>S-1-5-4</GroupId>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT72H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>%s</Command>
    </Exec>
  </Actions>
</Task>""" % (ADOBE_DESC, ADOBE_EXE)


def _flat(xml: str) -> str:
    """PS 端做的事：把 XML 的 tab／換行攤成空白，變一行。"""
    import re
    return re.sub(r"[\t\r\n]+", " ", xml)


ADOBE_COM = "\r\n".join([
    "SRC=schtasks",
    "ERR\ttasks\tDENIED\tAccess is denied. 0x80070005",
    "\t".join(["TASK", ADOBE_ID, "\\", "Adobe Acrobat Update Task", "Ready", "", "", "", "",
               "2026-10-21 09:00:02", "0", "2026-10-21 23:00:00", "", "", "", "", "", "com"]),
    "XML\t" + ADOBE_ID + "\t" + _flat(ADOBE_XML),
    "SRC2=com\t1",
])


def _view(text):
    p = si.parse_windows_schedule(text)
    assert len(p["tasks"]) == 1
    return si.task_view(p["tasks"][0]), p


def test_adobe_CIM路徑_重點欄位都有():
    v, _ = _view(ADOBE_CIM)
    g = v["general"]
    assert g == {"name": "Adobe Acrobat Update Task", "path": "\\",
                 "author": "Adobe Systems Incorporated", "description": ADOBE_DESC}
    assert [(t["type_label"], t["text"], t["enabled_label"]) for t in v["triggers"]] == [
        ("登入時", "任何使用者登入時，之後每 3 小時 30 分重複", "已啟用"),
        ("每天", "每天 23:00，到期 2032/5/2 12:05", "已啟用"),
    ]
    assert len(v["actions"]) == 1
    a = v["actions"][0]
    assert a["kind_label"] == "啟動程式" and a["exe"] == ADOBE_EXE and a["args"] == "" and a["workdir"] == ""
    assert a["unquoted"] is True and a["user_writable"] is False          # Program Files 有空白、沒加引號
    s = v["security"]
    assert s["account"] == "INTERACTIVE"
    assert s["logon_label"] == "只有使用者登入時才執行"
    assert s["highest"] is False and s["hidden"] is False
    assert s["compat_label"] == "Windows Vista、Windows Server 2008"
    assert v["last_result_code"] == "0x0" and v["last_result_text"] == "成功"
    assert v["unknown_reason"] is None and v["info_ok"] is True


def test_adobe_COM退路解析XML_跟CIM得出一樣的內容():
    cim, _ = _view(ADOBE_CIM)
    com, p = _view(ADOBE_COM)
    assert com == cim
    # 權限不足（CIM 被拒）那一條照樣講得出來
    notes = [si.explain_win_error(e) for e in p["errors"]]
    assert any(n.startswith("排程工作清單：權限不足") for n in notes)


def test_主表列_摘要欄與旗標():
    for text in (ADOBE_CIM, ADOBE_COM):
        rows = si.finalize(si.windows_rows(si.parse_windows_schedule(text)))
        r = rows[0]
        assert r["no"] == 1 and r["name"] == "Adobe Acrobat Update Task" and r["source"] == "\\"
        assert r["state"] == "就緒" and r["account"] == "INTERACTIVE" and r["highest"] is False
        assert r["trigger_summary"] == "任何使用者登入時，之後每 3 小時 30 分重複；每天 23:00，到期 2032/5/2 12:05"
        assert r["action_summary"] == ADOBE_EXE
        assert r["next_run"] == "2026-10-21 23:00:00"
        assert r["flags"] == ["未加引號"] and r["builtin"] is False
        assert r["kind"] == "daily" and r["hours"] == [23]        # 排序／時段分布照舊


def test_XML_各種觸發與動作():
    xml = """<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Author>CORP\\ops</Author></RegistrationInfo>
  <Triggers>
    <BootTrigger><Delay>PT10M</Delay><Enabled>true</Enabled></BootTrigger>
    <CalendarTrigger><StartBoundary>2026-01-05T03:00:00</StartBoundary>
      <ScheduleByWeek><WeeksInterval>2</WeeksInterval><DaysOfWeek><Monday/><Wednesday/></DaysOfWeek></ScheduleByWeek>
    </CalendarTrigger>
    <CalendarTrigger><StartBoundary>2026-01-01T01:30:00</StartBoundary>
      <ScheduleByMonth><DaysOfMonth><Day>1</Day><Day>15</Day></DaysOfMonth>
        <Months><January/><April/><July/><October/></Months></ScheduleByMonth>
    </CalendarTrigger>
    <CalendarTrigger><StartBoundary>2026-01-01T04:00:00</StartBoundary>
      <ScheduleByMonthDayOfWeek><Weeks><Week>1</Week></Weeks><DaysOfWeek><Sunday/></DaysOfWeek>
        <Months><January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/></Months>
      </ScheduleByMonthDayOfWeek>
    </CalendarTrigger>
    <TimeTrigger><StartBoundary>2026-11-01T02:00:00</StartBoundary><Enabled>false</Enabled>
      <RandomDelay>PT1H</RandomDelay></TimeTrigger>
    <EventTrigger><Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='User32'] and EventID=1074]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription></EventTrigger>
    <SessionStateChangeTrigger><StateChange>SessionUnlock</StateChange><UserId>CORP\\alice</UserId></SessionStateChangeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><Hidden>true</Hidden><Compatibility>V2_1</Compatibility></Settings>
  <Actions Context="Author">
    <Exec><Command>"C:\\Program Files\\Acme\\run.exe"</Command><Arguments>-q</Arguments><WorkingDirectory>C:\\Acme</WorkingDirectory></Exec>
    <Exec><Command>C:\\Users\\Public\\x.bat</Command></Exec>
    <ComHandler><ClassId>{A6BA00FE-40E8-477C-B713-C64A14F18ADB}</ClassId></ComHandler>
  </Actions>
</Task>"""
    d = si.parse_task_xml(_flat(xml))
    t = {"name": "Acme", "path": "\\Acme\\", "state": "Ready", "src": "com", "def_error": None,
         "last_result": 0x80070002, "info_ok": True, **d}
    v = si.task_view(t)
    texts = [x["text"] for x in v["triggers"]]
    assert texts == [
        "開機時，延遲 10 分",
        "每 2 週的週一、三 03:00",
        "每月 1、15 日（1、4、7、10 月） 01:30",
        "每月第 1 個週日 04:00",
        "一次：2026/11/1 02:00，隨機延遲最多 1 小時",
        "發生事件時（System 記錄，來源 User32，事件 1074）",
        "CORP\\alice 解除鎖定工作站時",
    ]
    assert v["triggers"][4]["enabled_label"] == "已停用"
    acts = v["actions"]
    assert [a["kind_label"] for a in acts] == ["啟動程式", "啟動程式", "COM 處理常式"]
    assert acts[0]["unquoted"] is False and acts[0]["args"] == "-q" and acts[0]["workdir"] == "C:\\Acme"
    assert acts[1]["user_writable"] is True
    s = v["security"]
    assert s["account"] == "SYSTEM" and s["logon"] == "service" and s["highest"] is True and s["hidden"] is True
    assert s["compat_label"] == "Windows 7、Windows Server 2008 R2"
    assert v["last_result_text"] == "找不到檔案" and v["last_result_code"] == "0x80070002"


def test_XML_拒絕_DOCTYPE_與壞掉的定義標不知道():
    with pytest.raises(ValueError):
        si.parse_task_xml('<!DOCTYPE x [<!ENTITY a "b">]><Task/>')
    text = "\n".join([
        "SRC=schtasks",
        "\t".join(["TASK", "\\Bad", "\\", "Bad", "Ready", "", "", "", "", "", "0", "", "", "", "", "", "", "com"]),
        "XML\t\\Bad\t<Task><Broken",
        "\t".join(["TASK", "\\NoDef", "\\", "NoDef", "Ready", "", "", "", "", "", "0", "", "", "", "", "", "", "com"]),
        "XERR\t\\NoDef\tAccess is denied",
    ])
    p = si.parse_windows_schedule(text)
    bad, nodef = (si.task_view(t) for t in p["tasks"])
    assert "解析失敗" in bad["unknown_reason"]
    assert nodef["unknown_reason"].startswith("權限不足")
    for v in (bad, nodef):        # 不知道就是 None，不可以變成「否」
        assert v["security"]["highest"] is None and v["security"]["hidden"] is None
        assert v["security"]["logon"] is None and v["security"]["compat"] is None
    assert any(e["item"] == "taskxml" for e in p["errors"])
    rows = si.windows_rows(p)
    assert rows[0]["sched"]["kind"] == "unknown" and "不知道" in rows[0]["sched"]["text"]


def test_舊版收集的資料_新欄位是不知道():
    old = "\n".join([
        "SRC=schtasks",
        "TASK\t\\Poll\t\\\tPoll\tReady\tSYSTEM\tServiceAccount\tHighest\t\t\t0\t\t",
        "ACT\t\\Poll\tC:\\tools\\poll.exe\t\t\t",
    ])
    v = si.task_view(si.parse_windows_schedule(old)["tasks"][0])
    assert v["security"]["hidden"] is None and v["security"]["compat"] is None
    assert v["security"]["highest"] is True and v["security"]["logon"] == "service"
    assert "舊版收集" in v["unknown_reason"]


def test_執行資訊被拒_下次執行是不知道不是沒有():
    text = "\n".join([
        "SRC=schtasks",
        "\t".join(["TASK", "\\X", "\\", "X", "Ready", "SYSTEM", "ServiceAccount", "Highest", "", "", "", "", "",
                   "False", "True", "Win8", "", "cim"]),
        "ERR\ttaskinfo\tDENIED\t1 tasks: Access is denied",
    ])
    rows = si.windows_rows(si.parse_windows_schedule(text))
    assert rows[0]["info_ok"] is False and rows[0]["next_run"] is None


def test_收集腳本_兩條路都帶新欄位且唯讀():
    ps = si.SCHEDULE_PS
    for k in ("$s.Hidden", "$s.Compatibility", "$g.EndBoundary", "$g.RandomDelay",
              "Principal.GroupId", "$t.Xml", "Schedule.Service"):
        assert k in ps, k
    assert "Export-" not in ps and "Bypass" not in ps


def test_detail_舊資料要提示重收(monkeypatch):
    import json
    import manage_state
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE hardware (asset_serial TEXT, ip TEXT, os TEXT)")
    conn.execute("INSERT INTO hardware VALUES ('W1','10.99.0.2','Windows Server 2019')")
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "windows")
    monkeypatch.setattr(manage_state, "expand_to_machines", lambda c, s: s)
    si._ensure(conn)
    old = si.parse_windows_schedule("SRC=schtasks\nTASK\t\\P\t\\\tP\tReady\tSYSTEM\tServiceAccount\tHighest\t\t\t0\t\t")
    conn.execute("INSERT INTO host_schedule VALUES ('W1','10.99.0.2','windows',?,'2026-10-21 10:00:00')",
                 (json.dumps({"tasks": old["tasks"], "notes": []}),))
    d = si.detail_for(conn, "W1")
    assert any("舊版收集" in n for n in d["notes"])
    new = si.parse_windows_schedule(ADOBE_CIM)
    conn.execute("UPDATE host_schedule SET data_json=?", (json.dumps({"tasks": new["tasks"], "notes": []}),))
    d = si.detail_for(conn, "W1")
    assert not any("舊版收集" in n for n in d["notes"])
    assert d["rows"][0]["task"]["security"]["compat"] == "vista"
