"""納管的可觀測性：做過什麼、為什麼失敗，畫面上要看得到。

背景（2026-09-22 使用者）：「AIX 我已經納管多次，每次狀態我看都是未納管」。
他做了三次才來問，是因為**畫面什麼都不講**：

* 納管只寫 `onboard_audit`，資產頁沒有呈現它
* 資產頁那個叫「歷史時間軸」的分頁讀的是 `comparison_result`（**組態差異偵測**），
  收集沒成功就永遠是空的——失敗時一條線索都沒有
* 失敗訊息只說「收不到任何欄位，原因不明」，跟權限不足長得一模一樣

使用者 2026-09-22 立的規矩：「我不希望你提供什麼樣的資料，我會繼續錄影給你看」
「換到正式區，如果遇到相同的問題才好排除」——**他手上只有畫面**，
不准叫他跑 SQL。畫面看不到的，就把它做到畫面上。

這支守的是：
1. 納管動作**成功失敗都要留紀錄**，而且資產詳細頁要回傳得出來
2. 驗證**失敗不可以留下 collect_ok=1**（否則畫面顯示已納管，隔天排程又打回去）
3. 失敗訊息要講得出**送出平台／判定平台／實際平台／收到幾個欄位**，
   不是只說「失敗」
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 模擬使用者那台：掃描產生的合成資產，**os 欄位是空的**
    db.insert_hardware(c, asset_serial="DYN-192.0.2.16", hostname="test1T",
                       ip="192.0.2.16", os=None, asset_status=None)
    c.commit()
    try:
        yield c
    finally:
        c.close()


def test_os為空時平台是用埠猜的_而且猜成linux(conn):
    """這是 AIX 一直納管不起來的根因：掃描產生的資產沒有 os，就退回看埠。

    `detect_platform` 是「3389→windows，**其餘一律 linux**」，
    AIX 不開 3389，所以永遠被猜成 linux → 拿 /etc/os-release 去問 AIX → 欄位全空。
    """
    import facts_collector as fc
    assert fc.detect_platform([22]) == "linux"
    assert fc.detect_platform([22, 111, 657]) == "linux", "AIX 典型埠仍被猜成 linux"
    assert fc.detect_platform([]) == "linux", "掃不到埠也猜 linux——這是最危險的一種"
    # os 空 → collect_platform_of 只能靠埠
    assert ms.collect_platform_of(conn, "192.0.2.16", None) == "linux"
    # os 有值就判得對
    assert ms.collect_platform_of(conn, "192.0.2.16", "AIX 7.2") == "aix"


def test_probe_uname只用內建指令且問不到回None():
    """`uname -s` 一個指令、免提權、Unix 三平台都有（使用者：只用內建指令）。

    **問不到要回 None，不可以推論成 linux**——Windows 本來就沒有這個指令。
    """
    assert ms.probe_uname("192.0.2.16", runner=lambda h: "AIX") == "AIX"
    assert ms.probe_uname("192.0.2.16", runner=lambda h: None) is None


def test_納管失敗訊息要講得出四件事(conn):
    """使用者手上只有畫面。訊息要能讓他自己判斷是平台判錯還是連不進去。"""
    import api

    msg = api._collect_diagnosis(conn, "192.0.2.16", sent_platform="linux",
                                 os_val=None, updated=0, base_error=None)
    for must in ("送出平台=linux", "判定平台=linux", "實際 uname -s=", "收到欄位=0 個"):
        assert must in msg, f"訊息缺少「{must}」：{msg}"
    assert "OS 欄位是空的" in msg, "os 空是根因，要明講"


def test_判定平台與實際不符要直接點出來(conn, monkeypatch):
    """真正的衝突：**清冊寫 Linux、機器實際是 AIX**。

    2026-09-22 第 2 刀之後，「os 空」不再是衝突——平台會從同一台其他登記
    或 `uname -s` 問出來，判定就對了。剩下會衝突的是**清冊本身寫錯**：
    CIA 登記 Red Hat，但機器實際回 AIX。那種要直接點出來，
    不要讓人自己比對兩個字串。
    """
    import api

    monkeypatch.setattr(ms, "probe_uname", lambda *a, **k: "AIX")
    msg = api._collect_diagnosis(conn, "192.0.2.16", sent_platform="linux",
                                 os_val="Red Hat Enterprise Linux 9", updated=0,
                                 base_error=None)
    assert "實際 uname -s=AIX" in msg
    assert "判定平台與實際不符" in msg, f"不符要直接講，不要讓人自己比對：{msg}"


def test_同一台其他登記有OS就不該再判錯(conn):
    """**第 2 刀的主修**：DYN- 合成資產 os 是空的，但同一台的 CIA 那筆寫著 AIX 7.2。

    使用者 2026-09-22 的納管漏斗截圖：8 台 AIX 每一台都標「同台 2 筆」，
    每筆的作業系統都是 AIX 7.2。他按納管時選到 DYN- 那筆（os 空）→ 判成 linux。
    這台的 AIX 身分系統早就知道，只是記在另一筆登記上。
    """
    # 同一台的第二筆：CIA 登記，os 有值
    db.insert_hardware(conn, asset_serial="CIA-0001", hostname="test1T",
                       ip="192.0.2.16", os="AIX 7.2", asset_status="使用中")
    conn.commit()
    # 從 os 空的那筆問，仍要判成 aix——而且**不必跑 uname**
    got = ms.collect_platform_of(conn, "192.0.2.16", None,
                                 asset_serial="DYN-192.0.2.16",
                                 probe=lambda h: pytest.fail("不該需要跑 uname"))
    assert got == "aix"


def test_Linux與Windows既有行為不可改變(conn):
    """第 2 刀動到共用邏輯，Linux／Windows 不可以被波及。"""
    import facts_collector as fc

    # 埠號猜法本身不動
    assert fc.detect_platform([22]) == "linux"
    assert fc.detect_platform([3389]) == "windows"

    # os 寫得出平台就直接用，不必問任何人
    assert ms.platform_from_os("Red Hat Enterprise Linux 9.4") == "linux"
    assert ms.platform_from_os("Microsoft Windows Server 2019") == "windows"
    assert ms.platform_from_os("AIX 7.2") == "aix"
    # 認不得的不可以硬猜成 linux
    assert ms.platform_from_os("VMware ESXi 7.0") is None
    assert ms.platform_from_os("") is None
    assert ms.platform_from_os(None) is None

    # 只開 22 的 Linux：os 空、同台無其他登記、uname 問不到 → 仍退回埠號猜 linux
    db.insert_hardware(conn, asset_serial="L-1", hostname="lx1", ip="192.0.2.90",
                       os=None, asset_status="使用中")
    conn.execute("INSERT INTO scan_history (ip, scan_time, scan_ok, open_ports) "
                 "VALUES (?,?,1,?)", ("192.0.2.90", "2026-09-22 10:00:00", "22"))
    conn.commit()
    assert ms.collect_platform_of(conn, "192.0.2.90", None, asset_serial="L-1",
                                  probe=lambda h: None) == "linux"


def test_連uname都問不到要說可能是連不進去而不是平台判錯(conn, monkeypatch):
    """兩種原因要分開講——處理方式完全不同。"""
    import api

    monkeypatch.setattr(ms, "probe_uname", lambda *a, **k: None)
    msg = api._collect_diagnosis(conn, "192.0.2.16", sent_platform="linux",
                                 os_val=None, updated=0, base_error=None)
    assert "連不進去" in msg and "而不是平台判錯" in msg


def test_資產詳細頁要回傳納管紀錄含失敗(conn):
    """失敗的也要列。只列成功的話，「試過三次都失敗」跟「從來沒試過」畫面上一樣。"""
    for ok, stage, msg in ((0, "verify", "收集失敗：判定平台=linux、實際 uname -s=AIX"),
                           (0, "verify", "收集失敗：第二次"),
                           (1, "verify", "驗證成功")):
        conn.execute(
            "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
            "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
            ("192.0.2.16", "linux", None, "verify", "tester", ok, stage, msg, None))
    conn.commit()
    rows = [dict(r) for r in conn.execute(
        "SELECT ok, message FROM onboard_audit WHERE target_ip = ? ORDER BY id DESC",
        ("192.0.2.16",))]
    assert len(rows) == 3
    assert sum(1 for r in rows if not r["ok"]) == 2, "失敗那兩筆一定要在"


def test_歷史時間軸讀的不是操作紀錄_這件要講清楚():
    """`comparison_result` 是**組態差異偵測**，不是「這台發生過什麼」。

    這個誤解讓使用者以為系統沒收到他的動作。畫面上兩塊要分開標題，
    這支測試守的是「別再把它當成操作紀錄用」——欄位結構本身就證明了：
    它只有 issue_type，沒有操作者、沒有動作。
    """
    import sqlite3
    import tempfile

    p = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(p)
    c: sqlite3.Connection = db.get_connection(p)
    cols = {r[1] for r in c.execute("PRAGMA table_info(comparison_result)")}
    c.close()
    assert "issue_type" in cols
    assert not ({"triggered_by", "action", "username"} & cols), \
        "comparison_result 沒有操作者欄位——它不是操作紀錄，不要拿它當時間軸"


# ── 2026-09-22 追加：一鍵納管收不到資料不准報成功 ──────────────────────
# 使用者原話：「AIX 我可以連線，**畫面也顯示納管成功**，納管後我沒看到納管後的
# 任何資訊跟畫面呈現，例如帳號、軟體、硬體資訊等等」。
#
# 真因：/api/onboard 在收集之後**把回傳值丟掉**——帳號確實建好了（腳本 ok），
# 但緊接著的收集拿到 0 個欄位而沒人看。post_note 只在丟例外時才寫，
# 「跑完了但什麼都沒收到」不算例外。於是「連得上、報成功、資料全空」同時成立。

def test_收不到資料時要回ok為False而且分得出腳本有沒有成功():
    """兩種失敗的下一步完全不同，不可以都講「納管失敗」：

    * 腳本失敗 → 帳密或權限問題，**重跑有用**
    * 腳本成功但收不到 → 帳號已建好，是平台判錯或收集端問題，**重跑沒有用**

    所以回傳要同時帶 ok 與 script_ok。
    """
    import inspect

    import api
    src = inspect.getsource(api.onboard_endpoint)
    assert "script_ok" in src, "回傳要分得出腳本成敗與整體成敗"
    assert 'collected.get("updated", 0) < 1' in src, \
        "收集回傳值一定要檢查——原本是丟掉的，那正是使用者被誤導的原因"
    assert '"ok": False' in src, "收不到任何欄位時不可以回 ok=True"


def test_收集回傳值不可以被丟掉():
    """防退化：這行原本是 `manage_state.collect_facts_into_assets(...)` 沒接回傳值。"""
    import inspect

    import api
    src = inspect.getsource(api.onboard_endpoint)
    assert "collected = manage_state.collect_facts_into_assets(" in src, \
        "收集結果要接起來看，不可以呼叫完就丟"


# ── 2026-09-22 追加：認證失敗不可以只回一句罐頭話 ──────────────────────
# 使用者證據：同一台、同一個帳號，OpenSSH 密碼登入**成功**（Welcome to AIX 7.2），
# 系統卻回「Permission denied（帳號或密碼不對）」。他照那句話去查帳號鎖定、
# PermitRootLogin、rlogin——全部是對的，因為問題不在那裡。
# 真正的 paramiko 例外被壓成同一句話，資訊全丟了。BOSS 也因此猜錯兩次。

def test_認證失敗要附原始例外不可以只回罐頭話():
    import onboard_engine as oe

    class FakeAuthError(Exception):
        pass
    FakeAuthError.__name__ = "AuthenticationException"

    exc = FakeAuthError("Authentication failed.")
    msg = oe._ssh_error_text("192.0.2.16", exc)
    assert "Permission denied" in msg, "白話留著，它對多數情況是對的"
    assert "原始錯誤" in msg and "Authentication failed" in msg, \
        f"原始例外不可以被吞掉：{msg}"


def test_有談判事實就要一起顯示():
    """`ssh -v` 印的那幾行——對方 SSH 版本、接受的認證方法、談成的演算法。

    使用者手上只有畫面，不該被叫去目標機跑 `ssh -v`。
    """
    import onboard_engine as oe

    class FakeAuthError(Exception):
        pass
    FakeAuthError.__name__ = "AuthenticationException"

    exc = FakeAuthError("Authentication failed.")
    exc._ssh_facts = {
        "remote_version": "SSH-2.0-OpenSSH_8.1",
        "allowed_auth": ["publickey", "password"],
        "kex": ["curve25519-sha256"], "cipher": ["aes256-ctr"], "mac": ["hmac-sha2-256"],
    }
    msg = oe._ssh_error_text("192.0.2.16", exc)
    assert "對方 SSH=SSH-2.0-OpenSSH_8.1" in msg
    assert "對方接受的認證方法=publickey、password" in msg, \
        "接受哪些方法是判斷『方法不對』還是『密碼不對』的關鍵"
    assert "kex=curve25519-sha256" in msg


def test_蒐證失敗不可以蓋掉原始例外():
    """談判事實是附加資訊。蒐不到就不顯示，**不可以因此讓錯誤訊息消失**。"""
    import onboard_engine as oe

    class FakeAuthError(Exception):
        pass
    FakeAuthError.__name__ = "AuthenticationException"

    exc = FakeAuthError("Authentication failed.")
    exc._ssh_facts = None            # 蒐證失敗
    msg = oe._ssh_error_text("192.0.2.16", exc)
    assert "Authentication failed" in msg
    assert oe._facts_text(exc) == ""


def test_不可以再宣稱paramiko會自動退回keyboard_interactive():
    """防退化：原註解寫「paramiko 的密碼認證會自動退回」——查 5.0.0 原始碼是 `elif`。

    給了密碼就只試 auth_password，永遠走不到 keyboard-interactive。
    那句錯註解會讓下一個人以為這條路徑已經處理過了。
    """
    import inspect

    import onboard_engine as oe
    src = inspect.getsource(oe._ssh_connect)
    assert "會自動退回用同一組" not in src, "那句話是錯的，不可以放回來"

    # 順便釘住 paramiko 的實際行為，哪天它改成真的 fallback 這條會紅，提醒我們重看
    import paramiko.client
    auth_src = inspect.getsource(paramiko.client.SSHClient._auth)
    assert "elif two_factor:" in auth_src, \
        "paramiko 改了認證流程——重新確認要不要自己做 keyboard-interactive 退路"


# ── 2026-09-22 追加：AIX account_locked 是我們自己鎖的 ──────────────────
# 8 台 AIX 全部卡在這裡。腳本原本寫 `chuser account_locked=true`，
# 註解說「只能用金鑰登入，沒有可用密碼」——**那是 Linux 的慣用法**：
#   Linux passwd -l / usermod -L → 只鎖密碼，金鑰登入照常
#   AIX   account_locked=true    → 鎖整個帳號，sshd 連公鑰也拒絕
# 而且這個錯誤的心智模型**先被寫進 docstring**，腳本才照著寫，然後沒人回頭質疑。

def test_AIX腳本不可以鎖整個帳號():
    import onboard_engine as oe

    sc = oe.build_aix_script("ssh-ed25519 AAAA", "10.0.0.1", "webit3sc")
    # 可執行的那行不可以是鎖帳號（註解裡解釋原因可以留）
    exec_lines = [ln.strip() for ln in sc.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    assert not any("account_locked=true" in ln for ln in exec_lines), \
        "AIX 的 account_locked=true 會連金鑰一起擋，不可以拿它當「鎖密碼」"
    assert any("account_locked=false" in ln for ln in exec_lines), \
        "已存在的帳號可能被前一版腳本鎖著，一定要主動解鎖"
    assert any("rlogin=true" in ln for ln in exec_lines)


def test_納管腳本不可以宣稱遠端收得到():
    """三個平台的腳本最後都印「現在可以收集這台」——**那句話沒有根據**。

    那些驗證指令是在**目標機本機**跑的，證明「欄位讀得到」，
    完全沒有驗證「收集器從遠端連得進來」，而後者才是納管的目的。
    使用者就是被這句話誤導：腳本說完成，實際上帳號鎖著、遠端一直進不來。
    """
    import onboard_engine as oe

    for name, sc in (
        ("aix", oe.build_aix_script("k", "10.0.0.1", "webit3sc")),
        ("linux", oe.build_linux_script("k", "10.0.0.1", "webit3scan", None)),
        ("windows", oe.build_windows_script("k", "10.0.0.1", "webit3scan")),
    ):
        assert "現在可以收集這台" not in sc, f"{name} 腳本還在宣稱遠端收得到"
        assert "遠端連線尚未驗證" in sc, f"{name} 腳本要講明遠端還沒驗證"


def test_AIX認證被拒要提示帳號可能被鎖():
    import onboard_engine as oe

    class FakeAuthError(Exception):
        pass
    FakeAuthError.__name__ = "AuthenticationException"

    exc = FakeAuthError("Authentication failed (publickey,password).")
    msg = oe._ssh_error_text("192.0.2.16", exc)
    assert "account_locked" in msg and "chuser account_locked=false" in msg, \
        f"公鑰剛佈好卻 publickey,password 都被拒，最常見就是帳號被鎖：{msg}"
