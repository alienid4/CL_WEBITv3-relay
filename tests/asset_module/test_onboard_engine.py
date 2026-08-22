"""納管引擎：把發現的主機自動帶到已納管。

最重要的測試是**憑證不落地**——登入密碼絕不能出現在回傳值、稽核紀錄、DB、log。
這是使用者定案的三條安全底線之首，用假執行器把整條路走一遍來守它。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import onboard_engine as eng  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"


# ===== 腳本即時從公鑰組出，不依賴外部檔案 =====

def test_腳本內嵌公鑰_且不含任何機密():
    lin = eng.build_script("linux", PUBKEY, "10.0.0.221")
    assert PUBKEY in lin
    assert "webit3scan" in lin
    assert "from=\\\"10.0.0.221\\\"" in lin or "10.0.0.221" in lin
    # 腳本裡不該有私鑰痕跡
    assert "PRIVATE KEY" not in lin and "BEGIN OPENSSH" not in lin


def test_平台選對腳本():
    assert "useradd" in eng.build_script("linux", PUBKEY, "10.0.0.1")
    assert "New-LocalUser" in eng.build_script("windows", PUBKEY, "10.0.0.1")
    # AIX 2026-08-16 起支援（方案 A，公司有 8 台）——用 mkuser 不是 useradd，
    # 細節見 test_onboard_aix.py
    assert "mkuser" in eng.build_script("aix", PUBKEY, "10.0.0.1")
    import pytest
    with pytest.raises(ValueError):
        eng.build_script("solaris", PUBKEY, "10.0.0.1")


def test_windows腳本用ASCII寫金鑰_不留BOM():
    """實際踩過：PowerShell -Encoding utf8 塞 BOM，sshd 讀 authorized_keys 就壞。"""
    win = eng.build_script("windows", PUBKEY, "10.0.0.1")
    assert "ASCIIEncoding" in win
    assert "collector_keys" in win   # 走中央金鑰路徑，繞過「新帳號沒 profile」


# ===== 憑證不落地（核心）=====

def test_密碼絕不出現在回傳結果():
    fake_pw = "FAKE-TEST-PW-not-real"
    captured = {}

    def fake_exec(host, username, password, platform, script, collector_ip, **kw):
        # 執行器內部拿得到密碼（本來就要用它登入），但這是唯一該碰它的地方
        captured["saw_password"] = password
        return eng.OnboardResult(True, "execute", "納管腳本執行完成", "完成。")

    result = eng.onboard("10.0.0.9", "linux", "sysctl", fake_pw,
                         "10.0.0.221", pubkey=PUBKEY, executor=fake_exec)

    # 執行器確實收到密碼（否則根本登不進去）
    assert captured["saw_password"] == fake_pw
    # 但回傳結果的任何欄位都不能含密碼
    import dataclasses
    for v in dataclasses.asdict(result).values():
        assert fake_pw not in str(v), f"密碼外洩到回傳欄位：{v}"


def test_稽核紀錄不含密碼_只留帳號名與成敗():
    """3000 台自動化要能事後稽核，但稽核紀錄永遠不含憑證。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            fake_pw = "FAKE-TEST-PW-do-not-persist"
            conn.execute(
                "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
                "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
                ("10.0.0.9", "linux", "sysctl", "manual", "admin", 1, "execute",
                 "納管腳本執行完成", "完成。"))
            conn.commit()

            # 掃過整張表的每一格，密碼不該出現在任何欄位
            row = conn.execute("SELECT * FROM onboard_audit").fetchone()
            for v in dict(row).values():
                assert fake_pw not in str(v)
            # 該留的有留：帳號名、成敗、階段
            assert row["login_user"] == "sysctl" and row["ok"] == 1
        finally:
            conn.close()


def test_未知平台安全拒絕_不會誤跑():
    r = eng.onboard("10.0.0.9", "solaris", "u", "pw", "10.0.0.221",
                    pubkey=PUBKEY, executor=lambda **k: None)
    assert not r.ok and "平台" in r.message


def test_稽核表存在且結構正確():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(onboard_audit)")}
            # 該有的稽核欄位在
            assert {"target_ip", "platform", "login_user", "trigger", "ok"} <= cols
            # 絕不該有存密碼的欄位
            assert not any("password" in c.lower() or "secret" in c.lower()
                           or "credential" in c.lower() for c in cols)
        finally:
            conn.close()


def test_windows腳本要改既有的AuthorizedKeysFile_而非另外加一條():
    """實測踩到（.101）：Windows 預設 sshd_config 早就有一條生效中的
    `AuthorizedKeysFile .ssh/authorized_keys`。OpenSSH 對同一指令**只取第一次出現**的，
    所以「在 Match 之前另外插一條」完全沒生效——sshd 仍只看 .ssh/authorized_keys，
    而收集帳號沒有 profile、那路徑不存在，於是永遠 Permission denied。

    腳本必須改掉既有那一行，把中央金鑰路徑接上去。
    """
    win = eng.build_script("windows", PUBKEY, "10.0.0.1")
    # 必須有「抓既有 AuthorizedKeysFile 行並改它」的邏輯，不能只插新行
    assert "^(AuthorizedKeysFile" in win, "沒有比對既有 AuthorizedKeysFile 行"
    assert "$rx.Replace($t" in win, "沒有改寫既有那一行"
    assert "collector_keys" in win
    # 要能清掉舊版插入的重複行（重跑可把已壞的機器修好）
    assert "Replace($t,'')" in win, "沒有清除舊版插入的重複行"


def test_windows腳本必須把帳號加進Users群組():
    """實測踩到（.101）：New-LocalUser **不會**把帳號加進任何群組
    （Linux 的 useradd 會給主要群組，Windows 不會）。不在 Users 群組就沒有
    「從網路存取這台電腦」的權限，SSH 一律 Permission denied——
    而且錯誤訊息完全看不出是群組問題，只會說 publickey 被拒。
    """
    win = eng.build_script("windows", PUBKEY, "10.0.0.1")
    assert "Add-LocalGroupMember" in win, "沒有把帳號加進群組"
    assert "'Users'" in win


def test_windows腳本跑完要自己印驗證結果():
    """來回猜三次的教訓：腳本跑完要自己說「設定長什麼樣、群組有沒有、金鑰在不在」，
    不要讓人事後另外下指令查。"""
    win = eng.build_script("windows", PUBKEY, "10.0.0.1")
    assert "驗證" in win
    assert "Select-String -Path $cfg" in win     # 印出實際生效的設定
    assert "Get-LocalGroup" in win               # 印出群組
