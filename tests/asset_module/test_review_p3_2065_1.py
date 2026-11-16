"""審查延後的兩個 P3（2.065.1）。

1. CIA 欄位對應 PUT：base_version 比對到寫檔之間加檔案鎖（.lock＋O_EXCL），兩人同時存只有一個成功、另一個 409；
   舊頁 pages/import.vue 也要送 base_version。⚠ D4。
2. 資料品質「作業系統一致」：host_account 證據沒記序號（NULL）時，只在該 IP 目前只對到一台機器
   （system_stats.machine_key 去重後）才採用；同 IP 兩台以上＝不知道是誰的，算無證據（不進分母，不是不一致）。⚠ D3。
全假資料、隔離暫存庫。
"""
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import excel_import  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
_REAL_MAPPING = excel_import.MAPPING_PATH.read_bytes()


def _imports_mod():
    import routers.imports as m
    return m


# ---------------------------------------------------------------- 1. 欄位對應並發存檔
@pytest.fixture
def env(monkeypatch, tmp_path):
    mp = tmp_path / "field_mapping.json"
    mp.write_bytes(_REAL_MAPPING)
    monkeypatch.setattr(excel_import, "MAPPING_PATH", mp)
    original = api.MAPPING_PATH
    api.MAPPING_PATH = mp
    db_path = tmp_path / "t.db"
    db.init_db(db_path)

    def _ov():
        cn = db.get_connection(db_path)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(db_path)
    db.create_user(cn, "tester", auth.hash_password(_STUB_CREDENTIAL))
    cn.close()

    def login():
        c = TestClient(api.app)
        assert c.post("/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}).status_code == 200
        return c
    try:
        yield login, mp
    finally:
        api.MAPPING_PATH = original
        api.app.dependency_overrides.clear()


def _hostname_header(fm: dict) -> str:
    return next(h for h, f in fm["硬體"].items() if f == "hostname")


def test_兩人同時存_只有一個成功_另一個409(env, monkeypatch):
    login, mp = env
    a, b = login(), login()
    fm = a.get("/api/import/field-mapping").json()
    base = fm["_version"]
    header = _hostname_header(fm)

    # 讓「讀檔→比版本→寫檔」中間拖 0.3 秒，兩個請求一定重疊；沒有鎖的話兩個都會比對通過、都 200（後寫的蓋掉先寫的）
    real_parse = excel_import.parse_mapping

    def slow_parse(raw):
        time.sleep(0.3)
        return real_parse(raw)
    monkeypatch.setattr(excel_import, "parse_mapping", slow_parse)

    results = {}

    def put(name, client, value):
        body = {"mapping": {**fm, "硬體": {**fm["硬體"], header: value}}, "base_version": base}
        results[name] = client.put("/api/import/field-mapping", json=body)

    t1 = threading.Thread(target=put, args=("a", a, "不匯入"))
    t2 = threading.Thread(target=put, args=("b", b, "hostname"))
    t1.start()
    t2.start()
    t1.join(30)
    t2.join(30)
    codes = sorted(r.status_code for r in results.values())
    assert codes == [200, 409], {k: (r.status_code, r.text[:200]) for k, r in results.items()}
    # 鎖用完要清掉
    assert not (mp.parent / (mp.name + ".lock")).exists()


def test_正常存檔照舊可用_連續兩次各帶新版本(env):
    login, mp = env
    c = login()
    fm = c.get("/api/import/field-mapping").json()
    header = _hostname_header(fm)
    r1 = c.put("/api/import/field-mapping",
               json={"mapping": {**fm, "硬體": {**fm["硬體"], header: "不匯入"}}, "base_version": fm["_version"]})
    assert r1.status_code == 200, r1.text
    fm2 = r1.json()
    r2 = c.put("/api/import/field-mapping",
               json={"mapping": {**fm2, "硬體": {**fm2["硬體"], header: "hostname"}}, "base_version": fm2["_version"]})
    assert r2.status_code == 200, r2.text
    # 拿舊版本存 → 409
    r3 = c.put("/api/import/field-mapping", json={"mapping": fm, "base_version": fm["_version"]})
    assert r3.status_code == 409
    assert not (mp.parent / (mp.name + ".lock")).exists()


def test_鎖被占住等不到回409_過期鎖會被清掉(tmp_path, monkeypatch):
    m = _imports_mod()
    target = tmp_path / "field_mapping.json"
    target.write_text("{}", encoding="utf-8")
    lock = tmp_path / "field_mapping.json.lock"
    lock.write_text("999", encoding="utf-8")
    monkeypatch.setattr(m, "MAPPING_LOCK_WAIT_S", 0.2)
    with pytest.raises(HTTPException) as ei:
        with m._mapping_file_lock(target):
            pass
    assert ei.value.status_code == 409
    assert lock.exists(), "別人的（未過期）鎖不能被清掉"

    # 過期：mtime 調到很久以前 → 清掉、拿得到鎖、用完再清
    old = time.time() - m.MAPPING_LOCK_STALE_S - 10
    import os
    os.utime(lock, (old, old))
    entered = False
    with m._mapping_file_lock(target):
        entered = True
        assert lock.exists()
    assert entered and not lock.exists()


def test_舊頁存檔有送base_version():
    src = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "import.vue").read_text(encoding="utf-8")
    i = src.index("async function saveMapping")
    seg = src[i:i + 800]
    assert "base_version" in seg and "_version" in seg


