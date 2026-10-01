"""部門報告圖表頁：頁A（各環境實體機分布）／頁B（主機系統總覽）。

守的是同一套規矩，跟既有的系統組月報一致：
1. 「帳外資產」（DYN-/VC-/AUTO-）不可以跟 CIA 登記的相加——2026-08-25 實測踩過，
   混算會讓報告數字對不上任何一邊（口徑已確認，見計畫檔）。
2. 加總與下鑽必須來自同一份計算，格子上的數字要等於點進去的筆數。
3. 業務分類對照表沒填之前，一律「未分類」，不可以猜、不可以悄悄併進某一類。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_report  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    """板橋 2 台實體（1 台正式、1 台測試）＋ 1 台虛擬機（不算進頁A）；
    內湖 1 台實體分公司（原值不在板橋/內湖/敦南三字裡）；
    1 台退役（兩頁都不該算）；2 台帳外（DYN-/VC-，不可混進主數字）。"""
    rows = [
        dict(asset_serial="HW-1", hostname="core-a", ip="10.0.0.1",
             os="Windows Server 2019", physical_location="板橋機房",
             environment="正式", api_id="N-001", asset_name="核心系統A",
             is_vm="0", asset_status="在用"),
        dict(asset_serial="HW-2", hostname="test-a", ip="10.0.0.2",
             os="Windows Server 2019", physical_location="板橋機房",
             environment="測試環境(UAT)", api_id="N-001", asset_name="核心系統A",
             is_vm="0", asset_status="在用"),
        dict(asset_serial="HW-3", hostname="vm-a", ip="10.0.0.3",
             os="Windows Server 2019", physical_location="板橋機房",
             environment="正式", api_id="N-002", asset_name="非核心系統B",
             is_vm="VM", asset_status="在用"),
        dict(asset_serial="HW-4", hostname="branch-a", ip="10.0.0.4",
             os="Windows Server 2019", physical_location="台中分公司",
             environment="正式", api_id="N-003", asset_name="系統C",
             is_vm="0", asset_status="在用"),
        dict(asset_serial="HW-5", hostname="retired-a", ip="10.0.0.5",
             os="Windows Server 2019", physical_location="板橋機房",
             environment="正式", api_id="N-001", asset_name="核心系統A",
             is_vm="0", asset_status="報廢"),
        dict(asset_serial="DYN-abc123", hostname="offbook-1", ip="10.0.0.6",
             os="Rocky Linux 9.7", physical_location="板橋機房",
             environment="正式", is_vm="0", asset_status="在用"),
        dict(asset_serial="VC-xyz789", hostname="offbook-2", ip="10.0.0.7",
             os="Rocky Linux 9.7", physical_location="板橋機房",
             environment="正式", is_vm="0", asset_status="在用"),
    ]
    for r in rows:
        db.insert_hardware(conn, **r)
    # HW-2 是測試機，但它跟 HW-1 共用 api_id N-001——所以「這台是不是測試」只能
    # 逐台設，不能靠 api_id 對照表推（實際資料裡 155 個 api_id 有 88 個橫跨多種分類）。
    #
    # ⚠️ 2026-08-26 口徑變更：頁B 的「測試」桶改成看**分類**，不再看 CIA 清冊的
    # 環境別。使用者原話：「這個應該跟 CIA 無關，這個分類是為了要算出這三張 PPT
    # 的類別所產生的一個獨特的分類。」——所以這裡要明確把 HW-2 分成「測試環境」，
    # 光是 environment 欄寫 UAT 已經不會讓它進測試桶了。
    conn.execute("UPDATE hardware SET system_category = ? WHERE asset_serial = ?",
                 ("AA.測試環境", "HW-2"))
    conn.commit()


# ===== 帳外資產判定 =====

def test_帳外前綴判定():
    assert system_report.is_off_book("DYN-abc")
    assert system_report.is_off_book("VC-abc")
    assert system_report.is_off_book("AUTO-1.2.3.4")
    assert not system_report.is_off_book("HW-00001355")
    assert not system_report.is_off_book(None)


def test_report_baseline排除退役與帳外與被排除平台(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        serials = {a["asset_serial"] for a in system_report.report_baseline(conn)}
        assert serials == {"HW-1", "HW-2", "HW-3", "HW-4"}
        assert "HW-5" not in serials, "退役的不該進基準"
        assert "DYN-abc123" not in serials, "帳外的不該進基準"
        assert "VC-xyz789" not in serials, "帳外的不該進基準"
    finally:
        conn.close()


def test_帳外資產分來源獨立統計_不併進主數字(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ob = system_report.off_book_summary(conn)
        assert ob["DYN"] == 1 and ob["VC"] == 1 and ob["AUTO"] == 0
    finally:
        conn.close()


# ===== 頁A：各環境實體機分布 =====

def test_頁A只算實體機_虛擬機不算(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = system_report.physical_distribution(conn)
        room = next(r for r in d["rooms"] if r["room"] == "板橋")
        assert room["total"] == 2, "HW-1/HW-2 兩台實體；HW-3 是 VM 不該算進來"
    finally:
        conn.close()


def test_頁A分公司逐一列出不合併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = system_report.physical_distribution(conn)
        assert d["branches"] == [{"name": "台中分公司", "count": 1}]
    finally:
        conn.close()


def test_頁A沒有對照表時全部未分類(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = system_report.physical_distribution(conn)
        room = next(r for r in d["rooms"] if r["room"] == "板橋")
        assert room["categories"] == [{"name": "未分類", "count": 2}]
        assert d["category_note"]
    finally:
        conn.close()


def test_頁A總數等於三機房加分公司(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = system_report.physical_distribution(conn)
        assert d["total_physical"] == sum(r["total"] for r in d["rooms"]) + \
            sum(b["count"] for b in d["branches"])
        assert d["total_physical"] == 3          # HW-1/HW-2（板橋）+ HW-4（分公司）
    finally:
        conn.close()


def test_頁A下鑽按機房_數字等於點進去的筆數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = system_report.physical_distribution(conn)
        room = next(r for r in d["rooms"] if r["room"] == "板橋")
        rows = system_report.drill_physical(conn, room="板橋")
        assert len(rows) == room["total"]
        assert {r["asset_serial"] for r in rows} == {"HW-1", "HW-2"}
    finally:
        conn.close()


def test_頁A下鑽按分公司(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        rows = system_report.drill_physical(conn, branch="台中分公司")
        assert [r["asset_serial"] for r in rows] == ["HW-4"]
    finally:
        conn.close()


def test_頁A下鑽不帶任何條件回空清單_避免誤觸全撈(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        assert system_report.drill_physical(conn) == []
    finally:
        conn.close()


# ===== 頁B：主機系統總覽 =====

def test_頁B測試環境獨立一類_不進核心非核心(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        o = system_report.system_overview(conn)
        assert o["test"] == 1          # HW-2
        assert o["core"] + o["noncore"] + o["uncategorized"] + o["test"] == o["total"]
    finally:
        conn.close()


def test_頁B沒有對照表時全部未分類(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        o = system_report.system_overview(conn)
        assert o["core"] == 0 and o["noncore"] == 0
        assert o["uncategorized"] == 3      # HW-1（正式）、HW-3、HW-4，不含 HW-2（測試）
        assert o["category_note"]
    finally:
        conn.close()


def test_頁B業務系統排行不需要對照表_可直接使用(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        o = system_report.system_overview(conn)
        top = {s["api_id"]: s["count"] for s in o["top5"]}
        assert top["N-001"] == 2      # HW-1 + HW-2（同一個 api_id，測試也算系統台數）
        assert top["N-002"] == 1 and top["N-003"] == 1
    finally:
        conn.close()


def test_頁B有對照表後核心非核心正確分類(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        assert r["accepted"] == 2 and r["rejected"] == 0
        o = system_report.system_overview(conn)
        assert o["core"] == 1          # HW-1（HW-2 分類是測試環境，另歸測試桶）
        assert o["noncore"] == 1       # HW-3
        assert o["uncategorized"] == 1  # HW-4（N-003 沒填）
    finally:
        conn.close()


def test_匯入不合法分類名稱會被擋掉_不是悄悄接受(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = system_report.import_system_category(conn, {"N-001": "亂打的分類"})
        assert r["accepted"] == 0 and r["rejected"] == 1
        o = system_report.system_overview(conn)
        assert o["core"] == 0, "無效分類不該讓機器被算進核心交易"
    finally:
        conn.close()


def test_匯入空白值會清掉既有分類(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(conn, {"N-001": "M.金融交易服務"})
        assert system_report.system_overview(conn)["core"] == 1
        system_report.import_system_category(conn, {"N-001": ""})
        assert system_report.system_overview(conn)["core"] == 0
    finally:
        conn.close()


def test_頁B下鑽四個桶各自的數字等於點進去的筆數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        o = system_report.system_overview(conn)
        for bucket in ("core", "noncore", "test", "uncategorized"):
            rows = system_report.drill_system_overview(conn, bucket=bucket)
            assert len(rows) == o[bucket], f"{bucket} 格子數字跟下鑽筆數不一致"
    finally:
        conn.close()


def test_頁B下鑽單一業務系統(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        rows = system_report.drill_system_overview(conn, api_id="N-001")
        assert {r["asset_serial"] for r in rows} == {"HW-1", "HW-2"}
    finally:
        conn.close()


# ===== 頁B：各機房系統資源分布表 =====

def test_頁B機房分布_總數等於四機房加分公司(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        o = system_report.system_overview(conn)
        rooms = {r["room"]: r for r in o["rooms"]}
        assert set(rooms) == {"板橋", "內湖", "敦南", "分公司"}
        assert sum(r["total"] for r in rooms.values()) == o["total"]
    finally:
        conn.close()


def test_頁B機房分布_分公司歸總不逐一列出(tmp_path):
    """跟頁A不同：頁A分公司逐台列出據點名，頁B這張表分公司只算一個彙總數。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        o = system_report.system_overview(conn)
        rooms = {r["room"]: r for r in o["rooms"]}
        assert rooms["分公司"]["total"] == 1       # HW-4（台中分公司）
        assert rooms["板橋"]["total"] == 3          # HW-1/HW-2/HW-3
    finally:
        conn.close()


