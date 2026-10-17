"""公司機用的唯讀試算腳本（scripts/machine_key_trial.py）跟正式程式不可漂移（2026-10-16，Q79）。

腳本必須只用標準函式庫、不 import 新版模組（公司機跑舊版），所以規則各寫一份；這裡把它們釘在一起。
IP 一律 10.99.x（範例），主機名是假的。
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
SCRIPT = ROOT / "APP" / "asset-module" / "scripts" / "machine_key_trial.py"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import dyn_shell  # noqa: E402
import manage_state  # noqa: E402
import system_report  # noqa: E402
import system_stats  # noqa: E402

_spec = importlib.util.spec_from_file_location("machine_key_trial", SCRIPT)
trial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trial)


def test_腳本只用標準函式庫_不import新版模組():
    src = SCRIPT.read_text(encoding="utf-8")
    for mod in ("system_stats", "manage_state", "system_report", "dyn_shell", "db", "fastapi"):
        assert f"import {mod}" not in src and f"from {mod}" not in src, mod
    assert "mode=ro" in src


def test_常數跟正式程式同一份():
    assert trial.DOMAIN_RE.pattern == system_stats.DOMAIN_RE.pattern
    assert trial.RETIRED_STATUS == set(manage_state.RETIRED_STATUS)
    assert trial.OFF_BOOK_PREFIXES == tuple(system_report.OFF_BOOK_PREFIXES)
    cfg = json.loads((BACKEND / "machine_identity.json").read_text(encoding="utf-8"))
    assert trial.GENERIC_NAMES == set(cfg["generic_names"])
    assert trial.GENERIC_PATTERNS == cfg["generic_patterns"]
    assert list(trial.STOP_ENV) == cfg["stop_loss"]["env_groups"]
    assert list(trial.GUARD_ENV) == cfg["guard_env_groups"]
    assert trial.STOP_TOTAL_PCT == cfg["stop_loss"]["max_total_change_pct"]
    assert trial.STOP_PENDING_PCT == cfg["stop_loss"]["max_pending_pct"]
    assert trial.BUSINESS == dyn_shell.BUSINESS


def test_現行口徑與環境分組_逐筆等於正式程式():
    cases = [("SVR01.corp.example.com", "10.99.1.1", "HW-1"), ("svr01", "10.99.1.1", "HW-2"),
             ("svr01", "0.0.0.0", "HW-3"), ("", "10.99.1.2", "HW-4"), (None, None, "HW-5"),
             ("  Box-7 ", " 10.99.1.7 ", "HW-6")]
    for h, ip, sn in cases:
        assert trial.old_key(h, ip, sn) == system_stats.legacy_machine_key(h, ip, sn)
    for raw in ("正式", "測試環境", "使用者測試(UAT)", "DR 備援", "", None, "其他值", "OA"):
        assert trial.group_env(raw) == manage_state.group_environment(raw), raw


def _rows():
    r = lambda sn, h, ip, env="測試", mac=None, hw=None, st="使用中": dict(  # noqa: E731
        asset_serial=sn, hostname=h, ip=ip, environment=env, mac=mac, hw_serial=hw, asset_status=st)
    return [
        r("HW-1", "apsvr01", "10.99.1.1"), r("HW-2", "APSVR01.corp.example.com", "10.99.2.1"),  # 同名兩 IP → 併
        r("HW-3", "dbsvr", "10.99.1.3", "正式"), r("VC-1", "dbsvr", "10.99.3.3", "測試"),        # 環境混併
        r("HW-4", "twin", "10.99.1.4", mac="00:11:22:33:44:01"),
        r("HW-5", "twin", "10.99.1.5", mac="00:11:22:33:44:02"),                                 # MAC 不同 → 待確認
        r("HW-6", "localhost", "10.99.1.6"), r("HW-7", "localhost", "10.99.1.7"),               # 預設名 → 不併
        r("HW-8", "", "10.99.1.8"), r("HW-9", "old", "10.99.1.9", st="報廢"),
    ]


def test_新口徑_同名併_護欄不併_環境不同也不併():
    a = trial.analyse(_rows())
    assert a["active"] == 9
    assert a["old"] == 9 and a["new"] == 8
    assert sorted(k[2:] for k in a["merged"]) == ["apsvr01"]
    assert a["env_mixed"] == []                      # 環境護欄：正式＋測試不會被併
    assert sorted(a["pending"]) == ["dbsvr", "twin"]
    assert len(a["generic_rows"]) == 2
    assert a["stops"]["a"][0] is False and a["stops"]["b"][0] is True
    m = a["misjudge"]
    assert (m["A"], m["B"], m["C"]) == (3, 1, 2) and m["A"] == m["B"] + m["C"]
    assert m["C_by"] == {"MAC": 1, "環境不同": 1} and m["no_hostname_rows"] == 1


def test_空殼可併回筆數_等於dyn_shell預覽():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            db.insert_hardware(conn, asset_serial="HW-A", hostname="appa", ip="10.99.5.1",
                               environment="測試", asset_status="使用中", api_id="X-1")
            db.insert_hardware(conn, asset_serial="DYN-aa", hostname="appa", ip="10.99.5.1",
                               mac="00:50:56:00:00:01")
            db.insert_hardware(conn, asset_serial="DYN-bb", hostname="lonely", ip="10.99.5.2")
            db.insert_hardware(conn, asset_serial="HW-C1", hostname="multi", ip="10.99.5.3", api_id="A")
            db.insert_hardware(conn, asset_serial="HW-C2", hostname="multi", ip="10.99.5.3", api_id="B")
            db.insert_hardware(conn, asset_serial="DYN-cc", hostname="multi", ip="10.99.5.3")
            conn.commit()
            want = dyn_shell.preview(conn)["counts"]["mergeable"]
        finally:
            conn.close()
        assert want == 1
        assert trial.mergeable_shells(trial.load(str(p))) == want


def test_唯讀_資料庫一個位元組都沒變():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = sqlite3.connect(p)
        c.execute("INSERT INTO hardware (asset_serial, hostname, ip) VALUES ('HW-1','a','10.99.9.1')")
        c.commit()
        c.close()
        before = p.read_bytes()
        assert trial.main(["x", str(p)]) == 0
        assert p.read_bytes() == before


def test_輸出去識別化():
    assert trial.mask_name("apsvr01") == "aps***"
    assert trial.mask_ip("10.99.1.23") == "10.99.*.*"


def test_試算腳本與正式實作_同一份資料數字一樣():
    import machine_identity

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            for r in _rows():
                db.insert_hardware(conn, **{k: v for k, v in r.items() if v is not None})
            conn.commit()
            real = machine_identity.analyse(conn)
        finally:
            conn.close()
        t = trial.analyse(trial.load(str(p)))
    assert (real["old"], real["new"]) == (t["old"], t["new"])
    assert sorted(g["name"] for g in real["merged"]) == sorted(k[2:] for k in t["merged"])
    assert sorted(g["name"] for g in real["pending"]) == sorted(t["pending"])
    assert {k: v["hit"] for k, v in real["stops"].items()} == {k: v[0] for k, v in t["stops"].items()}


def test_台數拆分_環境與作業系統各自加總等於總台數():
    rows = _rows()
    for r, o in zip(rows, ["RHEL 8", "Red Hat Enterprise Linux 8", "Windows Server 2019", "Windows 2019",
                           "AIX 7.2", "", "Cisco IOS", None, "SUSE 15", "RHEL 7"]):
        r["os"] = o
    a = trial.analyse(rows)
    for k in ("env", "os"):
        b = a["breakdown"][k]
        assert sum(b["before"].values()) == b["before_total"] == a["old"]
        assert sum(b["after"].values()) == b["after_total"] == a["new"]
    assert trial.os_class("Red Hat Enterprise Linux 8.10") == "Linux"
    assert trial.os_class("AIX 7.2") == "AIX" and trial.os_class("") == "未知"
    assert trial.os_class("Windows Server 2022") == "Windows" and trial.os_class("Cisco IOS") == "其他"


def test_停損a原始環境判法_腳本與正式實作同一份():
    import machine_identity

    assert trial._RAW_ENV == machine_identity._RAW_ENV
    for v in ("正式", "DR 備援", "UAT", "prod-db", "", None, "其他"):
        assert trial.raw_env_class(v) == machine_identity.raw_env_class(v)


def test_環境分組_腳本與設定檔逐字一樣():
    cfg = json.loads((BACKEND / "environment_groups.json").read_text(encoding="utf-8"))
    assert trial.ENV_GROUPS == [(g["name"], g["match"]) for g in cfg["groups"]]
