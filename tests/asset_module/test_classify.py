"""主機分類作業（逐台 system_category）。

守的是這頁存在的理由：475 台要人工判斷，所以「批次改」與「匯入先乾跑」不能壞。
特別守三件容易寫錯的事：
1. 逐台分類要**壓過** api_id 對照表推出來的預設值（不然人改了畫面不動）
2. 分類名稱要驗白名單（打錯字會讓那台永遠卡在未分類，畫面看不出原因）
3. 匯入預設 dry_run，**不寫入**（先試跑再寫入是 2026-08-24 待複核佇列的教訓）
"""
import io
import sys
import tempfile
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import system_report  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _seed(conn):
    db.insert_hardware(conn, asset_serial="HW-1", hostname="core-a", ip="10.99.0.1",
                       os="Windows Server 2019", physical_location="板橋機房",
                       environment="正式", api_id="N-001", asset_name="核心系統A",
                       is_vm="0", asset_status="在用")
    db.insert_hardware(conn, asset_serial="HW-2", hostname="vm-a", ip="10.99.0.2",
                       os="Windows Server 2019", physical_location="內湖機房",
                       environment="測試", api_id="N-002", asset_name="系統B",
                       is_vm="VM", asset_status="在用")
    conn.commit()


def _conn(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    _seed(conn)
    return db_path, conn


def _client(tmp):
    db_path, conn = _conn(tmp)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200
    return client


def _a_valid_category(conn):
    return system_report.classify_summary(conn)["valid_categories"][0]


# ===== 邏輯層 =====

def test_一開始全部未分類且進度是0():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            s = system_report.classify_summary(conn)
            assert s["total"] == 2
            assert s["classified"] == 0
            assert s["unclassified"] == 2
            assert s["percent"] == 0.0
        finally:
            conn.close()


def test_逐台設定會壓過api_id對照表推出來的預設():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            cats = system_report.classify_summary(conn)["valid_categories"]
            default_cat, chosen = cats[0], cats[1]
            # 先讓 api_id 對照表把 N-001 推成 default_cat
            system_report.import_system_category(conn, {"N-001": default_cat})
            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            assert row["category"] == default_cat
            assert row["from_asset"] is False   # 推出來的，不是人設的

            # 逐台設定之後要蓋過去，而且標成 from_asset
            system_report.set_asset_categories(conn, ["HW-1"], chosen, "tester")
            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            assert row["category"] == chosen
            assert row["from_asset"] is True
        finally:
            conn.close()


def test_清除分類會回到未分類而不是變成其他():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            cat = _a_valid_category(conn)
            system_report.set_asset_categories(conn, ["HW-1"], cat)
            system_report.set_asset_categories(conn, ["HW-1"], None)
            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            # 「查不到」跟「歸類為其他」是兩件事，畫面要分得出來
            assert row["category"] is None
        finally:
            conn.close()


def test_分類名稱不在白名單就擋下來():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            with pytest.raises(ValueError):
                system_report.set_asset_categories(conn, ["HW-1"], "我亂打的分類")
            assert system_report.classify_summary(conn)["classified"] == 0
        finally:
            conn.close()


def test_匯入預設乾跑不寫入():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            cat = _a_valid_category(conn)
            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": cat}])
            assert res["dry_run"] is True
            assert res["matched"] == 1
            assert system_report.classify_summary(conn)["classified"] == 0   # 沒寫進去

            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": cat}], dry_run=False)
            assert res["matched"] == 1
            assert system_report.classify_summary(conn)["classified"] == 1
        finally:
            conn.close()


def test_匯入的主機名比對不分大小寫也不看網域():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            cat = _a_valid_category(conn)
            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "CORE-A.example.local", "category": cat}],
                dry_run=False)
            assert res["matched"] == 1
            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            assert row["category"] == cat
        finally:
            conn.close()


