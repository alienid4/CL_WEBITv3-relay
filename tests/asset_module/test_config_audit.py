"""[B-15] 組態檢核（選單 9-2）：解析各平台檢核結果、四態分開、合規率有分母。

測資寫在這裡而不是讀 DATA/——DATA 不進版控、也不會跟著去識別化產出物走，
測試不可以依賴那些檔案存不存在。

守的是這幾件（每一條都是「不這樣做就會算錯」，不是風格問題）：

1. **欄數各平台不同**：Linux／AIX 6 欄，**Windows 7 欄**（多一個「角色」）。
   照位置取值的話，Windows 每一列的「檢查結果」都會讀成 `Common`。
2. **Error 是獨立第三態**，不准併進 Compliant 也不准併進 Non-Compliant。
3. **合規率分母 = 總數 − 不適用 − 未查到**；分母 0 回 None 不是 0%。
4. **截斷的檔案要拒收**——少查的條目照收會變成「那些都沒問題」。
5. **平台讀表頭不看檔名**；Windows 檔帶 BOM 要剝掉。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import config_audit as ca  # noqa: E402
import db  # noqa: E402

LINUX = """系統組態檢查
平台: Linux
主機名: SECSVR198-011T
IP地址: 192.0.2.11
作業系統版本: Red Hat Enterprise Linux 9.8 (Plow)
核心版本: 5.14.0-687
腳本版本: SV.202606290800
Check stated at: 2026-09-20 15:10:01
TWGCB-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值
TWGCB-01-012-0066;Compliant;系統設定與維護;系統命令檔案權限;755 或更低;無異常
TWGCB-01-012-0072;Non-Compliant;系統設定與維護;帳號不使用空白密碼;不得空白;發現 1 筆
"""

# Windows：BOM ＋ 7 欄（多「角色」）。照位置取第 2 欄會讀到 Common。
WINDOWS = ("﻿系統組態檢查\n"
           "平台: Windows\n"
           "主機名: SECSVR055-033\n"
           "IP地址: 192.0.2.33\n"
           "作業系統版本: Microsoft Windows Server 2022 Standard\n"
           "核心版本: 21H2\n"
           "腳本版本: v4.0(結案)\n"
           "檢核基準: Windows組態安全配置檢核表-v4.0\n"
           "Check stated at: 2026-09-21 10:00:30\n"
           "TWGCB-ID;角色;檢查結果;類別;原則設定名稱;標準設定值;目前值\n"
           "TWGCB-01-007-0001;Common;Compliant;密碼原則;密碼最短使用期限;1 天;無異常\n"
           "CSCB-01-2025-0006;Common;Non-Compliant;服務;SSDP Discovery;停用;執行中\n")

AIX = """系統組態檢查
平台: AIX
主機名: sec01
IP地址: 192.0.2.12
作業系統版本: AIX 7.2
核心版本: 2.7 64bit
腳本版本: SV.AIX.202609210900
檢核基準: CIS IBM AIX 7.2 Benchmark v1.0.0
Check stated at: 2026-09-21 09:00:00
Check runas: root
FCB-AIX-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值
FCB-AIX-0001;Compliant;系統服務;Disable writesrv;inittab 需 off;已停用
FCB-AIX-0002;Non-Compliant;系統服務;dt;inittab 需 off;dt:2:wait:/etc/rc.dt
FCB-AIX-0018;Not-Applicable;系統服務;NFS nosuid;需帶 nosuid;本機無 NFS 掛載
FCB-AIX-0091;Error;帳號與存取控制;minlen;minlen >= 8;非 root，讀不到
Check ended at: 2026-09-21 09:00:03
Check summary: 合計 4 項；Compliant 1；Non-Compliant 1；Not-Applicable 1；Error 1
"""


def _b(s):
    return s.encode("utf-8")


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="sec01", ip="192.0.2.12",
                       os="AIX 7.2", asset_status="使用中")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def test_平台讀表頭不看檔名():
    for blob, want in ((_b(LINUX), "Linux"), (_b(WINDOWS), "Windows"), (_b(AIX), "AIX")):
        assert ca.parse(blob)["header"]["platform"] == want


def test_windows七欄不可錯位():
    """照位置取第 2 欄會讀到 Common，整台的合規統計就全錯。"""
    d = ca.parse(_b(WINDOWS))
    assert "role" in d["columns"], "Windows 多一個「角色」欄，要認得出來"
    first = d["items"][0]
    assert first["result"] == "Compliant", f"讀到 {first['result']!r}——欄位錯位了"
    assert first["role"] == "Common"
    assert first["category"] == "密碼原則"


def test_六欄平台沒有角色欄():
    d = ca.parse(_b(LINUX))
    assert d["items"][0]["role"] is None
    assert d["items"][1]["result"] == "Non-Compliant"


def test_BOM要剝掉():
    """不剝 BOM 的話第一行變成 \\ufeff系統組態檢查，平台判不出來整份被退。"""
    assert ca.parse(_b(WINDOWS))["header"]["platform"] == "Windows"


def test_四態分開_Error不併進任何一邊():
    c = ca.tally(ca.parse(_b(AIX))["items"])
    assert c == {"Compliant": 1, "Non-Compliant": 1, "Not-Applicable": 1, "Error": 1}
    assert sum(c.values()) == 4, "四態加總要等於總條數，不可以有條目被吞掉"


def test_認不得的結果值歸未查到而不是吞掉():
    bad = AIX.replace("FCB-AIX-0001;Compliant;", "FCB-AIX-0001;???;")
    c = ca.tally(ca.parse(_b(bad))["items"])
    assert c["Error"] == 2 and sum(c.values()) == 4, "認不得的值要如實算進未查到，不可以消失"


def test_合規率分母扣掉不適用與未查到():
    c = ca.tally(ca.parse(_b(AIX))["items"])
    # 合規 1 ÷（4 − 1 不適用 − 1 未查到 ＝ 2）= 50%
    assert ca.coverage(c) == 50.0
    f = ca.coverage_formula(c)
    assert "總數 4" in f and "不適用 1" in f and "未查到 1" in f, f


def test_分母為零回None不是零百分比():
    c = {"Compliant": 0, "Non-Compliant": 0, "Not-Applicable": 3, "Error": 2}
    assert ca.coverage(c) is None, "「沒有可判定的項目」跟「一條都不合規」完全不同"


def test_有完成標記才判得出完整性():
    assert ca.parse(_b(AIX))["completeness"] == "完整"
    # Linux／Windows 那兩支腳本沒有寫結尾，**不可以假裝完整，也不可以整份退掉**
    assert ca.parse(_b(LINUX))["completeness"] == "無完成標記"
    assert ca.parse(_b(WINDOWS))["completeness"] == "無完成標記"


def test_截斷的檔案要擋下來(conn):
    """宣稱 4 條、實際只有 2 條 → 少查的會被當成沒問題，所以不收。"""
    truncated = "\n".join(
        [ln for ln in AIX.split("\n") if not ln.startswith(("FCB-AIX-0018", "FCB-AIX-0091"))])
    assert ca.parse(_b(truncated))["completeness"] == "截斷"
    with pytest.raises(ValueError, match="截斷"):
        ca.import_report(conn, _b(truncated), "t.txt", "tester")
    # 失敗要留痕，而且不可以留下半套資料
    h = ca.history(conn)
    assert h and h[0]["status"] == "failed"
    assert ca.runs(conn) == []


def test_不是檢核結果檔要明確報錯():
    with pytest.raises(ValueError, match="欄位標題"):
        ca.parse(b"hello world\nnot a report\n")


def test_匯入後對得回資產(conn):
    r = ca.import_report(conn, _b(AIX), "FCB_sec01.txt", "tester")
    assert r["asset_serial"] == "A-1" and r["match_by"] in ("ip", "hostname")
    assert r["total"] == 4 and r["coverage"] == 50.0


def test_對不回資產的要列得出來而不是丟掉(conn):
    ca.import_report(conn, _b(LINUX), "linux.txt", "tester")   # 清冊裡沒有這台
    un = ca.unmatched(conn)
    assert len(un) == 1 and un[0]["hostname"] == "SECSVR198-011T"


def test_有未查到就標不可信(conn):
    ca.import_report(conn, _b(AIX), "aix.txt", "tester")
    row = ca.runs(conn)[0]
    assert row["trustworthy"] is False
    assert "沒查到" in row["untrust_reason"]


def test_清單每台只留最新一份(conn):
    ca.import_report(conn, _b(AIX), "a1.txt", "tester")
    ca.import_report(conn, _b(AIX), "a2.txt", "tester")
    rows = ca.runs(conn)
    assert len(rows) == 1 and rows[0]["file_name"] == "a2.txt"
    # 但歷史兩筆都要在——匯過幾次要查得到
    assert len(ca.history(conn)) == 2


def test_總覽數字對得起來(conn):
    ca.import_report(conn, _b(AIX), "aix.txt", "tester")
    ca.import_report(conn, _b(LINUX), "linux.txt", "tester")
    s = ca.summary(conn)
    assert s["hosts"] == 2
    assert s["unmatched"] == 1          # Linux 那台不在清冊
    assert s["untrustworthy"] == 1      # AIX 那台有 Error
    assert s["no_end_marker"] == 1      # Linux 沒有結尾標記
    assert sum(p["hosts"] for p in s["by_platform"]) == s["hosts"], "分平台加總要等於總數"


# ── 2026-09-21 追加：四欄拆解與匯出 ─────────────────────────────────────
# 使用者貼了 RHEL 實際輸出，指出行內慣例是「說明句子，( 建議值 )」，
# 目前值是「無異常: ( 實際值 )」。三個平台共用同一套，不為 AIX 另立規則。

def test_建議值只在括號緊接逗號時才抓():
    """「…5 次以下 (包含 5)」的句末括號是補充語不是建議值。

    硬抓會得到「包含 5」，正確答案其實是「0 以上，5 次以下」。
    **判不出來要留白**——給錯的建議值比沒有更糟，人會照著錯的去改設定。
    """
    e, d = ca.split_expected("這項原則設定決定使用者帳號之密碼可包含的最少字元數，( 12個字元以上 )")
    assert e == "12個字元以上" and d.endswith("最少字元數")

    e2, d2 = ca.split_expected(
        "這項原則設定決定使用者帳號被鎖定的嘗試登入失敗次數，"
        "( /etc/security/faillock.conf ) 應設定為 0 以上，5 次以下 (包含 5)")
    assert e2 == "", f"抓到 {e2!r}——那是補充語不是建議值，寧可留白"

    e3, d3 = ca.split_expected("為每個使用者帳號設定唯一的UID，以提供適當的存取防護")
    assert e3 == "" and d3.endswith("存取防護")


def test_目前值拆不出來要原樣回傳不是空字串():
    assert ca.observed_value("無異常: ( minlen = 8)") == "minlen = 8"
    # 拆不出來就原樣回——回空字串會讓畫面看起來像「沒量到」
    assert ca.observed_value("無異常") == "無異常"
    assert ca.observed_value("-rw-r----- 1 root system 953 auditd.conf").startswith("-rw-")


def test_匯出要吐原始檔全文不是重組(conn):
    """DYN 照字串比對，重組會差空白。所以存原文、匯出原文。"""
    ca.import_report(conn, _b(AIX), "aix.txt", "tester")
    out = ca.export_raw(conn)
    assert "Check summary:" in out
    assert "FCB-AIX-0001;Compliant;" in out
    # 逐字相同：原檔每一列都要在匯出結果裡
    for line in AIX.strip().split("\n"):
        assert line in out, f"匯出漏了：{line[:40]}"


# ═══ 截斷的 AIX 檔（2026-09-23 於 221 實測抓到）═══════════════════

def _aix_lines(n_rows: int, with_tail: bool) -> bytes:
    head = [
        "系統組態檢查", "平台: AIX", "主機名: HOST-A", "IP地址: 192.0.2.16",
        "作業系統版本: 7200-05-09-2446", "核心版本: AIX 7.2",
        "腳本版本: SV.TEST", "Check stated at: 2026-09-23 14:00:01",
        "TWGCB-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值",
    ]
    rows = [f"FCB-AIX-{i:04d};Compliant;帳號管理;第 {i} 項;建議值為 3;minother=3"
            for i in range(1, n_rows + 1)]
    tail = ([f"Check ended at: 2026-09-23 14:03:22",
             f"Check summary: 合計 {n_rows} 項；Compliant {n_rows}；"
             f"Non-Compliant 0；Not-Applicable 0；Error 0"] if with_tail else [])
    return ("\n".join(head + rows + tail) + "\n").encode("utf-8")


def test_aix檔沒有收尾就是中途死掉_不可以收(conn):
    """⚠️ 這條防的是整個系統最危險的一種假象。

    2026-09-23 於 221 實測：`fcbaixsh` 跑到第 57 條死掉的檔案被收下來，
    而且因為前 57 條剛好都合規，畫面顯示 **100% 合規**——
    一台跑到一半掛掉的機器看起來完美無缺。

    原本的截斷判定要「有 Check summary 可以比對」才成立，
    但腳本中途死掉時本來就寫不到結尾 -> 沒東西可比 -> 放行。
    **越嚴重的截斷越擋不住。**

    fcbaixsh 的檔頭自己保證「收尾一定有這兩行，沒有就是中途死掉」，
    這條規則就是把那個保證用起來。
    """
    with pytest.raises(ValueError) as e:
        ca.import_report(conn, _aix_lines(57, with_tail=False), file_name="t.txt")
    msg = str(e.value)
    assert "截斷" in msg
    assert "57" in msg, "要講清楚只收到幾條，不然人不知道斷在哪"
    assert "Check summary" in msg, "要講依據是什麼"


def test_aix檔有收尾而且對得上就收(conn):
    r = ca.import_report(conn, _aix_lines(98, with_tail=True), file_name="t.txt")
    assert r["total"] == 98
    assert r["completeness"] == "完整"


def test_windows沒有收尾是正常的_不可以一起擋掉(conn):
    """Linux／Windows 的腳本本來就不寫 Check summary。

    拿 AIX 那條規則去擋它們，會把正常的檔案整批退掉——
    修一個假象不可以製造另一個。
    """
    blob = ("系統組態檢查\n平台: Windows\n主機名: WIN-1\nIP地址: 192.0.2.20\n"
            "TWGCB-ID;檢查結果;角色;類別;原則設定名稱;標準設定值;目前值\n"
            "TWGCB-01-012-0001;Compliant;Common;帳戶原則;第一項;建議值為 X;X\n"
            ).encode("utf-8")
    r = ca.import_report(conn, blob, file_name="w.txt")
    assert r["completeness"] == "無完成標記"


def test_兩種截斷的訊息要分得出來(conn):
    """下一步都是「重跑」，但『怎麼看出來的』不一樣——

    使用者要靠那個判斷「是不是我操作有問題」。
    """
    with pytest.raises(ValueError) as e1:
        ca.import_report(conn, _aix_lines(57, with_tail=False), file_name="a.txt")
    head = ("系統組態檢查\n平台: AIX\n主機名: HOST-A\nIP地址: 192.0.2.16\n"
            "TWGCB-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值\n")
    rows = "".join(f"FCB-AIX-{i:04d};Compliant;X;第 {i} 項;建議 3;3\n" for i in range(1, 11))
    blob = (head + rows + "Check summary: 合計 98 項；Compliant 10；"
            "Non-Compliant 0；Not-Applicable 0；Error 0\n").encode("utf-8")
    with pytest.raises(ValueError) as e2:
        ca.import_report(conn, blob, file_name="b.txt")
    assert "沒有收尾" in str(e1.value)
    assert "宣稱的條數" in str(e2.value)
