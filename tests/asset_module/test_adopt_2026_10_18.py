"""2-1 納入管理 2026-10-18 一批：上次失敗提醒／已納管可改分類／依收集到的 OS 分類／
大型主機頁簽／空位址不再混進頁簽與「只能匯入」。

IP 一律 10.99.x（範例），主機名是假的。
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import asset_classify  # noqa: E402
import collect_dispatch as cd  # noqa: E402
import collected_os  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
import onboard_eligibility  # noqa: E402
import onboard_failures as of  # noqa: E402
import os_class  # noqa: E402
import pipeline  # noqa: E402

FRONT = ROOT / "APP" / "asset-module" / "frontend"
ADOPT_VUE = (FRONT / "pages" / "adopt.vue").read_text(encoding="utf-8")
BATCH_VUE = (FRONT / "components" / "BatchOnboardModal.vue").read_text(encoding="utf-8")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, ip, os_text, hostname, collect_ok=0, **kw):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os_text, hostname=hostname,
                       environment="測試", asset_status="使用中", **kw)
    conn.execute("UPDATE hardware SET collect_ok = ? WHERE asset_serial = ?", (collect_ok, serial))
    conn.commit()


def _audit(conn, ip, ok, stage, message, trigger="manual", when="2026-10-18 20:50:00"):
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, ok, "
        "stage, message, created_at) VALUES (?, 'linux', 'sysinfra', ?, 'tester', ?, ?, ?, ?)",
        (ip, trigger, 1 if ok else 0, stage, message, when))
    conn.commit()


# ===================== 1. 上次失敗提醒 =====================

def test_短原因_各類訊息歸對():
    cases = [
        ("connect", "登入被拒——帳號或密碼不對，或該帳號不允許 SSH 登入", of.SHORT_BAD_CRED),
        ("execute", "進得去，但這個帳號不在 sudoers 裡（沒有 sudo 權限）", of.SHORT_SUDO),
        # sudo 要排在密碼前面：sudo 不接受密碼是「進得去、卡在提權」
        ("execute", "進得去，但 sudo 不接受這個密碼（登入密碼與 sudo 密碼可能不同）", of.SHORT_SUDO),
        ("execute", "進得去，但執行身分不是 root（腳本需要 root 才能建帳號）", of.SHORT_SUDO),
        ("connect", "連不到這台的 22 埠（逾時）——確認機器開著", of.SHORT_TIMEOUT),
        ("connect", "對方拒絕連線——22 埠沒有服務在聽", of.SHORT_UNREACHABLE),
        ("refused", "ESXi 不可納管，請改由 vCenter／RVTools 取得", of.SHORT_VMWARE),
        ("verify", "account_locked: true", of.SHORT_LOCKED),
        ("execute", "腳本執行未回報完成", of.SHORT_SCRIPT_PREFIX + "執行"),
    ]
    for stage, msg, want in cases:
        assert of.short_reason(stage, msg) == want, msg


def test_最後一次失敗才有標籤_後來成功就消失_撤銷不算():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-1", "10.99.18.1", "Rocky Linux 9", "app-1")
        _audit(conn, "10.99.18.1", False, "execute", "進得去，但這個帳號不在 sudoers 裡",
               when="2026-10-18 20:50:00")
        got = of.last_attempts(conn, ["10.99.18.1", "10.99.18.2"])
        assert set(got) == {"10.99.18.1"}, "沒試過的 IP 不在回傳裡（不是假的 0）"
        f = got["10.99.18.1"]["fail"]
        assert f["short"] == of.SHORT_SUDO and f["at"] == "2026-10-18 20:50:00"
        assert got["10.99.18.1"]["serial"] == "S-1"
        # 撤銷不算納管嘗試：撤銷成功不可以把上次失敗洗掉
        _audit(conn, "10.99.18.1", True, "execute", "撤銷完成", trigger="revoke",
               when="2026-10-18 21:00:00")
        assert of.last_attempts(conn, ["10.99.18.1"])["10.99.18.1"]["fail"] is not None
        # 後來任一路徑成功（這裡是 paste 自動偵測）→ 標籤消失
        _audit(conn, "10.99.18.1", True, "verify", "自動偵測到收集帳號可登入", trigger="paste",
               when="2026-10-18 21:10:00")
        assert of.last_attempts(conn, ["10.99.18.1"])["10.99.18.1"]["fail"] is None
        conn.close()


def test_前端_待納管已納管與一次納管視窗都顯示上次失敗_欄位可排序():
    assert "上次失敗" in ADOPT_VUE and "上次失敗" in BATCH_VUE
    # 兩張表的「上次失敗」欄都有 SortTh（可排序）
    assert re.search(r'<SortTh k="last_fail_at"[^>]*@sort="dToggle"', ADOPT_VUE)
    assert re.search(r'<SortTh k="last_fail_at"[^>]*@sort="mToggle"', ADOPT_VUE)
    assert re.search(r'<SortTh k="last_fail_at"', BATCH_VUE)
    # 幾百台時網址太長：一律 POST
    assert "method: 'POST'" in ADOPT_VUE.split("async function fetchLastAttempts")[1][:600]


# ===================== 2. 已納管可改分類 =====================

def test_前端_已納管主機每列有改分類_走同一個搬移視窗():
    on = ADOPT_VUE.split("v-show=\"activeTab === 'onboarded'\"")[1]
    assert "改分類" in on and "openMoveManaged(" in on
    assert "function openMoveManaged" in ADOPT_VUE


# ===================== 3. 依收集到的 OS 分類 =====================

def test_AIX納管後os被寫成oslevel_大類與OS類型都是AIX():
    """舊紅：7200-05-09-2446 → 大類「其他」、OS 類型「設備」（使用者看到的「設備」）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "DYN-10.99.18.40", "10.99.18.40", "7200-05-09-2446", "aix-40", collect_ok=1)
        c = asset_classify.classify_one(conn, "DYN-10.99.18.40")
        assert c["os_class"] == "AIX"
        assert c["os_type"] == pipeline.OS_AIX
        assert c["os_collected"] == "aix"
        conn.close()