def test_頁AB_is_vm欄位空白時退回device_model判斷(tmp_path):
    """2026-08-25 查證：真實資料裡 device_model 寫「(VM)」但 is_vm 欄位空白/0 的
    情況大量存在，害頁A實體機數字與頁B運算平台的實體機數字都灌水——這裡驗證
    兩頁都吃到 manage_state.is_vm_value() 的 device_model 退路，不是只有其中一頁。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        db.insert_hardware(
            conn, asset_serial="HW-6", hostname="vm-nodeviceflag", ip="10.0.0.8",
            os="Windows Server 2019", physical_location="板橋機房",
            environment="正式", api_id="N-004", asset_name="系統D",
            is_vm=0, device_model="(VM)", asset_status="在用")
        conn.commit()

        pd = system_report.physical_distribution(conn)
        room = next(r for r in pd["rooms"] if r["room"] == "板橋")
        assert room["total"] == 2, "HW-6 device_model=(VM) 該被當虛擬機，不該算進頁A實體機"

        o = system_report.system_overview(conn)
        assert o["vm"] == 2 and o["physical"] == 3
    finally:
        conn.close()


def test_頁B運算平台VM與實體機加總等於全環境(tmp_path):
    """HW-3 是 VM，其餘（HW-1/HW-2/HW-4）是實體機——1 vm + 3 physical = 4 = total。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        o = system_report.system_overview(conn)
        assert o["vm"] == 1 and o["physical"] == 3
        assert o["vm"] + o["physical"] == o["total"]
        assert o["virtualization_rate"] == 25.0
    finally:
        conn.close()


