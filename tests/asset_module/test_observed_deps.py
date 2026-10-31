"""方案 B（2026-10-22）：用觀察到的 TCP 連線推「哪個系統呼叫哪個系統」。

釘住的事：
- 收集：同一份 ss／netstat 輸出多留 ESTAB 列；LISTEN 的解析不變（ESTAB 不可混進服務數）
- AIX netstat（PCB 開頭、點分隔埠號）的 ESTABLISHED 解析得出來
- 方向：本機端是自己在聽的位址（埠＋綁定位址）＝inbound，其餘＝outbound；臨時埠不留
- 寫入：同一組累加 count、更新 last_seen；超過上限刪最久沒看到的；每台寫完就 commit
- 推論：inbound 來自 T 的連線到 S 的主機 ⇒ T 依賴 S；同系統、同一台、迴路、管理埠、收集器、
  排除清單、模稜兩可的 IP 不造邊但計數；對方對不到 APID 另計未知來源；超過門檻天數的不算
- 樞紐系統：列為直接受影響，但不經過它擴散；連線推論邊最多走 observed_deps_max_depth 段
- 影響範圍：沒有觀察資料時跟 fc31903 完全一樣（fixtures 基準）
- 系統地圖：逐系統規則（有人工登記用人工，否則連線推論），每列帶 source 與證據
例子 IP 一律 10.99.x。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius as br  # noqa: E402
import ci_graph  # noqa: E402
import db  # noqa: E402
import observed_deps as od  # noqa: E402
import service_collector as sc  # noqa: E402

BASELINE = Path(__file__).parent / "fixtures" / "observed_deps_baseline_fc31903.json"
COLLECTOR = "10.99.200.1"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """收集器位址固定（不讓跑測試那台的網卡位址混進來）、每個測試清快取。"""
    monkeypatch.setattr(od, "_LOCAL_IPS", set())
    od.invalidate()
    yield
    od.invalidate()


# ss -tanp | grep -Ei 'listen|estab' 的樣子（沒有 root：沒有 users: 欄）
SS_ALL = """LISTEN 0      128          0.0.0.0:22         0.0.0.0:*
LISTEN 0      128          0.0.0.0:1521       0.0.0.0:*
ESTAB  0      0         10.99.1.10:1521    10.99.2.20:40001
ESTAB  0      0         10.99.1.10:1521    10.99.2.20:40002
ESTAB  0      0   [::ffff:10.99.1.10]:1521 [::ffff:10.99.2.21]:40003
ESTAB  0      0         10.99.1.10:51000   10.99.5.5:443
ESTAB  0      0          127.0.0.1:1521     127.0.0.1:40009
ESTAB  0      0     [fe80::1%eth0]:22     [fe80::2%eth0]:50000
"""

NETSTAT_ALL = """tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1234/sshd
tcp        0      0 10.99.1.10:22           10.99.2.20:51234        ESTABLISHED 1235/sshd
tcp        0      0 10.99.1.10:47000        10.99.3.3:8080          ESTABLISHED 900/app-listener
"""

AIX_ALL = """f1000e0001d0b3b8 tcp4       0      0  *.22               *.*                LISTEN
f1000e0001d0b3c0 tcp4       0      0  *.1521             *.*                LISTEN
f1000e0004c3e222 tcp4       0      0  10.99.1.10.1521    10.99.2.20.5566    ESTABLISHED
f1000e0004c3e333 tcp4       0      0  10.99.1.10.33000   10.99.5.5.443      ESTABLISHED
f1000e0004c3e444 tcp        0      0  127.0.0.1.1521     127.0.0.1.40000    ESTABLISHED"""

WILD = [("0.0.0.0", 22), ("0.0.0.0", 1521)]


# ═══ 收集 ═══════════════════════════════════════════════════════════

def test_指令只改旗標與grep_仍唯讀不提權():
    lin = sc.LINUX_CMDS["listen"]
    assert "ss -tanp" in lin and "netstat -tanp" in lin and "ss -tan " in lin
    assert "grep -Ei 'listen|estab'" in lin
    aix = sc.AIX_CMDS["listen"]
    assert aix == "netstat -Aan 2>/dev/null | grep -Ei 'listen|estab'"
    for cmd in (lin, aix):
        for bad in ("sudo", "base64", "sshpass", ">", "rm ", "kill"):
            assert bad not in cmd.replace("2>/dev/null", ""), f"{bad!r} 不該出現在收集指令裡"


def test_ss_LISTEN解析不變_ESTAB不混進服務():
    ports = sorted(r["port"] for r in sc.parse_listen(SS_ALL))
    assert ports == [22, 1521]


def test_ss_保留ESTAB_去迴路與link_local_v4mapped正規化():
    conns = sc.parse_connections(SS_ALL)
    got = sorted((c["local_ip"], c["local_port"], c["peer_ip"], c["peer_port"]) for c in conns)
    assert got == [
        ("10.99.1.10", 1521, "10.99.2.20", 40001),
        ("10.99.1.10", 1521, "10.99.2.20", 40002),
        ("10.99.1.10", 1521, "10.99.2.21", 40003),
        ("10.99.1.10", 51000, "10.99.5.5", 443),
    ]


def test_linux_netstat_行程名含listen的ESTAB不可以變成監聽埠():
    assert [r["port"] for r in sc.parse_listen(NETSTAT_ALL)] == [22]
    conns = sc.parse_connections(NETSTAT_ALL)
    assert {(c["peer_ip"], c["peer_port"]) for c in conns} == {("10.99.2.20", 51234), ("10.99.3.3", 8080)}


def test_aix_netstat_ESTABLISHED解析得出來():
    assert sorted(r["port"] for r in sc.parse_listen(AIX_ALL)) == [22, 1521]
    conns = sc.parse_connections(AIX_ALL)
    assert sorted((c["local_port"], c["peer_ip"], c["peer_port"]) for c in conns) == [
        (1521, "10.99.2.20", 5566), (33000, "10.99.5.5", 443)]


def test_方向判定與彙總_臨時埠不留():
    s = sc.summarize_connections(sc.parse_connections(SS_ALL), WILD)
    rows = {(r["direction"], r["local_port"], r["peer_ip"], r["peer_port"]): r["count"] for r in s["rows"]}
    assert rows == {
        ("inbound", 1521, "10.99.2.20", 0): 2,      # 兩條連線、兩個臨時埠 → 一組 count=2
        ("inbound", 1521, "10.99.2.21", 0): 1,
        ("outbound", 0, "10.99.5.5", 443): 1,
    }
    assert s["truncated"] == 0


def test_inbound要對得上綁定位址_不是只看埠():
    """#7：8080 只綁在 10.99.1.10；另一張卡 10.99.1.11 上用 8080 當臨時埠連出去＝outbound。"""
    listens = [("10.99.1.10", 8080), ("::", 22)]
    assert sc.is_inbound("10.99.1.10", 8080, listens) is True
    assert sc.is_inbound("10.99.1.11", 8080, listens) is False
    assert sc.is_inbound("10.99.1.11", 22, listens) is True          # 萬用位址
    conns = [{"local_ip": "10.99.1.11", "local_port": 8080, "peer_ip": "10.99.9.1", "peer_port": 443}]
    assert sc.summarize_connections(conns, listens)["rows"][0]["direction"] == "outbound"


