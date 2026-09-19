"""納管漏斗：每台機器落在哪一關。

使用者 2026-08-16：「300 台測試機要一台一台匯，我至少要知道哪些是我還需要處理的。」

這頁能不能信，全看兩件事：
1. **互斥窮盡**——每台剛好落一關，各關加總 = 母體。對不起來就是有 bug，
   而不是「大概差不多」。這是稽核看板的信任基礎。
2. **關卡判定跟四態一致**——四態沿用 manage_state，不另外算一套。
   同一件事兩處各算一次，遲早算出不同答案，然後兩個畫面互相打臉。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import pipeline  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _scan(conn, ip, when="2026-08-16 01:00:00"):
    conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok) VALUES (?,?,1)", (when, ip))
    conn.commit()


def _asset(conn, serial, ip, **kw):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, environment="正式",
                       asset_status="使用中", **kw)


def _service(conn, serial, ip):
    conn.execute("INSERT INTO host_service (asset_serial, ip, proto, port, source) "
                 "VALUES (?,?,'tcp',22,'test')", (serial, ip))
    conn.commit()


def _account(conn, serial, ip):
    conn.execute("INSERT INTO host_account (asset_serial, ip, username) VALUES (?,?,'root')",
                 (serial, ip))
    conn.commit()


def test_每台剛好落一關_加總等於母體():
    """互斥窮盡是這頁可信的前提。任何一台落在兩關、或哪一關都不落，
    畫面上的數字就再也對不起來。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for i, ip in enumerate(["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"], 1):
            _scan(conn, ip)
            if i > 1:
                _asset(conn, f"A-{i}", ip, collect_ok=1 if i > 2 else 0)
        _asset(conn, "A-LOST", "10.0.0.9", collect_ok=1)      # 沒掃到 → 失聯

        out = pipeline.summarize(conn)
        assert out["reconcile"]["ok"] is True
        assert out["reconcile"]["sum_of_stages"] == out["total"]
        assert len(out["items"]) == out["total"]
        conn.close()


def test_關卡依序推進():
    """一台機器隨著資料越收越多，關卡要往後走，不能卡住也不能跳關。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.5")

        def stage_of():
            return {i["ip"]: i["stage"] for i in pipeline.summarize(conn)["items"]}["10.0.0.5"]

        assert stage_of() == "unregistered"          # 掃到但沒登記

        _asset(conn, "A-5", "10.0.0.5", collect_ok=0)
        assert stage_of() == "not_onboarded"         # 登記了，進不去

        conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial='A-5'")
        conn.commit()
        assert stage_of() == "no_facts"              # 進得去，但事實還沒收到

        conn.execute("UPDATE hardware SET os='Rocky Linux 9' WHERE asset_serial='A-5'")
        conn.commit()
        assert stage_of() == "no_services"           # 有事實，沒服務

        _service(conn, "A-5", "10.0.0.5")
        assert stage_of() == "no_accounts"           # 有服務，沒帳號

        _account(conn, "A-5", "10.0.0.5")
        assert stage_of() == "complete"              # 齊全
        conn.close()


def test_失聯是終點不是中間關卡():
    """登記卻掃不到，問題是「這台還在不在」，不是「還沒收資料」——
    不該被歸到某個收集關卡然後叫人去收。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.1")                       # 有掃描紀錄，但不含 A-X
        _asset(conn, "A-X", "10.0.0.99", collect_ok=1, os="Rocky Linux 9")
        out = pipeline.summarize(conn)
        by_ip = {i["ip"]: i for i in out["items"]}
        assert by_ip["10.0.0.99"]["stage"] == "lost"
        assert by_ip["10.0.0.99"]["tone"] == "bad"
        conn.close()


def test_序號機型收不到不算卡在事實那關():
    """序號/機型多半要 root 才讀得到，唯讀收集帳號常常拿不到（已知取捨）。
    要求全有的話幾乎所有機器都會永遠卡在同一關，那個數字就不再有意義。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.6")
        _asset(conn, "A-6", "10.0.0.6", collect_ok=1, os="AIX 7.2")   # 只有 OS
        by_ip = {i["ip"]: i for i in pipeline.summarize(conn)["items"]}
        assert by_ip["10.0.0.6"]["stage"] == "no_services"
        conn.close()


def test_每一關都講得出下一步():
    """「還需要處理」的每一關都要有可執行的下一步，否則使用者知道卡住也不知道要幹嘛。"""
    for s in pipeline.STAGES:
        if s["key"] == "complete":
            continue
        assert s["next"] and len(s["next"]) > 5, f"{s['key']} 沒有下一步說明"


def test_待辦數等於母體減去齊全的():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for ip in ("10.0.0.1", "10.0.0.2"):
            _scan(conn, ip)
        _asset(conn, "A-2", "10.0.0.2", collect_ok=1, os="Rocky Linux 9")
        _service(conn, "A-2", "10.0.0.2")
        _account(conn, "A-2", "10.0.0.2")
        out = pipeline.summarize(conn)
        assert out["complete"] == 1
        assert out["todo"] == out["total"] - out["complete"]
        conn.close()


def test_收集結果表不存在也不會炸():
    """舊 DB、或那個功能還沒開的環境，不該讓整頁 500。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        conn.execute("DROP TABLE IF EXISTS host_service")
        conn.commit()
        _scan(conn, "10.0.0.1")
        _asset(conn, "A-1", "10.0.0.1", collect_ok=1, os="Rocky Linux 9")
        out = pipeline.summarize(conn)
        assert out["reconcile"]["ok"] is True
        conn.close()


