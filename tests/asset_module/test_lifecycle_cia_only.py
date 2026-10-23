"""資產狀態只有 CIA 可寫（使用者 2026-10-20）＋已退役組成＋掃描紅燈合併涵蓋。

使用者原話：「停用、報廢、閒置，如果是從原來的 CIA 異動，該是什麼就是什麼，我們自己不能變成這種狀態」
「我沒有權限去改成停用、報廢或閒置」「退役有 2,029，這一定是有算錯」。
例子 IP 一律 10.99.x。
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import cia_pending  # noqa: E402
import db  # noqa: E402
import lifecycle  # noqa: E402
import manage_state  # noqa: E402

NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
OLD = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")


class _Tmp:
    """Windows 上連線沒關乾淨時 TemporaryDirectory 刪不掉 → 只建不刪（OS 會清 temp）。"""
    def __enter__(self):
        return tempfile.mkdtemp()

    def __exit__(self, *a):
        return False


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _st(c, s, col="asset_status"):
    r = c.execute(f"SELECT {col} FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
    return None if r is None else r[0]


def test_退役口徑全站只有一份():
    import number_proof

    assert manage_state.RETIRED_STATUS is lifecycle.RETIRED_STATUS
    assert number_proof.RETIRED is lifecycle.RETIRED_STATUS
    assert lifecycle.retired_sql_list() == "('停用','報廢','閒置')"
    # 原始碼裡不准再寫死三個值（定義檔本身除外）
    hits = []
    for f in BACKEND.glob("*.py"):
        if f.name == "lifecycle.py":
            continue
        t = f.read_text(encoding="utf-8")
        for bad in ('{"停用", "報廢", "閒置"}', '("停用", "報廢", "閒置")', "('停用','報廢','閒置')"):
            if bad in t:
                hits.append(f"{f.name}: {bad}")
    assert not hits, hits


def test_批次標下線只記待CIA異動_不改資產狀態也不改台數():
    with _Tmp() as tmp:
        c = _conn(tmp)
        db.insert_hardware(c, asset_serial="HW-1", hostname="a1", ip="10.99.1.1", asset_status="使用中",
                           environment="正式")
        c.commit()
        before = manage_state.summarize(c)["counts"]
        r = cia_pending.batch_set_status(c, ["HW-1"], "停用", "現場確認已下線", "tester")
        assert r["updated"] == ["HW-1"] and r["pending_only"]
        assert _st(c, "HW-1") == "使用中"                       # CIA 的值不動
        assert _st(c, "HW-1", "pending_status") == "停用"
        assert manage_state.summarize(c)["counts"] == before     # 台數不變
        assert len(cia_pending.list_changes(c)) == 1              # CIA 待異動清單照樣有一筆
        # 改回 CIA 的值＝取消待異動
        cia_pending.batch_set_status(c, ["HW-1"], "使用中", "看錯了", "tester")
        assert _st(c, "HW-1", "pending_status") is None


def test_資產頁編輯資產狀態_改記待CIA異動():
    from fastapi.testclient import TestClient

    with _Tmp() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        import os

        os.environ["ASSET_DB_PATH"] = str(p)
        import api

        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
        try:
            c = db.get_connection(p)
            db.insert_hardware(c, asset_serial="HW-2", hostname="a2", ip="10.99.1.2", asset_status="使用中")
            c.commit()
            cl = TestClient(api.app)
            r = cl.put("/api/assets/HW-2", json={"fields": {"asset_status": "報廢", "remark": "x"}})
            assert r.status_code == 200, r.text
            c = db.get_connection(p)
            assert _st(c, "HW-2") == "使用中" and _st(c, "HW-2", "pending_status") == "報廢"
            assert _st(c, "HW-2", "remark") == "x"
        finally:
            api.app.dependency_overrides.clear()


def test_併回空殼_搬進合併紀錄_不算退役_可還原():
    import dyn_shell

    with _Tmp() as tmp:
        c = _conn(tmp)
        db.insert_hardware(c, asset_serial="HW-3", hostname="secsvr-a3", ip="10.99.1.3", asset_status="使用中",
                           environment="測試", asset_purpose="AP")
        db.insert_hardware(c, asset_serial="DYN-aa", hostname="secsvr-a3", ip="10.99.1.3", mac="00:11:22:33:44:55")
        c.commit()
        r = dyn_shell.apply(c, [{"shell_serial": "DYN-aa", "target_serial": "HW-3"}], "tester")
        assert [m["shell_serial"] for m in r["merged"]] == ["DYN-aa"]
        assert _st(c, "DYN-aa") is None                          # 不在 hardware
        assert lifecycle.merged_into_of(c, "DYN-aa") == "HW-3"
        s = manage_state.summarize(c)
        assert s["counts"][manage_state.RETIRED] == 0             # 不再多一台退役
        b = manage_state.retired_breakdown(c, s)
        assert b["merged_shells"] == 1 and b["total"] == 0
        dyn_shell.restore(c, dyn_shell.history(c)[0]["id"], "tester")
        assert tuple(c.execute("SELECT asset_status FROM hardware WHERE asset_serial='DYN-aa'").fetchone()) == (None,)
        assert lifecycle.merged_into_of(c, "DYN-aa") is None


def test_遷移_舊做法標停用的空殼與系統填的值_改回並留紀錄_可還原():
    import json

    with _Tmp() as tmp:
        c = _conn(tmp)
        c.execute("DELETE FROM app_settings WHERE key = ?", (lifecycle.MIGRATION_KEY,))
        db.insert_hardware(c, asset_serial="HW-4", hostname="h4", ip="10.99.1.4", asset_status="使用中",
                           environment="正式")
        # 舊做法：空殼被標停用、備註寫已併入
        db.insert_hardware(c, asset_serial="DYN-bb", hostname="h4", ip="10.99.1.4", asset_status="停用",
                           remark="已併入 HW-4")
        import dyn_shell

        dyn_shell._ensure(c)
        snap = {"shell": {"asset_serial": "DYN-bb", "asset_status": None, "remark": None},
                "target": {"asset_serial": "HW-4"}, "source_record": [], "tables": {}}
        c.execute("INSERT INTO shell_merge_audit (merged_at, actor, shell_serial, target_serial, snapshot, reason) "
                  "VALUES (?,?,?,?,?,?)", (NOW, "sys", "DYN-bb", "HW-4", json.dumps(snap), "自動"))
        # 系統替非 CIA 填的狀態／環境
        db.insert_hardware(c, asset_serial="AUTO-10.99.1.5", ip="10.99.1.5", asset_status="使用中",
                           environment="正式")
        db.insert_hardware(c, asset_serial="VC-x", hostname="vmx", ip="10.99.1.6", environment="正式")
        # 人改過的 CIA 狀態（CIA 待異動還沒同步）
        db.insert_hardware(c, asset_serial="HW-7", hostname="h7", ip="10.99.1.7", asset_status="報廢")
        cia_pending._ensure(c)
        c.execute("INSERT INTO cia_pending_change (asset_serial, field, old_value, new_value, reason, changed_by, "
                  "changed_at) VALUES ('HW-7','asset_status','使用中','報廢','下線了','tester',?)", (NOW,))
        c.commit()
        out = lifecycle.migrate_v1(c)
        assert out["merged"] == 1 and out["status_fix"] == 1 and out["env_fix"] == 2 and out["pending"] == 1
        assert _st(c, "DYN-bb") is None and lifecycle.merged_into_of(c, "DYN-bb") == "HW-4"
        assert _st(c, "AUTO-10.99.1.5") is None and _st(c, "AUTO-10.99.1.5", "environment") is None
        assert _st(c, "VC-x", "environment") is None
        assert _st(c, "HW-7") == "使用中" and _st(c, "HW-7", "pending_status") == "報廢"
        assert lifecycle.migrate_v1(c) == {"skipped": lifecycle.migrate_v1(c)["skipped"]}   # 冪等
        lifecycle.revert_v1(c)
        assert _st(c, "DYN-bb") == "停用" and _st(c, "AUTO-10.99.1.5") == "使用中"
        assert _st(c, "VC-x", "environment") == "正式" and _st(c, "HW-7") == "報廢"


def test_新建的非CIA資產_不替它填狀態與環境():
    import inspect

    import batch_onboard_service
    import paste_watch
    import rvtools_import
    from routers import collect_scan, onboard

    for mod in (paste_watch, batch_onboard_service, collect_scan, onboard):
        src = inspect.getsource(mod)
        assert "'正式', '使用中'" not in src and 'environment="正式", asset_status="使用中"' not in src, mod.__name__
        assert 'environment="測試", asset_status="使用中"' not in src, mod.__name__
    assert '"environment": "正式"' not in inspect.getsource(rvtools_import)


def test_已退役組成_加總等於卡片():
    with _Tmp() as tmp:
        c = _conn(tmp)
        for i, st in enumerate(("停用", "停用", "報廢", "閒置")):
            db.insert_hardware(c, asset_serial=f"HW-R{i}", hostname=f"r{i}", ip=f"10.99.2.{i + 1}", asset_status=st)
        # 同一台：舊登記報廢、新登記使用中
        db.insert_hardware(c, asset_serial="HW-R9", hostname="r2", ip="10.99.2.3", asset_status="使用中")
        c.commit()
        s = manage_state.summarize(c)
        b = manage_state.retired_breakdown(c, s)
        assert b["total"] == s["counts"][manage_state.RETIRED] == 4
        assert sum(m["machines"] for m in b["matrix"]) == 4
        assert b["by_status"] == {"停用": 2, "報廢": 1, "閒置": 1}
        assert b["also_active"] == 1


def test_掃描紅燈_2_1探測過的算掃過_分母不含退役():
    with _Tmp() as tmp:
        c = _conn(tmp)
        db.insert_hardware(c, asset_serial="HW-S1", hostname="s1", ip="10.99.3.1", asset_status="使用中")
        db.insert_hardware(c, asset_serial="HW-S2", hostname="s2", ip="10.99.3.2", asset_status="使用中")
        db.insert_hardware(c, asset_serial="HW-S3", hostname="s3", ip="10.99.3.3", asset_status="停用")
        # 最近一次網路掃描只涵蓋別段
        c.execute("INSERT INTO scan_history (hostname, ip, scan_ok, scan_time) VALUES ('x','10.99.9.9',1,?)", (NOW,))
        import scan_scope

        scan_scope._ensure(c)
        c.execute("INSERT INTO scan_coverage (scan_time, cidr, source, ok, created_at) VALUES (?, '10.99.9.0/24', 's', 1, ?)",
                  (NOW, NOW))
        # 2-1 探測過 S1（沒回應）、S2 很久以前（超過 N 天，不算）
        c.execute("CREATE TABLE IF NOT EXISTS collect_dispatch_run (id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, created_at) VALUES (1,'10.99.3.1',0,?)", (NOW,))
        c.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, created_at) VALUES (1,'10.99.3.2',1,?)", (OLD,))
        c.commit()
        s = manage_state.summarize(c)
        g = s["scan_gap"]
        assert g["base"] == 2                                     # 退役那台不在分母
        assert s["counts"][manage_state.LOST] == 1                # S1 探測過沒回應＝失聯，不是沒掃
        assert g["n"] == s["counts"][manage_state.NOT_COVERED] == 1   # S2 只有 90 天前的結果＝近 N 天沒掃
        assert [x["asset_serial"] for x in s["rep_items"][manage_state.NOT_COVERED]] == ["HW-S2"]


def test_審查1_空殼有CI圖的邊_併回與遷移不撞外鍵():
    import json

    import ci_graph
    import dyn_shell

    with _Tmp() as tmp:
        c = _conn(tmp)
        c.execute("INSERT INTO network_segment (cidr, raw_cidr) VALUES ('10.99.5.0/24', '10.99.5.0/24')")
        db.insert_hardware(c, asset_serial="HW-5", hostname="h5", ip="10.99.5.5", asset_status="使用中",
                           asset_purpose="AP")
        db.insert_hardware(c, asset_serial="DYN-c1", hostname="h5", ip="10.99.5.5", mac="00:11:22:33:44:01")
        db.insert_hardware(c, asset_serial="HW-6", hostname="h6", ip="10.99.5.6", asset_status="使用中",
                           asset_purpose="AP")
        db.insert_hardware(c, asset_serial="DYN-c2", hostname="h6", ip="10.99.5.6", asset_status="停用")
        dyn_shell._ensure(c)
        snap = {"shell": {"asset_serial": "DYN-c2", "asset_status": None, "remark": None},
                "target": {"asset_serial": "HW-6"}, "source_record": [], "tables": {}}
        c.execute("INSERT INTO shell_merge_audit (merged_at, actor, shell_serial, target_serial, snapshot, reason) "
                  "VALUES (?,?,?,?,?,?)", (NOW, "sys", "DYN-c2", "HW-6", json.dumps(snap), "自動"))
        c.commit()
        ci_graph.rebuild(c)
        # 正式庫上空殼幾乎都有一條 located_in 邊指到網段節點：直接造出來（不依賴網段表格式）
        c.execute("INSERT OR IGNORE INTO ci_node (node_id, node_type, label, source) "
                  "VALUES ('seg:10.99.5.0/24','segment','seg','test')")
        for sn in ("DYN-c1", "DYN-c2"):
            c.execute("INSERT OR IGNORE INTO ci_node (node_id, node_type, label, asset_serial, source) "
                      "VALUES (?, 'host', ?, ?, 'test')", (f"hw:{sn}", sn, sn))
            c.execute("INSERT INTO ci_edge (src_node_id, dst_node_id, edge_type, confidence, source) "
                      "VALUES (?, 'seg:10.99.5.0/24', 'located_in', '推論', 'test')", (f"hw:{sn}",))
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM ci_edge e JOIN ci_node n ON n.node_id = e.src_node_id "
                         "WHERE n.asset_serial IN ('DYN-c1','DYN-c2')").fetchone()[0] >= 1
        r = dyn_shell.apply(c, [{"shell_serial": "DYN-c1", "target_serial": "HW-5"}], "t")
        assert [m["shell_serial"] for m in r["merged"]] == ["DYN-c1"]
        c.execute("DELETE FROM app_settings WHERE key = ?", (lifecycle.MIGRATION_KEY,))
        c.commit()
        assert lifecycle.migrate_v1(c)["merged"] == 1


def test_審查2_非CIA登記被系統標退役_改記待CIA異動():
    with _Tmp() as tmp:
        c = _conn(tmp)
        c.execute("DELETE FROM app_settings WHERE key = ?", (lifecycle.MIGRATION_KEY,))
        db.insert_hardware(c, asset_serial="VC-z", hostname="vz", ip="10.99.6.1", asset_status="停用",
                           manual_updated_at=NOW)
        c.commit()
        out = lifecycle.migrate_v1(c)
        assert out["offbook_retired"] == 1
        assert _st(c, "VC-z") is None and _st(c, "VC-z", "pending_status") == "停用"
        assert manage_state.summarize(c)["counts"][manage_state.RETIRED] == 0


def test_審查4_還原後_下次啟動不會再遷移():
    with _Tmp() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        c.execute("DELETE FROM app_settings WHERE key = ?", (lifecycle.MIGRATION_KEY,))
        db.insert_hardware(c, asset_serial="AUTO-10.99.7.1", ip="10.99.7.1", asset_status="使用中")
        c.commit()
        lifecycle.migrate_v1(c)
        assert _st(c, "AUTO-10.99.7.1") is None
        lifecycle.revert_v1(c)
        c.close()
        db.init_db(p)                                  # 下一次啟動
        c = db.get_connection(p)
        assert _st(c, "AUTO-10.99.7.1") == "使用中"
