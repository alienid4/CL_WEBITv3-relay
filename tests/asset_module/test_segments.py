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


# 2026-08-26 使用者提供的新版檔案：環境別獨立成一欄（UAT／PROD），
# 使用類別純寫 SERVER，另外多了註解與 VLAN 兩欄。
HEADER_V2 = ("使用狀況\t使用位置\t用途說明\t環境別\t使用類別\t使用目的\t網段\t"
             "弱掃說明\t註解\tVLAN\n")
SAMPLE_V2 = HEADER_V2 + (
    "已使用\t01_內湖機房\t模擬內湖大型主機\tUAT\tSERVER\tUNIX-DB\t10.92.1.0/24\t"
    "UAT環境，建議排除掃描\t\t2001\n"
    "已使用\t02_板橋機房\tAP主機網段\tPROD\tSERVER\tx86-AP\t10.99.163.0/24\t\t"
    "正式AP\t2163\n"
)


def test_新版檔案_環境別看明文欄不是看使用類別前綴():
    """**這是新版檔案最容易踩的地雷。**

    舊版檔案用 `UAT-SERVER` 表達測試環境，程式是從使用類別的 UAT- 前綴推的。
    使用者 2026-08-26 提供的版本把 UAT 放進獨立的「環境別」欄，使用類別純寫
    `SERVER`——照舊規則推的話，**所有 UAT 網段都會被標成「正式」**。

    這個欄位餵給「機房→環境→網段」的 IP 選單與掃描範圍建議，標錯的後果是
    有人照著它把測試 IP 當正式 IP 發出去。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            resp = _upload(client, _write(tmp, SAMPLE_V2))
            assert resp.status_code == 200, resp.text
            assert resp.json()["environment_unknown"] == 0

            segs = {x["cidr"]: x for x in client.get("/api/segments").json()["segments"]}
            uat = segs["10.92.1.0/24"]
            assert uat["category"] == "SERVER", "前提：使用類別沒有 UAT- 前綴"
            assert uat["environment"] == "測試", "環境別欄寫 UAT 就該是測試環境"
            assert segs["10.99.163.0/24"]["environment"] == "正式"
        finally:
            api.app.dependency_overrides.clear()


def test_新版檔案_VLAN與註解要存下來():
    """原本這兩欄整個被丟掉——使用者填了卻不知道系統沒收。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE_V2))
            segs = {x["cidr"]: x for x in client.get("/api/segments").json()["segments"]}
            assert segs["10.92.1.0/24"]["vlan"] == "2001"
            assert segs["10.99.163.0/24"]["vlan"] == "2163"
            assert segs["10.99.163.0/24"]["remark"] == "正式AP"
        finally:
            api.app.dependency_overrides.clear()


def test_環境別填看不懂的值_留空白並警告_不要猜成正式():
    """猜錯的方向是「把測試網段標成正式」，代價比留白高得多：
    留白只是選單少一個選項，猜錯是有人把測試 IP 當正式 IP 發出去。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            bad = HEADER_V2 + (
                "已使用\t02_板橋機房\tAP網段\t生產\tSERVER\tx86-AP\t10.99.164.0/24\t\t\t2164\n"
            )
            r = _upload(client, _write(tmp, bad)).json()
            assert r["environment_unknown"] == 1
            assert any("生產" in w["reason"] for w in r["warnings"])

            seg = client.get("/api/segments").json()["segments"][0]
            assert seg["environment"] is None, "認不出來就留白，不要猜成正式"
            assert seg["environment_raw"] == "生產", "原值要留著，人才知道要改哪一格"
        finally:
            api.app.dependency_overrides.clear()


def test_舊版檔案照樣匯得進來_前綴推導仍是備援():
    """新規則不能讓 8/15 那份舊檔案重匯時整批變成未知環境。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            r = _upload(client, _write(tmp, SAMPLE)).json()
            assert r["environment_unknown"] == 0
            segs = {x["cidr"]: x for x in client.get("/api/segments").json()["segments"]}
            assert segs["10.92.161.0/24"]["environment"] == "測試"
            assert segs["10.99.161.0/24"]["environment"] == "正式"
        finally:
            api.app.dependency_overrides.clear()