def test_登記OS空白_規格收集用AIX指令跑過_歸AIX():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "DYN-10.99.18.41", "10.99.18.41", None, "aix-41", collect_ok=1)
        assert asset_classify.classify_one(conn, "DYN-10.99.18.41")["os_class"] == "未知"
        conn.execute("INSERT INTO host_spec (asset_serial, ip, platform, collected_at) "
                     "VALUES ('DYN-10.99.18.41', '10.99.18.41', 'aix', '2026-10-18 20:00:00')")
        conn.commit()
        c = asset_classify.classify_one(conn, "DYN-10.99.18.41")
        assert c["os_class"] == "AIX" and c["os_type"] == pipeline.OS_AIX
        conn.close()


def test_沒納管的不用收集證據_特定類別不被蓋掉():
    assert collected_os.apply_os_class("其他", None) == "其他"
    assert collected_os.apply_os_class("其他", "aix") == "AIX"
    assert collected_os.apply_os_class("Linux", "aix") == "AIX"
    # VMware 判定 fail-closed：vCenter（Photon）就算回了 Linux 字樣也不翻成 Linux
    for keep in ("虛擬化平台", "網路設備", "儲存設備", "IBM i", "管理介面"):
        assert collected_os.apply_os_class(keep, "linux") == keep
    assert collected_os.apply_os_type(pipeline.OS_ESXI, "linux") == pipeline.OS_ESXI
    assert collected_os.apply_os_type(pipeline.OS_OPENSHIFT, "linux") == pipeline.OS_OPENSHIFT
    assert collected_os.apply_os_type(pipeline.OS_APPLIANCE, "aix") == pipeline.OS_AIX


def test_人工指定仍然最優先():
    import os_override
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "DYN-10.99.18.42", "10.99.18.42", "7200-05-09-2446", "aix-42", collect_ok=1)
        os_override.set_override(conn, "aix-42", "10.99.18.42", "DYN-10.99.18.42",
                                 pipeline.OS_LINUX, pipeline.OS_AIX, "測試", "tester")
        assert asset_classify.classify_one(conn, "DYN-10.99.18.42")["os_type"] == pipeline.OS_LINUX
        conn.close()


def test_首頁長條與已納管表與漏斗_同一份結論():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "DYN-10.99.18.43", "10.99.18.43", "7200-05-09-2446", "aix-43", collect_ok=1)
        comp = manage_state.composition(conn)
        assert comp["by_os_class"].get("AIX") == 1
        assert comp["by_os_class"].get("其他", 0) == 0
        conn.close()


def test_紅線_納管資格不讀收集到的OS():
    """分類改了，納管白名單不能跟著改（白名單只讀資產登記的作業系統欄）。"""
    src = (BACKEND / "onboard_eligibility.py").read_text(encoding="utf-8")
    assert "collected_os" not in src
    src_b = (BACKEND / "batch_onboard_service.py").read_text(encoding="utf-8")
    assert "collected_os" not in src_b
    # 同一個 oslevel 字串，白名單的結論跟以前一樣（不因這次修改而變成可納管或不可納管）
    assert onboard_eligibility.classify_os("7200-05-09-2446")[0] == "appliance"


def test_oslevel格式本身也認得是AIX_FortiOS裸版本號不受影響():
    assert os_class.classify("7200-05-09-2446") == "AIX"
    assert os_class.classify("7.2.10") == "其他"


# ===================== 大型主機頁簽／空位址 =====================