def test_空資料庫不會炸也不會亂報():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out = pipeline.summarize(conn)
        assert out["total"] == 0 and out["todo"] == 0
        assert out["reconcile"]["ok"] is True
        conn.close()


# ===== OS 類型分流（Linux/Windows/AIX）=====

def test_os_type_純函式分類():
    """OS 類型判定：登記的看 os 字串（[B-02] 以 classify_os 為準）、沒 OS 字串才看埠且標「推測」。"""
    assert pipeline._os_type("Rocky Linux 9.7", None) == "Linux"
    assert pipeline._os_type("Windows Server 2019", None) == "Windows"
    assert pipeline._os_type("AIX 7.2", None) == "AIX"
    # 未登記（無 os 字串）→ 靠埠推測，結果要標明是推測，不可以跟登記的混成同一類
    assert pipeline._os_type(None, "22") == "推測 Linux 類"
    assert pipeline._os_type(None, "3389,445") == "推測 Windows"
    assert pipeline._os_type(None, "5985") == "推測 Windows"
    assert pipeline._os_type(None, "") == "未填"
    assert pipeline._os_type("N/A", None) == "未填"


def test_os_type_不是Linux的不可以落進Linux():
    """[B-02] 221 實測統計的 Linux 2255 筆裡有 1127 筆不是 Linux（設備 880、OpenShift 171、
    ESXi 76）。以前的黑名單「不是 AIX／Windows 就當 Linux」會把這些全算成 Linux。"""
    assert pipeline._os_type("VMware ESXi 7.0.3", None) == "VMware"
    assert pipeline._os_type("Red Hat Enterprise Linux CoreOS 4.12", None) == "OpenShift 節點"
    # 不是 Linux 的設備各歸各類（網路設備／BMC／IBM i，沿用首頁平台規則）
    assert pipeline._os_type("Cisco IOS-XE 17.3", None) == "網路設備"
    assert pipeline._os_type("Dell iDRAC 9", None) == "管理韌體(BMC)"
    assert pipeline._os_type("IBM i V7R3", None) == "IBM i"
    assert pipeline._os_type("電力設備", None) == "設備"
    # 規格：不可以反過來放寬白名單——明顯的 Linux 仍要認得
    for lx in ("Red Hat Enterprise Linux 8.10", "CentOS 7.9", "Oracle Linux 8"):
        assert pipeline._os_type(lx, None) == "Linux", lx


def test_summarize帶os_type欄位與os_counts():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.1")
        _asset(conn, "A-1", "10.0.0.1", collect_ok=1, os="Rocky Linux 9")
        _scan(conn, "10.0.0.2")
        _asset(conn, "A-2", "10.0.0.2", collect_ok=1, os="Windows Server 2022")
        out = pipeline.summarize(conn)
        by_ip = {r["ip"]: r["os_type"] for r in out["items"]}
        assert by_ip["10.0.0.1"] == "Linux"
        assert by_ip["10.0.0.2"] == "Windows"
        assert out["os_counts"]["Linux"] >= 1 and out["os_counts"]["Windows"] >= 1
        # os_counts 加總 = 總台數（每台剛好歸一類）
        assert sum(out["os_counts"].values()) == out["total"]
        conn.close()


def test_os_type_Storage與SAN獨立一類_含OS空白看設備機型():
    """2026-09-18 使用者：HP SAN Switch、IBM Storage、EMC Storage、Synology「還是分到 storage/san」。

    OS 空白時看設備機型——那是 CIA 登記的資料，不是推測，所以不標「推測」。
    判斷式跟分佈統計的 Storage 分群共用 system_stats.STORAGE_RE。
    """
    for model in ("HP SAN Switch", "IBM Storage-FS5300", "IBM San Switch", "EMC Storage", "Synology"):
        assert pipeline._os_type(None, None, model) == "Storage/SAN", model
        assert pipeline._os_type("N/A", "22", model) == "Storage/SAN", "設備機型是登記資料，優先於埠推測"
    # OS 欄本身寫了儲存字樣（認不出的設備）也歸 Storage/SAN
    assert pipeline._os_type("StorageSAN_SW", None) == "Storage/SAN"
    # 伺服器與 VM 不可以被誤歸
    assert pipeline._os_type(None, None, "HPE DL380 Gen10") == "未填"
    assert pipeline._os_type(None, "22", "(VM)") == "推測 Linux 類"
    # 認不出來、也不是儲存／網路／BMC 的，仍是設備
    assert pipeline._os_type("電力設備", None) == "設備"
    # OS 認不出來（設備）但機型是儲存 → 也算（221 實際漏網的兩種）
    assert pipeline._os_type("FOS v9", None, "EMC SAN Switch") == "Storage/SAN"
    assert pipeline._os_type("Avamar - 客製OS", None, "EMC Storage") == "Storage/SAN"
    # 有真的 OS 的，不被設備機型蓋掉
    assert pipeline._os_type("Red Hat Enterprise Linux 8", None, "EMC Storage") == "Linux"


