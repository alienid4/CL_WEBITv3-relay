"""進不去的機器先判斷「是什麼機器」，再給下一步（2026-10-19）。

以前：活著但 22／445／5985 都不通 → 一律「待裝 Agent」「請按取得安裝包」。
但 Push Agent 只支援 Linux／Unix（bootstrap_watcher.sh 以 sudo sh 執行，沒有 Windows 版；
AIX 上也跑不起來）。於是只開 3389 的 Windows、只開管理埠的設備，都被叫去裝一個裝不上的東西。

這支釘住：
  1. 判斷規則（host_kind，規則在 host_kind_rules.json）：證據由強到弱、矛盾就降成推測、
     沒證據就是不明（查不到的不要猜）
  2. 分派：Windows → 待開 WinRM、WinRM 收集頁簽、不給安裝包；AIX → 待開 SSH、大型主機頁簽；
     設備 → 另有納管方式；Linux／Unix → 照舊 Push Agent；不明 → Push Agent 但先確認
  3. 舊 run（那時一律待裝 Agent）讀出來也要分得出來
  4. 頁簽數字＝下鑽列數、各頁簽加總＝探測位址數
  5. 前端：「判斷」欄可排序、Windows 那幾列沒有「取得安裝包」
"""
import contextlib
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import host_kind as hk  # noqa: E402

ADOPT_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")


