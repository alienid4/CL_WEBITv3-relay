"""Windows 值班健檢（8-3）。

使用者 2026-09-23：「AIX／Windows 也要啊」。權限那題使用者拍板
**不做登入失敗偵測（4625）、不加 Event Log Readers、不動任何權限**。

這組測試守的是「做錯會比沒做更糟」的五件，不是解析細節：

1. **沒有等價概念的指標不可以填 0**（iowait／load／inode）——0 會被讀成
   「量到了，而且很好」，那是在編一個數字
2. **記憶體門檻不可以沿用 Linux**——Windows 正常就把記憶體用在快取上，
   照 Linux 的使用率判會整批誤判成吃緊
3. **服務的對應是「設為自動啟動卻沒在跑」**，不是「列出所有服務」——
   名稱像不算對應
4. **收不到要講原因**，而且「沒有憑證」不可以顯示成「連不上」——
   一個是去設定頁補帳號，一個是去機房
5. **登入失敗那一項要明說沒查**，不可以讓人以為「沒有爆破」是查過的結論
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import health_probe as hp  # noqa: E402
import winrm_collector as wc  # noqa: E402

CREDS = ("svc_webit3", "pw")

FULL = """@@OS
caption=Microsoft Windows Server 2019 Standard
version=10.0.17763
@@UP
boot=2026-09-20 03:00:00
now=2026-09-23 15:00:00
@@CPU
ncpu=8
usage=12.5
@@MEM
total_kb=8388608
free_kb=4194304
commit_total_kb=16777216
commit_free_kb=10000000
@@DISK
C:|107374182400|53687091200
D:|10737418240|107374182
@@SVC
Spooler|Print Spooler|Stopped
@@LISTEN
LISTEN 0 0 0.0.0.0:3389 *:* users:(("TermService"))
@@TIME
rc=0
src=dc01.example.invalid
@@END
"""


def _run(text=FULL, creds=CREDS):
    return hp.check_one("10.99.0.50", platform="windows", creds=creds,
                        _winrm=lambda ip, u, p: text)


# ── 1 沒有等價概念的不可以填 0 ────────────────────────────────────────
def test_iowait與load沒有等價概念就不要編一個數字():
    r = _run()
    assert r["cpu"]["usage_pct"] == 12.5
    assert r["cpu"]["iowait_pct"] is None, "Windows 沒有 iowait，填 0 會被讀成『量到了而且很好』"
    assert r["load"] is None, "Windows 沒有 load average"


def test_磁碟沒有inode概念():
    r = _run()
    assert r["disks"][0]["mount"] == "C:" and r["disks"][0]["use_pct"] == 50
    assert r["disks"][0]["inode_pct"] is None, "NTFS 沒有 inode，這一欄不是 0"


# ── 2 記憶體門檻不可以沿用 Linux ──────────────────────────────────────
def test_記憶體用windows自己的判準():
    """8G 機器用掉一半、commit 40%——Linux 的 80% 門檻在這裡根本沒意義，
    真正要看的是「還剩多少可用」與「再開得起東西嗎」。"""
    r = _run()
    m = r["mem"]
    assert m["used_pct"] == 50.0 and m["free_mb"] == 4096
    assert m["commit_pct"] == 40.4
    assert m["level"] == "green"


def test_實體吃緊但commit還好不算紅():
    """Windows 正常運作就會把記憶體用在 standby cache。
    只看實體可用量就判紅，會把一台完全正常的機器報成記憶體不足。"""
    t = FULL.replace("free_kb=4194304", "free_kb=204800")        # 剩 200MB
    r = _run(t)
    assert r["mem"]["level"] == "yellow", "兩個指標只有一個壞，不該直接紅"


def test_兩個都壞才是紅():
    t = (FULL.replace("free_kb=4194304", "free_kb=204800")
             .replace("commit_free_kb=10000000", "commit_free_kb=100000"))
    r = _run(t)
    assert r["mem"]["level"] == "red"


# ── 3 服務的對應要語意等價 ────────────────────────────────────────────
def test_只列設為自動啟動卻沒在跑的():
    r = _run()
    assert r["failed_units"] == ["Spooler"]
    assert r["overall"] == "red", "有自動啟動的服務沒在跑＝紅（跟 Linux 的 failed 一致）"
    assert any("沒在跑" in n for n in r["notes"])


def test_正在啟動的不算沒在跑():
    """Start Pending 是**正在啟動**。算進去的話，每次重開機後幾分鐘內都會紅一次，
    值班很快就學會忽略這個燈——假警報會把真警報一起淹掉。"""
    t = FULL.replace("Spooler|Print Spooler|Stopped", "Spooler|Print Spooler|Start Pending")
    r = _run(t)
    assert r["failed_units"] == []


# ── 4 收不到要講原因，而且要分得出是哪一種 ────────────────────────────
def test_沒有憑證不可以顯示成連不上():
    """一個是去設定頁補帳號，一個是去機房——下一步完全不同。"""
    r = hp.check_one("10.99.0.50", platform="windows", creds=lambda ip: None)
    assert r["reachable"] is False
    assert "憑證" in (r["error"] or "")
    assert "連不上" not in (r["error"] or "")
    assert r["coverage"]["done"] == 0 and r["coverage"]["missing"]


def test_winrm連不上要原樣講原因並附開通方法():
    def boom(ip, u, p):
        raise ConnectionError("connection refused")

    r = hp.check_one("10.99.0.50", platform="windows", creds=CREDS, _winrm=boom)
    assert r["reachable"] is False
    assert "refused" in (r["error"] or "")
    assert "Enable-PSRemoting" in (r["error"] or ""), "連不上卻沒告訴人怎麼開通"


def test_時間查不到不是沒同步():
    """回 None ＝查不到，回 False ＝查過了真的沒同步。兩者不可以混。"""
    assert hp.parse_win_time("rc=1") is None
    assert hp.parse_win_time("rc=0\nsrc=Local CMOS Clock") is False
    assert hp.parse_win_time("rc=0\nsrc=dc01.example.invalid") is True


def test_缺的維度要說得出為什麼():
    r = _run()
    keys = {m["key"]: m["why"] for m in r["coverage"]["missing"]}
    assert "mounts" in keys, "Windows 沒有唯讀重掛這個機制，要標成缺"
    assert keys["mounts"], "缺了卻不說為什麼，跟顯示 0 是同一種病"
    assert r["coverage"]["done"] == 8 and r["coverage"]["total"] == 9
    assert r["complete"] is False


# ── 5 沒查的要明說 ────────────────────────────────────────────────────
def test_登入失敗偵測要明說沒查():
    """使用者拍板不開這個權限。**沒有這句，值班會以為「沒有爆破」是查過的結論。**"""
    r = _run()
    assert r["ssh_fails"] is None
    assert r["sudo_log_authorized"] is False
    note = " ".join(r["notes"])
    assert "安全性記錄檔" in note and "不是「沒有異常」" in note


# ── 收集指令本身 ──────────────────────────────────────────────────────
def test_收集指令不用效能計數器也不用status():
    """計數器路徑名與 /status 的欄位標題**都會隨系統語言變**，
    在中文版 Windows 上會查不到，而錯誤長得像「這台沒有這個指標」。"""
    ps = wc.HEALTH_PS
    assert "Get-Counter" not in ps, "效能計數器路徑會隨語言變"
    assert "/query /source" in ps and "/query /status" not in ps
    for m in ("@@OS", "@@UP", "@@CPU", "@@MEM", "@@DISK", "@@SVC", "@@LISTEN", "@@TIME"):
        assert m in ps, f"少收 {m}"
    # 只讀不改：健檢不可以動到別人的機器
    for bad in ("Set-", "Stop-Service", "Restart-", "Remove-", "New-Item"):
        assert bad not in ps, f"健檢指令裡出現會改東西的 {bad}"


def test_沒有的指標一律是None不是0():
    """把守門收在一條：任何「這個平台沒有」的欄位都必須是 None。

    2026-09-23 讀畫面時抓到：JS 的 `null >= 0` 是 true，所以
    `d.inode_pct >= 0 ? d.inode_pct + '%' : '—'` 會把 NTFS 的 inode 印成 **0%**。
    那是憑空生出一個數字，而且長得像「量到了，而且很好」。

    前端已經改成先判 null；這條盯後端不要哪天為了「欄位齊一點」而填 0——
    真正的齊一是**型別齊一**（沒有就是 None），不是數字齊一。
    """
    r = _run()
    assert r["cpu"]["iowait_pct"] is None
    assert r["load"] is None
    for d in r["disks"]:
        assert d["inode_pct"] is None
    # 反過來：真的量到的就一定要有值，不可以偷懶全給 None
    assert r["cpu"]["usage_pct"] is not None
    assert r["mem"]["total_kb"] > 0 and r["mem"]["avail_kb"] > 0
    assert all(d["use_pct"] is not None for d in r["disks"])