def test_表頭大小寫不同也要認得出VLAN():
    """VLAN 這種英文表頭別人可能寫成 Vlan／vlan。大小寫敏感的比對會讓整欄
    安靜地匯不進來——沒有錯誤訊息，只是資料不見了。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            lower = SAMPLE_V2.replace("\tVLAN\n", "\tVlan\n", 1)
            _upload(client, _write(tmp, lower))
            segs = {x["cidr"]: x for x in client.get("/api/segments").json()["segments"]}
            assert segs["10.92.1.0/24"]["vlan"] == "2001"
        finally:
            api.app.dependency_overrides.clear()


def test_範圍寫法要展開成每一段_不是整格丟掉():
    """2026-08-26 使用者拿真實檔案匯進 221，警告列出 9 列「無法解析」。實際算過，
    光是 `A/24----B/24` 那七格加起來就是**約 120 個 /24 完全沒進系統**——而這張表
    餵的是 IP 配置選單、掃描範圍、資料品質的涵蓋率分母，三個全部少算且沒人看得出來。

    「不猜」是對的立場，但「連能確定的都不解析」不是保守，是漏資料。
    `10.99.121.0/24----10.99.127.0/24` 的意思是確定的（同前綴長度、遞增），要展開。
    """
    assert segments.parse_cidr("10.99.121.0/24----10.99.127.0/24") == (None, None, None)
    entries, warn = segments.expand_segments("10.99.121.0/24----10.99.127.0/24")
    assert len(entries) == 7
    assert [e["cidr"] for e in entries] == [f"10.99.{n}.0/24" for n in range(121, 128)]
    # 每一列都要標得出「原檔沒有這一列，是系統拆的」
    assert all(e["expanded_from"] == "10.99.121.0/24----10.99.127.0/24" for e in entries)
    assert "展開成 7" in warn

    # `--`（兩個）與 `----`（四個）原檔都出現過，不可以寫死四個
    assert len(segments.expand_segments("10.99.230.0/24--10.99.232.0/24")[0]) == 3


def test_位址範圍寫法_算得出起訖就不算整段消失():
    """`172.16.156.0/24~172.16.157.230` 不是標準 CIDR，但**起訖位址是確定的**。

    cidr 留 None（所以不會出現在「機房→環境→網段」選單），但 net_start/net_end
    要算出來——「這個 IP 屬於哪一段」與「這段已登記幾台」查的就是這兩欄。
    以前這種列連 net_start 都是 NULL，等於整段從系統消失。
    """
    for raw in ("172.16.156.0/24~172.16.157.230", "172.16.157.231~249"):
        entries, warn = segments.expand_segments(raw)
        assert len(entries) == 1, raw
        assert entries[0]["cidr"] is None, raw
        assert entries[0]["net_start"] is not None, f"{raw} 的起訖位址是算得出來的"
        assert entries[0]["net_end"] >= entries[0]["net_start"]
        assert warn and "範圍" in warn

    # 尾碼簡寫要接上前三段，不是變成 0.0.0.249
    e = segments.expand_segments("172.16.157.231~249")[0][0]
    import ipaddress
    assert str(ipaddress.ip_address(e["net_start"])) == "172.16.157.231"
    assert str(ipaddress.ip_address(e["net_end"])) == "172.16.157.249"


def test_一格塞多段要拆成多列():
    entries, warn = segments.expand_segments("10.99.255.23/28 10.99.255.17/28")
    assert len(entries) == 2
    assert all(e["expanded_from"] for e in entries)
    assert "拆成 2" in warn


def test_真的看不懂的寫法還是要保留並警告_不能靜默丟掉():
    """展開能處理的變多了，但「看不懂就不猜」這條沒有放寬。
    看不懂的列仍然入庫（raw 保留）並列警告——丟掉的網段之後不會有人發現，
    而「系統裡沒有這段」跟「這段不存在」在盤點上是完全不同的兩件事。"""
    dirty = HEADER + (
        "已使用\t02_板橋機房\t看不懂\tNETWORK\tnone\t這格是手寫的中文\t\n"
        "已使用\t02_板橋機房\t正常\tSERVER\tDMZ\t10.99.161.0/24\t\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            s = _upload(client, _write(tmp, dirty)).json()
            assert s["unparsed"] == 1
            assert sum(1 for w in s["warnings"] if "無法解析" in w["reason"]) == 1

            # 前綴長度不同（10.99.200.0/24----10.99.201.0/28）**不是**看不懂：
            # 它不能展開成一段一段，但起訖位址是確定的，所以當位址範圍收下。
            e, w = segments.expand_segments("10.99.200.0/24----10.99.201.0/28")
            assert len(e) == 1 and e[0]["cidr"] is None
            assert e[0]["net_start"] is not None and "範圍" in w
            raws = [x["raw_cidr"] for x in client.get("/api/segments").json()["segments"]]
            assert "這格是手寫的中文" in raws, "看不懂的網段不可以被丟掉"
        finally:
            api.app.dependency_overrides.clear()


def test_展開上限_打錯字不會炸出六萬列():
    """`10.0.0.0/8----10.255.0.0/8` 這種打錯字會展開出六萬多列。
    超過上限就退回「當成一個大範圍」並**明講展開了幾段**，不要靜默截斷——
    截斷會讓人以為全部都進來了。"""
    entries, warn = segments.expand_segments("10.0.0.0/24----10.255.255.0/24")
    assert len(entries) == 1
    assert entries[0]["cidr"] is None
    assert entries[0]["net_start"] is not None, "退回大範圍時起訖仍要算得出來"
    assert "超過一格展開上限" in warn


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


# ===== 匯出空白範本（2026-08-25 使用者通則：有匯入就要有配對的匯出範本）=====

def test_匯出範本表頭跟匯入認得的HEADER_MAP完全一致():
    """表頭一定要直接取自 HEADER_MAP，不能另外寫一份——兩份遲早會漂走，
    漂走的後果是「範本填完上傳，系統說看不懂」，比沒有範本更糟。"""
    headers, example = segments.export_template_rows()
    assert headers == list(segments.HEADER_MAP.keys())
    assert len(example) == len(headers)


def test_匯出範本的網段那格是可以被匯入自己解析成功的CIDR():
    """範例列要是「示範正確格式」，不是「示範錯誤格式」——填的值必須通得過
    parse_cidr()，不然新使用者照抄範例反而學到錯的寫法。"""
    headers, example = segments.export_template_rows()
    row = dict(zip(headers, example))
    cidr, start, end = segments.parse_cidr(row["網段"])
    assert cidr is not None, f"範例網段「{row['網段']}」連系統自己都解析不了"


def test_匯出範本可以被自己的匯入邏輯讀回來():
    """最直接的 round-trip 驗證：範本存成 xlsx，餵回 read_rows()，
    每個表頭都要能對到欄位，不能有一欄匯出去匯不回來。"""
    import openpyxl

    headers, example = segments.export_template_rows()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "template.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        ws.append(example)
        wb.save(p)

        rows = segments.read_rows(p)
        assert len(rows) == 1
        got = rows[0]
        for header, field in segments.HEADER_MAP.items():
            assert field in got, f"表頭「{header}」匯出後讀不回對應欄位"


def test_列出網段的台數要跟逐段COUNT完全一致():
    """2026-08-27 使用者回報「每次查詢都會卡一下」，DevTools 顯示 /api/segments 要 6.13 秒。

    原因：每一段各跑一次 COUNT，而那句 SQL 用的 `_ip_int` 是註冊給 SQLite 的
    **Python 函式**——等於「段數 × 資產數」次 Python 呼叫。實測 443 段 × 4784 台
    要 11.8 秒。而且範圍寫法展開後段數從 327 變 443 又慢三成，
    **資料愈完整愈慢**，那個方向完全是反的。

    改成一次讀出所有 IP 排序 + 二分搜尋，快 300 倍。這條測試守的是**改快之後
    數字沒有變**——效能改動最容易出的錯就是「快了但算錯了」，而算錯的網段用量
    不會有人一眼看出來。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _upload(client, _write(tmp, SAMPLE))
            import db as _db
            conn = _db.get_connection(db_path)
            try:
                # 塞幾台落在不同段裡的資產，讓數字不是全 0（全 0 的話兩邊都對，測不到東西）
                for i, ip in enumerate(("10.99.161.5", "10.99.161.200", "10.99.163.9",
                                        "10.92.161.1", "10.99.10.77", "不是IP", None)):
                    _db.insert_hardware(conn, asset_serial=f"CNT-{i}", hostname=f"h{i}",
                                        ip=ip, asset_status="在用")
                conn.commit()

                fast = segments.list_segments(conn)
                assert any(d["asset_count"] for d in fast), "前提：至少有一段要算得到台數"

                rows = conn.execute(
                    "SELECT * FROM network_segment ORDER BY location, cidr, raw_cidr"
                ).fetchall()
                slow = [segments._count_assets(conn, r) for r in rows]
                assert [d["asset_count"] for d in fast] == slow, "快版跟逐段 COUNT 算出來不一樣"
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()
