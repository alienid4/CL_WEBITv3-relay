"""自動解掉複核佇列的規則：**只收證據足夠的，其餘一律不碰**。

這組測試守的是「不會合併錯」，不是「能合併多少」。每一條 skip 規則都要有測試——
少一條守衛，就是把兩台不同機器安靜地變成一台，而且很難發現、更難還原
（identity.py 檔頭那句話）。
"""
import contextlib
import itertools
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import merge_autoresolve  # noqa: E402


@contextlib.contextmanager
def _conn():
    """Windows 上 sqlite 連線沒關就刪暫存目錄會 PermissionError，所以統一用 with。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


_seq = itertools.count(1)


def _seed(conn, *, asset, source, candidates=None):
    """建一台資產 + 一筆卡在複核的來源紀錄，回傳 hardware_id。

    source_key 要每筆不同：(source, source_key) 上有 UNIQUE，同一個測試裡塞兩筆
    來源紀錄時會撞。
    """
    hid = db.insert_hardware(conn, **asset)
    conn.execute(
        "INSERT INTO source_record (source, source_key, payload, resolved_status) "
        "VALUES (?,?,?,'ambiguous')",
        (source.get("_source", "vcenter"), f"k{next(_seq)}",
         json.dumps({k: v for k, v in source.items() if not k.startswith("_")})),
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO merge_review (source_record_id, reason, candidates, status) "
        "VALUES (?,?,?,'open')",
        (sid, "測試用", json.dumps(candidates if candidates is not None else [{"id": hid}])),
    )
    conn.commit()
    return hid


def test_資產還沒有uuid而vCenter有_且IP相符_可以自動補():
    with _conn() as conn:
        hid = _seed(conn,
                    asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
                    source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})
        out = merge_autoresolve.plan(conn)
        assert len(out["resolvable"]) == 1
        r = out["resolvable"][0]
        assert r["hardware_id"] == hid and r["vm_uuid"] == "uuid-aaa"
        assert "ip" in r["matched_on"] and "hostname" in r["matched_on"]


def test_兩邊uuid不同是衝突_絕對不能自動合():
    """這正是「IP 被回收」或「機器被複製」的樣子，合下去就是把兩台變一台。"""
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1",
                     "vm_uuid": "UUID-OLD"},
              source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-NEW"})
        out = merge_autoresolve.plan(conn)
        assert out["resolvable"] == []
        assert out["skipped"]["兩邊 vm_uuid 不同＝衝突，一定要人看"] == 1


def test_uuid已經被別台佔用_不能再補給第二台():
    """少了這條守衛會做出兩台資產同一個 uuid，下次 resolve() 走強識別碼那關
    就會撞上「對到多筆」——等於把問題推到未來，而且放大。"""
    with _conn() as conn:
        db.insert_hardware(conn, asset_serial="OTHER", hostname="other",
                           ip="10.99.9.9", vm_uuid="uuid-aaa")
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
              source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})
        out = merge_autoresolve.plan(conn)
        assert out["resolvable"] == []
        assert out["skipped"]["這個 vm_uuid 已經被別台資產佔用"] == 1


def test_候選不只一個_不碰():
    with _conn() as conn:
        h2 = db.insert_hardware(conn, asset_serial="A2", hostname="web01", ip="10.99.1.2")
        hid = _seed(conn,
                    asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
                    source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"},
                    candidates=[{"id": 1}, {"id": h2}])
        assert hid
        out = merge_autoresolve.plan(conn)
        assert out["resolvable"] == []
        assert out["skipped"]["候選不只一個，要先釐清是哪一台"] == 1


def test_來源不是vCenter_不碰():
    """dynassets 沒有 vm_uuid 這種強識別碼，放寬對它不成立。"""
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
              source={"_source": "dynassets", "hostname": "web01", "ip": "10.99.1.1",
                      "vm_uuid": "UUID-AAA"})
        out = merge_autoresolve.plan(conn)
        assert out["resolvable"] == []
        assert out["skipped"]["來源不是 vCenter（沒有夠強的識別碼）"] == 1


def test_弱識別碼一個都對不上_不碰():
    """候選是別人塞進來的也可能發生；連 IP 和主機名都對不上就沒有任何依據。"""
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "aaa", "ip": "10.99.1.1"},
              source={"hostname": "bbb", "ip": "10.99.2.2", "vm_uuid": "UUID-AAA"})
        out = merge_autoresolve.plan(conn)
        assert out["resolvable"] == []
        assert out["skipped"]["連弱識別碼都對不上"] == 1


def test_同一個uuid指向不同台資產_整組退回人工():
    """真的說不清是哪一台，兩筆都不能收（收任何一筆都是猜的）。"""
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
              source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})
        _seed(conn,
              asset={"asset_serial": "A2", "hostname": "web02", "ip": "10.99.1.2"},
              source={"hostname": "web02", "ip": "10.99.1.2", "vm_uuid": "UUID-AAA"})
        out = merge_autoresolve.plan(conn)
        assert out["resolvable"] == []
        assert out["skipped"]["同一個 vm_uuid 指向不同台資產，說不清是哪一台"] == 2


def test_同一個決定被重複記了好幾筆_要一起解掉不是當成衝突():
    """實查 221：vCenter 匯入跑過幾輪，同一台被記了 4 筆待審核（資產編號、主機名、
    IP 全一樣）。第一版把這當成衝突擋掉，1464 筆全被略過——清完還剩 3/4 卡著。"""
    with _conn() as conn:
        hid = _seed(conn,
                    asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
                    source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})
        for _ in range(3):   # 同一台又被記了三次
            conn.execute(
                "INSERT INTO source_record (source, source_key, payload, resolved_status) "
                "VALUES ('vcenter', ?, ?, 'ambiguous')",
                (f"dup{next(_seq)}",
                 json.dumps({"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})))
            sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO merge_review (source_record_id, reason, candidates, status) "
                         "VALUES (?,?,?,'open')", (sid, "重複記錄", json.dumps([{"id": hid}])))
        conn.commit()
        out = merge_autoresolve.plan(conn)
        assert len(out["resolvable"]) == 4, "四筆待審核都要被解掉"
        assert out["distinct_assets"] == 1, "但它們其實只是同一台資產的一個決定"


def test_plan不寫任何東西():
    """乾跑就是乾跑：跑完 merge_review 與 hardware 必須原封不動。"""
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
              source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})
        before = (conn.execute("SELECT count(*) FROM merge_review WHERE status='open'").fetchone()[0],
                  conn.execute("SELECT vm_uuid FROM hardware WHERE asset_serial='A1'").fetchone()[0])
        merge_autoresolve.plan(conn)
        after = (conn.execute("SELECT count(*) FROM merge_review WHERE status='open'").fetchone()[0],
                 conn.execute("SELECT vm_uuid FROM hardware WHERE asset_serial='A1'").fetchone()[0])
        assert before == after


def test_apply_只補uuid_不動業務欄位():
    """合併的範圍要越小越好：只寫 vm_uuid，用途／保管者／機房這些人維護的欄位不准碰。"""
    with _conn() as conn:
        hid = _seed(conn,
                    asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1",
                           "physical_location": "內湖", "environment": "正式"},
                    source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA",
                            "physical_location": "板橋", "environment": "測試"})
        out = merge_autoresolve.apply(conn, "tester")
        assert out["merged"] == 1 and out["remaining_open"] == 0
        r = conn.execute("SELECT vm_uuid, physical_location, environment FROM hardware "
                         "WHERE id = ?", (hid,)).fetchone()
        assert r["vm_uuid"] == "uuid-aaa"
        assert r["physical_location"] == "內湖" and r["environment"] == "正式", "業務欄位被覆蓋了"


def test_apply之後待審核狀態變merged且記得住是誰決定的():
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1"},
              source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-AAA"})
        merge_autoresolve.apply(conn, "tester")
        row = conn.execute("SELECT status, decided_by FROM merge_review").fetchone()
        assert row["status"] == "merged" and row["decided_by"] == "tester"
        sr = conn.execute("SELECT resolved_status, resolved_rule FROM source_record").fetchone()
        assert sr["resolved_status"] == "matched"
        assert sr["resolved_rule"] == "batch:vcenter_uuid_backfill", "要查得出是靠哪條規則併的"


def test_apply不碰不合格的那些():
    """衝突的那批跑完 apply 之後必須原封不動還在佇列裡。"""
    with _conn() as conn:
        _seed(conn,
              asset={"asset_serial": "A1", "hostname": "web01", "ip": "10.99.1.1",
                     "vm_uuid": "UUID-OLD"},
              source={"hostname": "web01", "ip": "10.99.1.1", "vm_uuid": "UUID-NEW"})
        out = merge_autoresolve.apply(conn, "tester")
        assert out["merged"] == 0 and out["remaining_open"] == 1
        assert conn.execute("SELECT vm_uuid FROM hardware").fetchone()[0] == "UUID-OLD"
