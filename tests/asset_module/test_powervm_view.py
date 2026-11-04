"""PowerVM 可觀測架構圖（3.2 架構圖「PowerVM」頁籤，第一版）。

守住的事：
  * 空表＝空狀態（不用示範補畫）、部分失敗（壞掉的 HMC 資料不拖垮其他 HMC）；
  * CPU／記憶體「0」和「沒回報（None）」分開；沒回報的不被加總成 0；
  * 重複收集不重複節點（同 IP 只一列；同一個 HMC 內重複的實體機／LPAR 只畫一次；兩個 HMC 管同一台只算一台）；
  * 過期標記；VIOS／網路／SAN／儲存四層一律「未取得」；
  * 示範資料是獨立資料集：不碰資料庫、DEMO- 命名、10.99.x；
  * 本機純讀：模組裡沒有任何連線／寫入／排程。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import powervm_view  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 0, 0)


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _put(conn, ip, inv, collected_at="2026-11-02 08:00:00", hostname=None, raw=None):
    conn.execute(
        "INSERT INTO hmc_console (ip, hostname, hmc_version, system_count, lpar_count, data_json, collected_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(ip) DO UPDATE SET data_json=excluded.data_json, collected_at=excluded.collected_at",
        (ip, hostname, "V10", 0, 0, raw if raw is not None else json.dumps(inv), collected_at))
    conn.commit()


def _sys(name, serial, lpars):
    return {"name": name, "type_model": "9080-HEX", "serial": serial, "state": "Operating", "firmware": "FW1", "lpars": lpars}


def _lp(i, name, **kw):
    d = {"lpar_id": str(i), "name": name, "env": "aixlinux", "state": "Running"}
    d.update(kw)
    return d


def test_空表_空狀態_不用示範補畫(tmp_path):
    r = powervm_view.overview(_conn(tmp_path), NOW)
    assert r["empty"] is True and r["mode"] == "real" and r["consoles"] == []
    assert r["summary"]["consoles"] == 0 and r["summary"]["lpars"] == 0
    assert "6.2" in r["next_step"]                       # 說明下一步
    assert "DEMO" not in json.dumps(r, ensure_ascii=False)


def test_CPU記憶體_0與未取得分開_合計不把未取得當0(tmp_path):
    c = _conn(tmp_path)
    _put(c, "10.99.9.1", {"systems": [_sys("FR1", "S1", [
        _lp(1, "A", proc_units="2.0", procs="4", mem_mb="4096"),
        _lp(2, "Z", proc_units="0", procs="0", mem_mb="0"),            # 回報 0
        _lp(3, "N"),                                                    # 沒回報
        _lp(4, "B", proc_units="", procs="abc", mem_mb=None),           # 空字串／亂碼／None 都是未取得
    ])]})
    lp = {l["name"]: l for l in powervm_view.overview(c, NOW)["consoles"][0]["systems"][0]["lpars"]}
    assert lp["Z"]["proc_units"] == 0 and lp["Z"]["mem_mb"] == 0 and lp["Z"]["procs"] == 0
    for n in ("N", "B"):
        assert lp[n]["proc_units"] is None and lp[n]["procs"] is None and lp[n]["mem_mb"] is None
    s = powervm_view.overview(c, NOW)["consoles"][0]["systems"][0]
    assert s["cpu_sum"] == 2.0 and s["cpu_unknown"] == 2          # 合計只含有取得的，未取得台數另列
    assert s["mem_sum_mb"] == 4096 and s["mem_unknown"] == 2
    assert powervm_view.overview(c, NOW)["summary"]["unknown_cpu_lpars"] == 2


def test_全部未取得_合計是None不是0(tmp_path):
    c = _conn(tmp_path)
    _put(c, "10.99.9.1", {"systems": [_sys("FR1", "S1", [_lp(1, "N")])]})
    s = powervm_view.overview(c, NOW)["consoles"][0]["systems"][0]
    assert s["cpu_sum"] is None and s["mem_sum_mb"] is None


def test_部分失敗_壞資料的HMC標錯誤_其他照常(tmp_path):
    c = _conn(tmp_path)
    _put(c, "10.99.9.1", {"systems": [_sys("FR1", "S1", [_lp(1, "A")])]})
    _put(c, "10.99.9.2", None, raw="{not json")
    _put(c, "10.99.9.3", None, raw="[1,2]")
    r = powervm_view.overview(c, NOW)
    by = {x["ip"]: x for x in r["consoles"]}
    assert by["10.99.9.1"]["status"]["code"] == "fresh" and len(by["10.99.9.1"]["systems"]) == 1
    assert by["10.99.9.2"]["status"]["code"] == "error" and by["10.99.9.2"]["systems"] == []
    assert by["10.99.9.3"]["status"]["code"] == "error"
    assert r["summary"]["error_consoles"] == 2 and r["summary"]["lpars"] == 1 and r["empty"] is False


def test_重複收集不重複節點(tmp_path):
    c = _conn(tmp_path)
    inv = {"systems": [_sys("FR1", "S1", [_lp(1, "A")])]}
    _put(c, "10.99.9.1", inv)
    _put(c, "10.99.9.1", inv, collected_at="2026-11-02 09:00:00")        # 同 IP 再收一次＝覆蓋
    r = powervm_view.overview(c, NOW)
    assert r["summary"]["consoles"] == 1 and r["summary"]["frames"] == 1 and r["summary"]["lpars"] == 1
    # 同一個 HMC 內重複回報的實體機／LPAR 只畫一次
    dup = {"systems": [_sys("FR1", "S1", [_lp(1, "A"), _lp(1, "A")]), _sys("FR1", "S1", [_lp(2, "B")])]}
    _put(c, "10.99.9.2", dup)
    s = [x for x in powervm_view.overview(c, NOW)["consoles"] if x["ip"] == "10.99.9.2"][0]["systems"]
    assert len(s) == 1 and s[0]["lpar_count"] == 1
    # 兩個 HMC 管同一台實體機（HMC 備援）：各 HMC 底下都畫，但實體機／LPAR 總數只算一次
    _put(c, "10.99.9.3", inv)
    r = powervm_view.overview(c, NOW)
    assert r["summary"]["consoles"] == 3 and r["summary"]["frames"] == 1 and r["summary"]["lpars"] == 1


def test_過期標記與未取得收集時間(tmp_path):
    c = _conn(tmp_path)
    inv = {"systems": [_sys("FR1", "S1", [_lp(1, "A")])]}
    _put(c, "10.99.9.1", inv, collected_at=(NOW - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
    _put(c, "10.99.9.2", inv, collected_at=(NOW - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"))
    _put(c, "10.99.9.3", inv, collected_at=None)
    by = {x["ip"]: x["status"] for x in powervm_view.overview(c, NOW)["consoles"]}
    assert by["10.99.9.1"]["code"] == "fresh"
    assert by["10.99.9.2"]["code"] == "stale" and "30" in by["10.99.9.2"]["label"]
    assert by["10.99.9.3"]["code"] == "unknown"
    assert powervm_view.overview(c, NOW)["summary"]["stale_consoles"] == 1
    # 過期天數可調
    db.set_setting(c, "powervm_stale_days", "2")
    c.commit()
    assert powervm_view.overview(c, NOW)["consoles"][0]["status"]["code"] == "stale"


def test_四層一律未取得_不猜VIOS(tmp_path):
    r = powervm_view.overview(_conn(tmp_path), NOW)
    layers = {l["key"]: l for l in r["layers"]}
    assert [l["key"] for l in r["layers"]] == ["hmc", "frame", "vios", "lpar", "network", "san", "storage"]
    for k in ("vios", "network", "san", "storage"):
        assert layers[k]["available"] is False and "尚未收集" in layers[k]["reason"]
    assert "不等於實際存取" in layers["san"]["reason"]
    # 就算 HMC 回報某 LPAR 類型是 vioserver，也只原樣顯示，不長出 VIOS 關係欄位
    c = db.get_connection(tmp_path / "t.db")
    _put(c, "10.99.9.1", {"systems": [_sys("FR1", "S1", [_lp(1, "VIOS-A", env="vioserver")])]})
    l = powervm_view.overview(c, NOW)["consoles"][0]["systems"][0]["lpars"][0]
    assert l["env"] == "vioserver" and not any(k in l for k in ("vios", "vios_pair", "network", "san", "storage"))


def test_下鑽連結只有資產庫序號完全相符才給(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="S1", hostname="fr1", ip="10.99.8.1", asset_status="使用中")
    c.commit()
    _put(c, "10.99.9.1", {"systems": [_sys("FR1", "S1", []), _sys("FR2", "S2", []), {"name": "NOSERIAL", "lpars": []}]})
    s = {x["name"]: x for x in powervm_view.overview(c, NOW)["consoles"][0]["systems"]}
    assert s["FR1"]["machine_link"] == "/v2/machine/S1"
    assert s["FR2"]["machine_link"] is None and s["NOSERIAL"]["machine_link"] is None


def test_示範資料獨立_不碰資料庫_去識別():
    d = powervm_view.demo_overview(NOW)
    assert d["mode"] == "demo" and d["empty"] is False
    txt = json.dumps(d, ensure_ascii=False)
    assert "DEMO-" in txt and "10.99." in txt and "10.92" not in txt and "10.93" not in txt
    names = [c["hostname"] for c in d["consoles"]] + [s["name"] for c in d["consoles"] for s in c["systems"]]
    assert all(n.startswith("DEMO-") for n in names)
    assert all(s["machine_link"] is None for c in d["consoles"] for s in c["systems"])     # 示範不下鑽
    codes = {c["status"]["code"] for c in d["consoles"]}
    assert {"fresh", "stale"} <= codes                                                       # 示範涵蓋新鮮與過期
    zero = [l for c in d["consoles"] for s in c["systems"] for l in s["lpars"] if l["proc_units"] == 0]
    unk = [l for c in d["consoles"] for s in c["systems"] for l in s["lpars"] if l["proc_units"] is None]
    assert zero and unk                                                                      # 0 與未取得都有示範
    # demo_overview 不吃資料庫連線：簽名裡沒有 conn
    import inspect
    assert "conn" not in inspect.signature(powervm_view.demo_overview).parameters


def test_示範不進正式表(tmp_path):
    c = _conn(tmp_path)
    powervm_view.demo_overview(NOW)
    assert c.execute("SELECT COUNT(*) FROM hmc_console").fetchone()[0] == 0
    r = powervm_view.overview(c, NOW)
    assert r["empty"] is True and "DEMO" not in json.dumps(r, ensure_ascii=False)


def test_模組純讀_沒有連線寫入排程():
    src = (BACKEND / "powervm_view.py").read_text(encoding="utf-8")
    for bad in ("hmc_collector", "onboard_engine", "_ssh_exec", "paramiko", "subprocess", "socket", "INSERT", "UPDATE ", "DELETE", "commit(", "schedule", "threading"):
        assert bad not in src.replace("不連 HMC、不排程、不寫任何表", ""), bad


def test_端點掛require_auth_且不回帳密欄位():
    src = (BACKEND / "routers" / "arch.py").read_text(encoding="utf-8")
    for name in ("arch_powervm", "arch_powervm_demo"):
        i = src.index(f"def {name}(")
        assert "Depends(require_auth)" in src[i:i + 200], name
    # 回傳結構不含收集者帳號／密碼／原始輸出
    c_src = (BACKEND / "powervm_view.py").read_text(encoding="utf-8")
    assert "collected_by" not in c_src and "password" not in c_src and "_raw" not in c_src


def test_架構圖頁有PowerVM頁籤_且選單編號沒動():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    a = (fe / "pages" / "v2" / "arch.vue").read_text(encoding="utf-8")
    assert "{ k: 'powervm', label: 'PowerVM' }" in a and "<V2PowerVm v-if=\"tab === 'powervm'\" />" in a
    nav = (fe / "composables" / "useNavV2.ts").read_text(encoding="utf-8")
    assert "{ no: '3.2', label: '架構圖', to: '/v2/arch' }" in nav
    comp = (fe / "components" / "v2" / "PowerVm.vue").read_text(encoding="utf-8")
    assert "/api/arch/powervm" in comp and "/api/hmc/collect" not in comp       # 本頁不呼叫收集端點
    assert "setInterval" not in comp and "setTimeout" not in comp                # 沒有任何自動輪詢／排程


def test_合法JSON但欄位型別損毀_只標該HMC無法解析_不讓整頁失敗():
    import json
    rows = [{"ip": "10.99.0.1", "hostname": "h1", "hmc_version": "V10", "collected_at": "2026-11-02 05:00:00", "data_json": json.dumps({"systems": 123})},
            {"ip": "10.99.0.2", "hostname": "h2", "hmc_version": "V10", "collected_at": "2026-11-02 05:00:00",
             "data_json": json.dumps({"systems": [{"name": "F", "serial": "S1", "lpars": [{"name": "L", "procs": "NaN"}]}]})}]
    r = powervm_view._assemble(rows, NOW, 14, "real", {})
    assert r["summary"]["consoles"] == 2 and r["summary"]["error_consoles"] >= 1
