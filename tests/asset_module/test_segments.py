"""網段配置表匯入與 IP 配置輔助。

測試盯的是真實檔案裡真的有的三種髒資料（2026-08-15 使用者提供的 183 段裡都有）：
一格塞兩段、寫成 IP 範圍、同一段出現兩次。這些不能靜默丟掉——丟掉的網段之後
不會有人發現，而「系統裡沒有這段」跟「這段不存在」在盤點上是兩件完全不同的事。
"""
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import segments  # noqa: E402
import api  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402

HEADER = "使用狀況\t使用位置\t用途說明\t使用類別\t使用目的\t網段\t弱掃說明\n"
SAMPLE = HEADER + (
    "已使用\t02_板橋機房\tDMZ主機網段1\tSERVER\tDMZ\t10.99.161.0/24\t\n"
    "已使用\t02_板橋機房\tAP主機網段\tSERVER\tx86-AP\t10.99.163.0/24\t\n"
    "已使用\t01_內湖機房\t模擬板橋DMZ1\tUAT-SERVER\tDMZ\t10.92.161.0/24\tUAT環境，建議排除掃描\n"
    "已使用\t06_分公司\t松江分公司\tOA\tPC\t10.99.10.0/24\t員工電腦，建議排除掃描\n"
)


def _write(tmp, text, name="seg.txt"):
    p = Path(tmp) / name
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p


def _upload(client, path):
    with io.open(path, "rb") as f:
        return client.post("/api/segments/import", files={"file": (path.name, f, "text/plain")})


def test_匯入_基本欄位與環境推導():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = _upload(client, _write(tmp, SAMPLE))
            assert resp.status_code == 200, resp.text
            s = resp.json()
            assert s["imported"] == 4 and s["parsed_cidr"] == 4
            assert s["scan_excluded"] == 2      # 兩列寫了「建議排除掃描」
            assert s["locations"] == 3

            segs = client.get("/api/segments").json()["segments"]
            uat = next(x for x in segs if x["cidr"] == "10.92.161.0/24")
            assert uat["environment"] == "測試", "UAT- 前綴要推導成測試環境"
            assert uat["scan_excluded"] == 1
            dmz = next(x for x in segs if x["cidr"] == "10.99.161.0/24")
            assert dmz["environment"] == "正式" and dmz["scan_excluded"] == 0
        finally:
            api.app.dependency_overrides.clear()


