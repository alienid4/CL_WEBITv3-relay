"""位置重疊待處理：分類（分割區／重複登記／現場圖說位置不同／其他）、四種處理、可復原、台數不變、CIA 待更正。全部假資料。"""
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import overlap_queue as oq  # noqa: E402
import rack_diagram as rd  # noqa: E402
import rack_layout as rl  # noqa: E402
from openpyxl import Workbook  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    def hw(serial, host, ip, rack, model, os=None, hw_serial=None):
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="02_內湖機房",
                           rack_no=rack, device_model=model, os=os, hw_serial=hw_serial)
    hw("L-1", "aixlpar01", "10.99.1.1", "1-8/20-23U", "IBM S922", "AIX 7.2")        # 分割區（同機型同 U、Power）
    hw("L-2", "aixlpar02", "10.99.1.2", "1-8/20-23U", "IBM S922", "AIX 7.2")
    hw("D-1", "dup01", "10.99.1.5", "1-8/10U", "DELL R750")                            # 重複登記（同 IP）
    hw("D-2", "dup01-old", "10.99.1.5", "1-8/10U", "DELL R750")
    hw("P-1", "appb21", "10.99.1.7", "2-2/7-10U", "IBM S1024")                          # 現場圖說在 B3 13-16
    hw("P-2", "appb23", "10.99.1.8", "2-2/7-10U", "IBM S814")
    hw("P-9", "other99", "10.99.1.9", "2-3/1U", "X")                                     # 讓 2-3（＝B3）這櫃存在
    hw("X-1", "misc01", "10.99.1.11", "1-9/5U", "DELL R660")                            # 其他
    hw("X-2", "misc02", "10.99.1.12", "1-9/5U", "HPE DL380")
    conn.commit()


def _diagram() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["機櫃", "U起", "U迄", "內容", "IP"])
    ws.append(["B3", 13, 16, "IBM S1024 appb21", "10.99.1.7"])
    ws.append(["B2", 7, 10, "IBM S814 appb23", "10.99.1.8"])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _totals(conn):
    d = rl.datacenter(conn)
    return d["total_racked"], d["total_devices"], {t["rack"]: t["device_count"] for r in d["rooms"] for t in r["racks"]}