def test_頁B細分類正確歸組到核心非核心(tmp_path):
    """2026-08-25 使用者拍板分類要跟簡報一樣細：M.金融交易服務屬核心交易組，
    監控維運平台屬非核心組——group 欄位決定歸屬，不是分類名稱字面。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        o = system_report.system_overview(conn)
        assert o["core"] == 1 and o["noncore"] == 1
        core_names = {c["name"]: c["count"] for c in o["core_categories"]}
        noncore_names = {c["name"]: c["count"] for c in o["noncore_categories"]}
        assert core_names["M.金融交易服務"] == 1
        assert noncore_names["Y.監控維運平台"] == 1
    finally:
        conn.close()


def test_頁B核心非核心細分類明細加總等於粗分類台數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        o = system_report.system_overview(conn)
        assert sum(c["count"] for c in o["core_categories"]) == o["core"]
        assert sum(c["count"] for c in o["noncore_categories"]) == o["noncore"]
    finally:
        conn.close()


def test_頁C核心Top5加其他系統小計等於核心台數(tmp_path):
    """跟簡報同一種結構：Top5 個別系統 + 其他系統小計 = 核心交易服務總台數。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        o = system_report.system_overview(conn)
        assert o["core_other_count"] + sum(s["count"] for s in o["core_top5"]) == o["core"]
        assert {s["api_id"] for s in o["core_top5"]} == {"N-001"}
    finally:
        conn.close()


