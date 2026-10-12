"""AD 人員名單匯入（B-23）。

⚠️ **測試資料全部是編的**（張三／01000001）。真名單是全體員工個資，
只能放 `DATA/`（已 gitignore），不進版控、不進測試。

守的是這幾件（每一條都是「不這樣做就會把人對錯」）：

1. **主管要用「姓名＋部門代碼」兩個鍵一起比**。AD 同名時 CN 會加後綴（`王五2`），
   只比姓名會對到錯的人——主管資訊拿來寄信、拿來簽核，對錯人比沒有更糟。
2. **對不上就標對不上，不准近似比對硬湊**，而且要分得出「沒有主管」與「對不上」。
3. **員編從 gecos 開頭取**，取不到回空字串——硬湊會把帳號掛到別人身上。
4. **`Enabled=FALSE` 但主機還有帳號 = 稽核發現**，要列得出來。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ad_import as ad  # noqa: E402
import db  # noqa: E402

HEADER = ["DisplayName", "SamAccountName", "UserPrincipalName", "mail",
          "employeeID", "manager", "department", "Enabled"]


def row(name, emp, mail, manager_dn, dept, enabled="TRUE"):
    return [name, emp, mail, mail, emp, manager_dn, dept, enabled]


# 一個小組織：張三的主管是李四；李四的主管是王五
SHEET = [
    HEADER,
    row("張三", "01000001", "zhangsan@example.com",
        "CN=李四,OU=cs7110000,OU=cs7000000,OU=Example,DC=example,DC=com",
        "營運管理處結算交割部(7110000)"),
    row("李四", "01000002", "lisi@example.com",
        "CN=王五,OU=cs7000000,OU=Example,DC=example,DC=com",
        "營運管理處結算交割部(7110000)"),
    row("王五", "01000003", "wangwu@example.com", "",
        "營運管理處(7000000)"),
]


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def test_部門拆成名稱與代碼():
    assert ad.split_department("營運管理處結算交割部(7110000)") == \
        ("營運管理處結算交割部", "7110000")
    # 拆不出來整段當名稱，代碼留空——不可以因為格式不合就丟掉整欄
    assert ad.split_department("資訊部") == ("資訊部", "")
    assert ad.split_department("") == ("", "")
    assert ad.split_department(None) == ("", "")


def test_DN取姓名與第一個OU代碼():
    name, ou, dup = ad.parse_manager_dn(
        "CN=李四,OU=cs7110000,OU=cs7000000,OU=Example,DC=example,DC=com")
    assert name == "李四"
    assert ou == "7110000", "要取**第一個** OU（他所屬部門），後面的是上層組織"
    assert dup is False
    # 字母前綴要拿掉，才跟 department 括號裡的代碼對得上
    assert ad.parse_manager_dn("CN=甲,OU=cs7000000,DC=x")[1] == "7000000"
    assert ad.parse_manager_dn("")[0] == ""


def test_CN帶數字後綴要標同名疑慮():
    """AD 遇到同名會加後綴。那種**不可以自動對**，要人看過。"""
    name, ou, dup = ad.parse_manager_dn("CN=王五2,OU=cs7000000,DC=x")
    assert name == "王五2" and dup is True


def test_主管用姓名加部門代碼對得上():
    people = ad.resolve_managers(ad.parse(SHEET)[0])
    by = {p["employee_id"]: p for p in people}
    assert by["01000001"]["manager_state"] == ad.MGR_OK
    assert by["01000001"]["manager_id"] == "01000002"
    assert by["01000001"]["manager_mail"] == "lisi@example.com"
    # 沒有主管跟對不上是兩件事
    assert by["01000003"]["manager_state"] == ad.MGR_NONE


def test_同名不同部門不可以對錯人():
    """兩個「李四」，一個在 7110000、一個在 7220000。

    只比姓名會挑到錯的那個。加上部門代碼才對得準。
    """
    sheet = [
        HEADER,
        row("張三", "01000001", "z@example.com",
            "CN=李四,OU=cs7220000,OU=Example,DC=x", "研發處(7220000)"),
        row("李四", "01000002", "lisi-a@example.com", "", "營運管理處結算交割部(7110000)"),
        row("李四", "01000009", "lisi-b@example.com", "", "研發處(7220000)"),
    ]
    by = {p["employee_id"]: p for p in ad.resolve_managers(ad.parse(sheet)[0])}
    assert by["01000001"]["manager_id"] == "01000009", "要對到研發處那個李四"
    assert by["01000001"]["manager_mail"] == "lisi-b@example.com"


def test_對不上要標對不上而不是硬湊():
    sheet = [
        HEADER,
        row("張三", "01000001", "z@example.com",
            "CN=不在名單裡的人,OU=cs9999999,DC=x", "營運管理處(7000000)"),
    ]
    p = ad.resolve_managers(ad.parse(sheet)[0])[0]
    assert p["manager_state"] == ad.MGR_FAIL
    assert p["manager_id"] == "" and p["manager_mail"] == "", \
        "對不上就留空並標記，**不准用近似比對挑一個最像的**"


def test_同姓名同部門有兩人要標同名疑慮不可挑一個():
    sheet = [
        HEADER,
        row("張三", "01000001", "z@example.com",
            "CN=李四,OU=cs7110000,DC=x", "營運管理處結算交割部(7110000)"),
        row("李四", "01000002", "a@example.com", "", "營運管理處結算交割部(7110000)"),
        row("李四", "01000008", "b@example.com", "", "營運管理處結算交割部(7110000)"),
    ]
    p = ad.resolve_managers(ad.parse(sheet)[0])[0]
    assert p["manager_state"] == ad.MGR_DUP and p["manager_id"] == ""


def test_沒有員編欄位要明確報錯():
    with pytest.raises(ValueError, match="員編"):
        ad.parse([["DisplayName", "mail"], ["張三", "z@example.com"]])


def test_從gecos取員編取不到回空字串():
    assert ad.employee_id_from_gecos("01002861-張三_數位金融部") == "01002861"
    assert ad.employee_id_from_gecos("01000001") == "01000001"
    # 取不到就空的——**不可以硬湊**，取錯員編會把帳號掛到別人身上
    assert ad.employee_id_from_gecos("webit3 readonly collector") == ""
    assert ad.employee_id_from_gecos("") == ""
    assert ad.employee_id_from_gecos(None) == ""


def test_匯入後統計與查詢(conn):
    c = ad.import_rows(conn, SHEET, "ad.xlsx", "tester")
    assert c["rows"] == 3 and c["enabled_n"] == 3 and c["disabled_n"] == 0
    assert c["mgr_ok"] == 2 and c["mgr_fail"] == 0
    p = ad.lookup(conn, "01000001")
    assert p["display_name"] == "張三" and p["dept_code"] == "7110000"
    assert p["manager_id"] == "01000002"


def test_重匯是整份取代不是累加(conn):
    """AD 名單是全量快照。累加會讓離職者永遠留在表裡。"""
    ad.import_rows(conn, SHEET, "a.xlsx", "tester")
    ad.import_rows(conn, SHEET, "b.xlsx", "tester")
    assert conn.execute("SELECT COUNT(*) FROM ad_person").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM ad_batch").fetchone()[0] == 2, \
        "但匯入歷史兩筆都要在"


def test_離職者還留著主機帳號要列得出來(conn):
    sheet = [HEADER,
             row("張三", "01000001", "z@example.com", "", "資訊部(7000000)", "FALSE")]
    ad.import_rows(conn, sheet, "ad.xlsx", "tester")
    conn.execute(
        "INSERT INTO host_account (ip, asset_serial, username, gecos) VALUES (?,?,?,?)",
        ("192.0.2.10", "A-1", "zhangsan", "01000001-張三_資訊部"))
    conn.commit()
    hits = ad.disabled_with_host_account(conn)
    assert len(hits) == 1 and hits[0]["employee_id"] == "01000001"


def test_對不上的帳號要分兩類(conn):
    """兩種原因、兩種處理方式，不可以混成一張清單。"""
    ad.import_rows(conn, SHEET, "ad.xlsx", "tester")
    conn.executemany(
        "INSERT INTO host_account (ip, asset_serial, username, gecos) VALUES (?,?,?,?)",
        [("192.0.2.10", "A-1", "root", "root"),                       # 解析不出員編
         ("192.0.2.10", "A-1", "someone", "09999999-查無此人_X部")])   # 有員編但不在名單
    conn.commit()
    u = ad.unmatched_accounts(conn)
    assert len(u["no_empid"]) == 1 and u["no_empid"][0]["username"] == "root"
    assert len(u["not_in_ad"]) == 1 and u["not_in_ad"][0]["employee_id"] == "09999999"


# ── 2026-09-22 追加：Excel 吃掉員編前導零 ──────────────────────────────
# 員編實際值是 01003XX8，開頭有 0。Excel 判定成數字會存成 1003XX8。
# 員編是把主機帳號對到人的**唯一鍵**，前導零一掉就全部對不上，
# **而且不會報錯**——只會安靜地變成「這些帳號都沒有主人」。

def test_Excel吃掉前導零要還原():
    """欄位裡只要有值保住了前導零，就用它的長度當基準。"""
    vals = ["01000001", "1000002", "1000003"]      # 第一筆保住了
    out, padded, width = ad.restore_leading_zeros(vals)
    assert out == ["01000001", "01000002", "01000003"]
    assert padded == 2 and width == 8


def test_整欄都被吃掉時用gecos長度當線索(conn):
    """整欄都變數字時欄位自己沒有證據，改用主機帳號 gecos 推——

    那邊是純文字，前導零不會被 Excel 吃掉。
    """
    conn.executemany(
        "INSERT INTO host_account (ip, asset_serial, username, gecos) VALUES (?,?,?,?)",
        [("192.0.2.10", "A-1", "u1", "01000001-甲_X部"),
         ("192.0.2.11", "A-2", "u2", "01000002-乙_X部")])
    conn.commit()
    assert ad.gecos_id_width(conn) == 8
    out, padded, width = ad.restore_leading_zeros(["1000009"], width_hint=8)
    assert out == ["01000009"] and padded == 1 and width == 8


def test_沒有任何證據就不補_不可以猜():
    """沒有線索時補錯長度，會製造一批看似有效、實際對不上任何人的員編。

    **比不補更難查**——所以沒證據就不補，並如實回 width=None。
    """
    out, padded, width = ad.restore_leading_zeros(["1000001", "1000002"])
    assert out == ["1000001", "1000002"] and padded == 0 and width is None


def test_gecos長度取最常見而不是最大值(conn):
    """偶爾一兩筆格式怪異，用 max 會把整欄補成錯的長度。"""
    conn.executemany(
        "INSERT INTO host_account (ip, asset_serial, username, gecos) VALUES (?,?,?,?)",
        [("192.0.2.10", "A-1", "u1", "01000001-甲"),
         ("192.0.2.11", "A-2", "u2", "01000002-乙"),
         ("192.0.2.12", "A-3", "u3", "0100000399-怪_格式")])   # 10 碼的異類
    conn.commit()
    assert ad.gecos_id_width(conn) == 8, "取最常見的 8，不是最大的 10"


def test_還原後要對得上主機帳號_而且筆數要回報(conn):
    """端到端：Excel 轉過的員編 → 還原 → 對得上 gecos。"""
    conn.execute(
        "INSERT INTO host_account (ip, asset_serial, username, gecos) VALUES (?,?,?,?)",
        ("192.0.2.10", "A-1", "zhangsan", "01000001-張三_X部"))
    conn.commit()
    broken = [HEADER,
              ["張三", "1000001", "z@example.com", "z@example.com", "1000001",
               "", "資訊部(7000000)", "TRUE"]]
    c = ad.import_rows(conn, broken, "ad.xlsx", "tester")
    assert c["zero_padded"] == 1, "補了幾筆要回報，不可以默默改資料"
    assert ad.lookup(conn, "01000001") is not None, "還原後要對得上 gecos 的員編"
    # 對不上的清單裡不該再有這個帳號
    assert not ad.unmatched_accounts(conn)["not_in_ad"]


def test_Excel浮點儲存格不可以變成小數字串():
    """openpyxl 對數值儲存格可能給 float，str(1000001.0) 會是 '1000001.0'，

    那串連還原前導零都救不回來（isdigit() 是 False）。
    """
    import io as _io

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    ws.append(["張三", 1000001, "z@x.com", "z@x.com", 1000001, "", "資訊部(7000000)", "TRUE"])
    buf = _io.BytesIO()
    wb.save(buf)
    rows = ad.rows_from_bytes(buf.getvalue(), "ad.xlsx")
    assert rows[1][1] == "1000001", f"不可以是 1000001.0：{rows[1][1]!r}"


# ═══ 錯誤訊息要指對方向（2026-09-23 於 221 實測後補）═══════════════

def test_不是表格的檔案不要叫人去找欄位():
    """221 實測：丟純文字檔進來，訊息是「找不到員編欄位」。

    那會叫人去一個**沒有欄位**的檔案裡找欄位，方向整個錯。
    「這個檔不是表格」跟「是表格但少了那一欄」要分開講。
    """
    with pytest.raises(ValueError) as e:
        ad.parse([["這不是名單"]])
    assert "讀不出表格" in str(e.value)
    assert "找不到員編欄位" not in str(e.value)


def test_是表格但少了員編那欄_要把讀到的標題印出來():
    """只說「找不到員編欄位」，人不知道系統到底讀到了什麼——
    印出實際讀到的標題，他一眼就看得出是欄位名不同還是檔案選錯。"""
    with pytest.raises(ValueError) as e:
        ad.parse([["姓名", "部門", "啟用"], ["王小明", "資訊處", "TRUE"]])
    msg = str(e.value)
    assert "找不到員編欄位" in msg
    assert "姓名" in msg and "部門" in msg