def test_os_type_網路設備_BMC_IBMi_沿用首頁平台規則():
    """2026-09-18 使用者：「是不是還要一個網路設備，switch f5 等」。
    沿用 manage_state._PLATFORM_RULES（首頁平台統計那套），不另寫規則。"""
    cases = {
        ("17.03.04b", "Cisco C9200L-48P-4X"): "網路設備",
        ("7.2.10", "Fortinet FG-61F"): "網路設備",
        ("8.10.0.17", "Aruba AP 515"): "網路設備",
        ("網路設備", None): "網路設備",
        ("Idrac 7.10.70.00", "DELL R760"): "管理韌體(BMC)",
        ("V7R3", "IBM AS400"): "IBM i",
    }
    for (os_, model), want in cases.items():
        assert pipeline._os_type(os_, None, model) == want, (os_, model)
    # OS 空白、設備機型寫明是網路設備 → 登記資料，不是推測
    assert pipeline._os_type(None, "22", "Cisco C9200L-48T-4G") == "網路設備"
    # ⚠️ SAN Switch 一定是 Storage/SAN，不可以因為有 switch 被歸網路設備（使用者 2026-09-10）
    assert pipeline._os_type("FOS v9", None, "EMC SAN Switch") == "Storage/SAN"
    assert pipeline._os_type(None, None, "HP SAN Switch") == "Storage/SAN"
    # 真的 OS 不被設備機型蓋掉
    assert pipeline._os_type("Windows Server 2019", None, "Cisco UCS") == "Windows"


def test_補的網路設備關鍵字_不影響Windows上的同名軟體():
    """221：Paloalto／FG-101F／WS-C3850／PulseSecure／ixia／Riverbed 原本掉在未知。
    但「VM-Riverbed NetIM」這種是跑在 Windows VM 上的軟體，OS 是 Windows 就必須還是 Windows。"""
    import manage_state as ms
    for model in ("Paloalto PA-3260", "FG-101F", "WS-C3850-48T", "PulseSecure PSA-3000",
                  "ixia TAP-SW-IXIA-SYS-E10S-16", "riverbed 3800"):
        assert pipeline._os_type("9.1.9", None, model) == "網路設備", model
    assert pipeline._os_type("Microsoft Windows Server 2016", None, "VM-Riverbed NetIM") == "Windows"
    assert ms.platform_of("Microsoft Windows Server 2016", "VM-Riverbed NetIM") == "Windows"


def test_os_type_OS與機型都認不出_最後看名稱():
    """2026-09-18 使用者：vCenter、VROPS「屬於 VM」→ VMware 類；Aruba_Airwave「屬於網路」。
    這幾台 OS 是 N/A、設備機型只寫 (VM)，線索只在主機名／資產名稱／用途。"""
    # names＝(主機名, 資產名稱)；不看用途欄
    assert pipeline._os_type("N/A", None, "(VM)", ("vCenter", "虛擬化平台")) == "VMware"
    assert pipeline._os_type("N/A", None, "(VM)", ("VROPS", "監控告警系統")) == "VMware"
    assert pipeline._os_type("N/A", None, "(VM)", ("N/A", "虛擬化平台")) == "VMware", "N-207 使用者點名歸 VMware"
    assert pipeline._os_type("N/A", None, "(VM)", ("Aruba_Airwave", "防毒防護系統")) == "網路設備"
    assert pipeline._os_type("N/A", None, None, ("x", "儲存設備")) == "Storage/SAN"
    # 名稱只是最後手段：OS 或機型認得出來就不看名稱
    assert pipeline._os_type("Red Hat Enterprise Linux 8", None, None, ("vcenter-proxy",)) == "Linux"
    # 短字不可以誤中（名稱不用 ios／f5／ilo 這種短字）
    assert pipeline._os_type(None, None, None, ("silo-db01",)) == "未填"
    assert pipeline._os_type(None, None, None, ("bios-test",)) == "未填"
    assert pipeline._os_type(None, None, None, ("secsvr-f5-web",)) == "未填"


def test_名稱判斷不看用途欄():
    """221 實例：用途寫「GCP-Storage Transfer Service」的雲端服務、「EMC SRM」儲存管理軟體，
    都不是儲存設備。pipeline 只把 (主機名, 資產名稱) 交給名稱判斷。"""
    import inspect
    src = inspect.getsource(pipeline)
    assert '(row["hostname"], row["asset_name"])' in src
    assert "asset_purpose\"]) if row is not None" not in src