def test_測試桶看分類不看CIA清冊的環境別(tmp_path):
    """2026-08-26 使用者指正的口徑：「這個應該跟 CIA 無關，這個分類是為了要算出
    這三張 PPT 的類別所產生的一個獨特的分類。」

    原本頁B 是 `if env == "測試"` 就歸測試桶，等於讓 CIA 清冊的環境別蓋過分類。
    改成看分類之後，這兩件事就分開了——這個測試同時釘住兩個方向：
      1. 環境別寫測試、但分類是核心 → 算**核心**（不是測試）
      2. 環境別寫正式、但分類是測試環境 → 算**測試**（不是核心）
    兩個方向都要守，只守一邊的話把條件寫反了測試照樣會過。
    """
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(conn, {"N-001": "M.金融交易服務"})

        # 方向1：HW-2 環境別是 UAT，把它的分類改成核心 → 應該算核心，不算測試
        system_report.set_asset_categories(conn, ["HW-2"], "M.金融交易服務")
        o = system_report.system_overview(conn)
        assert o["test"] == 0, "環境別寫測試不該把它拉進測試桶"
        assert o["core"] == 2, "HW-1 與 HW-2 分類都是核心，兩台都要算核心"

        # 方向2：HW-1 環境別是正式，把它的分類改成測試環境 → 應該算測試
        system_report.set_asset_categories(conn, ["HW-1"], "AA.測試環境")
        o = system_report.system_overview(conn)
        assert o["test"] == 1, "環境別寫正式但分類是測試環境，要算測試"
        assert o["core"] == 1

        # 天條：加總與下鑽必須走同一份計算
        for bucket in ("core", "noncore", "test", "uncategorized"):
            rows = system_report.drill_system_overview(conn, bucket=bucket)
            assert len(rows) == o[bucket], f"{bucket} 格子數字跟下鑽筆數不一致"
    finally:
        conn.close()


def test_頁C細分類下鑽數字等於點進去的筆數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        # N-001 底下有 HW-1 與 HW-2，但 HW-2 逐台設成「測試環境」——**逐台分類會
        # 壓過 api_id 對照表**，所以照分類下鑽只會撈到 HW-1。
        # 這正是分類要掛在每一台上而不是掛在 api_id 上的理由：同一個業務系統
        # 底下的機器不見得同一類（實測 155 個 api_id 有 88 個橫跨多種分類）。
        rows = system_report.drill_system_overview(conn, category="M.金融交易服務")
        assert {r["asset_serial"] for r in rows} == {"HW-1"}
        rows_core = system_report.drill_system_overview(
            conn, category="M.金融交易服務", bucket="core")
        assert {r["asset_serial"] for r in rows_core} == {"HW-1"}
        rows_test = system_report.drill_system_overview(conn, category="AA.測試環境")
        assert {r["asset_serial"] for r in rows_test} == {"HW-2"}
        rows2 = system_report.drill_system_overview(conn, category="Y.監控維運平台")
        assert {r["asset_serial"] for r in rows2} == {"HW-3"}
    finally:
        conn.close()


def test_頁B機房分布_下鑽數字等於點進去的筆數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(
            conn, {"N-001": "M.金融交易服務", "N-002": "Y.監控維運平台"})
        o = system_report.system_overview(conn)
        for r in o["rooms"]:
            for bucket in ("core", "noncore", "test", "uncategorized"):
                if r[bucket]:
                    rows = system_report.drill_system_overview(
                        conn, room=r["room"], bucket=bucket)
                    assert len(rows) == r[bucket], f"{r['room']}/{bucket} 對不上下鑽筆數"
            rows_all = system_report.drill_system_overview(conn, room=r["room"])
            assert len(rows_all) == r["total"]
    finally:
        conn.close()


# ===== 對照表匯出範本 =====

