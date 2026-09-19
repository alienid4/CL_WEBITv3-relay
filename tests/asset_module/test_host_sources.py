"""來源對照表：把「首頁兩個頭條相加大於全庫」這件事鎖成等式（2026-09-18）。

使用者在公司機看到：在管 3,377 ＋ 另有未登記 4,155 ＝ 7,532，可是分佈統計寫「共 5,405 台」。
同一個畫面的加減法對不起來，而且**沒有任何一頁看得出差額是哪幾台**。

真因不是算錯，是兩個頭條不互斥：同一台機器可以同時有一筆 CIA 登記和一筆 DY 登記
（主機名＋IP 相同、序號不同），兩邊各算一次；全庫去重時只算一台。

這支把那個關係寫成不變式：

    CIA在管台 ＋ 只在帳外台 ＋ 兩邊都有台 ＋ 退役台 ＝ 全庫台

以後誰改了去重或前綴判定，這條會先紅。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_sources as hs  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 1. 只在 CIA
    db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="192.0.2.1",
                       asset_status="使用中", environment="正式")
    # 2. 同一台，CIA 與 DY 各一筆 → 兩邊都有（就是差額的來源）
    db.insert_hardware(c, asset_serial="A-2", hostname="h2", ip="192.0.2.2",
                       asset_status="使用中", environment="正式")
    db.insert_hardware(c, asset_serial="DYN-2", hostname="h2", ip="192.0.2.2",
                       asset_status="使用中")
    # 3. 只在 DY
    db.insert_hardware(c, asset_serial="DYN-3", hostname="h3", ip="192.0.2.3")
    # 4. 只在 vCenter
    db.insert_hardware(c, asset_serial="VC-4", hostname="h4", ip="192.0.2.4")
    # 5. 退役
    db.insert_hardware(c, asset_serial="A-5", hostname="h5", ip="192.0.2.5",
                       asset_status="報廢")
    # 6. 缺 IP → machine_key 退回序號，無法跟別的來源比對
    db.insert_hardware(c, asset_serial="A-6", hostname="h6", ip=None,
                       asset_status="使用中")
    c.commit()
    return c


def test_來源分類與加總互斥且窮盡(tmp_path):
    c = _conn(tmp_path)
    items = hs.rows(c)
    s = hs.summary(items)
    assert s["total_hosts"] == 6, f"應該是 6 台（h2 的兩筆要併成一台），實際 {s['total_hosts']}"
    assert s["total_rows"] == 7, "登記筆數一筆都不能少"
    assert s["both_hosts"] == 1, "h2 同時有 CIA 與 DY 登記"
    assert s["offbook_only_hosts"] == 2, "h3(DY) 與 h4(vCenter)"
    assert s["retired_hosts"] == 1
    # 互斥且窮盡：四類相加＝全庫
    assert s["cia_hosts"] + s["offbook_only_hosts"] + s["retired_hosts"] == s["total_hosts"], (
        "CIA在管 ＋ 只在帳外 ＋ 退役 要等於全庫台數，否則有機器沒被歸到任何一類")


def test_兩個頭條相加的差額就是兩邊都有的台數(tmp_path):
    """首頁「在管」＋「另有未登記」－ 全庫 ＝ 兩邊都有。這就是使用者看到 7532 vs 5405 的那個差。"""
    c = _conn(tmp_path)
    items = hs.rows(c)
    s = hs.summary(items)
    在管 = s["cia_hosts"]
    另有未登記 = sum(1 for h in items if not h["in_cia"] or h["in_dy"] or h["in_vc"] or h["in_auto"])
    # 頭條「另有未登記」＝所有帶帳外前綴的台（含同時有 CIA 登記的那些）
    帳外台 = sum(1 for h in items if h["in_dy"] or h["in_vc"] or h["in_auto"])
    assert 在管 + 帳外台 - s["total_hosts"] + s["retired_hosts"] == s["both_hosts"], (
        f"在管 {在管} ＋ 帳外 {帳外台} － 全庫 {s['total_hosts']} ＋ 退役 {s['retired_hosts']} "
        f"應該等於兩邊都有 {s['both_hosts']}")
    assert 另有未登記 >= 帳外台


def test_無法比對的要標出來而不是當成CIA沒這台(tmp_path):
    """缺主機名或 IP 的一定配不到別的來源——那是無法判斷，不可以讀成「確定只在一邊」。"""
    c = _conn(tmp_path)
    items = hs.rows(c)
    h6 = [h for h in items if h["cia_serial"] == "A-6"]
    assert len(h6) == 1
    assert h6[0]["match_basis"].startswith("只有序號"), "缺 IP 的要標明無法比對"
    s = hs.summary(items)
    assert s["unmatchable_hosts"] == 1


def test_匯出欄位定義與預設一致():
    """預設勾選的欄位必須都是合法欄位——不然匯出會少欄還不報錯。"""
    assert set(hs.DEFAULT_COLUMNS) <= set(hs.COLUMNS)
    assert "hostname" in hs.DEFAULT_COLUMNS and "ip" in hs.DEFAULT_COLUMNS
    for k in ("in_cia", "in_dy", "source_class", "match_basis"):
        assert k in hs.COLUMNS, f"{k} 是這張表的重點欄位，不可以拿掉"


def test_放寬比對只標示不合併(tmp_path):
    """缺 IP 的那台，若主機名跟某台完整列相同 → 標成疑似，但仍然是獨立的一台。

    **絕不可以自動併**：IP 會被回收、主機名會重複，把兩台不同的機器併成一台
    比漏抓更糟。這支就是釘住「只標示、不合併」。
    """
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="dup", ip="192.0.2.1",
                       asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-9", hostname="dup", ip=None,
                       asset_status="使用中")      # 缺 IP → 退回序號
    c.commit()
    items = hs.rows(c)
    assert len(items) == 2, "疑似同一台也必須維持兩列——不可以自動合併"
    lone = [h for h in items if h["cia_serial"] == "A-9"][0]
    full = [h for h in items if h["cia_serial"] == "A-1"][0]
    assert lone["loose_match"], "主機名相同，應該要給出候選"
    assert "主機名相同" in lone["loose_basis"]
    assert not full["loose_match"], "完整列不做放寬比對，它已經有正規的鍵"
    assert hs.summary(items)["loose_candidate_hosts"] == 1


def test_配不到候選就不要亂標(tmp_path):
    """沒有同名也沒有同 IP 的殘缺列，疑似欄要留空——空白代表『真的找不到』。"""
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="192.0.2.1",
                       asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-9", hostname="lonely", ip=None,
                       asset_status="使用中")
    c.commit()
    items = hs.rows(c)
    lone = [h for h in items if h["cia_serial"] == "A-9"][0]
    assert lone["loose_match"] == ""
    assert hs.summary(items)["loose_candidate_hosts"] == 0


def test_vm_uuid_比主機名可靠_要當成候選依據(tmp_path):
    """兩筆都缺主機名／IP，但 vm_uuid 相同 → 要配成候選。

    只拿「完整列」當候選來源會漏掉這種（兩邊都殘缺），所以身分欄位的索引兩邊都收。
    """
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="x1", ip=None,
                       asset_status="使用中", vm_uuid="564d-aaaa")
    db.insert_hardware(c, asset_serial="DYN-1", hostname=None, ip="192.0.2.9",
                       vm_uuid="564d-aaaa")
    c.commit()
    items = hs.rows(c)
    assert len(items) == 2, "仍然不可以自動合併"
    for h in items:
        assert h["loose_match"], "vm_uuid 相同要互指對方"
        assert "VM UUID 相同" in h["loose_basis"]


def test_NA_不可以被當成值(tmp_path):
    """匯入來源會把空值填成「NA」。當成值比對，所有填 NA 的機器會互相配成同一台。"""
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="x1", ip=None,
                       asset_status="使用中", hw_serial="NA", mac="NA")
    db.insert_hardware(c, asset_serial="A-2", hostname="x2", ip=None,
                       asset_status="使用中", hw_serial="NA", mac="NA")
    c.commit()
    items = hs.rows(c)
    for h in items:
        assert h["loose_match"] == "", f"NA 被當成值了：{h['loose_basis']}"


def test_自己不可以配到自己(tmp_path):
    """同一台的多筆登記共用同一個 key，不可以因此變成『疑似同一台』指向自己。"""
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname=None, ip="192.0.2.1",
                       asset_status="使用中", vm_uuid="564d-bbbb")
    c.commit()
    items = hs.rows(c)
    assert items[0]["loose_match"] == "", "只有自己一台，不該有候選"


def test_一筆報廢一筆使用中_算在管_跟首頁同口徑(tmp_path):
    """公司機實測差 35 台（3,342 vs 首頁 3,377）的回歸。

    首頁「在管」＝排掉退役的那幾筆後，還有使用中 CIA 登記就算。這裡以前寫成
    「任一筆退役就算退役」，同一台一筆報廢一筆使用中時就被踢出在管。
    """
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="m", ip="192.0.2.1", asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-2", hostname="m", ip="192.0.2.1", asset_status="報廢")
    c.commit()
    s = hs.summary(hs.rows(c))
    assert s["cia_hosts"] == 1, "有一筆使用中就算在管"
    assert s["retired_hosts"] == 0, "還有使用中的，不能算已退役"
    assert s["cia_hosts"] + s["offbook_only_hosts"] + s["retired_hosts"] == s["total_hosts"]