def test_匯入的分類名稱要原樣保留_不可以剝掉字母編號():
    """2026-08-26 使用者指正：「我已經給你分類了，你還把 A.XXX 的 A. 拿掉」。

    白名單存的就是 `X.資安管理系統` 這種完整字串，來源表也是——直接對上，
    **中間不做任何修剪**。我原本判斷那個字母是排序編號不是名稱，那是我的推論。
    """
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            cat = "X.資安管理系統"
            assert cat in system_report.classify_summary(conn)["valid_categories"]
            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": cat}], dry_run=False)
            assert res["invalid_category"] == 0
            assert res["matched"] == 1
            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            assert row["category"] == cat, "存進去的要是完整字串，不是被剪過的"
        finally:
            conn.close()


def test_對不上的主機名要列出來不能只給數字():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            cat = _a_valid_category(conn)
            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "不存在的主機", "category": cat}])
            assert res["unmatched"] == 1
            assert res["unmatched_samples"][0]["hostname"] == "不存在的主機"
        finally:
            conn.close()


def test_篩選參數只回該類的列():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            system_report.set_asset_categories(conn, ["HW-1"], _a_valid_category(conn))
            assert [r["asset_serial"] for r in
                    system_report.classify_rows(conn, "classified")] == ["HW-1"]
            assert [r["asset_serial"] for r in
                    system_report.classify_rows(conn, "unclassified")] == ["HW-2"]
        finally:
            conn.close()


# ===== HTTP 層 =====