@contextlib.contextmanager
def _db():
    """暫存庫；結束一定 close（Windows 上沒關的 sqlite 檔刪不掉）。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


def _seed(conn, ip, serial, os=None, hostname=None, **kw):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os, hostname=hostname,
                       environment="測試", asset_status="使用中", **kw)


def _ports(table):
    return lambda ip: table.get(ip)


def _ttl(table):
    return lambda ip: table.get(ip)


# ===================== 1. 判斷規則 =====================

def test_只開3389_推測是Windows():
    g = hk.infer(open_ports=[3389])
    assert g["kind"] == "windows" and g["confidence"] == hk.CONF_LIKELY
    assert g["action"] == hk.ACTION_WINRM_PORT
    assert any("3389" in e for e in g["evidence"])


def test_什麼線索都沒有_就是不明_不猜():
    g = hk.infer(open_ports=[])
    assert g["kind"] == hk.KIND_UNKNOWN and g["confidence"] == hk.CONF_UNKNOWN
    assert g["confidence_label"] == "不明"
    assert g["action"] == hk.ACTION_AGENT_UNCONFIRMED


def test_TTL區間():
    assert hk.infer(ttl=127)["kind"] == "windows"
    # v1.468.3：TTL 64 單獨不下判斷（很多網路設備也是 64），只能佐證——結論「不明」，依據照列
    g64 = hk.infer(ttl=63)
    assert g64["kind"] == hk.KIND_UNKNOWN and g64["confidence"] == hk.CONF_UNKNOWN
    assert any("ESXi" in e and "TTL 63" in e for e in g64["evidence"]), "TTL 64 分不出 ESXi，要講出來"
    g255 = hk.infer(ttl=250)
    assert g255["kind"] == "network" and g255["action"] == hk.ACTION_NOK
    assert hk.infer(ttl=150)["kind"] == "device"


def test_資產登記是確定_探測矛盾就降成推測():
    g = hk.infer(registry_class="Windows", registry_text="Windows Server 2019")
    assert g["kind"] == "windows" and g["confidence"] == hk.CONF_CONFIRMED
    assert "Windows Server 2019" in g["evidence"][0]
    g2 = hk.infer(registry_class="Linux", registry_text="RHEL 8", open_ports=[3389])
    assert g2["kind"] == "linux", "登記優先於探測"
    assert g2["confidence"] == hk.CONF_LIKELY, "跟 3389 矛盾，不能說確定"
    assert any("Windows" in e and "3389" in e for e in g2["evidence"])


def test_收集到的OS優先於登記():
    g = hk.infer(collected="aix", registry_class="Linux")
    assert g["kind"] == "aix" and g["confidence"] == hk.CONF_CONFIRMED
    assert g["action"] == hk.ACTION_SSH_PORT


def test_banner自報廠牌與埠號組合():
    assert hk.infer(banner="Cisco IOS Software")["kind"] == "network"
    assert hk.infer(banner="SSH-2.0-OpenSSH_for_Windows_8.1")["kind"] == "windows"
    assert hk.infer(banner="VMware ESXi 8.0")["action"] == hk.ACTION_NOK
    assert hk.infer(open_ports=[161])["kind"] == "network"
    assert hk.infer(open_ports=[161, 22])["kind"] != "network", "有 SSH 就不是只開 SNMP"
    assert hk.infer(open_ports=[135])["kind"] == "windows"
    assert hk.infer(open_ports=[111, 2049])["kind"] == "linux"
    assert hk.infer(open_ports=[80])["kind"] == hk.KIND_UNKNOWN, "只開 80 什麼都證明不了"


def test_規則在設定檔_不寫死(monkeypatch):
    cfg = hk.load_rules()
    import os_class
    classes = [c for c in os_class.load_rules()["classes"] if c not in ("其他", "未知")]
    for c in classes:
        assert c in cfg["os_class_kind"], f"os_class 大類「{c}」沒有對應的判斷"
    # 拿掉 3389 那條規則，只開 3389 就判不出來——證明判斷真的讀設定檔
    trimmed = dict(cfg, scan_rules=[r for r in cfg["scan_rules"] if 3389 not in r.get("ports_any", [])])
    monkeypatch.setattr(hk, "_RULES", trimmed)
    assert hk.infer(open_ports=[3389])["kind"] == hk.KIND_UNKNOWN


# ===================== 2. 分派 =====================

def _run(conn, ports, ttls=None):
    return cd.run_dispatch(conn, "\n".join(ports), prober=_ports(ports),
                           ttl_prober=_ttl(ttls or {}), ssh_prober=lambda ip: (False, "denied"))


def test_只開3389的Windows_不再叫人裝Agent_改請開WinRM():
    with _db() as conn:
        out = _run(conn, {"10.99.5.10": [3389]})
        r = out["results"][0]
        assert r["route"] == cd.ROUTE_AGENT, "route 照舊是探測事實"
        assert r["status"] == cd.STATUS_NEEDS_WINRM_PORT
        assert r["tab"] == cd.TAB_WINRM
        assert "請開 WinRM（5985）" in r["message"]
        assert "取得安裝包" not in r["message"]
        assert r["kind_guess"]["kind"] == "windows" and r["kind_guess"]["confidence"] == "likely"
        assert out["by_tab"][cd.TAB_AGENT] == 0
        assert out["by_tab"][cd.TAB_WINRM] == 1
        assert out["needs_action"] == 1, "開 WinRM 仍是待辦"


def test_登記為Windows_沒開任何埠_也是請開WinRM_確定():
    with _db() as conn:
        _seed(conn, "10.99.5.11", "W-1", os="Windows Server 2019 Standard", hostname="win-a")
        r = _run(conn, {"10.99.5.11": []})["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_WINRM_PORT and r["tab"] == cd.TAB_WINRM
        assert r["kind_guess"]["confidence"] == "confirmed"


def test_AIX沒開22_請開SSH_在大型主機頁簽_不給安裝包():
    with _db() as conn:
        _seed(conn, "10.99.5.12", "X-1", os="AIX 7.2", hostname="aix-a")
        r = _run(conn, {"10.99.5.12": []})["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_SSH_PORT
        assert r["tab"] == cd.TAB_MAINFRAME
        assert "請開 SSH（22）" in r["message"] and "取得安裝包" not in r["message"]


def test_Linux推測_照舊PushAgent_給安裝包():
    """Linux 推測要 TTL 以外的線索（這裡：上次網段掃描看到 SSH 22）。"""
    with _db() as conn:
        conn.execute("INSERT INTO scan_history (ip, scan_ok, ttl, os_guess, open_ports) "
                     "VALUES ('10.99.5.13', 1, 63, 'Linux/Unix（SSH 22）', NULL)")
        conn.commit()
        out = _run(conn, {"10.99.5.13": []}, {"10.99.5.13": 63})
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_AGENT and r["tab"] == cd.TAB_AGENT
        assert r["kind_guess"]["kind"] == "linux"
        assert "取得安裝包" in r["message"]
        assert r["ttl"] == 63


def test_判斷不出來_留在PushAgent_但先確認():
    with _db() as conn:
        r = _run(conn, {"10.99.5.14": []})["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_AGENT and r["tab"] == cd.TAB_AGENT
        assert r["kind_guess"]["kind"] == "unknown" and r["kind_guess"]["confidence_label"] == "不明"
        assert "先確認是什麼機器" in r["message"]


def test_TTL像網路設備_另有納管方式():
    with _db() as conn:
        out = _run(conn, {"10.99.5.15": []}, {"10.99.5.15": 254})
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE and r["tab"] == cd.TAB_NETWORK
        assert "推測" in r["message"]
        assert out["not_onboardable"] == 1
        # 重新整理（讀 DB）：狀態不變、判斷照樣看得到（TTL 存在結果表）
        again = cd.latest_run(conn)["results"][0]
        assert again["status"] == cd.STATUS_NOT_ONBOARDABLE and again["tab"] == cd.TAB_NETWORK
        assert again["kind_guess"]["kind"] == "network" and again["ttl"] == 254


def test_上次掃描的TTL也算線索():
    with _db() as conn:
        conn.execute("INSERT INTO scan_history (ip, scan_ok, ttl, os_guess, open_ports) "
                     "VALUES ('10.99.5.16', 1, 127, 'Windows（TTL≈128）', NULL)")
        conn.commit()
        r = _run(conn, {"10.99.5.16": []})["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_WINRM_PORT
        assert any("TTL 127" in e for e in r["kind_guess"]["evidence"])


def test_人工指定是最強的證據():
    import os_override
    import pipeline as p
    with _db() as conn:
        _seed(conn, "10.99.5.17", "U-1", hostname="mystery")
        os_override.set_override(conn, "mystery", "10.99.5.17", "U-1", p.OS_WINDOWS, None, "看過了", "tester")
        r = _run(conn, {"10.99.5.17": []}, {"10.99.5.17": 63})["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_WINRM_PORT and r["tab"] == cd.TAB_WINRM
        assert "人工指定" in r["kind_guess"]["evidence"][0]


def test_頁簽加總與下鑽一致_重新整理後也一樣():
    ports = {"10.99.5.20": [3389], "10.99.5.21": [], "10.99.5.22": [], "10.99.5.23": [],
             "10.99.5.24": [22], "10.99.5.25": None}
    with _db() as conn:
        _seed(conn, "10.99.5.22", "X-2", os="AIX 7.3", hostname="aix-b")
        for out in (_run(conn, ports, {"10.99.5.23": 250}), cd.latest_run(conn)):
            assert sum(out["by_tab"].values()) == out["total"] == len(ports)
            for t, n in out["by_tab"].items():
                assert n == sum(1 for r in out["results"] if r["tab"] == t), t
            agent = [r for r in out["results"] if r["tab"] == cd.TAB_AGENT]
            assert all(r["kind_guess"]["kind"] in ("linux", "unknown") for r in agent)
            assert [r["ip"] for r in agent] == ["10.99.5.21"]


# ===================== 3. 舊 run =====================

def test_舊run存成待裝Agent的Windows_讀出來改到WinRM頁簽():
    """升版後不重跑收集：舊結果（一律 needs_agent）也要分得出 Windows。舊程式這裡是紅的。"""
    with _db() as conn:
        cur = conn.execute("INSERT INTO collect_dispatch_run (trigger, status, target_count) "
                           "VALUES ('manual', 'ok', 2)")
        rid = cur.lastrowid
        for ip, ports in (("10.99.5.30", "3389"), ("10.99.5.31", None)):
            conn.execute(
                "INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, status, "
                "registered, message) VALUES (?,?,1,?,'agent','needs_agent',0,'舊訊息')",
                (rid, ip, ports))
        conn.commit()
        out = cd.latest_run(conn)
        by = {r["ip"]: r for r in out["results"]}
        assert by["10.99.5.30"]["status"] == cd.STATUS_NEEDS_WINRM_PORT
        assert by["10.99.5.30"]["tab"] == cd.TAB_WINRM
        assert by["10.99.5.31"]["status"] == cd.STATUS_NEEDS_AGENT
        assert by["10.99.5.31"]["kind_guess"]["kind"] == "unknown"
        assert out["by_tab"][cd.TAB_AGENT] == 1


def test_正式庫還沒補ttl欄時_latest_run與verify_numbers不炸():
    """deploy.sh [2d] 在服務重啟（db.init_db 補欄）**之前**就拿正式庫跑 verify_numbers。
    2026-10-19 部署 1.467.0 實際踩到：no such column: ttl → 中止部署。"""
    import verify_numbers as vn
    with _db() as conn:
        _run(conn, {"10.99.5.50": [3389], "10.99.5.51": []})
        # 模擬舊庫：重建一張沒有 ttl 欄的表（221 的 SQLite 沒有 DROP COLUMN，不能用那個）
        cols = [c[1] for c in conn.execute("PRAGMA table_info(collect_dispatch_result)") if c[1] != "ttl"]
        conn.execute(f"CREATE TABLE _old AS SELECT {', '.join(cols)} FROM collect_dispatch_result")
        conn.execute("DROP TABLE collect_dispatch_result")
        conn.execute("ALTER TABLE _old RENAME TO collect_dispatch_result")
        conn.commit()
        assert "ttl" not in {c[1] for c in conn.execute("PRAGMA table_info(collect_dispatch_result)")}
        out = cd.latest_run(conn)
        by = {r["ip"]: r for r in out["results"]}
        assert by["10.99.5.50"]["status"] == cd.STATUS_NEEDS_WINRM_PORT
        assert by["10.99.5.51"]["ttl"] is None
        before = len(vn.RESULTS)
        vn.check_adopt_tabs(conn)
        vn.check_adopt_registered_split(conn)
        bad = [x for x in vn.RESULTS[before:] if x["ok"] is False]
        assert not bad, bad


def test_verify_numbers的不變式在這批資料上全過():
    import verify_numbers as vn
    with _db() as conn:
        _run(conn, {"10.99.5.40": [3389], "10.99.5.41": [], "10.99.5.42": [22]},
             {"10.99.5.41": 60})
        before = len(vn.RESULTS)
        vn.check_adopt_tabs(conn)
        mine = vn.RESULTS[before:]
        assert any("Push Agent 頁簽沒有判斷為 Windows" in x["name"] for x in mine)
        bad = [x for x in mine if x["ok"] is False]
        assert not bad, bad


# ===================== 4. 前端 =====================

def test_前端_判斷欄可排序_Windows列沒有安裝包按鈕():
    assert re.search(r'<SortTh v-if="showKindCol" k="kind_sort"', ADOPT_VUE), "判斷欄要能排序"
    # 安裝包按鈕只綁 needs_agent（Windows 是 needs_winrm_port，自然不會出現）
    m = re.search(r'<button v-if="r.status === \'needs_agent\'"[^>]*>', ADOPT_VUE, re.S)
    assert m, "安裝包按鈕只給 needs_agent"
    assert "needs_winrm_port: '請開 WinRM（5985）'" in ADOPT_VUE
    assert "先確認是什麼機器" in ADOPT_VUE
