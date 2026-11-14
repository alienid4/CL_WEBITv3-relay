"""重複登記：堆疊交換器／機箱元件移出「疑似真重複」（2.060.x，CIA 修正包規格十-1；GPT 第 7 點拆出的獨立小修）。

只改「重複登記清單的分類」：分組照舊是 machine_key（不動），儀表板、清單、健康檢查同一份分類；
堆疊／機箱元件不給可少筆數。規則在 duplicate_stack_rules.json（不寫死）。全部假資料（IP 10.99.x）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import _app_shared  # noqa: E402
import db  # noqa: E402
import host_lookup  # noqa: E402
import system_stats as ss  # noqa: E402
import verify_numbers as vn  # noqa: E402
import vip_view as vv  # noqa: E402


def _sw(serial, hw, model="C9300-48P Stack", ip="10.99.8.1"):
    return {"asset_serial": serial, "hostname": "core-sw01", "ip": ip, "api_id": "N-001", "big_ip_vip": None,
            "asset_purpose": "核心交換器", "asset_name": "核心交換器", "device_model": model, "hw_serial": hw}


def test_堆疊交換器_共用IP_型號寫stack_序號不同_不算疑似重複():
    c = vv.classify_duplicate_group([_sw("S1", "FOC1"), _sw("S2", "FOC2")])
    assert c["kind"] == vv.DUP_STACK and c["extra_rows"] == 0 and c["kind_label"] == "堆疊／機箱元件"


def test_少一個條件就還是疑似重複_保守():
    assert vv.classify_duplicate_group([_sw("S1", "FOC1"), _sw("S2", "FOC1")])["kind"] == vv.DUP_SUSPECT          # 序號一樣＝真的登記兩次
    assert vv.classify_duplicate_group([_sw("S1", None), _sw("S2", "NA")])["kind"] == vv.DUP_SUSPECT              # 沒有硬體序號可比
    assert vv.classify_duplicate_group([_sw("S1", "A", model="R750"), _sw("S2", "B", model="R750")])["kind"] == vv.DUP_SUSPECT   # 型號不像堆疊
    assert vv.classify_duplicate_group([_sw("S1", "A", ip=""), _sw("S2", "B", ip="")])["kind"] == vv.DUP_SUSPECT  # 沒有共用管理 IP


def test_機箱元件_名稱寫機箱_MAC不同也算():
    rows = [{**_sw("E1", None, model="BladeSystem"), "asset_name": "刀鋒機箱", "mac": "aa:bb:cc:00:00:01"},
            {**_sw("E2", None, model="BladeSystem"), "asset_name": "刀鋒機箱", "mac": "aa:bb:cc:00:00:02"}]
    assert vv.classify_duplicate_group(rows)["kind"] == vv.DUP_STACK


def test_規則在設定檔_改設定檔就改判定(tmp_path, monkeypatch):
    rules = json.loads(vv.STACK_RULES_PATH.read_text(encoding="utf-8"))
    assert r"\bstack\b" in rules["patterns"] and rules["require_distinct_hw"] is True
    assert rules["match_fields"] == ["device_model", "infra_type", "asset_name"]
    rules["patterns"] = ["只認這個字"]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vv, "STACK_RULES_PATH", p)
    assert vv.classify_duplicate_group([_sw("S1", "FOC1"), _sw("S2", "FOC2")])["kind"] == vv.DUP_SUSPECT


def test_清單_儀表板_同一份分類_machine_key不受影響_verify_numbers綠(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    for r in (_sw("S1", "FOC1"), _sw("S2", "FOC2")):
        db.insert_hardware(c, **r)
    db.insert_hardware(c, asset_serial="D1", hostname="web01", ip="10.99.8.9", api_id="N-001", hw_serial="X")
    db.insert_hardware(c, asset_serial="D2", hostname="web01", ip="10.99.8.9", api_id="N-001", hw_serial="X")
    c.commit()
    keys_before = {r[0]: ss.machine_key(r[1], r[2], r[0]) for r in c.execute("SELECT asset_serial, hostname, ip FROM hardware")}
    d = host_lookup.duplicates(c)
    by = {g["hostname"]: g for g in d["groups"]}
    assert by["core-sw01"]["kind"] == vv.DUP_STACK and by["core-sw01"]["extra_rows"] == 0
    assert by["web01"]["kind"] == vv.DUP_SUSPECT and d["extra_rows"] == 1
    assert d["by_kind"][vv.DUP_STACK] == 1 and vv.DUP_STACK in d["kind_order"]
    summ = vv.duplicate_summary(c)
    assert (summ["groups"], summ["suspect_groups"], summ["stack_groups"], summ["suspect_extra_rows"]) == (2, 1, 1, 1)
    assert len(_app_shared._duplicate_groups(c)) == 2                                  # 分組（machine_key）沒變：還是兩組
    assert keys_before == {r[0]: ss.machine_key(r[1], r[2], r[0]) for r in c.execute("SELECT asset_serial, hostname, ip FROM hardware")}
    before = vn.FAILS
    vn.check_duplicate_consistency(c)
    assert vn.FAILS == before


# ---- 獨立審查（a69a3268）P1／P2 ----
def test_P1_子字串不誤中_OpenStack_與自由文字欄位不看():
    rows = [{**_sw("S1", "FOC1", model="R750"), "asset_name": "OpenStack-ctrl"},
            {**_sw("S2", "FOC2", model="R750"), "asset_name": "OpenStack-ctrl"}]
    assert vv.classify_duplicate_group(rows)["kind"] == vv.DUP_SUSPECT
    rows = [{**_sw("S1", "FOC1", model="R750"), "remark": "stack module"}, {**_sw("S2", "FOC2", model="R750"), "remark": "stack module"}]
    assert vv.classify_duplicate_group(rows)["kind"] == vv.DUP_SUSPECT          # 附加說明不看
    rows = [_sw("S1", "FOC1", model="Sirf-9"), _sw("S2", "FOC2", model="Sirf-9")]
    assert vv.classify_duplicate_group(rows)["kind"] == vv.DUP_SUSPECT          # Sirf 不是 IRF


def test_P1_佔位IP不算共用管理IP():
    for ip in ("0.0.0.0", "NA", "N/A", "無", "-", "not-an-ip"):
        assert vv.classify_duplicate_group([_sw("S1", "A", ip=ip), _sw("S2", "B", ip=ip)])["kind"] == vv.DUP_SUSPECT, ip


def test_P2_規則檔不見或壞掉_不擋頁面_照舊算疑似重複(tmp_path, monkeypatch):
    monkeypatch.setattr(vv, "STACK_RULES_PATH", tmp_path / "nope.json")
    assert vv.classify_duplicate_group([_sw("S1", "FOC1"), _sw("S2", "FOC2")])["kind"] == vv.DUP_SUSPECT
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(vv, "STACK_RULES_PATH", bad)
    assert vv.classify_duplicate_group([_sw("S1", "FOC1"), _sw("S2", "FOC2")])["kind"] == vv.DUP_SUSPECT
    bad2 = tmp_path / "bad2.json"
    bad2.write_text(json.dumps({"patterns": ["("]}), encoding="utf-8")                # 正規式寫錯
    monkeypatch.setattr(vv, "STACK_RULES_PATH", bad2)
    assert vv.classify_duplicate_group([_sw("S1", "FOC1"), _sw("S2", "FOC2")])["kind"] == vv.DUP_SUSPECT


def test_P2_規則依mtime快取():
    assert vv._stack_rules() is vv._stack_rules()