def test_匯入_解析不掉的寫法要保留並列警告不能靜默丟掉():
    dirty = HEADER + (
        "已使用\t02_板橋機房\t兩段寫一格\tNETWORK\tnone\t10.99.255.23/28\n10.99.255.17/28\t\n"
        "已使用\t02_板橋機房\tIP範圍寫法\tNETWORK\tnone\t172.16.156.0/24~172.16.157.230\t\n"
        "已使用\t02_板橋機房\t正常\tSERVER\tDMZ\t10.99.161.0/24\t\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            # 一格兩段的那列含換行，用 Excel 才寫得出來；文字檔這裡直接測 parse 函式
            assert segments.parse_cidr("10.99.255.23/28\n10.99.255.17/28") == (None, None, None)
            assert segments.parse_cidr("172.16.156.0/24~172.16.157.230") == (None, None, None)
            assert segments.parse_cidr("10.99.161.0/24")[0] == "10.99.161.0/24"

            s = _upload(client, _write(tmp, dirty)).json()
            # 解析不掉的列仍然入庫（raw 保留），而且要有警告指出是哪一列
            assert s["imported"] > s["parsed_cidr"]
            assert any("無法解析" in w["reason"] for w in s["warnings"])
            raws = [x["raw_cidr"] for x in client.get("/api/segments").json()["segments"]]
            assert any("172.16.156" in r for r in raws), "解析不掉的網段不可以被丟掉"
        finally:
            api.app.dependency_overrides.clear()


def test_匯入_重複網段要出警告但兩列都留著():
    dup = HEADER + (
        "已使用\t02_板橋機房\tA\tSERVER\tDMZ\t172.16.158.0/24\t\n"
        "已使用\t04_敦南總公司\tB\tOA\tPC\t172.16.158.0/24\t\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            s = _upload(client, _write(tmp, dup)).json()
            assert s["imported"] == 2
            assert any("已經出現過" in w["reason"] for w in s["warnings"])
        finally:
            api.app.dependency_overrides.clear()


def test_匯入是整批取代不是累加():
    """Excel 是唯一真相：段被刪掉就該從系統消失，不然作廢網段會永遠留著。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            smaller = HEADER + "已使用\t02_板橋機房\tDMZ\tSERVER\tDMZ\t10.99.161.0/24\t\n"
            _upload(client, _write(tmp, smaller, "seg2.txt"))
            segs = client.get("/api/segments").json()["segments"]
            assert len(segs) == 1 and segs[0]["cidr"] == "10.99.161.0/24"
        finally:
            api.app.dependency_overrides.clear()


def test_匯入_格式不對時不可以把現有清單清空():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            bad = "隨便\t亂寫\n1\t2\n"
            resp = _upload(client, _write(tmp, bad, "bad.txt"))
            assert resp.status_code == 400
            assert len(client.get("/api/segments").json()["segments"]) == 4, "匯入失敗不該清掉舊資料"
        finally:
            api.app.dependency_overrides.clear()


def test_三層樹_機房到環境到網段():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            tree = client.get("/api/segments/tree").json()["tree"]
            locs = {n["location"] for n in tree}
            assert locs == {"02_板橋機房", "01_內湖機房", "06_分公司"}
            neihu = next(n for n in tree if n["location"] == "01_內湖機房")
            assert [e["environment"] for e in neihu["environments"]] == ["測試"]
            banqiao = next(n for n in tree if n["location"] == "02_板橋機房")
            assert len(banqiao["environments"][0]["segments"]) == 2
        finally:
            api.app.dependency_overrides.clear()


def test_網段內已用IP與建議可用IP():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            _insert_hardware(db_path, "S-001", "h1", "10.99.161.1")
            _insert_hardware(db_path, "S-002", "h2", "10.99.161.2")
            _insert_hardware(db_path, "S-003", "other", "10.99.163.1")  # 別段的不該混進來

            r = client.get("/api/segments/ips", params={"cidr": "10.99.161.0/24"}).json()
            assert r["used_count"] == 2
            assert {u["ip"] for u in r["used"]} == {"10.99.161.1", "10.99.161.2"}
            assert r["suggestion"] == "10.99.161.3"
            # 不可以宣稱「這個 IP 沒人用」——清單本來就不完整
            assert "不代表實際上沒人在用" in r["suggestion_caveat"]
        finally:
            api.app.dependency_overrides.clear()


def test_每段算得出已登記幾台():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            _insert_hardware(db_path, "S-010", "h1", "10.99.161.5")
            _insert_hardware(db_path, "S-011", "h2", "10.99.161.6")
            segs = {s["cidr"]: s for s in client.get("/api/segments").json()["segments"]}
            assert segs["10.99.161.0/24"]["asset_count"] == 2
            assert segs["10.99.163.0/24"]["asset_count"] == 0
        finally:
            api.app.dependency_overrides.clear()


def test_掃描範圍建議_排除註記的段不進建議清單():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            r = client.get("/api/segments/scan-candidates").json()
            assert r["include_count"] == 2 and r["exclude_count"] == 2
            assert {s["cidr"] for s in r["exclude"]} == {"10.92.161.0/24", "10.99.10.0/24"}
        finally:
            api.app.dependency_overrides.clear()


def test_反查IP屬於哪一段():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            conn = db.get_connection(db_path)
            try:
                seg = segments.find_segment_for_ip(conn, "10.99.161.77")
                assert seg["purpose_desc"] == "DMZ主機網段1"
                assert segments.find_segment_for_ip(conn, "8.8.8.8") is None
                assert segments.find_segment_for_ip(conn, "not-an-ip") is None
                # 2026-08-19 正式機真的踩到：vCenter 收到 VM 的 IPv6 link-local
                # 位址當 hardware.ip，int(IPv6位址) 可以到 2^128，遠超過 SQLite
                # INTEGER 範圍，綁進查詢會炸 OverflowError 讓整批 CI 圖譜重建中斷。
                # 這裡要回 None（「問錯問題」），不能讓呼叫端整個炸掉。
                assert segments.find_segment_for_ip(conn, "fe80::3cb4:e3bb:483c:c831") is None
                assert segments.find_segment_for_ip(conn, "::1") is None
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_ip_int_UDF對IPv6回None不拋例外():
    """跟上面同一個bug的另一半：segments.py 兩處範圍查詢用的 `_ip_int()` SQL
    函式本身，對 IPv6 輸入也要回 NULL，不能讓 SQLite 收到超出 INTEGER 範圍的
    回傳值而拋 OverflowError。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = tmp + "/t.db"
        from pathlib import Path
        db.init_db(Path(db_path))
        conn = db.get_connection(Path(db_path))
        try:
            row = conn.execute(
                "SELECT _ip_int('fe80::3cb4:e3bb:483c:c831') AS a, _ip_int('10.99.1.5') AS b"
            ).fetchone()
            assert row["a"] is None
            assert row["b"] == int.from_bytes(bytes([10, 99, 1, 5]), "big")
        finally:
            conn.close()
