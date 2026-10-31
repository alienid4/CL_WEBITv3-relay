"""天條：ESXi 不可納管、不可在 ESXi 上建任何帳號（使用者 2026-10-17，backlog ESXI-NO-ONBOARD）。

守的是「任何路徑都不會對 ESXi 送 bootstrap」：
- 引擎（onboard）連上後探測到 ESXi（uname -s=VMkernel 或 vmware -v）→ 拒絕，不產腳本、不跑執行器
- 批次（batch_auto_onboard）探測到 ESXi → 不呼叫 onboard
- 排程（auto_onboard）資產登記已寫 ESXi → 候選階段就跳過，連執行器都不碰
- 已經被納管的 ESXi 只列出來（onboarded_esxi），不自動刪
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auto_onboard as ao  # noqa: E402
import db  # noqa: E402
import onboard_eligibility as oe  # noqa: E402
import onboard_engine as eng  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"
COLLECTOR = "10.99.0.221"
HOST = "10.99.7.11"

# 兩個訊號各一份真實長相的探測輸出
RAW_UNAME = "VMkernel\n0\nHASSUDO\n"
RAW_VMWARE_V = "\n0\nHASSUDO\nVMware ESXi 6.7.0 build-17700523\n"


def test_探測指令有問_vmware_v():
    assert "vmware -v" in eng.PROBE_COMMAND


def test_parse_probe_兩個訊號都認得出ESXi():
    assert eng.parse_probe(RAW_UNAME)["os"] == "esxi"
    assert eng.parse_probe(RAW_VMWARE_V)["os"] == "esxi"
    # 一般 Linux 不受影響（vmware -v 不存在時輸出是空的）
    assert eng.parse_probe("Linux\n0\nHASSUDO\n")["os"] == "linux"


def _fake_ssh_layer(monkeypatch, probe_raw):
    """模擬 SSH 層：記下每一條送出去的遠端指令；任何執行器／產腳本都算 bootstrap。"""
    sent, side_effects = [], []

    def fake_exec(host, username, password, command, stdin_text=None, timeout=20, **kw):
        sent.append(command)
        return 0, probe_raw

    monkeypatch.setattr(eng, "_ssh_exec", fake_exec)
    monkeypatch.setattr(eng, "_ssh_executor",
                        lambda **kw: side_effects.append("executor") or
                        eng.OnboardResult(True, "execute", "ok", "完成。"))
    monkeypatch.setattr(eng, "build_script",
                        lambda *a, **k: side_effects.append("build_script") or "echo")
    return sent, side_effects


def test_引擎_uname為VMkernel就拒絕_沒有送出任何bootstrap(monkeypatch):
    sent, side = _fake_ssh_layer(monkeypatch, RAW_UNAME)
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR, pubkey=PUBKEY)
    assert r.ok is False
    assert r.stage == eng.ESXI_REFUSED_STAGE
    assert "ESXi 不可納管" in r.message
    assert sent == [eng.PROBE_COMMAND], "只准送唯讀探測，不准送任何納管指令"
    assert side == [], "ESXi 不准產腳本、不准跑執行器（建帳號／佈金鑰／sudoers）"


def test_引擎_vmware_v訊號也拒絕(monkeypatch):
    sent, side = _fake_ssh_layer(monkeypatch, RAW_VMWARE_V)
    r = eng.onboard(HOST, "aix", "root", "x", COLLECTOR, pubkey=PUBKEY)
    assert r.ok is False and r.stage == eng.ESXI_REFUSED_STAGE
    assert sent == [eng.PROBE_COMMAND] and side == []


def test_引擎_注入探測時即使執行器是假的也照樣守門():
    calls = []
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR, pubkey=PUBKEY,
                    executor=lambda **kw: calls.append(kw) or eng.OnboardResult(True, "x", "ok"),
                    probe_runner=lambda h, u, p: RAW_UNAME)
    assert r.ok is False and r.stage == eng.ESXI_REFUSED_STAGE
    assert calls == []


def test_引擎_呼叫端直接給esxi平台也拒絕():
    calls = []
    r = eng.onboard(HOST, "esxi", "root", "x", COLLECTOR, pubkey=PUBKEY,
                    executor=lambda **kw: calls.append(kw))
    assert r.ok is False and r.stage == eng.ESXI_REFUSED_STAGE and calls == []


def test_批次_探測到ESXi就不納管也不改密碼(monkeypatch):
    onboard_calls, passwd_calls, engine_calls = [], [], []
    real_onboard = eng.onboard
    monkeypatch.setattr(eng, "onboard",
                        lambda **kw: engine_calls.append(kw) or real_onboard(**kw))
    rows = eng.batch_auto_onboard(
        [{"ip": HOST}], "root", ["pwA", "pwB"], COLLECTOR,
        account_of=lambda p: "webit3scan", pubkey=PUBKEY, unify_password=True,
        probe_runner=lambda h, u, p: RAW_VMWARE_V,
        onboard_executor=lambda **kw: onboard_calls.append(kw),
        passwd_executor=lambda **kw: passwd_calls.append(kw))
    r = rows[0]
    assert r["onboarded"] is False
    assert r["fail_stage"] == eng.ESXI_REFUSED_STAGE
    assert "ESXi" in r["fail_reason"]
    assert onboard_calls == [] and passwd_calls == []
    assert engine_calls == [], "批次探測已認出 ESXi，連 onboard() 都不該進"


# ===== 排程自動納管：登記為 ESXi 的候選階段就跳過 =====

def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_排程_登記為ESXi的未納管主機直接跳過_不碰執行器():
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="ESX-1", ip=HOST, hostname="esx-lab-01",
                               os="VMware ESXi 6.7", collect_ok=0,
                               environment="測試", asset_status="使用中")
            conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) "
                         "VALUES ('2026-10-17 01:00:00', ?, NULL, 1, '22,443')", (HOST,))
            conn.commit()
            ao.save_segment(conn, "10.99.7.")
            r = ao.run_auto_onboard(
                conn, COLLECTOR, pubkey=PUBKEY,
                executor=lambda **kw: calls.append(kw) or eng.OnboardResult(True, "x", "ok"),
                cred_lookup=lambda ip: ("lab", "root", "pw"))
            assert r["candidates"] == 1
            assert r["skipped"] == 1 and r["onboarded"] == 0
            assert "ESXi 不可納管" in r["details"][0]["message"]
            assert calls == [], "登記為 ESXi 的不准送到引擎"
        finally:
            conn.close()


# ===== 已被納管的 ESXi：只列出來，不刪 =====

def test_onboarded_esxi_只列出已納管或有帳號的ESXi():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="ESX-ON", ip="10.99.7.21", hostname="esx-on",
                               os="VMware ESXi 6.7", collect_ok=1)
            db.insert_hardware(conn, asset_serial="ESX-OFF", ip="10.99.7.22", hostname="esx-off",
                               os="VMware ESXi 7.0", collect_ok=0)
            db.insert_hardware(conn, asset_serial="LNX-ON", ip="10.99.7.23", hostname="lnx-on",
                               os="Red Hat Enterprise Linux 8", collect_ok=1)
            got = oe.onboarded_esxi(conn)
            assert [g["asset_serial"] for g in got] == ["ESX-ON"]
            # 唯讀：資產列一筆都沒少
            assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 3
        finally:
            conn.close()


# ===== fail-closed：探測不確定就不納管（審查 2026-10-17 SEVERE）=====

import pytest  # noqa: E402


@pytest.mark.parametrize("probe", [
    {"os": "", "uid": None, "has_sudo": False},                     # 輸出空白
    {"os": "", "uid": None, "has_sudo": False, "error": "連線逾時"},   # 探測逾時
    {"os": "", "uid": 0, "has_sudo": True},                         # 認不出平台
    {"os": "", "uid": None, "has_sudo": False, "error": "連不上 22"},  # 連線錯誤
])
def test_探測失敗一律不送bootstrap(monkeypatch, probe):
    side = []
    monkeypatch.setattr(eng, "probe_target", lambda *a, **k: dict(probe))
    monkeypatch.setattr(eng, "_ssh_executor",
                        lambda **kw: side.append("executor") or
                        eng.OnboardResult(True, "execute", "ok", "完成。"))
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR, pubkey=PUBKEY)
    assert r.ok is False and "探測失敗" in r.message
    assert side == [], "不知道這台是什麼就不准送納管腳本"


def test_探測輸出空白_走真的parse也擋(monkeypatch):
    sent, side = _fake_ssh_layer(monkeypatch, "")
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR, pubkey=PUBKEY)
    assert r.ok is False and side == [] and sent == [eng.PROBE_COMMAND]


# ===== 最後一道：腳本本身認出 ESXi 就離開，且在任何變更之前 =====

_CHANGE_MARKERS = ("useradd", "mkuser", "sudoers", "authorized_keys", "New-LocalUser",
                   "ansible.builtin.user")


def _assert_guard_first(text, guard):
    gi = text.find(guard)
    assert gi >= 0, "腳本缺 ESXi 自我守門"
    firsts = [text.find(m) for m in _CHANGE_MARKERS if text.find(m) >= 0]
    assert firsts and gi < min(firsts), "ESXi 守門必須在任何建帳號／sudoers／金鑰步驟之前"


@pytest.mark.parametrize("platform", ["linux", "aix"])
def test_shell腳本開頭就擋ESXi(platform):
    text = eng.build_script(platform, PUBKEY, COLLECTOR)
    _assert_guard_first(text, eng.ESXI_SCRIPT_GUARD_SH)
    assert "VMkernel" in eng.ESXI_SCRIPT_GUARD_SH and "exit 3" in eng.ESXI_SCRIPT_GUARD_SH


def test_windows腳本開頭就擋ESXi():
    _assert_guard_first(eng.build_script("windows", PUBKEY, COLLECTOR), eng.ESXI_SCRIPT_GUARD_PS)


def test_playbook第一個task就擋ESXi():
    text = eng.build_linux_playbook(PUBKEY, COLLECTOR)
    # 檔頭註解會提到 sudoers／authorized_keys，所以只比 tasks: 之後的實際步驟
    _assert_guard_first(text[text.index("tasks:"):], "VMkernel")
    assert "exit 3" in text


# ===== 排程跳過 ESXi 要留下理由（審查 LOW）=====

def test_排程跳過ESXi_明細與稽核都看得到判定依據():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="ESX-2", ip="10.99.7.12", hostname="esx-lab-02",
                               os="VMware ESXi 7.0", collect_ok=0,
                               environment="測試", asset_status="使用中")
            conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) "
                         "VALUES ('2026-10-17 01:00:00', '10.99.7.12', NULL, 1, '22')")
            conn.commit()
            ao.save_segment(conn, "10.99.7.")
            r = ao.run_auto_onboard(conn, COLLECTOR, pubkey=PUBKEY,
                                    executor=lambda **kw: None,
                                    cred_lookup=lambda ip: ("lab", "root", "pw"))
            msg = r["details"][0]["message"]
            assert "依據" in msg and "VMware ESXi 7.0" in msg
            row = conn.execute("SELECT * FROM onboard_audit WHERE target_ip='10.99.7.12'").fetchone()
            assert row is not None and row["stage"] == eng.ESXI_REFUSED_STAGE and row["ok"] == 0
        finally:
            conn.close()
