"""收集帳號遷移單機版腳本（fcb_collect_account_migrate.sh）的守門。

這支要帶去公司的正式機上以 root 執行，所以守的是「會不會害人」：
預設不動手、沒有攻擊常見手法、不可逆的動作有前提檢查。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "APP" / "asset-module" / "scripts" / "fcb_collect_account_migrate.sh"
TXT = SH.read_text(encoding="utf-8")


def test_檔案存在且是LF():
    """要在 AIX／Linux 上跑，CRLF 會讓 #!/bin/sh 直接壞掉，
    而錯誤訊息完全看不出原因。"""
    assert SH.exists()
    # 讀 bytes，不要用 Path.read_text(newline=...)：那個參數是 Python 3.13 才有的，
    # 221 是 3.11 → TypeError。本機 3.14 跑得過、221 跑不過——
    # 這條測試本身就示範了它要防的那件事：這台綠不代表那台綠。
    assert b"\r\n" not in SH.read_bytes()


def test_預設不動手():
    """不加參數就只看現況。**預設值錯了，第一個跑的人就中標。**"""
    assert 'MODE="check"' in TXT
    assert "現況檢查模式：不會改任何東西" in TXT


def test_沒有攻擊常見手法():
    """金融業鐵律：不能看起來像攻擊，也不能真的留下漏洞。

    貼給人執行的東西要純文字可讀——混淆換不到任何好處，
    只會讓執行的人看不懂自己在跑什麼，同時讓 SOC 以為有人在投毒。
    """
    body = "\n".join(l for l in TXT.splitlines() if not l.lstrip().startswith("#"))
    for bad in ("base64", "curl ", "wget ", "| bash", "| sh", "eval ",
                "ExecutionPolicy", "StrictHostKeyChecking=no", "sshpass",
                "chmod 777", "chmod -R 777"):
        assert bad not in body, f"腳本出現了 {bad}"


def test_不可逆的動作要先檢查前提():
    """移除舊帳號之前，新帳號必須真的可用。

    不檢查就刪，萬一新帳號沒佈好，那台會完全連不進來，只能派人去機房。
    """
    assert "[移除前檢查]" in TXT
    assert "前提沒過" in TXT
    assert "沒有移除任何東西" in TXT
    # 還要人打 yes，不是按一下就刪
    assert 'ANS' in TXT and '"yes"' in TXT


def test_動到的東西要先備份():
    assert "/var/backups/webit3" in TXT
    assert "chmod 700" in TXT          # 備份目錄不可以人人可讀


def test_sudoers要visudo驗過才放上去():
    """語法錯的 sudoers 會讓**整台機器**的 sudo 全部失效，包括管理員自己。"""
    assert "visudo -c -f" in TXT
    assert "已丟棄、沒有放上去" in TXT


def test_aix的account_locked要設成false():
    """AIX 的 account_locked=true 會連公鑰一起擋——2026-09-22 八台全卡在這。"""
    assert "chuser account_locked=false rlogin=true" in TXT


def test_不可以用ssh開頭比對金鑰():
    """納管佈的那一行開頭是 from="..." 選項，金鑰型別在中間。

    用 ^ssh- 會數出 0 把金鑰，而「0 把」跟「沒有」在畫面上長得一樣——
    2026-09-24 在 221 實跑時就是這樣被誤導的。
    """
    # 只看真的會執行的行——註解裡寫著這個教訓，那是刻意留的
    body = chr(10).join(l for l in TXT.splitlines() if not l.lstrip().startswith("#"))
    assert "^ssh-" not in body
    assert "ssh-ed25519" in TXT and "ssh-rsa" in TXT


def test_不支援的平台要直說沒跑():
    assert "沒跑，不要當成做過了" in TXT