def test_分類與四種處理_可復原_台數不變(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        rd.import_diagram(conn, "內湖", "diag.xlsx", _diagram(), "tester")
        g = {tuple(x["keys"]): x for x in oq.groups(conn, "內湖機房")["groups"]}
        by_name = {d["name"]: d["key"] for x in g.values() for d in x["devices"]}
        part = next(x for x in g.values() if x["rack"] == "1-8" and x["u_lo"] == 20)
        dup = next(x for x in g.values() if x["rack"] == "1-8" and x["u_lo"] == 10)
        moved = next(x for x in g.values() if x["rack"] == "2-2")
        other = next(x for x in g.values() if x["rack"] == "1-9")
        assert (part["cause"], dup["cause"], moved["cause"], other["cause"]) == ("partition", "duplicate", "diagram", "other")
        reloc = moved["relocate"][by_name["appb21"]]
        assert reloc["rack"] == "2-3" and reloc["diagram_rack"] == "B3" and (reloc["u_start"], reloc["u_end"]) == (13, 16)
        # 分割區（Power 同機型同 U）與重複登記（同 IP、同 U）系統自動判斷為同一台 → 不進待處理
        assert part["resolved"] and part["auto"]
        assert not dup["resolved"]                       # 同 IP 但主機名不同（dup01／dup01-old）→ 留給人判斷
        assert oq.groups(conn, "內湖機房")["open"] == 3
        before = _totals(conn)
        det = {x["name"]: x for x in rl.rack_detail(conn, "內湖機房", "1-8")["devices"]}
        assert det["aixlpar01"]["overlap_with"] == [] and det["aixlpar02"]["merged_into"] == det["aixlpar01"]["key"]
        assert det["aixlpar01"]["merge_auto"] is True and "分割區" in det["aixlpar01"]["merge_reason"]

        # 使用者說「拆開（其實是不同台）」→ 回到待處理；再人工合併 → 以使用者為準
        ds = oq.decide(conn, "內湖機房", "1-8", part["keys"], "split", "alice", "其實是兩台")
        # 2026-10-26 起台數＝箱子：拆開＝兩台 → 該櫃多 1 台；再合併 → 回原數
        after_split = _totals(conn)
        assert oq.groups(conn, "內湖機房")["open"] == 4 and after_split[0] == before[0] + 1
        assert after_split[2]["1-8"] == before[2]["1-8"] + 1
        dm =oq.decide(conn, "內湖機房", "1-8", part["keys"], "merge", "alice", "同一台 S922 的兩個 LPAR")
        assert _totals(conn) == before                                        # 台數完全不變
        det = {x["name"]: x for x in rl.rack_detail(conn, "內湖機房", "1-8")["devices"]}
        assert det["aixlpar01"]["merge_auto"] is False and [m["name"] for m in det["aixlpar01"]["merged"]] == ["aixlpar02"]
        assert oq.groups(conn, "內湖機房")["open"] == 3

        # 這筆已不在櫃上（重複登記留一筆）→ 既有機櫃狀態＋CIA 待更正
        dr = oq.decide(conn, "內湖機房", "1-8", dup["keys"], "removed", "alice", "重複登記", target_key=by_name["dup01-old"])
        assert conn.execute("SELECT state FROM rack_state_override WHERE key = ?", (by_name["dup01-old"],)).fetchone()[0] == "removed"
        assert any(r["主機名稱"] == "dup01-old" and r["更正類型"] == "已不在櫃上" for r in rl.pending_cia_rows(conn))

        # 位置更正到現場圖 → 既有位置更正＋CIA 待更正
        with pytest.raises(ValueError):
            oq.decide(conn, "內湖機房", "2-2", moved["keys"], "relocate", "alice", "", target_key=by_name["appb21"],
                      relocate={"rack": "2-3", "u_start": 13})
        dl = oq.decide(conn, "內湖機房", "2-2", moved["keys"], "relocate", "alice", "依現場圖", target_key=by_name["appb21"],
                       relocate={"rack": reloc["rack"], "u_start": reloc["u_start"]})
        m = next(x for x in rl.machines(conn) if x["key"] == by_name["appb21"])
        assert (m["rack"], m["u_start"], m["u_end"]) == ("2-3", 13, 16)
        assert any(r["主機名稱"] == "appb21" and r["新Rack"] == "2-3" for r in rl.pending_cia_rows(conn))

        # 確認是兩台
        da = oq.decide(conn, "內湖機房", "1-9", other["keys"], "accept_half", "alice", "半寬")
        assert oq.groups(conn, "內湖機房")["open"] == 0
        with pytest.raises(PermissionError):
            oq.decide(conn, "內湖機房", "1-9", other["keys"], "accept_half", None)

        # 全部復原 → 回到原狀
        for d in (dm, ds, dr, dl, da):
            oq.undo(conn, d["id"], "alice", "測試復原")
        assert conn.execute("SELECT COUNT(*) FROM rack_state_override").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rack_position_override WHERE status = 'active'").fetchone()[0] == 0
        assert oq.groups(conn, "內湖機房")["open"] == 3 and _totals(conn) == before
        with pytest.raises(ValueError):
            oq.undo(conn, dm["id"], "alice")                                  # 復原過不能再復原
        assert len(oq.history(conn, "內湖機房")) == 5                        # 紀錄還在（只標復原）
    finally:
        conn.close()


def test_復原時狀態被別人改過就不動(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        dup = next(x for x in oq.groups(conn, "內湖機房")["groups"] if x["rack"] == "1-8" and x["u_lo"] == 10)
        k = dup["keys"][0]
        d = oq.decide(conn, "內湖機房", "1-8", dup["keys"], "removed", "alice", None, target_key=k)
        rl.set_state_override(conn, k, "off", "後來改成關機未下架", "bob")
        r = oq.undo(conn, d["id"], "alice")
        assert r["warning"] and conn.execute("SELECT state FROM rack_state_override WHERE key = ?", (k,)).fetchone()[0] == "off"
    finally:
        conn.close()


def test_審查意見_復原還原原本標記與位置_櫃名正規化_組員變了要重判_跨櫃不沿用(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        g = {x["rack"] + str(x["u_lo"]): x for x in oq.groups(conn, "內湖機房")["groups"]}
        dup, other = g["1-810"], g["1-95"]
        k = dup["keys"][0]
        rl.set_state_override(conn, k, "off", "原本就標關機未下架", "bob")
        d = oq.decide(conn, "內湖機房", "1-8", dup["keys"], "removed", "alice", "留一筆", target_key=k)
        with pytest.raises((ValueError, LookupError)):                          # 同一台有未復原的處理：不能再來一筆
            oq.decide(conn, "內湖機房", "1-8", dup["keys"], "removed", "alice", None, target_key=k)
        oq.undo(conn, d["id"], "alice")
        assert conn.execute("SELECT state FROM rack_state_override WHERE key = ?", (k,)).fetchone()[0] == "off"   # 還原，不是清掉
        # 位置更正：原本已有一筆更正 → 復原後回到原本那筆；櫃名用「1-09」輸入也要能復原
        x1 = other["keys"][0]
        rl.set_position(conn, x1, "內湖機房", "1-9", 30, 1, "舊的更正", "bob")
        other = next(x for x in oq.groups(conn, "內湖機房")["groups"] if x["rack"] == "1-8" and x["u_lo"] == 10)
        with pytest.raises(ValueError):
            oq.decide(conn, "內湖機房", "1-8", other["keys"], "relocate", "alice", "x", target_key=other["keys"][0],
                      relocate={"rack": "Z-99", "u_start": 1})                  # 這個機房沒有這一櫃
        r = oq.decide(conn, "內湖機房", "1-8", other["keys"], "relocate", "alice", "依現場圖", target_key=other["keys"][1],
                      relocate={"rack": "1-09", "u_start": 40})
        assert next(m for m in rl.machines(conn) if m["key"] == other["keys"][1])["rack"] == "1-9"
        u = oq.undo(conn, r["id"], "alice")
        assert u["warning"] is None
        pos = conn.execute("SELECT rack, u_start FROM rack_position_override WHERE key = ? AND status = 'active'", (x1,)).fetchone()
        assert tuple(pos) == ("1-9", 30)                                         # 別台原本的更正沒被動
        with pytest.raises(ValueError):
            oq.undo(conn, r["id"], "alice")
    finally:
        conn.close()


def test_決策只對當下那一櫃和那幾台有效(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        other = next(x for x in oq.groups(conn, "內湖機房")["groups"] if x["rack"] == "1-9")
        oq.decide(conn, "內湖機房", "1-9", other["keys"], "accept_half", "alice", None)
        # 第三台搬進同一段 U → 組員變了，要重新判斷（不沿用舊決策）
        db.insert_hardware(conn, asset_serial="X-3", hostname="misc03", ip="10.99.1.13", physical_location="02_內湖機房",
                           rack_no="1-9/5U", device_model="Lenovo SR630")
        conn.commit()
        g = next(x for x in oq.groups(conn, "內湖機房")["groups"] if x["rack"] == "1-9")
        assert len(g["keys"]) == 3 and g["resolved"] is False
    finally:
        conn.close()


def test_系統判斷為同一台_規則與不該併的(tmp_path):
    conn = _conn(tmp_path)
    try:
        def hw(serial, host, ip, rack, model, os=None, hw_serial=None):
            db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                               rack_no=rack, device_model=model, os=os, hw_serial=hw_serial)
        hw("F-1", "FakeStore-FS5300", "10.99.5.154", "B10/35U", "IBM FS5300", hw_serial="78F21WV")   # 同主機名同機型同 U ×3
        hw("F-2", "FakeStore-FS5300", "10.99.5.155", "B10/35U", "IBM FS5300")
        hw("F-3", "FakeStore-FS5300", "10.99.5.156", "B10/35U", "IBM FS5300")
        hw("S-1", "core-sw1", "10.99.6.1", "B10/40U", "Cisco C9300")                                  # 堆疊交換器：同 IP、不同 U
        hw("S-2", "core-sw2", "10.99.6.1", "B10/42U", "Cisco C9300")
        hw("H-1", "fw-ha-01", "10.99.8.1", "B10/9U", "FortiGate 91G")                                # HA 一對：同 VIP 同 U 同機型、不同名
        hw("H-2", "fw-ha-02", "10.99.8.1", "B10/9U", "FortiGate 91G")
        hw("Q-1", "box-a", "10.99.7.1", "B10/20U", "DELL R750")                                      # 同 U、不同機型不同名：不自動併
        hw("Q-2", "box-b", "10.99.7.2", "B10/20U", "HPE DL380")
        conn.commit()
        before = rl.datacenter(conn)
        det = rl.rack_detail(conn, "甲機房", "B10")
        by = {x["key"]: x for x in det["devices"]}
        heads = [x for x in det["devices"] if x.get("merged")]
        assert len(heads) == 1 and heads[0]["asset_serial"] == "F-1"                     # 代表＝有硬體序號的那筆
        assert len(heads[0]["merged"]) == 2 and heads[0]["merge_auto"] is True
        assert not any(x.get("merged_into") for x in det["devices"] if x["name"].startswith("core-sw"))   # 堆疊交換器兩台
        assert by[next(k for k, v in by.items() if v["name"] == "box-a")]["overlap_with"]                 # 不明的留著提醒
        assert not any(x.get("merged") or x.get("merged_into") for x in det["devices"] if x["name"].startswith("fw-ha"))
        assert len(det["devices"]) == 9                                                   # 台數不變
        g = oq.groups(conn, "甲機房", "B10")
        assert g["open"] == 2 and g["auto_merged"] == 1
        # 用電只算一次：known＋unknown 仍＝active
        pw = det["power"]
        assert pw["known_count"] + pw["unknown_count"] == pw["active_count"] == 9
        after = rl.datacenter(conn)
        assert (before["total_racked"], before["total_devices"]) == (after["total_racked"], after["total_devices"])
        # CIA 待更正：同一台的多筆登記建議合併
        rows = [r for r in rl.pending_cia_rows(conn) if r["更正類型"].startswith("建議合併")]
        assert len(rows) == 1 and "F-1" in rows[0]["資產序號"] and "F-3" in rows[0]["資產序號"]
    finally:
        conn.close()


def test_審查意見_無效IP不併_代表挑在用有瓦數的_傳遞不併(tmp_path):
    conn = _conn(tmp_path)
    try:
        def hw(serial, host, ip, rack, model, status=None):
            db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                               rack_no=rack, device_model=model, asset_status=status)
        hw("Z-1", "zero-a", "0.0.0.0", "C1/10U", "DELL R750")          # 同 U、IP 都是 0.0.0.0、不同名 → 不併
        hw("Z-2", "zero-b", "0.0.0.0", "C1/10U", "DELL R750")
        hw("T-1", "chain-a", "10.99.9.1", "C1/20U", "DELL R750")        # A~B 同 IP 同機型、B~C 同主機名同機型，A~C 沒規則
        hw("T-2", "chain-b", "10.99.9.1", "C1/20U", "DELL R750")
        hw("T-3", "chain-b", "10.99.9.3", "C1/20U", "DELL R750")
        conn.commit()
        det = {x["name"]: x for x in rl.rack_detail(conn, "甲機房", "C1")["devices"]}
        assert not det["zero-a"].get("merged") and not det["zero-b"].get("merged_into")
        assert det["zero-a"]["overlap_with"]                            # 還在提醒
        assert not det["chain-a"].get("merged") and not det["chain-a"].get("merged_into")   # chain-a 跟 chain-b 只同 IP、不同名 → 不併
        assert oq._ipv4_set("0.0.0.0, 127.0.0.1, 10.99.1.1") == {"10.99.1.1"}
    finally:
        conn.close()