def test_彙總超過上限_留連線數最多的並回報截掉幾組():
    conns = [{"local_ip": "10.99.1.10", "local_port": 1521, "peer_ip": f"10.99.4.{i}", "peer_port": 1}
             for i in range(1, 6)] + [{"local_ip": "10.99.1.10", "local_port": 1521,
                                       "peer_ip": "10.99.4.1", "peer_port": 2}]
    s = sc.summarize_connections(conns, [("0.0.0.0", 1521)], cap=2)
    assert len(s["rows"]) == 2 and s["truncated"] == 3
    assert s["rows"][0]["peer_ip"] == "10.99.4.1" and s["rows"][0]["count"] == 2


def test_collect_回傳connections_同一條指令():
    sent = []

    def run(host, cmd):
        sent.append(cmd)
        return "" if "systemctl" in cmd else SS_ALL
    r = sc.collect(run, "10.99.1.10", "linux")
    assert len(sent) == 2, "不可以為了連線多送指令"
    assert {x["direction"] for x in r["connections"]} == {"inbound", "outbound"}
    assert r["connections_truncated"] == 0


# ═══ 寫入 ═══════════════════════════════════════════════════════════

def _conn(tmp_path, name="t.db"):
    p = tmp_path / name
    db.init_db(p)
    c = db.get_connection(p)
    db.set_setting(c, "collector_ip", COLLECTOR)
    return c


def test_寫入_同一組累加count更新last_seen(tmp_path):
    conn = _conn(tmp_path)
    try:
        row = [{"direction": "inbound", "local_port": 1521, "peer_ip": "10.99.2.20", "peer_port": 0, "count": 2}]
        assert od.store(conn, "10.99.1.10", "HW-A", row, now="2026-10-20 10:00:00")["added"] == 1
        assert od.store(conn, "10.99.1.10", "HW-A", row, now="2026-10-21 10:00:00")["updated"] == 1
        r = conn.execute("SELECT count, first_seen, last_seen FROM host_connection").fetchone()
        assert (r["count"], r["first_seen"], r["last_seen"]) == (4, "2026-10-20 10:00:00", "2026-10-21 10:00:00")
    finally:
        conn.close()


def test_寫入_每台寫完就commit_不抱寫入鎖(tmp_path):
    """#9：另一條連線馬上看得到，代表 store 自己 commit 了。"""
    conn = _conn(tmp_path)
    other = db.get_connection(tmp_path / "t.db")
    try:
        od.store(conn, "10.99.1.10", "HW-A", [{"direction": "outbound", "local_port": 0,
                                              "peer_ip": "10.99.4.1", "peer_port": 443, "count": 1}])
        assert conn.in_transaction is False
        assert other.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 1
    finally:
        other.close()
        conn.close()