def _dispatch(conn, ips, prober=None):
    calls = []

    def ssh(ip):
        calls.append(ip)
        return (False, "Permission denied")
    out = cd.run_dispatch(conn, "\n".join(ips), prober=prober or (lambda ip: [22]),
                          ssh_prober=ssh, winrm_runner=lambda *a, **k: (False, "no cred"))
    return out, calls


def test_AIX與AS400歸大型主機_AIX可佈帳號_AS400不行_SSH頁沒有它們():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-50", "10.99.18.50", "AIX 7.2", "aix-50")
        _hw(conn, "S-51", "10.99.18.51", "OS/400 V7R4", "as400-51")
        _hw(conn, "S-52", "10.99.18.52", "Rocky Linux 9", "app-52")
        for out in (_dispatch(conn, ["10.99.18.50", "10.99.18.51", "10.99.18.52"])[0],
                    cd.latest_run(conn)):
            tab = {r["ip"]: r for r in out["results"]}
            assert tab["10.99.18.50"]["tab"] == cd.TAB_MAINFRAME
            assert tab["10.99.18.50"]["mainframe_onboardable"] is True
            assert tab["10.99.18.51"]["tab"] == cd.TAB_MAINFRAME
            assert tab["10.99.18.51"]["mainframe_onboardable"] is False
            assert tab["10.99.18.51"]["status"] == cd.STATUS_NOT_ONBOARDABLE
            assert tab["10.99.18.52"]["tab"] == cd.TAB_SSH
            assert out["by_tab"][cd.TAB_MAINFRAME] == 2 and out["by_tab"][cd.TAB_SSH] == 1
            assert sum(out["by_tab"].values()) == out["total"]
        conn.close()


def test_連不上的AIX_在大型主機頁簽但不算動得了的():
    """221 實測抓到（v1.464.1）：完全沒回應的 AIX 被標成可佈帳號，會被算進「動得了的」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-55", "10.99.18.55", "AIX 7.2", "aix-55")
        out, _ = _dispatch(conn, ["10.99.18.55"], prober=lambda ip: None)
        r = out["results"][0]
        assert r["tab"] == cd.TAB_MAINFRAME and r["status"] == cd.STATUS_IMPORT_ONLY
        assert r["mainframe_onboardable"] is False
        conn.close()


def test_空位址自成一格_只能匯入只算有登記的():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-60", "10.99.18.60", "Rocky Linux 9", "app-60")
        out, _ = _dispatch(conn, ["10.99.18.60", "10.99.18.61", "10.99.18.62"],
                           prober=lambda ip: None)
        tab = {r["ip"]: r["tab"] for r in out["results"]}
        assert tab == {"10.99.18.60": cd.TAB_IMPORT, "10.99.18.61": cd.TAB_EMPTY,
                       "10.99.18.62": cd.TAB_EMPTY}
        assert out["by_tab"][cd.TAB_EMPTY] == out["empty_addresses"] == 2
        assert out["by_tab"][cd.TAB_IMPORT] == 1
        conn.close()


def test_搬到大型主機_往返一致_人工指定IBMi照樣擋不試連():
    import os_override
    assert cd.TAB_MAINFRAME in cd.MOVABLE_TABS
    assert cd.tab_for_os_type(cd.os_type_for_tab(cd.TAB_MAINFRAME)) == cd.TAB_MAINFRAME
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "S-70", "10.99.18.70", "Rocky Linux 9", "box-70")
        os_override.set_override(conn, "box-70", "10.99.18.70", "S-70", pipeline.OS_IBMI,
                                 pipeline.OS_LINUX, "其實是 AS400", "tester")
        out, calls = _dispatch(conn, ["10.99.18.70"])
        r = out["results"][0]
        assert r["tab"] == cd.TAB_MAINFRAME
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE and calls == []
        conn.close()


def test_大型主機分組放設定檔不寫死():
    g = cd.mainframe_group()
    assert set(g["classes"]) == {"AIX", "IBM i"} and g["onboardable_classes"] == ("AIX",)
    src = (BACKEND / "collect_dispatch.py").read_text(encoding="utf-8")
    assert 'classes": ["AIX"' not in src


def test_前端_空位址與探測位址卡拿掉_隱藏已納管_結果篩選():
    assert "pickStatus('total')" not in ADOPT_VUE
    assert "pickStatus('empty')" not in ADOPT_VUE
    assert "隱藏已納管" in ADOPT_VUE
    for chip in ("未納管", "已納管", "上次失敗"):
        assert chip in ADOPT_VUE
    # v1.468.4：搜尋只在目前頁簽，「總」＝目前頁簽的列數
    assert "顯示 {{ dVisible.length }} ／ 總 {{ dScoped.length }}" in ADOPT_VUE
    assert "'mainframe'" in ADOPT_VUE
