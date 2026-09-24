"""收集帳號統一成 `webit3sc` 的遷移：探測與狀態。

守的是兩件事，都是我們這幾天反覆修的同一個病：

1. **狀態要由實測決定，不是由設定決定。**
   2026-09-23 的坑就是「設定說 A、機器上是 B」——
   一台好好的 221 因此顯示成「連不上・未完整檢查 0/9」。
2. **兩個帳號都連不上 ≠ 遷移失敗。**
   第二種解釋是機器根本不通。不分層的話，一批關機的機器會被算成遷移失敗，
   完成率被拉低而且沒人知道該修哪個。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_account_migration as cam  # noqa: E402
import db  # noqa: E402
import manage_state as ms  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def _add(c, serial, ip, host=None):
    db.insert_hardware(c, asset_serial=serial, hostname=host or serial,
                       ip=ip, os="AIX 7.2")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?", (serial,))
    c.commit()


def _probe(new_ok, old_ok):
    def f(ip, account, key):
        if account == ms.READONLY_ACCOUNT:
            return (True, None) if new_ok else (False, "Permission denied (publickey).")
        return (True, None) if old_ok else (False, "Permission denied (publickey).")
    return f


# ═══ 每台一個帳號：這是整份設計的支點 ═══════════════════════════════

def test_沒設就沿用全域_行為跟以前一模一樣(conn):
    """這一步**不可以改變任何一台機器的行為**。全是 NULL 就等於現狀。"""
    _add(conn, "A-1", "10.0.0.1")
    assert cam.account_for_host(conn, "A-1", "linux") == \
        ms.get_collect_account(conn, "linux")


def test_設了就只有那一台改變(conn):
    """全域一次翻＝2026-09-23 那個「好好的機器變成 0/9」的機隊放大版。

    有了這一欄：驗過的那一台才寫新帳號，其餘完全不受影響。
    """
    _add(conn, "A-1", "10.0.0.1")
    _add(conn, "A-2", "10.0.0.2")
    conn.execute("UPDATE hardware SET collect_account = ? WHERE asset_serial = ?",
                 ("webit3sc", "A-1"))
    conn.commit()
    assert cam.account_for_host(conn, "A-1", "linux") == "webit3sc"
    assert cam.account_for_host(conn, "A-2", "linux") == \
        ms.get_collect_account(conn, "linux")


def test_空字串當成沒設(conn):
    _add(conn, "A-1", "10.0.0.1")
    conn.execute("UPDATE hardware SET collect_account = '  ' WHERE asset_serial = ?",
                 ("A-1",))
    conn.commit()
    assert cam.account_for_host(conn, "A-1", "linux") == \
        ms.get_collect_account(conn, "linux")


# ═══ 狀態由實測決定 ═════════════════════════════════════════════════

@pytest.mark.parametrize("new_ok,old_ok,alive,want", [
    (True,  False, True,  cam.DONE),
    (True,  True,  True,  cam.SWITCHABLE),
    (False, True,  True,  cam.PENDING),
    (False, False, True,  cam.FAILED),
    (False, False, False, cam.UNKNOWN),
])
def test_四種狀態分得開(new_ok, old_ok, alive, want):
    assert cam.classify(new_ok, old_ok, alive, "linux") == want


def test_windows不列入遷移():
    """Windows 走 WinRM，沒有 SSH 收集帳號這回事。

    把它算進分母，完成率永遠到不了 100%，而那不是任何人做錯什麼。
    """
    assert cam.classify(False, False, True, "windows") == cam.NA


def test_機器不通不可以算成遷移失敗(conn):
    """⚠️ 這條是這組最重要的。

    兩個帳號都連不上，**第二種解釋是機器根本不通**。
    混在一起的話：一批關機的機器會被算成遷移失敗，完成率被拉低，
    而且沒人知道該去修哪個——那又是一次「把別的問題顯示成這個問題」。
    """
    _add(conn, "A-9", "10.0.0.9")
    r = cam.probe_one(conn, "A-9", "10.0.0.9", "linux",
                      _probe=_probe(False, False))
    # host_ping 在測試環境探不到 -> _host_alive 回 False -> 機器不通
    assert r["state"] == cam.UNKNOWN
    d = cam.detail(conn, cam.UNKNOWN)[0]
    assert "機器本身不通" in d["why"]
    assert "不是遷移失敗" in d["why"]


def test_探測是唯讀的_只跑id():
    """遷移探測絕對不可以改到目標主機。"""
    assert cam.PROBE_CMD == "id"
    for bad in ("useradd", "mkuser", "rm", "chmod", "chuser", ">"):
        assert bad not in cam.PROBE_CMD


# ═══ 每台的證據要留著 ═══════════════════════════════════════════════

def test_每台都要記下兩個帳號各自的結果與時間(conn):
    _add(conn, "A-3", "10.0.0.3")
    cam.probe_one(conn, "A-3", "10.0.0.3", "linux", _probe=_probe(False, True))
    d = cam.detail(conn)[0]
    assert d["new_account"] == ms.READONLY_ACCOUNT
    assert d["old_account"] == ms.LEGACY_LINUX_ACCOUNT
    assert d["new_ok"] == 0 and d["old_ok"] == 1
    assert "Permission denied" in d["new_err"]      # 錯誤原文要留著
    assert d["probed_at"], "沒有記錄幾點測的，畫面就講不出這份資料多新"


def test_待納管要講不要先翻設定(conn):
    """先翻設定那一台就立刻收不到——那正是 2026-09-23 踩的坑。"""
    _add(conn, "A-4", "10.0.0.4")
    cam.probe_one(conn, "A-4", "10.0.0.4", "linux", _probe=_probe(False, True))
    why = cam.detail(conn, cam.PENDING)[0]["why"]
    assert "重新納管" in why
    assert "不要先翻設定" in why


# ═══ 分母要講清楚是哪些 ═════════════════════════════════════════════

def test_無法判斷與不適用不算進分母(conn):
    _add(conn, "L-1", "10.0.0.11")
    _add(conn, "L-2", "10.0.0.12")
    _add(conn, "W-1", "10.0.0.13")
    cam.probe_one(conn, "L-1", "10.0.0.11", "linux", _probe=_probe(True, False))
    cam.probe_one(conn, "L-2", "10.0.0.12", "linux", _probe=_probe(False, False))
    cam.probe_one(conn, "W-1", "10.0.0.13", "windows")

    s = cam.summary(conn)
    assert s["counts"][cam.DONE] == 1
    assert s["counts"][cam.UNKNOWN] == 1       # 機器不通
    assert s["counts"][cam.NA] == 1            # Windows
    assert s["denominator"] == 1, "機器不通或 Windows 被算進分母了"
    assert s["done_pct"] == 100.0
    assert "不算在內" in s["denominator_text"]


def test_一台都還沒測不可以顯示成0百分比(conn):
    """「還沒測」跟「一台都還沒完成」是兩件事。"""
    s = cam.summary(conn)
    assert s["probed"] == 0
    assert s["done_pct"] is None, "還沒測卻顯示 0%，那是把沒查到講成有答案"


# ═══ 第 4 步：切換這一台 ════════════════════════════════════════════

def test_只切一台_其餘不受影響(conn):
    _add(conn, "A-1", "10.0.0.1")
    _add(conn, "A-2", "10.0.0.2")
    cam.switch_one(conn, "A-1", _probe=_probe(True, True))
    assert cam.account_for_host(conn, "A-1", "linux") == ms.READONLY_ACCOUNT
    assert cam.account_for_host(conn, "A-2", "linux") == \
        ms.get_collect_account(conn, "linux")


def test_不是可切換就不准切(conn):
    """只有舊帳號進得去的機器切了會**立刻收不到**。"""
    _add(conn, "A-3", "10.0.0.3")
    with pytest.raises(ValueError) as e:
        cam.switch_one(conn, "A-3", _probe=_probe(False, True))
    assert "待納管" in str(e.value)
    # 沒切成功就不可以留下任何痕跡
    assert cam.account_for_host(conn, "A-3", "linux") == \
        ms.get_collect_account(conn, "linux")


def test_切換前一定重新探測_不信過期的狀態(conn):
    """⚠️ 這條是第 4 步最重要的守門。

    看板上的「可切換」是上一次探測的結果，**可能已經過期**。
    拿過期的狀態去切，那台立刻收不到——2026-09-23 那個
    「好好的機器變成 0/9」正是這個形狀（設定與實況不一致）。
    """
    _add(conn, "A-4", "10.0.0.4")
    # 先探一次：當時是可切換
    cam.probe_one(conn, "A-4", "10.0.0.4", "linux", _probe=_probe(True, True))
    assert cam.detail(conn)[0]["state"] == cam.SWITCHABLE
    # 但現在新帳號已經不通了（例如金鑰被清掉）-> 切換必須擋下來
    with pytest.raises(ValueError) as e:
        cam.switch_one(conn, "A-4", _probe=_probe(False, True))
    assert "不是「可切換」" in str(e.value)
    assert cam.account_for_host(conn, "A-4", "linux") != ms.READONLY_ACCOUNT


def test_還原路徑一定要有(conn):
    """切了才發現收不到時要能立刻退回去，不是等人去改資料庫。"""
    _add(conn, "A-5", "10.0.0.5")
    cam.switch_one(conn, "A-5", _probe=_probe(True, True))
    assert cam.account_for_host(conn, "A-5", "linux") == ms.READONLY_ACCOUNT
    cam.revert_one(conn, "A-5")
    assert cam.account_for_host(conn, "A-5", "linux") == \
        ms.get_collect_account(conn, "linux")


def test_切換要留前後筆數對照(conn):
    """沒有這個對照，**數字變大跟資料跑掉在畫面上長得一樣**。"""
    _add(conn, "A-6", "10.0.0.6")
    cam.switch_one(conn, "A-6", _probe=_probe(True, True))
    h = cam.switch_history(conn, "A-6")[0]
    assert "before_counts" in h and "after_counts" in h
    assert "換之前" in h["text"] and "換之後" in h["text"]
    assert h["switched_at"]


# ═══ 第 5 步：移除舊帳號 —— 只產指令，不代為執行 ═══════════════════

def test_只列出新帳號已驗證且舊帳號還在的機器(conn):
    """新帳號還沒佈好就移除舊的 -> 那台完全連不進去，只能派人去機房。"""
    _add(conn, "B-1", "10.0.1.1")   # 新舊都通 -> 要移除舊的
    _add(conn, "B-2", "10.0.1.2")   # 只有舊的通 -> 不可以移除
    _add(conn, "B-3", "10.0.1.3")   # 只有新的通 -> 舊的已經不在
    cam.probe_one(conn, "B-1", "10.0.1.1", "linux", _probe=_probe(True, True))
    cam.probe_one(conn, "B-2", "10.0.1.2", "linux", _probe=_probe(False, True))
    cam.probe_one(conn, "B-3", "10.0.1.3", "linux", _probe=_probe(True, False))

    plan = cam.removal_plan(conn)
    assert plan["count"] == 1
    assert plan["items"][0]["asset_serial"] == "B-1"


def test_aix用rmuser_linux用userdel(conn):
    _add(conn, "C-1", "10.0.2.1")
    _add(conn, "C-2", "10.0.2.2")
    cam.probe_one(conn, "C-1", "10.0.2.1", "aix", _probe=_probe(True, True))
    cam.probe_one(conn, "C-2", "10.0.2.2", "linux", _probe=_probe(True, True))
    cmds = {i["asset_serial"]: i["command"] for i in cam.removal_plan(conn)["items"]}
    assert cmds["C-1"].startswith("rmuser -p ")
    assert cmds["C-2"].startswith("userdel -r ")


def test_移除指令必須是純文字可讀_不可以是一鍵管道(conn):
    """金融業鐵律：給人貼上去執行的東西，讀的人要看得懂自己在跑什麼。

    base64／落地暫存檔／`| sudo bash` 對應 MITRE 的混淆與削弱防禦，
    在這種環境會被 SOC 當成事件，而且執行的人看不懂內容。
    """
    _add(conn, "D-1", "10.0.3.1")
    cam.probe_one(conn, "D-1", "10.0.3.1", "linux", _probe=_probe(True, True))
    txt = cam.removal_script(conn)
    for bad in ("base64", "| sudo bash", "| bash", "curl ", "wget ",
                "ExecutionPolicy", "eval "):
        assert bad not in txt, f"移除指令出現了 {bad}"
    assert "userdel -r" in txt
    assert "以 root 執行" in txt          # 講清楚要用什麼身分
    assert "10.0.3.1" in txt              # 哪一台要看得到


def test_沒有符合條件的機器也要講清楚(conn):
    txt = cam.removal_script(conn)
    assert "沒有任何一台符合條件" in txt


def test_系統不代為執行要寫在回應裡(conn):
    plan = cam.removal_plan(conn)
    assert "系統不會替你執行" in plan["warning"]
    assert "唯讀非 root" in plan["warning"]