def test_未登入打不到分類端點():
    with tempfile.TemporaryDirectory() as tmp:
        db_path, conn = _conn(tmp)
        conn.close()

        def _override():
            c = db.get_connection(db_path)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _override
        client = TestClient(api.app)
        try:
            assert client.get("/api/classify").status_code == 401
            assert client.get("/api/classify/summary").status_code == 401
            assert client.put("/api/classify",
                              json={"asset_serials": ["HW-1"], "category": None}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_端點批次修改與亂填分類回400():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            cat = client.get("/api/classify/summary").json()["valid_categories"][0]
            r = client.put("/api/classify",
                           json={"asset_serials": ["HW-1", "HW-2"], "category": cat})
            assert r.status_code == 200
            assert r.json()["updated"] == 2
            assert client.get("/api/classify/summary").json()["classified"] == 2

            bad = client.put("/api/classify",
                             json={"asset_serials": ["HW-1"], "category": "亂填"})
            assert bad.status_code == 400

            assert client.get("/api/classify", params={"only": "亂填"}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def _xlsx(rows, header=("主機名稱", "分類")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_匯入端點乾跑與寫入():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            cat = client.get("/api/classify/summary").json()["valid_categories"][0]
            # 分類名稱原樣送進去（含字母編號），不做任何修剪
            content = _xlsx([("core-a", cat)])

            r = client.post("/api/classify/seed",
                            files={"file": ("t.xlsx", content,
                                            "application/vnd.openxmlformats-officedocument."
                                            "spreadsheetml.sheet")})
            assert r.status_code == 200
            assert r.json()["dry_run"] is True and r.json()["matched"] == 1
            assert client.get("/api/classify/summary").json()["classified"] == 0

            r = client.post("/api/classify/seed",
                            data={"commit": "true"},
                            files={"file": ("t.xlsx", content,
                                            "application/vnd.openxmlformats-officedocument."
                                            "spreadsheetml.sheet")})
            assert r.status_code == 200 and r.json()["dry_run"] is False
            assert client.get("/api/classify/summary").json()["classified"] == 1
        finally:
            api.app.dependency_overrides.clear()


def test_匯入端點找不到必要表頭就報錯而不是猜欄位():
    """這份表是別人維護的，欄位順序會變。位置寫死會在某次他們插一欄之後
    安靜地把整欄資料對錯——那比直接報錯難查得多。"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            content = _xlsx([("core-a", "隨便")], header=("機器", "類別"))
            r = client.post("/api/classify/seed",
                            files={"file": ("t.xlsx", content,
                                            "application/vnd.openxmlformats-officedocument."
                                            "spreadsheetml.sheet")})
            assert r.status_code == 400
            assert "主機名稱" in r.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()


def test_來源表沒寫前綴時_對得到唯一一個就用它():
    """補充清單可能是手打的，只寫 `資安管理系統` 沒有 `X.`。

    去尾比對後只對到一個就用它；對到多個就當不合法讓人自己挑——
    猜錯會把機器歸到錯的分類，寧可讓人看到。
    """
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": "資安管理系統"}], dry_run=False)
            assert res["invalid_category"] == 0
            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            assert row["category"] == "X.資安管理系統"
        finally:
            conn.close()


# ===== X4：new_categories 要回報＋殭屍分類要改名不新增 =====

def test_來源表帶全新分類會回報到new_categories():
    """乾跑時就要能預告「這次會新增哪個分類」，不是寫入後才讓人發現。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": "ZZ.全新分類"}])
            assert res["dry_run"] is True
            assert res["new_categories"] == [{"name": "ZZ.全新分類", "group": "非核心"}]
            assert res["renamed_categories"] == []
            assert "ZZ.全新分類" not in {c["name"] for c in system_report._category_defs(conn)}

            system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": "ZZ.全新分類"}], dry_run=False)
            assert "ZZ.全新分類" in {c["name"] for c in system_report._category_defs(conn)}
        finally:
            conn.close()


def test_殭屍分類同字母且0台時直接改名不新增():
    """X4修法：來源表這次的字面跟既有「Q.XXX」對不起來時（例如去識別化 patch
    把公司名改成「（示範企業）」），只要字母相同、既有那筆現在 0 台在用，
    就是改名不是新增——不然分類清單只會愈積愈多 0 台的殭屍項目，
    而且那些殭屍項目還會出現在下拉選單裡讓人誤選。

    用整份**乾淨、獨立**的分類清單（字母 Q，report_groups.json 目前沒人用這個
    字母）取代預設值，不跟出廠預設（也含一筆 0 台的示範企業佔位名稱）混在一起，
    不然這個測試會意外撞到別的殭屍，驗的就不是同一件事。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            system_report.set_category_defs(
                conn, [{"name": "Q.（示範企業）App", "group": "核心交易", "color": "chart-gray"}])
            assert len(system_report._category_defs(conn)) == 1

            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": "Q.某證券App"}], dry_run=False)
            assert res["new_categories"] == []
            assert res["renamed_categories"] == [
                {"old": "Q.（示範企業）App", "new": "Q.某證券App"}]

            after = system_report._category_defs(conn)
            names = {c["name"] for c in after}
            assert "Q.（示範企業）App" not in names, "殭屍舊名稱要被換掉，不能兩筆並存"
            assert "Q.某證券App" in names
            assert len(after) == 1, "改名不是新增，總數不該再多一筆"

            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            assert row["category"] == "Q.某證券App"
        finally:
            conn.close()


def test_同字母但既有那筆非0台時不改名_正常新增():
    """殭屍改名只在「既有那筆真的沒人在用」才安全——已經有台掛在上面的分類
    不能被靜靜蓋掉，那會讓已經分好類的機器憑空換了分類名稱。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            system_report.set_category_defs(
                conn, [{"name": "Q.舊名稱", "group": "核心交易", "color": "chart-gray"}])
            system_report.seed_categories_from_rows(
                conn, [{"hostname": "core-a", "category": "Q.舊名稱"}], dry_run=False)

            res = system_report.seed_categories_from_rows(
                conn, [{"hostname": "vm-a", "category": "Q.新名稱"}], dry_run=False)
            assert res["renamed_categories"] == []
            names = {c["name"] for c in system_report._category_defs(conn)}
            assert "Q.舊名稱" in names and "Q.新名稱" in names, \
                "Q.舊名稱有台在用，不該被 Q.新名稱 取代"
        finally:
            conn.close()