def test_寫入_超過每台上限刪最久沒看到的(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        monkeypatch.setattr(od, "MAX_ROWS_PER_HOST", 2)
        for i, ts in ((1, "2026-10-01 00:00:00"), (2, "2026-10-02 00:00:00"), (3, "2026-10-03 00:00:00")):
            od.store(conn, "10.99.1.10", "HW-A", [{"direction": "outbound", "local_port": 0,
                                                   "peer_ip": f"10.99.4.{i}", "peer_port": 443, "count": 1}], now=ts)
        left = [r[0] for r in conn.execute("SELECT peer_ip FROM host_connection ORDER BY peer_ip")]
        assert left == ["10.99.4.2", "10.99.4.3"]
    finally:
        conn.close()


# ═══ 推論 ═══════════════════════════════════════════════════════════

def _now(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _hw(conn, serial, host, ip, api=None, name=None, **kw):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, api_id=api,
                       asset_name=name, environment=kw.pop("environment", "正式"), **kw)


def _seed(conn):
    """S＝核心交易（主機 core01、core02），T＝下單前台（web01），U＝報表（rpt01），noapi01 沒填 APID。"""
    _hw(conn, "HW-A", "core01", "10.99.1.10", "S-1", "核心交易", availability=3)
    _hw(conn, "HW-A2", "core02", "10.99.1.11", "S-1", "核心交易")
    _hw(conn, "HW-B", "web01", "10.99.2.20", "T-1", "下單前台")
    _hw(conn, "HW-C", "rpt01", "10.99.3.30", "U-1", "報表")
    _hw(conn, "HW-D", "noapi01", "10.99.4.40")
    conn.commit()
    now = _now()
    od.store(conn, "10.99.1.10", "HW-A", [
        {"direction": "inbound", "local_port": 1521, "peer_ip": "10.99.2.20", "peer_port": 0, "count": 3},
        {"direction": "inbound", "local_port": 1521, "peer_ip": "10.99.1.11", "peer_port": 0, "count": 1},
        {"direction": "inbound", "local_port": 1521, "peer_ip": "10.99.9.9", "peer_port": 0, "count": 1},
        {"direction": "inbound", "local_port": 1521, "peer_ip": "10.99.4.40", "peer_port": 0, "count": 1},
        {"direction": "inbound", "local_port": 22, "peer_ip": "127.0.0.1", "peer_port": 0, "count": 1},
    ], now=now)
    # web01 自己那端看到的：outbound 到 core01:1521（同一條關係的另一端，T→S 第二筆證據）
    od.store(conn, "10.99.2.20", "HW-B", [
        {"direction": "outbound", "local_port": 0, "peer_ip": "10.99.1.10", "peer_port": 1521, "count": 2},
    ], now=now)
    # rpt01 很久以前連過 core01（超過門檻天數，不算）
    od.store(conn, "10.99.3.30", "HW-C", [
        {"direction": "outbound", "local_port": 0, "peer_ip": "10.99.1.10", "peer_port": 1521, "count": 9},
    ], now=_now(40))


def test_推論_inbound與outbound都推出T依賴S_證據帶齊(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = od.infer(conn)
        assert [(e["caller"], e["callee"]) for e in r["edges"]] == [("T-1", "S-1")]
        e = r["edges"][0]
        assert e["evidence_count"] == 2 and e["observations"] == 5      # #6 觀察次數，兩端各算
        assert e["hosts"] == ["10.99.1.10"] and e["ports"] == [1521]
        assert len(od.edge_rows(conn, "T-1", "S-1")) == e["evidence_count"]
        assert r["stats"]["same_system"] == 1          # core02 → core01 同系統不造邊
        assert r["stats"]["ignored"] == 1              # 127.0.0.1
        assert r["unknown_callers"]["S-1"] == {"rows": 2, "peers": ["10.99.4.40", "10.99.9.9"]}
    finally:
        conn.close()


def test_推論_超過門檻天數的不算_門檻可設定(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        assert ("U-1", "S-1") not in {(e["caller"], e["callee"]) for e in od.infer(conn)["edges"]}
        db.set_setting(conn, od.STALE_KEY, "60")
        assert ("U-1", "S-1") in {(e["caller"], e["callee"]) for e in od.infer(conn)["edges"]}
        db.set_setting(conn, od.STALE_KEY, "壞掉")
        assert od.stale_days(conn) == od.DEFAULT_STALE_DAYS
    finally:
        conn.close()


def test_推論_同一台的兩筆登記用machine_key_不算跨系統(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "M1", "dual01", "10.99.6.1", "S-1")
        _hw(conn, "M2", "dual01", "10.99.6.1", "T-1")
        conn.commit()
        od.store(conn, "10.99.6.1", "M1", [
            {"direction": "inbound", "local_port": 80, "peer_ip": "10.99.6.1", "peer_port": 0, "count": 1}],
            now=_now())
        r = od.infer(conn)
        assert r["edges"] == [] and r["stats"]["same_machine"] == 1
    finally:
        conn.close()


def _two(conn):
    _hw(conn, "HW-A", "core01", "10.99.1.10", "S-1", "核心交易")
    _hw(conn, "HW-B", "web01", "10.99.2.20", "T-1", "下單前台")
    conn.commit()


def test_管理埠與收集器自己的連線不造邊_有計數(tmp_path):
    """RDP、WinRM 一律不是業務呼叫；收集器自己 SSH 進來（任何埠）也不是。"""
    conn = _conn(tmp_path)
    try:
        _two(conn)
        rows = [{"direction": "inbound", "local_port": p, "peer_ip": "10.99.2.20", "peer_port": 0, "count": 1}
                for p in (3389, 5985, 5986)]
        rows += [{"direction": "inbound", "local_port": p, "peer_ip": COLLECTOR, "peer_port": 0, "count": 1}
                 for p in (22, 1521)]
        od.store(conn, "10.99.1.10", "HW-A", rows)
        r = od.infer(conn)
        assert r["edges"] == []
        assert r["stats"]["infra"] == 3 and r["stats"]["collector"] == 2
    finally:
        conn.close()


def test_SSH22_系統間SFTP造邊_跳板與排除清單才丟(tmp_path):
    """22 拆開判：系統間 SFTP／SCP 批次是真依賴（造邊、標 SSH/SFTP）；對方是跳板／PAM
    （observed_deps_admin_peers）或在排除清單才丟，而且有計數。埠清單可設定。"""
    conn = _conn(tmp_path)
    try:
        _two(conn)
        _hw(conn, "HW-J", "jump01", "10.99.9.50", "PAM-1", "跳板")
        conn.commit()
        od.store(conn, "10.99.1.10", "HW-A", [
            {"direction": "inbound", "local_port": 22, "peer_ip": ip, "peer_port": 0, "count": 1}
            for ip in ("10.99.2.20", "10.99.9.50")])
        r = od.infer(conn)
        assert {(e["caller"], e["callee"]) for e in r["edges"]} == {("T-1", "S-1"), ("PAM-1", "S-1")}
        e = next(x for x in r["edges"] if x["caller"] == "T-1")
        assert e["protocols"] == ["SSH/SFTP"]
        ci_graph.rebuild(conn)
        m = br.system_map(conn, "S-1")
        assert {d["dep_type"] for d in m["depended_by"]} == {"連線推論（SSH/SFTP）"}
        db.set_setting(conn, od.ADMIN_PEERS_KEY, "10.99.9.0/24")
        r = od.infer(conn)
        assert [(x["caller"], x["callee"]) for x in r["edges"]] == [("T-1", "S-1")]
        assert r["stats"]["admin_ssh"] == 1
        # 埠清單可設定：把 22 放進去就一律當管理埠
        db.set_setting(conn, od.INFRA_PORTS_KEY, "22, 3389")
        r = od.infer(conn)
        assert r["edges"] == [] and r["stats"]["infra"] == 2
        assert r["settings"]["infra_ports"] == [22, 3389]
        db.set_setting(conn, od.INFRA_PORTS_KEY, "亂填")
        assert 22 not in od.infer(conn)["settings"]["infra_ports"]
    finally:
        conn.close()


def test_排除清單_IP_CIDR_APID_被排除的有計數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _two(conn)
        _hw(conn, "HW-M", "mon01", "10.99.7.5", "MON-1", "監控")
        _hw(conn, "HW-P", "pam01", "10.99.8.5", "PAM-1", "跳板")
        conn.commit()
        od.store(conn, "10.99.1.10", "HW-A", [
            {"direction": "inbound", "local_port": 1521, "peer_ip": ip, "peer_port": 0, "count": 1}
            for ip in ("10.99.7.5", "10.99.8.5", "10.99.2.20")])
        assert len(od.infer(conn)["edges"]) == 3
        db.set_setting(conn, od.EXCLUDE_KEY, "10.99.7.0/24, PAM-1")
        r = od.infer(conn)
        assert [(e["caller"], e["callee"]) for e in r["edges"]] == [("T-1", "S-1")]
        assert r["stats"]["excluded"] == 2
    finally:
        conn.close()


def test_IP對到好幾台而且APID不同_放進模稜兩可不造邊(tmp_path):
    """#3：同一個 IP 登記在兩台不同系統的主機上（共用 IP／VIP／舊登記）——不猜。"""
    conn = _conn(tmp_path)
    try:
        _two(conn)
        _hw(conn, "HW-X", "vipa", "10.99.8.8", "X-1")
        _hw(conn, "HW-Y", "vipb", "10.99.8.8", "Y-1")
        _hw(conn, "HW-Z1", "z01", "10.99.8.9", "Z-1")
        _hw(conn, "HW-Z2", "z02", "10.99.8.9", "Z-1")     # 同系統的 VIP：不算模稜兩可
        conn.commit()
        od.store(conn, "10.99.1.10", "HW-A", [
            {"direction": "inbound", "local_port": 1521, "peer_ip": ip, "peer_port": 0, "count": 1}
            for ip in ("10.99.8.8", "10.99.8.9")])
        r = od.infer(conn)
        assert [(e["caller"], e["callee"]) for e in r["edges"]] == [("Z-1", "S-1")]
        assert r["stats"]["ambiguous"] == 1
        assert r["ambiguous"] == [{"ip": "10.99.8.8", "rows": 1, "apis": ["X-1", "Y-1"]}]
    finally:
        conn.close()


# ═══ 影響範圍：樞紐、深度、逐系統規則 ═════════════════════════════════

def _hub_world(conn):
    """A（主機 HA）＋ 12 個系统 X01..X12 ＋ 監控 M。
    M 去輪詢每個系統（M → X:10050，所以 M 依賴每個 X），每個系統的 agent 又推資料給 M（X → M:10051，
    X 依賴 M）。再加收集器 SSH 進 HA 的那一條。沒有樞紐規則的話：HA 掛 → A → M → 全部。"""
    _hw(conn, "HA", "ha01", "10.99.1.1", "A-1", "系統A")
    _hw(conn, "HM", "mon01", "10.99.7.1", "M-1", "監控")
    for i in range(1, 13):
        _hw(conn, f"HX{i}", f"x{i:02d}", f"10.99.3.{i}", f"X{i:02d}", f"系統X{i}")
    conn.commit()
    targets = [("10.99.1.1", "HA")] + [(f"10.99.3.{i}", f"HX{i}") for i in range(1, 13)]
    for ip, serial in targets:
        od.store(conn, ip, serial, [
            {"direction": "inbound", "local_port": 10050, "peer_ip": "10.99.7.1", "peer_port": 0, "count": 1},
            {"direction": "outbound", "local_port": 0, "peer_ip": "10.99.7.1", "peer_port": 10051, "count": 1}])
    od.store(conn, "10.99.1.1", "HA", [
        {"direction": "inbound", "local_port": 22, "peer_ip": COLLECTOR, "peer_port": 0, "count": 1}])
    ci_graph.rebuild(conn)


def test_樞紐系統_列為直接受影響但不經過它擴散(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hub_world(conn)
        r = od.infer(conn)
        assert [h["api_id"] for h in r["hubs"]] == ["M-1"]
        res = br.impact(conn, "hw:HA", depth=6)
        ids = {d["node_id"] for d in res["dependents"]}
        assert ids == {"bizsys:A-1", "bizsys:M-1"}, "HA 掛只影響 A（＋監控直接受影響），不可以經監控擴散到全部"
        assert set(x["api_id"] for x in res["summary"]["by_biz_system"]) == {"M-1"}
        assert [h["api_id"] for h in res["observed"]["hubs"]] == ["M-1"]
        # 門檻調高就不是樞紐了 → 會經過它擴散（證明上面是樞紐規則擋的，不是沒有邊）
        db.set_setting(conn, od.HUB_MIN_KEY, "50")
        res2 = br.impact(conn, "hw:HA", depth=6)
        assert len({d["node_id"] for d in res2["dependents"]}) == 14
    finally:
        conn.close()


def test_連線推論邊的層數上限(tmp_path):
    """B 呼叫 A、C 呼叫 B、D 呼叫 C：預設最多走 2 段 → HA 掛影響 A、B、C，D 不算；設成 3 才到 D。"""
    conn = _conn(tmp_path)
    try:
        for sid, ip in (("A", "10.99.1.1"), ("B", "10.99.1.2"), ("C", "10.99.1.3"), ("D", "10.99.1.4")):
            _hw(conn, f"H{sid}", f"h{sid.lower()}", ip, f"{sid}-1", f"系統{sid}")
        conn.commit()
        for callee, caller in (("10.99.1.1", "10.99.1.2"), ("10.99.1.2", "10.99.1.3"), ("10.99.1.3", "10.99.1.4")):
            serial = "H" + "ABCD"[int(callee[-1]) - 1]
            od.store(conn, callee, serial, [{"direction": "inbound", "local_port": 8080, "peer_ip": caller,
                                            "peer_port": 0, "count": 1}])
        ci_graph.rebuild(conn)
        ids = {d["node_id"] for d in br.impact(conn, "hw:HA", depth=10)["dependents"]}
        assert ids == {"bizsys:A-1", "bizsys:B-1", "bizsys:C-1"}
        db.set_setting(conn, od.MAX_DEPTH_KEY, "3")
        ids = {d["node_id"] for d in br.impact(conn, "hw:HA", depth=10)["dependents"]}
        assert "bizsys:D-1" in ids
    finally:
        conn.close()


def test_影響範圍_主機掛_經連線推論走到呼叫它的系統(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ci_graph.rebuild(conn)
        res = br.impact(conn, "hw:HW-A", depth=6)
        ids = {d["node_id"] for d in res["dependents"]}
        assert {"bizsys:S-1", "bizsys:T-1"} <= ids
        t = next(d for d in res["dependents"] if d["node_id"] == "bizsys:T-1")
        assert t["edge_type"] == od.EDGE_TYPE and t["confidence"] == "推論"
        biz = {b["api_id"]: b for b in res["summary"]["by_biz_system"]}
        # 起點自己的系統 S 不另列成 0 台的一列；只有經連線推論走到的 T
        assert set(biz) == {"T-1"}
        assert biz["T-1"]["assets"] == [] and biz["T-1"]["name"] == "下單前台"
        assert res["deps_source_by_system"] == {"T-1": "observed"}
        only = br.impact(conn, "hw:HW-A", depth=6, only_evidence=True)
        assert "bizsys:T-1" not in {d["node_id"] for d in only["dependents"]}
    finally:
        conn.close()


def test_檢查清單_經連線推論受影響的系統也有一列_聯絡人取該系統的負責人(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        conn.execute("UPDATE hardware SET user_name = '陳大文', usage_unit = '交易部', custodian = 'SP某人' "
                     "WHERE asset_serial = 'HW-B'")
        conn.execute("INSERT INTO personnel (asset_serial, person_name, phone) VALUES ('HW-B', '林小美', '1234')")
        conn.commit()
        ci_graph.rebuild(conn)
        snap = br.save_snapshot(conn, "hw:HW-A", "incident", "測試", "tester")
        items = br.create_checklist(conn, snap["id"])
        t_rows = [i for i in items if i["biz_system"] == "下單前台"]
        assert {(i["contact_name"], i["contact_role"]) for i in t_rows} == {("林小美", "登記負責人"),
                                                                             ("陳大文", "AP User")}
        assert all(i["asset_serial"] is None for i in t_rows)
    finally:
        conn.close()


def _seed_regression(conn):
    """沒有任何連線資料的一個小世界：ESXi 上兩台 VM（兩個系統）、一台有負責人、一條人工邊。"""
    _hw(conn, "RG-1", "rg01", "10.99.10.1", "R-1", "回歸系統一", availability=3,
        physical_location="01_機房甲", user_name="王小明", usage_unit="資訊部")
    _hw(conn, "RG-2", "rg02", "10.99.10.2", "R-2", "回歸系統二", physical_location="02_機房乙")
    _hw(conn, "RG-ESXI", "esxirg", "10.99.10.9")
    conn.execute("INSERT INTO personnel (asset_serial, person_name, phone) VALUES ('RG-1', '負責人甲', '111')")
    conn.commit()
    ci_graph.rebuild(conn)
    for s in ("RG-1", "RG-2"):
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES (?, 'hw:RG-ESXI','runs_on','證據','manual','t')", (f"hw:{s}",))
    conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                 "VALUES ('bizsys:R-2','bizsys:R-1','depends_on','推論','manual','t')")
    conn.commit()


def test_沒有連線資料時_影響範圍跟fc31903一字不差(tmp_path):
    """#2：基準檔是 fc31903（改動前）的 impact() 對同一份資料的輸出（dependents＋summary）。"""
    conn = _conn(tmp_path)
    try:
        _seed_regression(conn)
        want = json.loads(BASELINE.read_text(encoding="utf-8"))
        for nid, exp in want.items():
            r = br.impact(conn, nid, depth=6)
            got = json.loads(json.dumps({"dependents": r["dependents"], "summary": r["summary"]},
                                        ensure_ascii=False, sort_keys=True))
            # 2026-10-23 名稱比對補負責人是後加的欄位（基準檔之後才有）：這份資料沒有任何比對，
            # 所以必須是空的／None，確認後拿掉再比，其他欄位仍要一字不差
            s = got["summary"]
            assert s.pop("name_matched") == [] and s.pop("name_match_ambiguous") == [], nid
            for b in s["by_biz_system"]:
                for a in b["assets"]:
                    assert a.pop("name_match") is None, nid
            assert got == exp, nid
    finally:
        conn.close()


def test_系統地圖_上下游用連線推論_帶source與證據(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ci_graph.rebuild(conn)
        m = br.system_map(conn, "S-1")
        assert m["deps_available"] is True and m["deps_source"] == "observed"
        assert [(d["api_id"], d["source"]) for d in m["depended_by"]] == [("T-1", "observed")]
        ev = m["depended_by"][0]["evidence"]
        assert ev["count"] == 2 and ev["observations"] == 5 and ev["ports"] == [1521]
        assert m["depends_on"] == []
        assert m["summary"]["depended_by"] == 1
        assert m["unknown_callers"]["rows"] == 2
        t = br.system_map(conn, "T-1")
        assert [(d["api_id"], d["source"]) for d in t["depends_on"]] == [("S-1", "observed")]
    finally:
        conn.close()


def test_一端有人工登記_連線推論邊兩端都看得到_兩端都登記才不用(tmp_path):
    """審查 S/T/X 重現：S 有人工登記（S 依賴 X，system_deps 帶前後空白），T 沒登記。
    · 連線 T→S（只有一端登記）：S 的地圖列 T（observed）、T 的地圖列 S（observed）、4-2 從 S 的主機走得到 T
    · 連線 S→X（兩端都登記）：以人工為準，連線推論不用，兩邊地圖都不出現 observed 列
    · 4-2 每個系統標的是「實際走到它的那條邊」的來源"""
    conn = _conn(tmp_path)
    try:
        _two(conn)
        _hw(conn, "HW-X", "x01", "10.99.5.5", "X-1", "外部報送")
        conn.commit()
        for sid in ("S-1 ", " X-1"):   # system_deps 有 FK 到 systems；故意帶空白（trim 要一致）
            conn.execute("INSERT INTO systems (id, label, health) VALUES (?,?,'ok')", (sid, sid))
        conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES ('S-1 ',' X-1','API')")
        conn.commit()
        od.store(conn, "10.99.1.10", "HW-A", [
            {"direction": "inbound", "local_port": 1521, "peer_ip": "10.99.2.20", "peer_port": 0, "count": 1},
            {"direction": "outbound", "local_port": 0, "peer_ip": "10.99.5.5", "peer_port": 443, "count": 1}])
        ci_graph.rebuild(conn)
        assert br.registered_systems(conn) == {"S-1", "X-1"}
        s = br.system_map(conn, "S-1")
        assert s["deps_source"] == "mixed"
        assert [(d["api_id"], d["source"]) for d in s["depends_on"]] == [("X-1", "registered")]
        assert [(d["api_id"], d["source"]) for d in s["depended_by"]] == [("T-1", "observed")]
        t = br.system_map(conn, "T-1")
        assert t["deps_source"] == "observed"
        assert [(d["api_id"], d["source"]) for d in t["depends_on"]] == [("S-1", "observed")]
        x = br.system_map(conn, "X-1")
        assert [d["source"] for d in x["depended_by"]] == ["registered"]
        res = br.impact(conn, "hw:HW-A", depth=6)
        assert "bizsys:T-1" in {d["node_id"] for d in res["dependents"]}
        assert res["deps_source_by_system"]["T-1"] == "observed"
        import verify_numbers as vn
        before = vn.FAILS
        vn.check_observed_deps(conn)
        assert vn.FAILS == before, "人工登記＋連線推論並存時 verify_numbers 也要全過"
    finally:
        conn.close()


def test_經登記關聯走到的系統標registered(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "HW-A", "core01", "10.99.1.10", "S-1", "核心交易")
        _hw(conn, "HW-V", "vm01", "10.99.1.20", "V-1", "虛機系統")
        conn.commit()
        ci_graph.rebuild(conn)
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES ('hw:HW-V','hw:HW-A','runs_on','證據','manual','t')")
        conn.commit()
        res = br.impact(conn, "hw:HW-A", depth=6)
        assert res["deps_source_by_system"] == {"V-1": "registered"}
    finally:
        conn.close()


def test_快取_同一台的判斷口徑變了就重算(tmp_path, monkeypatch):
    """machine_identity 換口徑／待確認名單：簽章含 mode 與名單，refresh 有變也會主動清快取。"""
    import machine_identity as mi

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        a = od.infer(conn)
        assert od.infer(conn) is a
        monkeypatch.setitem(mi._STATE, "pending", frozenset({"core01"}))
        b = od.infer(conn)
        assert b is not a, "待確認名單變了不可以沿用舊結果"
        monkeypatch.setitem(mi._STATE, "mode", "legacy" if mi.mode() == "hostname" else "hostname")
        assert od.infer(conn) is not b, "口徑變了不可以沿用舊結果"
        c = od.infer(conn)
        calls = []
        monkeypatch.setattr(od, "invalidate", lambda: calls.append(1))
        db.set_setting(conn, mi.MODE_SETTING, "legacy" if mi.mode() == "hostname" else "hostname")
        mi.refresh(conn)
        assert calls, "refresh 判斷有變時要清連線推論快取"
        calls.clear()
        mi.refresh(conn)
        assert not calls, "沒變就不清（refresh 每開一條連線都會跑）"
        assert c is not None
    finally:
        conn.close()


def test_樞紐經登記關聯走到就照樣擴散_回應講得出來(tmp_path):
    """樞紐只在「經連線推論走到」時停；監控主機自己掛（起點的系統就是樞紐）會照樣擴散，回應列在 hubs_expanded。"""
    conn = _conn(tmp_path)
    try:
        _hub_world(conn)
        res = br.impact(conn, "hw:HM", depth=6)
        assert {"api_id": "M-1", "node_id": "bizsys:M-1", "reason": "registered_edge"} in \
            res["observed"]["hubs_expanded"]
        assert len({d["node_id"] for d in res["dependents"]}) > 2
        res2 = br.impact(conn, "hw:HA", depth=6)
        assert res2["observed"]["hubs_expanded"] == []
    finally:
        conn.close()


def test_沒有任何觀察資料_deps_available維持False(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "HW-A", "core01", "10.99.1.10", "S-1")
        conn.commit()
        m = br.system_map(conn, "S-1")
        assert m["deps_available"] is False and m["deps_source"] is None
    finally:
        conn.close()


def test_api_id前後有空白_節點id跟ci_graph同一個(tmp_path):
    """#8：節點 id 一律 ci_graph.bizsys_node_id(原值)，api_id 帶空白也走得到。"""
    conn = _conn(tmp_path)
    try:
        _hw(conn, "HW-A", "core01", "10.99.1.10", "S-1 ", "核心交易")
        _hw(conn, "HW-B", "web01", "10.99.2.20", "T-1", "下單前台")
        conn.commit()
        od.store(conn, "10.99.1.10", "HW-A", [{"direction": "inbound", "local_port": 1521,
                                              "peer_ip": "10.99.2.20", "peer_port": 0, "count": 1}])
        ci_graph.rebuild(conn)
        assert od.infer(conn)["nodes_of_api"]["S-1"] == [ci_graph.bizsys_node_id("S-1 ")]
        ids = {d["node_id"] for d in br.impact(conn, "hw:HW-A", depth=6)["dependents"]}
        assert "bizsys:T-1" in ids
    finally:
        conn.close()


def test_edge_type常數兩邊一致():
    assert br.OBSERVED_EDGE_TYPE == od.EDGE_TYPE


def test_推論有快取_資料變了才重算(tmp_path):
    """#5：同一份資料重複呼叫拿到同一個結果物件；寫進新連線後重算。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        a = od.infer(conn)
        assert od.infer(conn) is a
        other = db.get_connection(tmp_path / "t.db")
        try:
            assert od.infer(other) is a, "同一個庫、另一條連線（下一個請求）也共用"
        finally:
            other.close()
        od.store(conn, "10.99.1.10", "HW-A", [{"direction": "inbound", "local_port": 1521,
                                              "peer_ip": "10.99.2.20", "peer_port": 0, "count": 1}])
        assert od.infer(conn) is not a
    finally:
        conn.close()


def test_verify_numbers_連線推論段全過(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ci_graph.rebuild(conn)
        import verify_numbers as vn
        before = vn.FAILS
        vn.check_observed_deps(conn)
        assert vn.FAILS == before
    finally:
        conn.close()


def test_服務盤點收集時寫進host_connection(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "HW-A", "core01", "10.99.1.10", "S-1")
        conn.execute("UPDATE hardware SET collect_ok = 1")
        conn.commit()
        import service_inventory

        def run(host, cmd):
            return "" if "systemctl" in cmd or "lssrc" in cmd else SS_ALL
        out = service_inventory.collect_services(conn, runner=run)
        assert out["hosts"][0]["connections"] == 3
        assert out["observed_error"] is None
        n = conn.execute("SELECT COUNT(*) FROM host_connection WHERE host_ip = '10.99.1.10'").fetchone()[0]
        assert n == 3
    finally:
        conn.close()


@pytest.mark.parametrize("ip,ignored", [("127.0.0.1", True), ("::1", True), ("fe80::1", True),
                                        ("169.254.1.1", True), ("0.0.0.0", True), ("10.99.1.1", False)])
def test_可忽略的對方位址(ip, ignored):
    assert sc.is_ignorable_peer(ip) is ignored


def test_自動判定管理連線_PAM用22連進40個系統_S00掛只影響S01(tmp_path):
    """沒人填 observed_deps_admin_peers 時的保底（審查重現）：PAM 主機用 :22 連進 40 個系統，
    扇出超過樞紐門檻 → 它的 :22 列自動當管理連線（計數、列出來、可關）。S01 真的呼叫 S00（8080），
    S05 用 SFTP 傳檔給 S06（單一對象的 :22）照樣造邊。"""
    conn = _conn(tmp_path)
    try:
        for i in range(40):
            _hw(conn, f"HS{i:02d}", f"s{i:02d}", f"10.99.20.{i + 1}", f"S{i:02d}", f"系統{i:02d}")
        _hw(conn, "HPAM", "pam01", "10.99.30.1", "PAM-1", "特權帳號管理")
        conn.commit()
        for i in range(40):
            od.store(conn, f"10.99.20.{i + 1}", f"HS{i:02d}", [
                {"direction": "inbound", "local_port": 22, "peer_ip": "10.99.30.1", "peer_port": 0, "count": 1}])
        od.store(conn, "10.99.20.1", "HS00", [
            {"direction": "inbound", "local_port": 8080, "peer_ip": "10.99.20.2", "peer_port": 0, "count": 1}])
        od.store(conn, "10.99.20.7", "HS06", [
            {"direction": "inbound", "local_port": 22, "peer_ip": "10.99.20.6", "peer_port": 0, "count": 1}])
        ci_graph.rebuild(conn)

        r = od.infer(conn)
        assert {(e["caller"], e["callee"]) for e in r["edges"]} == {("S01", "S00"), ("S05", "S06")}
        assert r["stats"]["admin_ssh"] == 40 and r["stats"]["admin_ssh_auto"] == 40
        assert r["auto_admin_ssh"] == [{"ip": "10.99.30.1", "apis": ["PAM-1"], "targets": 40, "rows": 40,
                                        "label": "自動判定為管理連線"}]
        res = br.impact(conn, "hw:HS00", depth=6)
        assert [b["api_id"] for b in res["summary"]["by_biz_system"]] == ["S01"]
        assert res["observed"]["auto_admin_ssh"][0]["ip"] == "10.99.30.1"

        db.set_setting(conn, od.AUTO_ADMIN_KEY, "off")       # 可推翻：關掉就照連線造邊
        r = od.infer(conn)
        assert r["auto_admin_ssh"] == [] and r["stats"]["admin_ssh"] == 0
        assert ("PAM-1", "S00") in {(e["caller"], e["callee"]) for e in r["edges"]}
        res = br.impact(conn, "hw:HS00", depth=6)
        assert sorted(b["api_id"] for b in res["summary"]["by_biz_system"]) == ["PAM-1", "S01"]
    finally:
        conn.close()


def test_沒造邊的列下鑽_排除與模稜兩可_筆數等於列數(tmp_path):
    """畫面上「排除 N／模稜兩可 N」點下去要列得出那幾列，而且跟 stats 同一份計算。"""
    conn = _conn(tmp_path)
    try:
        _two(conn)
        _hw(conn, "HW-M", "mon01", "10.99.7.5", "MON-1", "監控")
        _hw(conn, "HW-X", "vipa", "10.99.8.8", "X-1")
        _hw(conn, "HW-Y", "vipb", "10.99.8.8", "Y-1")
        conn.commit()
        od.store(conn, "10.99.1.10", "HW-A", [
            {"direction": "inbound", "local_port": 1521, "peer_ip": ip, "peer_port": 0, "count": 1}
            for ip in ("10.99.7.5", "10.99.8.8", "10.99.2.20")])
        db.set_setting(conn, od.EXCLUDE_KEY, "MON-1")
        r = od.infer(conn)
        ex = od.bucket_rows(conn, "excluded")
        assert ex["total"] == r["stats"]["excluded"] == len(ex["rows"]) == 1
        assert ex["rows"][0]["peer_ip"] == "10.99.7.5"
        am = od.bucket_rows(conn, "ambiguous")
        assert am["total"] == r["stats"]["ambiguous"] == 1 and am["rows"][0]["peer_ip"] == "10.99.8.8"
        assert od.bucket_rows(conn, "admin_ssh_auto")["total"] == r["stats"]["admin_ssh_auto"] == 0
        with pytest.raises(ValueError):
            od.bucket_rows(conn, "bogus")
    finally:
        conn.close()