def test_匯出範本依台數大到小排序(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        rows = system_report.system_category_template(conn)
        counts = [r["count"] for r in rows]
        assert counts == sorted(counts, reverse=True)
        n001 = next(r for r in rows if r["api_id"] == "N-001")
        assert n001["count"] == 2 and n001["name"] == "核心系統A"
        assert n001["category"] == ""
    finally:
        conn.close()


def test_匯出範本帶已有的分類值_不是永遠空白(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.import_system_category(conn, {"N-001": "M.金融交易服務"})
        rows = system_report.system_category_template(conn)
        n001 = next(r for r in rows if r["api_id"] == "N-001")
        assert n001["category"] == "M.金融交易服務"
    finally:
        conn.close()


# ===== 型號排除（2026-08-26 使用者逐一確認的清單）=====
#
# 頁A 要排除 PC／NB／入侵偵測設備這類「OS 看起來像伺服器、實際不是機房伺服器」的機器。
# 這是業務判斷、程式推不出來，所以放設定檔由人維護。

def test_型號排除必須整格相等不可用子字串(tmp_path):
    """清單裡有「PC」。若用子字串比對，「HPE ProLiant」「Exadata High Capacity」
    這種含 pc/PC 的正常伺服器會一起被排掉——那會讓頁A 憑空少掉幾十台。

    這跟 2026-08-26 修 is_vm 時的教訓同一個：當時若用子字串找「VM」，
    「ATEN…KVM」這種實體 KVM 切換器就會被誤判成虛擬機。
    """
    assert system_report.is_excluded_model("PC") is True
    assert system_report.is_excluded_model("  pc  ") is True        # 去空白、不分大小寫
    assert system_report.is_excluded_model("TippingPoint 5600 TXE") is True

    # 這幾個都含 "pc"／"nb" 的字母，但絕不可被排除
    for keep in ("HPE ProLiant DL360 Gen10",
                 "Oracle Exadata X10M High Capacity 1/4 Rack",
                 "Lenovo x3650 M5", "DELL R750", "IBM AS400"):
        assert system_report.is_excluded_model(keep) is False, keep

    assert system_report.is_excluded_model(None) is False
    assert system_report.is_excluded_model("") is False


def test_排除的型號不算進機房台數但要講得出來(tmp_path):
    """排除的**不是刪掉**：要回報是哪些型號、各幾台，否則總數對不起來時
    沒有人講得出為什麼（使用者還要拿去跟管理員核對）。"""
    conn = _conn(tmp_path)
    try:
        # 兩台正常伺服器 + 一台 PC，全部實體、全部板橋
        for serial, model in (("HW-A", "DELL R750"), ("HW-B", "HPE DL360 Gen10"),
                              ("HW-C", "PC")):
            db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(),
                               os="Windows Server 2019", device_model=model,
                               physical_location="01_板橋機房", is_vm=0)
        conn.commit()

        d = system_report.physical_distribution(conn)
        rooms = {r["room"]: r["total"] for r in d["rooms"]}
        assert rooms.get("板橋") == 2, "PC 不該算進機房台數"
        assert d["excluded_models_total"] == 1
        assert d["excluded_models"] == [{"device_model": "PC", "count": 1}]
    finally:
        conn.close()


def test_排除的那批也要能點開看是哪幾台(tmp_path):
    """使用者鐵律：每個數字都要可以追。排除區的數字也是數字。"""
    conn = _conn(tmp_path)
    try:
        for serial, model in (("HW-A", "DELL R750"), ("HW-C", "PC"), ("HW-D", "PC")):
            db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(),
                               os="Windows Server 2019", device_model=model,
                               physical_location="01_板橋機房", is_vm=0)
        conn.commit()

        d = system_report.physical_distribution(conn)
        n = d["excluded_models"][0]["count"]
        got = system_report.drill_physical(conn, excluded_model="PC")
        assert len(got) == n == 2, "格子數字必須等於下鑽筆數"
        assert {r["asset_serial"] for r in got} == {"HW-C", "HW-D"}

        # 查機房時不可以混進被排除的那批
        room = system_report.drill_physical(conn, room="板橋")
        assert {r["asset_serial"] for r in room} == {"HW-A"}
    finally:
        conn.close()


def test_型號排除只影響頁A不影響頁B(tmp_path):
    """頁B 算的是「全環境系統組成」，那些機器仍然是資產、仍要算進去；
    頁A 問的是「機房裡有幾台實體伺服器」。兩頁問的不是同一件事。"""
    conn = _conn(tmp_path)
    try:
        for serial, model in (("HW-A", "DELL R750"), ("HW-C", "PC")):
            db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(),
                               os="Windows Server 2019", device_model=model,
                               physical_location="01_板橋機房", is_vm=0)
        conn.commit()
        # report_baseline 是兩頁共用的起點，不可以先把型號排掉
        assert len(system_report.report_baseline(conn)) == 2
    finally:
        conn.close()