# ---------------------------------------------------------------- 2. NULL 序號的 OS 證據
def _now(days: float = 0) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _acct_null_serial(db_path, ip, os_id, ver):
    c = db.get_connection(db_path)
    try:
        c.execute("INSERT INTO host_account (ip, asset_serial, username, os_id, os_version, last_seen) "
                  "VALUES (?, NULL, 'root', ?, ?, ?)", (ip, os_id, ver, _now()))
        c.commit()
    finally:
        c.close()


def _os_dim(db_path):
    import data_quality
    c = db.get_connection(db_path)
    try:
        dims = {d["key"]: d for d in data_quality.measure(c)["dimensions"]}
        return dims["os_match"], data_quality.list_offenders(c, "os_match")
    finally:
        c.close()


def test_NULL序號證據_IP只對到一台_照常採用(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "N-1", "solo", "10.99.7.1", asset_status="使用中", os="Ubuntu 22.04")
    _acct_null_serial(db_path, "10.99.7.1", "rhel", "8.6")
    d, rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 0)
    assert [r["asset_serial"] for r in rows] == ["N-1"]


def test_NULL序號證據_同一台兩筆登記去重後仍是一台_照常採用(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    # 同主機名＋同 IP、兩個序號 → machine_key 同一台
    _insert_hardware(db_path, "D-1", "same", "10.99.7.2", asset_status="使用中", os="Ubuntu 22.04")
    _insert_hardware(db_path, "D-2", "same", "10.99.7.2", asset_status="使用中", os="Ubuntu 22.04")
    _acct_null_serial(db_path, "10.99.7.2", "ubuntu", "22.04")
    d, rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (2, 2) and rows == []


def test_NULL序號證據_IP對到兩台以上_算無證據不算不一致(tmp_path):
    """IP 換手：舊機器的帳號資料（沒記序號）不能拿來判新機器 OS 不一致。"""
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "H-OLD", "oldbox", "10.99.7.3", asset_status="使用中", os="RHEL 8.6")
    _insert_hardware(db_path, "H-NEW", "newbox", "10.99.7.3", asset_status="使用中", os="Ubuntu 22.04")
    _acct_null_serial(db_path, "10.99.7.3", "rhel", "8.6")
    d, rows = _os_dim(db_path)
    assert d["checked"] == 0 and rows == []


def test_有序號的證據不受IP台數影響(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "S-OLD", "oldbox", "10.99.7.4", asset_status="使用中", os="RHEL 8.6")
    _insert_hardware(db_path, "S-NEW", "newbox", "10.99.7.4", asset_status="使用中", os="Ubuntu 22.04")
    c = db.get_connection(db_path)
    c.execute("INSERT INTO host_account (ip, asset_serial, username, os_id, os_version, last_seen) "
              "VALUES ('10.99.7.4', 'S-NEW', 'root', 'rhel', '9', ?)", (_now(),))
    c.commit()
    c.close()
    d, rows = _os_dim(db_path)
    assert (d["checked"], d["ok"]) == (1, 0)
    assert [r["asset_serial"] for r in rows] == ["S-NEW"]
