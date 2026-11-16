"""數字健檢：同一台多筆登記（現役＋退役／現役多筆 IP 不一致）的重算與歸因（2026-10-31，D3 只動檢核側）。

背景：正式機 lost 89／not_covered 28 未解釋，真因是畫面把退役拆成 "ret:" 自成一組、現役照判，
而重算「任一筆退役就整台跳過」；以及現役多筆時畫面取最小序號、重算取「任一筆」。
假資料一律 192.0.2.x（文件保留網段）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

import db  # noqa: E402
import number_proof as np_  # noqa: E402
import pipeline  # noqa: E402
import system_stats  # noqa: E402
from test_b09_number_proof import _conn  # noqa: E402


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    import machine_identity
    machine_identity._STATE.update({"mode": machine_identity.MODE_LEGACY, "pending": frozenset(), "host_fix": {}})


def _conn_h(tmp_path):
    """正式機口徑（machine_key_mode=hostname：同主機名＝同一台，不管 IP 有無）。"""
    import machine_identity
    c = _conn(tmp_path)
    db.set_setting(c, "machine_key_mode", "hostname")
    machine_identity.refresh(c)
    return c


def _add(c, sn, host, ip, status="使用中"):
    db.insert_hardware(c, asset_serial=sn, hostname=host, ip=ip, os="RHEL 9", asset_status=status)
    c.commit()


def _mk(host, ip, sn):
    return system_stats.machine_key(host, ip, sn)


def _drop(monkeypatch, key, names):
    """模擬『重算漏掉某台』：造出可歸因的差異，驗歸因文字。"""
    real = np_.independent

    def fake(conn):
        r = real(conn)
        for n in names:
            r["sets"][n].discard(key)
        return r

    monkeypatch.setattr(np_, "independent", fake)


def _expl(c, key):
    n = {x["key"]: x for x in np_.health(c)["numbers"]}[key]
    return n, [e["why"] for e in n["explanations"]]


def test_同台現役加退役_重算不再整台漏掉_數字一致(tmp_path):
    c = _conn_h(tmp_path)
    _add(c, "R-1", "dualret", "192.0.2.80", "使用中")     # 現役：在涵蓋網段、掃不到＝失聯
    _add(c, "R-2", "dualret", "192.0.2.80", "報廢")       # 同一台的退役筆
    ind = np_.independent(c)
    k = _mk("dualret", "192.0.2.80", "R-1")
    assert k in ind["sets"]["lost"], "現役那筆照判，不因另一筆退役就整台排除"
    assert ind["multi"][k]["n_ret"] == 1 and ind["multi"][k]["n_act"] == 1
    h = {n["key"]: n for n in np_.health(c)["numbers"]}
    assert all(n["mismatch"] == 0 for n in h.values()), {k2: n["explanations"] for k2, n in h.items() if n["mismatch"]}


def test_同台現役加退役_差異歸因為退役分開判(tmp_path, monkeypatch):
    c = _conn_h(tmp_path)
    _add(c, "R-1", "dualret", "192.0.2.80")
    _add(c, "R-2", "dualret", "192.0.2.80", "報廢")
    k = _mk("dualret", "192.0.2.80", "R-1")
    _drop(monkeypatch, k, ["lost"])
    n, whys = _expl(c, "lost")
    assert n["mismatch"] == 1 and n["unexplained"] == 0
    assert "同一台另有退役登記：畫面把現役與退役分開判" in whys[0]
    assert "畫面代表筆 1 筆" in whys[0] and "重算依據現役 1 筆、退役 1 筆" in whys[0]
    assert "已退役" not in whys[0].split("（")[0]


def test_全部都退役_整台排除(tmp_path):
    c = _conn_h(tmp_path)
    _add(c, "X-1", "allret", "192.0.2.81", "報廢")
    _add(c, "X-2", "allret", "192.0.2.81", "停用")
    ind = np_.independent(c)
    k = _mk("allret", "192.0.2.81", "X-1")
    assert k in ind["sets"]["total"]
    assert all(k not in ind["sets"][n] for n in ("onboarded", "lost", "not_covered"))
    assert k not in ind["multi"]
    h = {n["key"]: n for n in np_.health(c)["numbers"]}
    assert all(n["mismatch"] == 0 for n in h.values())


def test_現役多筆IP有無不一致_歸因第二種(tmp_path, monkeypatch):
    c = _conn_h(tmp_path)
    _add(c, "P-0", "multiip", "")                         # 沒 IP
    _add(c, "P-1", "multiip", "192.0.2.82")               # 有 IP、涵蓋、掃不到
    k = _mk("multiip", "", "P-0")
    ind = np_.independent(c)
    assert ind["multi"][k]["n_act"] == 2 and ind["multi"][k]["n_flag_kinds"] > 1
    n, whys = _expl(c, "lost")        # 真實差異：畫面取最小序號（沒 IP 那筆），重算任一筆有 IP 就判失聯
    assert n["mismatch"] == 1 and n["unexplained"] == 0
    assert "同一台多筆登記的 IP 有無／涵蓋不一致" in whys[0]
    assert "重算依據 2 筆現役、其中 1 筆有 IP" in whys[0]


def test_單筆正常_不帶多筆佐證(tmp_path):
    c = _conn_h(tmp_path)
    ind = np_.independent(c)
    assert ind["multi"] == {}, "web1 兩筆但特徵一致、其餘單筆：不該有多筆佐證"
    h = {n["key"]: n for n in np_.health(c)["numbers"]}
    assert all(n["mismatch"] == 0 for n in h.values())


def test_pick取畫面實際那筆_不是被蓋掉的退役筆():
    its = [{"asset_serial": "R-2", "stage": "retired", "stage_label": "已退役"},
           {"asset_serial": "R-1", "stage": "lost", "stage_label": "失聯"}]
    assert np_._pick("lost", its, True)["asset_serial"] == "R-1"     # 畫面側：stage 落在該數字內那筆
    assert np_._pick("lost", its[::-1], True)["asset_serial"] == "R-1"   # 順序不影響（舊版後蓋前會取到退役）
    assert np_._pick("lost", its, False)["stage"] == "lost"          # 重算側：現役代表筆優先
    assert np_._pick("lost", [], True) is None


def test_真的無法歸因的仍標未解釋_不是萬用(tmp_path, monkeypatch):
    c = _conn_h(tmp_path)
    _add(c, "S-1", "solo", "192.0.2.83")                  # 單筆、無退役、無多筆不一致
    k = _mk("solo", "192.0.2.83", "S-1")
    assert k in np_.independent(c)["sets"]["lost"]
    _drop(monkeypatch, k, ["lost"])
    n, whys = _expl(c, "lost")
    assert n["mismatch"] == 1 and n["unexplained"] == 1
    assert "未解釋" in whys[0]


def test_diff_list帶出新歸因欄位不變(tmp_path, monkeypatch):
    c = _conn_h(tmp_path)
    _add(c, "R-1", "dualret", "192.0.2.80")
    _add(c, "R-2", "dualret", "192.0.2.80", "報廢")
    _drop(monkeypatch, _mk("dualret", "192.0.2.80", "R-1"), ["lost"])
    d = np_.diff_list(c)
    row = [i for i in d["items"] if i["key"] == "lost"][0]
    assert row["unexplained"] is False and row["reason"].startswith("同一台另有退役登記")
    assert {"machine", "hostname", "ip", "side", "reason", "label"} <= set(row)
    assert pipeline  # 只為確認 import 正常
