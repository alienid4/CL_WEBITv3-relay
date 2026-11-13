"""Windows 連線收集（2026-10-26）：服務收集同一次 WinRM 執行多帶 ESTABLISHED 連線，給 observed_deps 推上下游。

釘住的事：
- 腳本：Get-NetTCPConnection -State Listen,Established（主路徑）；失敗才退 .NET IPGlobalProperties
  （行程內、不起子行程，印 PORTSRC=dotnet）。不用 netstat.exe（起子行程＝EDR 偵測樣式）。
  一樣走 stdin／PSRP 的唯讀腳本（SOC 規則由 test_agent_scripts_security 自動掃 SERVICES_PS）
- 解析：ESTAB 行跟 ss 同格式、共用 parse_connections／summarize_connections；inbound 看綁定位址；
  IPv6、::ffff: 映射、迴路都處理；IIS 段不算
- 寫入：走 observed_deps.store（同上限、每台 commit）；Windows 勾選閘（win_onboard.collect_block）擋住就不登入
- 涵蓋率：現役機器（machine_key）依 OS 大類、累積天數、跨月底；數字＝下鑽清單
例子 IP 一律 10.99.x。
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import observed_deps as od  # noqa: E402
import service_collector as sc  # noqa: E402
import service_inventory  # noqa: E402
import winrm_collector  # noqa: E402

FAKE_PW = "FAKE-TEST-PW-not-real-2468"

# SERVICES_PS 主路徑（Get-NetTCPConnection）的輸出樣子：SVC、LISTEN、ESTAB、IIS 段
WIN_OUT_CIM = """SRC=winrm
SVC\tMSSQLSERVER\tSQL Server (MSSQLSERVER)\tRunning\tAuto\tNT Service\\MSSQLSERVER\t2840\t"C:\\Program Files\\Microsoft SQL Server\\sqlservr.exe"\t0\tdb\tMicrosoft Corporation
LISTEN 0 0 0.0.0.0:1433 *:* users:(("sqlservr (MSSQLSERVER)"))
LISTEN 0 0 :::1433 *:* users:(("sqlservr (MSSQLSERVER)"))
LISTEN 0 0 10.99.40.10:8443 *:* users:(("java"))
LISTEN 0 0 0.0.0.0:5985 *:* users:(("System"))
LISTEN 0 0 0.0.0.0:3389 *:* users:(("svchost (TermService)"))
ESTAB 0 0 10.99.40.10:1433 10.99.41.20:52001
ESTAB 0 0 10.99.40.10:1433 10.99.41.20:52002
ESTAB 0 0 [::ffff:10.99.40.10]:1433 [::ffff:10.99.41.21]:52003
ESTAB 0 0 10.99.40.11:8443 10.99.41.22:50000
ESTAB 0 0 10.99.40.10:50123 10.99.42.30:445
ESTAB 0 0 10.99.40.10:5985 10.99.200.1:61000
ESTAB 0 0 127.0.0.1:1433 127.0.0.1:52010
ESTAB 0 0 [::1]:49674 [::1]:445
ESTAB 0 0 [fe80::10%12]:445 [fe80::20%12]:50001
@@IIS
IIS=1
ESTAB 0 0 10.99.40.10:80 10.99.43.1:1
"""

# .NET 退路的輸出（沒有行程名）
WIN_OUT_DOTNET = """SRC=winrm
LISTEN 0 0 0.0.0.0:1433 *:*
LISTEN 0 0 [::]:1433 *:*
ESTAB 0 0 10.99.40.10:1433 10.99.41.20:52001
ESTAB 0 0 [::ffff:10.99.40.10]:1433 [::ffff:10.99.41.21]:52003
PORTSRC=dotnet
"""


# ═══ 腳本 ═══════════════════════════════════════════════════════════

def _ports_block() -> str:
    s = winrm_collector.SERVICES_PS
    return s[s.index("function W3Pn"):s.index('"@@IIS"')]


def test_腳本_同一次執行收監聽與已建立_退路不起子行程():
    blk = _ports_block()
    assert "Get-NetTCPConnection -State Listen,Established -ErrorAction Stop" in blk
    assert blk.index("Get-NetTCPConnection") < blk.index("IPGlobalProperties"), ".NET 只能是退路"
    assert '"PORTSRC=dotnet"' in blk
    assert "netstat" not in blk.lower(), "不可以起 netstat.exe（子行程）"
    # 舊的 LISTEN 行格式一字不差（Linux 解析器共用）
    assert '"LISTEN 0 0 $($c.LocalAddress):$($c.LocalPort) *:* " + (W3Pn $c.OwningProcess)' in blk
    # 只送一段腳本：collect_services 一次 _run
    calls = []
    winrm_collector.collect_services("10.99.40.10", "u", FAKE_PW,
                                     runner=lambda h, script: calls.append(script) or "")
    assert len(calls) == 1 and "Established" in calls[0]


def _pwsh():
    return shutil.which("powershell") or shutil.which("pwsh")


def _run_ps(script: str) -> list[str]:
    """本機跑一段（只讀、跑的是假資料）。腳本當參數傳（Unicode 不經主控台字碼頁），輸出強制 UTF-8。"""
    exe = _pwsh()
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command",
                        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return [x for x in r.stdout.splitlines() if x.strip()]


# Get-NetTCPConnection 物件（ConvertTo-Json 取樣後改成 10.99.x）
NETTCP_JSON = [
    {"LocalAddress": "0.0.0.0", "LocalPort": 1433, "RemoteAddress": "0.0.0.0", "RemotePort": 0,
     "State": "Listen", "OwningProcess": 999999},
    {"LocalAddress": "::", "LocalPort": 1433, "RemoteAddress": "::", "RemotePort": 0,
     "State": "Listen", "OwningProcess": 999999},
    {"LocalAddress": "10.99.40.10", "LocalPort": 1433, "RemoteAddress": "10.99.41.20", "RemotePort": 52001,
     "State": "Established", "OwningProcess": 999999},
    {"LocalAddress": "::ffff:10.99.40.10", "LocalPort": 1433, "RemoteAddress": "::ffff:10.99.41.21",
     "RemotePort": 52003, "State": "Established", "OwningProcess": 999999},
    {"LocalAddress": "fe80::10%12", "LocalPort": 445, "RemoteAddress": "fe80::20%12", "RemotePort": 50001,
     "State": "Established", "OwningProcess": 999999},
]

#: .NET 退路用的假 IPGlobalProperties（Add-Member 造的物件，方法名跟真的一樣）
FAKE_GP = r"""$gp = New-Object psobject
$gp | Add-Member ScriptMethod GetActiveTcpListeners {
  @([pscustomobject]@{Address='0.0.0.0'; Port=1433}, [pscustomobject]@{Address='::'; Port=1433})
}
$gp | Add-Member ScriptMethod GetActiveTcpConnections {
  @([pscustomobject]@{State='Established'; LocalEndPoint=[pscustomobject]@{Address='10.99.40.10'; Port=1433};
                      RemoteEndPoint=[pscustomobject]@{Address='10.99.41.20'; Port=52001}},
    [pscustomobject]@{State='TimeWait'; LocalEndPoint=[pscustomobject]@{Address='10.99.40.10'; Port=50999};
                      RemoteEndPoint=[pscustomobject]@{Address='10.99.42.30'; Port=445}},
    [pscustomobject]@{State='Established'; LocalEndPoint=[pscustomobject]@{Address='::ffff:10.99.40.10'; Port=1433};
                      RemoteEndPoint=[pscustomobject]@{Address='::ffff:10.99.41.21'; Port=52003}})
}"""
GP_LINE = "$gp = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()"


@pytest.mark.skipif(not _pwsh(), reason="沒有 PowerShell（CI 的 Linux）")
def test_腳本實跑_GetNetTCPConnection物件轉成ss格式():
    data = json.dumps(NETTCP_JSON).replace("'", "''")
    blk = _ports_block().replace("Get-NetTCPConnection -State Listen,Established -ErrorAction Stop",
                                 f"(ConvertFrom-Json '{data}')")
    out = _run_ps(winrm_collector._PS_ERR_FN + "\n$byPid=@{}\n" + blk)
    assert out == [
        "LISTEN 0 0 0.0.0.0:1433 *:* ",
        "LISTEN 0 0 :::1433 *:* ",
        "ESTAB 0 0 10.99.40.10:1433 10.99.41.20:52001",
        "ESTAB 0 0 [::ffff:10.99.40.10]:1433 [::ffff:10.99.41.21]:52003",
        "ESTAB 0 0 [fe80::10%12]:445 [fe80::20%12]:50001",
    ]
    conns = sc.parse_connections("\n".join(out))
    assert [(c["local_ip"], c["peer_ip"]) for c in conns] == [("10.99.40.10", "10.99.41.20"),
                                                             ("10.99.40.10", "10.99.41.21")]


@pytest.mark.skipif(not _pwsh(), reason="沒有 PowerShell（CI 的 Linux）")
def test_腳本實跑_NET退路():
    blk = _ports_block().replace("Get-NetTCPConnection -State Listen,Established -ErrorAction Stop",
                                 'throw "no NetTCPIP"')
    assert GP_LINE in blk
    blk = blk.replace(GP_LINE, FAKE_GP)
    out = _run_ps(winrm_collector._PS_ERR_FN + "\n$byPid=@{}\n" + blk)
    assert out == [
        "LISTEN 0 0 0.0.0.0:1433 *:* ",
        "LISTEN 0 0 [::]:1433 *:* ",
        "PORTSRC=dotnet",
        "ESTAB 0 0 10.99.40.10:1433 10.99.41.20:52001",
        "ESTAB 0 0 [::ffff:10.99.40.10]:1433 [::ffff:10.99.41.21]:52003",
    ]


@pytest.mark.skipif(not _pwsh(), reason="沒有 PowerShell（CI 的 Linux）")
def test_腳本實跑_兩條都拿不到_回報ports錯誤():
    blk = _ports_block().replace("Get-NetTCPConnection -State Listen,Established -ErrorAction Stop",
                                 'throw "Access is denied"').replace(GP_LINE, 'throw "also gone"')
    out = _run_ps(winrm_collector._PS_ERR_FN + "\n$byPid=@{}\n" + blk)
    errs = winrm_collector.parse_errors("\n".join(out))
    assert [(e["item"], e["kind"]) for e in errs] == [("ports", "DENIED")]


@pytest.mark.skipif(not _pwsh(), reason="沒有 PowerShell（CI 的 Linux）")
def test_腳本實跑_NET退路監聽有_連線那段失敗_報connections不報ports():
    """審查 A：監聽拿到了、GetActiveTcpConnections 才丟例外 → 照用監聽＋PORTSRC，另報 connections 的錯；
    不可以退回主路徑的 ports DENIED（那會讓畫面以為監聽埠也沒收到）。"""
    gp = FAKE_GP.split("$gp | Add-Member ScriptMethod GetActiveTcpConnections")[0] + \
        "$gp | Add-Member ScriptMethod GetActiveTcpConnections { throw 'Access is denied' }"
    blk = _ports_block().replace("Get-NetTCPConnection -State Listen,Established -ErrorAction Stop",
                                 'throw "Access is denied"').replace(GP_LINE, gp)
    out = _run_ps(winrm_collector._PS_ERR_FN + "\n$byPid=@{}\n" + blk)
    assert out[:3] == ["LISTEN 0 0 0.0.0.0:1433 *:* ", "LISTEN 0 0 [::]:1433 *:* ", "PORTSRC=dotnet"]
    text = "\n".join(out)
    errs = winrm_collector.parse_errors(text)
    assert [e["item"] for e in errs] == ["connections"]
    got = sc.parse_windows_services(text)
    assert sorted(p["port"] for p in got["ports"]) == [1433] and got["connections"] == []
    assert winrm_collector.explain_errors(got["errors"])[0].startswith("已建立連線")


@pytest.mark.skipif(not _pwsh(), reason="沒有 PowerShell（CI 的 Linux）")
def test_腳本實跑_已建立連線超過上限_截斷並印TRUNC():
    """審查 B：腳本端最多送回 WIN_ESTAB_CAP 條（測試把上限改成 2），其餘略過、印 TRUNC 行。"""
    assert winrm_collector.WIN_ESTAB_CAP == 20000
    est = [{"LocalAddress": "10.99.40.10", "LocalPort": 1433, "RemoteAddress": f"10.99.41.{i}",
            "RemotePort": 50000 + i, "State": "Established", "OwningProcess": 999999} for i in range(1, 6)]
    data = json.dumps(NETTCP_JSON[:1] + est).replace("'", "''")
    blk = _ports_block().replace("Get-NetTCPConnection -State Listen,Established -ErrorAction Stop",
                                 f"(ConvertFrom-Json '{data}')")
    assert "cap = 20000" in blk
    blk = blk.replace("cap = 20000", "cap = 2")
    out = _run_ps(winrm_collector._PS_ERR_FN + "\n$byPid=@{}\n" + blk)
    assert [x for x in out if x.startswith("ESTAB")] == [
        "ESTAB 0 0 10.99.40.10:1433 10.99.41.1:50001", "ESTAB 0 0 10.99.40.10:1433 10.99.41.2:50002"]
    assert out[-1] == "TRUNC\tconnections\t3"
    got = sc.parse_windows_services("\n".join(out))
    assert got["connections_dropped"] == 3 and got["connections_truncated"] == 3
    assert len(got["connections"]) == 2


# ═══ 解析 ═══════════════════════════════════════════════════════════

def test_解析_主路徑_連線與方向_IIS段不算():
    got = sc.parse_windows_services(WIN_OUT_CIM)
    assert sorted(p["port"] for p in got["ports"]) == [1433, 3389, 5985, 8443]
    rows = {(r["direction"], r["local_port"], r["peer_ip"], r["peer_port"]): r["count"]
            for r in got["connections"]}
    assert rows == {
        ("inbound", 1433, "10.99.41.20", 0): 2,
        ("inbound", 1433, "10.99.41.21", 0): 1,          # ::ffff: 映射正規化成 IPv4
        ("outbound", 0, "10.99.41.22", 50000): 1,        # 8443 只綁 10.99.40.10，從 .11 出去＝outbound
        ("outbound", 0, "10.99.42.30", 445): 1,
        ("inbound", 5985, "10.99.200.1", 0): 1,          # WinRM 收集本身：存起來，推論時當管理埠丟
    }
    assert got["ports_source"] == "cim" and got["connections_truncated"] == 0


def test_解析_NET退路標得出來():
    got = sc.parse_windows_services(WIN_OUT_DOTNET)
    assert got["ports_source"] == "dotnet"
    assert {(r["direction"], r["peer_ip"]) for r in got["connections"]} == {
        ("inbound", "10.99.41.20"), ("inbound", "10.99.41.21")}


def test_舊腳本沒有ESTAB行_連線是空的不是錯():
    got = sc.parse_windows_services("LISTEN 0 0 0.0.0.0:5985 *:*\n")
    assert got["connections"] == [] and got["errors"] == []


# ═══ 寫入與閘門 ═══════════════════════════════════════════════════════

def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.set_setting(c, "collector_ip", "10.99.200.1")
    return c


def _win_env(monkeypatch, conn, raw, blocked=None):
    import credential_store
    import win_onboard

    calls = []
    monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: blocked)
    monkeypatch.setattr(credential_store, "pick_for_host", lambda *a, **k: "win-svc")
    monkeypatch.setattr(credential_store, "get_for_use", lambda *a, **k: ("svc", FAKE_PW))
    monkeypatch.setattr(credential_store, "audit_use", lambda *a, **k: None)
    monkeypatch.setattr(service_inventory, "_platform_for", lambda *a, **k: "windows")
    monkeypatch.setattr(winrm_collector, "collect_services",
                        lambda ip, u, p, **kw: calls.append(ip) or raw)
    return calls


def test_Windows服務收集順便寫進host_connection(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="W-1", hostname="winsql01", ip="10.99.40.10", collect_ok=1,
                           os="Microsoft Windows Server 2019 Standard", api_id="DB-1")
        conn.commit()
        calls = _win_env(monkeypatch, conn, WIN_OUT_CIM)
        out = service_inventory.collect_services(conn)
        assert calls == ["10.99.40.10"], "只登入一次"
        assert out["hosts"][0]["connections"] == 5
        rows = conn.execute("SELECT direction, local_port, peer_ip, peer_port, count FROM host_connection "
                            "WHERE host_ip = '10.99.40.10' ORDER BY peer_ip").fetchall()
        assert len(rows) == 5
        # 推論：收集器那條（5985）不造邊
        assert od.infer(conn)["stats"]["collector"] == 1
    finally:
        conn.close()


def test_Windows勾選閘擋住就不登入_也不寫連線(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="W-1", hostname="winsql01", ip="10.99.40.10", collect_ok=1)
        conn.commit()
        calls = _win_env(monkeypatch, conn, WIN_OUT_CIM, blocked="這台 Windows 沒有勾選納管")
        out = service_inventory.collect_services(conn)
        assert calls == []
        assert out["failed"][0]["error"] == "這台 Windows 沒有勾選納管"
        assert conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 0
    finally:
        conn.close()


def test_NET退路有一句說明(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="W-1", hostname="winsql01", ip="10.99.40.10", collect_ok=1)
        conn.commit()
        _win_env(monkeypatch, conn, WIN_OUT_DOTNET)
        out = service_inventory.collect_services(conn)
        assert any(".NET 退路" in n for n in out["hosts"][0]["notes"])
    finally:
        conn.close()


def test_主機端截斷有一句說明_收到的照存(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="W-1", hostname="winsql01", ip="10.99.40.10", collect_ok=1)
        conn.commit()
        _win_env(monkeypatch, conn, WIN_OUT_CIM.replace("@@IIS", "TRUNC\tconnections\t18000\n@@IIS"))
        out = service_inventory.collect_services(conn)
        notes = out["hosts"][0]["notes"]
        assert any("略過 18000 條" in n for n in notes)
        assert not any("組數超過上限" in n for n in notes), "主機端截斷不可以被講成 2000 組上限"
        assert conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 5
    finally:
        conn.close()


# ═══ 涵蓋率 ═══════════════════════════════════════════════════════════

def _ts(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _seed_cov(conn):
    for sn, host, ip, os_ in (("W-1", "win01", "10.99.40.1", "Microsoft Windows Server 2019"),
                              ("W-2", "win02", "10.99.40.2", "Microsoft Windows Server 2016"),
                              ("L-1", "lnx01", "10.99.50.1", "Red Hat Enterprise Linux 8.6"),
                              ("L-2", "lnx01", "10.99.50.1", "Red Hat Enterprise Linux 8.6"),   # 同一台兩筆
                              ("L-3", "lnx03", "10.99.50.3", "Red Hat Enterprise Linux 8.6"),
                              ("R-1", "old01", "10.99.50.9", "Red Hat Enterprise Linux 6")):
        db.insert_hardware(conn, asset_serial=sn, hostname=host, ip=ip, os=os_)
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'R-1'")
    conn.commit()
    row = [{"direction": "inbound", "local_port": 1433, "peer_ip": "10.99.41.20", "peer_port": 0, "count": 1}]
    od.store(conn, "10.99.40.1", "W-1", row, now=_ts(45))      # 第一次觀察：45 天前
    od.store(conn, "10.99.40.1", "W-1", row, now=_ts(1))
    od.store(conn, "10.99.50.1", "L-2", row, now=_ts(2))       # 序號是同一台的另一筆
    od.store(conn, "10.99.50.3", "L-3", row, now=_ts(40))      # 太久沒看到（預設 30 天）
    od.store(conn, "10.99.50.9", "R-1", row, now=_ts(1))       # 退役不算


def test_涵蓋率_依OS大類_台數走machine_key_退役不算(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_cov(conn)
        c = od.coverage(conn)
        assert c["total"] == 4                       # win01、win02、lnx01（兩筆一台）、lnx03
        assert c["observed_count"] == 2 and c["pct"] == 50.0
        fam = {f["family"]: f for f in c["by_family"]}
        assert len(fam) == 2
        for f in fam.values():
            assert f["observed_count"] == len(f["observed"]) and f["total"] == 2 and f["pct"] == 50.0
        assert {x["hostname"] for f in fam.values() for x in f["observed"]} == {"win01", "lnx01"}
        assert c["cumulative_days"] == 45
        assert c["month_end_crossed"] is True
        assert od.coverage(conn, days=60)["observed_count"] == 3
    finally:
        conn.close()


def test_涵蓋率_沒有觀察_累積0天_沒跨月底(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="W-1", hostname="win01", ip="10.99.40.1", os="Windows Server 2019")
        conn.commit()
        c = od.coverage(conn)
        assert (c["total"], c["observed_count"], c["pct"], c["cumulative_days"], c["month_end_crossed"]) == \
            (1, 0, 0.0, 0, False)
    finally:
        conn.close()


def test_涵蓋率_verify_numbers全過(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_cov(conn)
        import verify_numbers as vn
        before = vn.FAILS
        vn.check_observed_coverage(conn)
        assert vn.FAILS == before
    finally:
        conn.close()


def test_涵蓋率_序號對不到時_IP只對到一台才算(tmp_path):
    """審查的涵蓋率註記：序號對不到（被併回／空）時才用 IP，而且 IP 只對到一台才算有觀察。"""
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="A-1", hostname="solo01", ip="10.99.60.1", os="Windows Server 2019")
        db.insert_hardware(conn, asset_serial="B-1", hostname="vipa", ip="10.99.60.9", os="Windows Server 2019")
        db.insert_hardware(conn, asset_serial="B-2", hostname="vipb", ip="10.99.60.9", os="Windows Server 2019")
        conn.commit()
        row = [{"direction": "inbound", "local_port": 1433, "peer_ip": "10.99.41.20", "peer_port": 0, "count": 1}]
        od.store(conn, "10.99.60.1", "GONE-1", row, now=_ts(1))     # 序號不存在 → IP 唯一 → 算
        od.store(conn, "10.99.60.9", "GONE-2", row, now=_ts(1))     # IP 對到兩台 → 不猜、不算
        c = od.coverage(conn)
        assert c["total"] == 3 and c["observed_count"] == 1
        assert [x["hostname"] for f in c["by_family"] for x in f["observed"]] == ["solo01"]
    finally:
        conn.close()
