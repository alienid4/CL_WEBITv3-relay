"""分類名稱改以系統管理員那份盤點表為準（2026-08-26 使用者拍板）。

原本白名單是照部門簡報抄的，有 8 個名字抄歪。改 JSON 很容易，**危險的是 DB 裡
已經存的舊名稱**：APID→分類 對照表存在 app_settings，值就是分類名稱字串；只改
白名單的話那些舊名稱不會報錯，只會從報表上靜靜消失（歸不進核心/非核心）。

所以這裡守兩件事：
1. migration 真的把兩處都換掉（hardware 逐台欄位、app_settings 那份 JSON）
2. 換完之後新名稱**在白名單裡**——不然等於把資料從一個孤兒改成另一個孤兒
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_report  # noqa: E402


def _valid_names():
    cfg = json.loads(
        (ROOT / "APP" / "asset-module" / "backend" / "report_groups.json")
        .read_text(encoding="utf-8"))
    return {c["name"] for c in cfg["system_categories"]}


def _strip(v):
    return re.sub(r"^[A-Za-z]{1,3}[.．、]\s*", "", str(v or "").strip()).strip()


def test_兩個migration接力之後每個舊值都落在白名單上():
    """兩支 migration 是**接力**的，要驗合成結果不是各自的中間值：

      1. `_rename_system_categories()` 修錯字（嘉寶→嘉實、電子→雷影…）
      2. `_prefix_categories()` 補字母編號（嘉實新樹精靈AP → I.嘉實新樹精靈AP）

    第 2 支刻意**不寫死對應表**，而是拿 DB 裡那份清單去比對——因為分類名稱含
    公司識別字，寫死在 `APP/` 底下會被去識別化改掉，那條 migration 就等於沒作用
    而且不會報錯（2026-08-26 打 patch 時當場踩到）。

    所以這裡驗的是：修完錯字之後的值，去掉前綴要能在白名單裡**唯一**對到一個。
    """
    valid = _valid_names()
    bare = {}
    for n in valid:
        bare.setdefault(_strip(n), []).append(n)

    resolved = db._resolve_renames(db._SYSTEM_CATEGORY_RENAMES)
    for old, mid in resolved.items():
        hits = bare.get(_strip(mid), [])
        assert len(hits) == 1, f"{old} → {mid}，但去前綴後在白名單裡對到 {len(hits)} 個"
        assert old not in valid, f"{old} 應該已經從白名單移除"
    # 具體釘住那條最長的鏈，不要只靠通則
    assert _strip(resolved["電子密碼中心"]) == "雷影密碼中心"


def test_分類名稱要保留字母編號():
    """2026-08-26 使用者指正：「我已經給你分類了，你還把 A.XXX 的 A. 拿掉」。

    那個 A~AA 的字母是使用者給的分類名稱的一部分，不是我可以自己修剪的排序編號。
    """
    valid = _valid_names()
    for expect in ("A.雷影密碼中心", "AA.測試環境", "X.資安管理系統", "N.技術中台"):
        assert expect in valid, f"{expect} 應該原樣留在白名單裡"
    # 剝掉前綴的版本不該還留著
    for stripped in ("雷影密碼中心", "測試環境", "資安管理系統"):
        assert stripped not in valid


def test_migration_會換掉逐台欄位與APID對照表裡的舊名稱():
    old, new = next(iter(db._resolve_renames(db._SYSTEM_CATEGORY_RENAMES).items()))
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(conn, asset_serial="HW-1", hostname="a", ip="10.99.0.1",
                               asset_status="在用")
            # 模擬升級前的既有資料：兩處都是舊名稱
            conn.execute("UPDATE hardware SET system_category = ? WHERE asset_serial = ?",
                         (old, "HW-1"))
            db.set_setting(conn, "report_system_category",
                           json.dumps({"N-001": old, "N-002": "其他"}, ensure_ascii=False))
            conn.commit()

            assert db._rename_system_categories(conn) == 2

            assert conn.execute(
                "SELECT system_category FROM hardware WHERE asset_serial = 'HW-1'"
            ).fetchone()[0] == new
            m = json.loads(db.get_setting(conn, "report_system_category"))
            assert m["N-001"] == new
            assert m["N-002"] == "其他"          # 沒改到的不要動

            # 冪等：再跑一次不該再改到任何東西
            assert db._rename_system_categories(conn) == 0
        finally:
            conn.close()


def test_舊名稱的資料兩支migration跑完真的被報表認得():
    """這是兩支 migration 存在的理由——舊值改完要能歸進核心/非核心，
    不是從一個孤兒變成另一個孤兒。這裡走完整條路：
    舊值 → 修錯字 → 補字母編號 → 報表認得。

    `_prefix_categories` 拿 **DB 裡那份清單**去比對（真實清單不進版控，
    因為會被去識別化改掉），所以測試要先把清單放進 app_settings——
    正式環境是匯入盤點表時登錄的。
    """
    old = next(iter(db._resolve_renames(db._SYSTEM_CATEGORY_RENAMES)))
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(conn, asset_serial="HW-1", hostname="a", ip="10.99.0.1",
                               os="Windows Server 2019", physical_location="板橋機房",
                               environment="正式", api_id="N-001", is_vm="0",
                               asset_status="在用")
            conn.execute("UPDATE hardware SET system_category = ? WHERE asset_serial = ?",
                         (old, "HW-1"))
            cfg = json.loads(
                (ROOT / "APP" / "asset-module" / "backend" / "report_groups.json")
                .read_text(encoding="utf-8"))
            system_report.set_category_defs(conn, cfg["system_categories"])
            conn.commit()

            db._rename_system_categories(conn)
            assert db._prefix_categories(conn) == 1

            row = next(r for r in system_report.classify_rows(conn)
                       if r["asset_serial"] == "HW-1")
            valid = system_report.classify_summary(conn)["valid_categories"]
            assert row["category"] in valid, f"改完是 {row['category']}，仍不在白名單裡"
            assert _strip(row["category"]) == _strip(
                db._resolve_renames(db._SYSTEM_CATEGORY_RENAMES)[old])
            # 冪等：再跑一次不該再動任何東西
            assert db._prefix_categories(conn) == 0
        finally:
            conn.close()
