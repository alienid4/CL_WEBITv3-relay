"""2026-10-22 使用者：「原來是 LINUX 可手動調整到我指定群，每一個都要可以調」＋
「改分類在最右邊，我要縮小才看到」。

1. 納管失敗清單每一列都有「改分類」，改了之後作業系統大類照人工指定顯示（同一張人工指定表）
2. 待納管大表自己限高捲動，左右捲軸永遠在畫面內；「操作」欄釘在右邊

IP 一律範例位址（192.0.2.x），主機名是假的。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import onboard_failures as of  # noqa: E402
import os_override  # noqa: E402

ADOPT_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _fail(conn, ip):
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, ok, "
        "stage, message, created_at) VALUES (?, 'linux', 'root', 'manual', 'tester', 0, 'connect', "
        "'登入被拒——帳號或密碼不對', '2026-10-20 10:00:00')", (ip,))
    conn.commit()


def test_人工改分類後_納管失敗清單的大類照人工指定():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        db.insert_hardware(conn, asset_serial="F-1", ip="192.0.2.61", os="Red Hat Enterprise Linux 8",
                           hostname="fail-61", environment="測試", asset_status="使用中")
        conn.commit()
        _fail(conn, "192.0.2.61")
        before = of.current_failures(conn)[0]
        assert before["os_class"] == "Linux" and before["os_override"] is None
        os_override.set_override(conn, "fail-61", "192.0.2.61", "F-1", "AIX", None, "其實是 AIX", "tester")
        after = of.current_failures(conn)[0]
        assert after["os_class"] == "AIX", after
        assert after["os_override"]["tab"] == "mainframe" and after["os_override"]["by"] == "tester"
        conn.close()


def test_前端_納管失敗每一列都有改分類_走同一個視窗():
    body = ADOPT_VUE.split("<div v-show=\"activeTab === 'failures'\">")[1].split("<div v-show=\"activeTab === 'onboarded'\">")[0]
    assert "@click=\"openMove([failMoveRow(f)])\">改分類</button>" in body
    assert "<th>操作</th>" in body
    assert "loadFailures()                 // 納管失敗清單" in ADOPT_VUE, "改完要重讀失敗清單"


def test_前端_待納管大表有自己的左右捲軸_操作欄釘右邊():
    body = ADOPT_VUE.split("<div v-show=\"activeTab === 'pending'\">")[1].split("<div v-show=\"activeTab === 'unregistered'\">")[0]
    assert 'class="tbl-wrap d-scroll"' in body
    assert ".d-scroll { max-height:" in ADOPT_VUE
    assert ".d-scroll th.ops-h, .d-scroll td.ops { position: sticky; right: 0;" in ADOPT_VUE
    # 上次失敗放最後（2026-10-22 使用者：「納管跟改分類才會比較好看到」）
    head = body.split("<thead>")[1].split("</thead>")[0]
    assert head.index('class="ops-h"') < head.index("上次失敗</SortTh>")


def test_同一台另一個IP已納管_舊失敗不列():
    """2026-10-22：SECSVR194-053T CIA 登記 .47、實際在 .53；.53 納管成功，.47 那筆舊失敗不是待辦。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for serial, ip, ok in (("H-47", "192.0.2.47", None), ("H-53", "192.0.2.53", 1)):
            db.insert_hardware(conn, asset_serial=serial, ip=ip, os="Red Hat Enterprise Linux 8",
                               hostname="SVR194-053T", environment="測試", asset_status="使用中")
            if ok:
                conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?", (serial,))
        conn.commit()
        _fail(conn, "192.0.2.47")
        assert [f["ip"] for f in of.current_failures(conn)] == []
        conn.close()


def test_通用主機名稱不算同一台_真失敗不可以被藏():
    """獨立審查 2026-10-22：localhost／template 很多台共用，不能拿來判「另一個 IP 已納管」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for serial, ip, ok in (("G-1", "192.0.2.81", None), ("G-2", "192.0.2.82", 1)):
            db.insert_hardware(conn, asset_serial=serial, ip=ip, os="Red Hat Enterprise Linux 8",
                               hostname="localhost", environment="測試", asset_status="使用中")
            if ok:
                conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?", (serial,))
        conn.commit()
        _fail(conn, "192.0.2.81")
        assert [f["ip"] for f in of.current_failures(conn)] == ["192.0.2.81"]
        conn.close()
