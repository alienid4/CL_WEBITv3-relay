"""系統(APID) × 機房統計：三層的證據強度不同，畫面不可以混為一談。

2026-09-10 使用者要求這一頁。查 221 真實資料（4784 台）之後發現：
- 系統與機房是**登記資料**，涵蓋得還可以（3443 台接上系統）
- **AP／DB 只能用關鍵字推**，而且只推得出 DB 157 台、AP 183 台，
  **4444 台推不出來（93%）**

所以這裡守的重點不是「算得對不對」，是**「不可以假裝分得出來」**：
未分類要當一等公民回傳，AP/DB 要標明是推論。
把 93% 藏起來畫成漂亮的圓餅圖，比不做這個功能更糟。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_stats as ss  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _sys(c, api_id, name, dept="資訊架構部"):
    c.execute("INSERT INTO business_system (api_id, name, ap_department) VALUES (?,?,?)",
              (api_id, name, dept))


def _hw(c, serial, api_id=None, loc=None, env="正式", **kw):
    db.insert_hardware(c, asset_serial=serial, api_id=api_id,
                       physical_location=loc, environment=env, **kw)


# ---------- 機房正規化 ----------

def test_同一個機房的兩種寫法要算成一個():
    """`02_內湖機房` 與 `內湖機房` 在真實資料裡同時存在。不合併會變成兩個機房。"""
    assert ss.normalize_location("02_內湖機房") == ss.normalize_location("內湖機房")
    assert ss.normalize_location("01_板橋機房") == "板橋機房"


def test_沒填機房要回未填而不是空字串():
    """「未填 1141 台」跟一格空白是兩回事——後者會被當排版問題忽略。"""
    for v in (None, "", "   "):
        assert ss.normalize_location(v) == "未填"


def test_正規化只脫編號_不做模糊比對():
    """猜錯了沒人會發現，所以只做看得懂也講得出來的那一種。"""
    assert ss.normalize_location("敦南總公司") == "敦南總公司"
    assert ss.normalize_location("分公司-高雄") == "分公司-高雄"


# ---------- AP／DB 推論 ----------

def test_判不出角色要寫不完整_不可以寫成沒資料():
    """整個檔案最重要的一條（使用者 2026-09-10 兩次糾正才定下來的用字）。

    他的原話：「目前就算沒有納管，資料還是會在啊，不一定要納管才能統計出來。
    我可以接受有些資訊不完整，你直接寫『不完整』就好，但不能因為沒納管，
    你就去說這都沒資料。」

    登記資料本來就在，缺的只是判角色的線索——**有沒有納管是原因，不是標籤**。
    所以同一台機器不論納管與否，判不出來時都叫「資料不完整」。
    """
    assert ss.guess_role("", "", "", "", onboarded=False) == ss.ROLE_INCOMPLETE
    assert ss.guess_role("", "", "", "", onboarded=True) == ss.ROLE_INCOMPLETE
    assert ss.guess_role("一般用途", "SECSVR001", "PowerEdge R750", "RedHat 8.5",
                         onboarded=True) == ss.ROLE_INCOMPLETE
    assert "沒資料" not in ss.ROLE_INCOMPLETE
    assert "納管" not in ss.ROLE_INCOMPLETE, "納管與否是原因，不該出現在標籤上"


def test_實際跑的服務優先於登記欄位():
    """服務是機器自己講的＝證據；登記欄位是人填的＝推論。證據要贏。"""
    # 登記欄位看起來像 AP，但機器上跑的是 oracle → 以機器為準
    assert ss.guess_role("Web 前端", "", "", "", services=["tnslsnr"]) == ss.ROLE_DB
    assert ss.guess_role("交易資料庫", "", "", "", services=["nginx"]) == ss.ROLE_AP


def test_OpenShift節點不可以被硬塞進AP或DB():
    """221 實測：技術中台 348 台裡 310 台是 OCP 節點（CoreOS／RHCOS）。

    它既不是應用也不是資料庫，塞進任何一邊都是錯的答案。
    """
    for v in ("OCP Worker (paas-bq1)", "OCP Master", "CoreOS4.20", "RHCOS 4"):
        assert ss.guess_role(v, "", "", "") == ss.ROLE_PLATFORM, v


def test_網通儲存設備不該被問是AP還是DB():
    for v in ("網路設備", "儲存設備", "FortiGate 101E", "IBM San Switch"):
        assert ss.guess_role("", "", v, "") == ss.ROLE_DEVICE, v


def test_先判DB再判AP():
    """`web` 這類字太泛，資料庫主機的網管介面也常帶 web。

    順序反過來會把 DB 誤標成 AP——而且錯得看不出來，因為兩種都是合理的答案。
    """
    assert ss.guess_role("Oracle 資料庫 web 管理介面", "", "", "") == "DB"


def test_認得出常見的DB與AP線索():
    assert ss.guess_role("交易資料庫", "", "", "") == "DB"
    assert ss.guess_role("", "", "Exadata X9M", "") == "DB"
    assert ss.guess_role("", "", "", "Oracle Linux 8") == "DB"
    assert ss.guess_role("Web 前端", "", "", "") == "AP"
    assert ss.guess_role("", "", "", "") != "AP"


def test_不完整的原因要拆開講_但那是說明不是分類(conn):
    """「不完整」只有一個分類；為什麼不完整拆成兩個數字放說明裡。

    這樣既不會把「沒納管」講成「沒資料」，又保留了「還有一條路沒走」的資訊。
    """
    _hw(conn, "HW-1", ip="10.9.0.1", asset_purpose="交易資料庫")
    _hw(conn, "HW-2", ip="10.9.0.2", asset_purpose="Web 前端")
    _hw(conn, "HW-3", ip="10.9.0.3", asset_purpose="")       # 沒納管
    _hw(conn, "HW-4", ip="10.9.0.4", asset_purpose="")       # 納管過但看不出來
    conn.execute("INSERT INTO onboard_audit (target_ip, trigger, ok) VALUES ('10.9.0.4','manual',1)")
    conn.commit()
    cov = ss.role_coverage(conn)
    assert cov["total"] == 4
    assert cov["roles"][ss.ROLE_AP] == 1
    assert cov["roles"][ss.ROLE_DB] == 1
    assert cov["incomplete"] == 2, "不完整就是一類，不再分成兩個標籤"
    assert cov["incomplete_not_onboarded"] == 1
    assert cov["incomplete_onboarded"] == 1
    assert cov["classified_pct"] == 50.0
    assert ss.ROLE_INCOMPLETE in cov["role_order"]


# ---------- 系統統計 ----------

def test_前N大排序而且要回總系統數(conn):
    _sys(conn, "N-107", "技術中台")
    _sys(conn, "N-001", "大州證券系統")
    for i in range(3):
        _hw(conn, f"A{i}", api_id="N-107")
    _hw(conn, "B0", api_id="N-001")
    conn.commit()
    out = ss.by_system(conn, limit=1)
    assert [s["api_id"] for s in out["systems"]] == ["N-107"]
    assert out["system_count"] == 2, "只回前 N 筆，但要讓畫面知道總共幾個系統"
    assert out["shown"] == 1


def test_對不上業務系統的台數要看得見(conn):
    """使用者要知道這張表涵蓋多少、漏了多少。靜默丟掉會讓合計對不起來。"""
    _sys(conn, "N-107", "技術中台")
    _hw(conn, "A0", api_id="N-107")
    _hw(conn, "X0", api_id=None)
    _hw(conn, "X1", api_id="沒有這個代碼")
    conn.commit()
    out = ss.by_system(conn, limit=None)
    assert out["mapped"] == 1
    assert out["unmapped"] == 2
    assert out["mapped"] + out["unmapped"] == out["total_assets"]


def test_環境別沒填要歸未填不是消失(conn):
    _sys(conn, "N-1", "S")
    _hw(conn, "A", api_id="N-1", env="正式")
    _hw(conn, "B", api_id="N-1", env="")
    conn.commit()
    out = ss.by_system(conn, limit=None)
    s = out["systems"][0]
    assert s["by_env"] == {"正式": 1, "未填": 1}
    assert sum(s["by_env"].values()) == s["total"], "環境別加總必須等於總數"


# ---------- 下鑽 ----------

def test_下鑽回機房與環境交叉(conn):
    _sys(conn, "N-107", "技術中台")
    _hw(conn, "A", api_id="N-107", loc="02_內湖機房", env="正式")
    _hw(conn, "B", api_id="N-107", loc="內湖機房", env="測試")
    _hw(conn, "C", api_id="N-107", loc="01_板橋機房", env="正式")
    conn.commit()
    d = ss.drilldown(conn, "N-107")
    assert d["total"] == 3
    locs = {x["location"]: x for x in d["locations"]}
    assert locs["內湖機房"]["total"] == 2, "兩種寫法要併成同一個機房"
    assert locs["內湖機房"]["by_env"] == {"正式": 1, "測試": 1}
    assert locs["板橋機房"]["total"] == 1


def test_下鑽的角色要標明依據(conn):
    _sys(conn, "N-107", "技術中台")
    _hw(conn, "A", api_id="N-107", ip="10.9.1.1", asset_purpose="交易資料庫")
    _hw(conn, "B", api_id="N-107", ip="10.9.1.2", asset_purpose="")
    conn.commit()
    d = ss.drilldown(conn, "N-107")
    assert d["roles"][ss.ROLE_DB] == 1
    # N-107 是業務系統 → 判不出來的那台落到 AP（使用者 2026-09-10：
    # 「除了 DB 以外都是 AP 主機」）
    assert d["roles"][ss.ROLE_AP] == 1
    assert "證據" in d["role_basis"] or "推論" in d["role_basis"],         "沒標明依據，看的人會當成已經分好的事實"


def test_不存在的系統代碼要明講(conn):
    with pytest.raises(ValueError, match="沒有這個系統代碼"):
        ss.drilldown(conn, "不存在")


# ---------- 基礎設施 / AP 系統分類（2026-09-10）----------

def test_前綴決定基礎還是AP():
    """221 實測：I- 全是基礎設施（Cisco／工作站／FortiGate／EMC Storage…）。"""
    assert ss.system_kind("I-010") == ss.KIND_INFRA
    assert ss.system_kind("i-003") == ss.KIND_INFRA
    assert ss.system_kind("N-107") == ss.KIND_AP
    assert ss.system_kind("") == ss.KIND_AP
    assert ss.system_kind(None) == ss.KIND_AP


def test_人工點名的例外要優先於前綴():
    """N-207 虛擬化平台掛 N- 但實際是基礎設施（使用者 2026-09-10 指定）。

    這是**使用者點名的**，不是程式猜的——猜的話猜錯不會有人發現。
    """
    assert ss.system_kind("N-207") == ss.KIND_INFRA
    assert "N-207" in ss.kind_overrides(), "例外清單要能被畫面拿到，人才知道它為什麼在那裡"


def test_只回某一類的系統(conn):
    _sys(conn, "I-010", "Cisco")
    _sys(conn, "N-107", "技術中台")
    _hw(conn, "A", api_id="I-010")
    _hw(conn, "B", api_id="N-107")
    conn.commit()
    infra = ss.by_system(conn, limit=None, kind=ss.KIND_INFRA)
    ap = ss.by_system(conn, limit=None, kind=ss.KIND_AP)
    assert [s["api_id"] for s in infra["systems"]] == ["I-010"]
    assert [s["api_id"] for s in ap["systems"]] == ["N-107"]


def test_切分頁時對不上的台數不可以跟著跳動(conn):
    """切到基礎設施分頁時「對不上 N 台」突然暴增，看的人會以為資料壞了。

    所以 unmapped 用**全庫口徑**算，不隨分頁篩選變動。
    """
    _sys(conn, "I-010", "Cisco")
    _sys(conn, "N-107", "技術中台")
    _hw(conn, "A", api_id="I-010")
    _hw(conn, "B", api_id="N-107")
    _hw(conn, "X", api_id=None)
    conn.commit()
    assert ss.by_system(conn, limit=None, kind=ss.KIND_INFRA)["unmapped"] == 1
    assert ss.by_system(conn, limit=None, kind=ss.KIND_AP)["unmapped"] == 1
    assert ss.by_system(conn, limit=None)["unmapped"] == 1


# ---------- 環境合併（2026-09-10）----------

def test_UAT與DEV要併進測試():
    for v in ("使用者測試(UAT)", "UAT", "開發環境(DEV)", "DEV", "OA"):
        assert ss.normalize_env(v) == "測試", v
    assert ss.normalize_env("正式") == "正式"
    assert ss.normalize_env("備援") == "備援"
    assert ss.normalize_env("") == "未填"


def test_合併之後環境欄只剩四種(conn):
    _sys(conn, "N-1", "S")
    for i, env in enumerate(("正式", "使用者測試(UAT)", "開發環境(DEV)", "測試", "備援", "")):
        _hw(conn, f"H{i}", api_id="N-1", env=env)
    conn.commit()
    out = ss.by_system(conn, limit=None)
    assert set(out["envs"]) == {"正式", "測試", "備援", "未填"}
    assert out["systems"][0]["by_env"]["測試"] == 3, "UAT+DEV+測試 要合起來算"


# ---------- 點一格數字下鑽（2026-09-10）----------

def test_點某一格只算那個環境(conn):
    """使用者：「譬如我點 207，這 207 機房各占多少」。"""
    _sys(conn, "N-1", "S")
    _hw(conn, "A", api_id="N-1", env="正式", loc="01_板橋機房")
    _hw(conn, "B", api_id="N-1", env="正式", loc="02_內湖機房")
    _hw(conn, "C", api_id="N-1", env="開發環境(DEV)", loc="01_板橋機房")
    conn.commit()

    d = ss.drilldown(conn, "N-1", env="正式")
    assert d["total"] == 2
    assert {x["location"]: x["total"] for x in d["locations"]} == {"板橋機房": 1, "內湖機房": 1}

    # DEV 會被併進「測試」，所以要用「測試」查得到它
    d2 = ss.drilldown(conn, "N-1", env="測試")
    assert d2["total"] == 1
    assert d2["locations"][0]["location"] == "板橋機房"

    # 不帶 env 就是全部
    assert ss.drilldown(conn, "N-1")["total"] == 3


def test_APDB要跟機房交叉_而且未分類不可以少一列(conn):
    """使用者 2026-09-10 要的形狀：

            板橋  內湖  合計
        AP  105   70    175
        DB  20    10    30

    每一列都要在（即使是 0），而且**合計必須等於系統總台數**——少一列使用者
    一加就發現對不起來，然後整張表都不能信了。
    """
    _sys(conn, "N-1", "S")
    _hw(conn, "A", api_id="N-1", loc="01_板橋機房", asset_purpose="Web 前端")
    _hw(conn, "B", api_id="N-1", loc="01_板橋機房", asset_purpose="交易資料庫")
    _hw(conn, "C", api_id="N-1", loc="02_內湖機房", asset_purpose="交易資料庫")
    _hw(conn, "D", api_id="N-1", loc="02_內湖機房", asset_purpose="")
    conn.commit()

    d = ss.drilldown(conn, "N-1")
    m = {r["role"]: r for r in d["role_matrix"]}
    assert [r["role"] for r in d["role_matrix"]] == list(ss.ROLE_ORDER), \
        "每一列都要在（即使是 0），順序固定"
    # A 是 Web 前端、D 沒填但屬於業務系統 → 兩台都算 AP
    assert m[ss.ROLE_AP]["by_location"] == {"板橋機房": 1, "內湖機房": 1}
    assert m[ss.ROLE_DB]["by_location"] == {"板橋機房": 1, "內湖機房": 1}
    # 交叉表的合計要等於系統總台數，不然使用者一加就發現對不起來
    assert sum(r["total"] for r in d["role_matrix"]) == d["total"] == 4


# ---------- 基礎設施再分群（2026-09-10）----------

def test_SAN_switch要歸Storage不是網路設備():
    """使用者特別交代的一條，因為名字裡有 switch，先比網路設備就會歸錯。

    所以規則順序是 Storage 在前——這條測試守的是**順序**，不是關鍵字。
    """
    assert ss.infra_group("I-002", "IBM San Switch") == ss.GROUP_STORAGE


def test_HMC要歸IBM_system():
    """使用者 2026-09-10 先說「其他大型系統」，看到分類結果後改名為「IBM system」。"""
    assert ss.infra_group("I-007", "HMC") == ss.GROUP_IBM


def test_網路設備認得出常見的():
    for name in ("Cisco Catalyst C2960X-48TD-L", "Fortinet FortiGate 101E",
                 "網路管理控制器", "Lan Console", "網管伺服器"):
        assert ss.infra_group("I-000", name) == ss.GROUP_NETWORK, name


def test_Storage認得出常見的():
    for name in ("EMC Storage", "NetApp", "儲存設備"):
        assert ss.infra_group("I-000", name) == ss.GROUP_STORAGE, name


def test_使用者點名的六群要照他說的分():
    """全部由使用者逐項指定（2026-09-10 分三次講完）。

    這裡用 **api_id** 斷言而不是名稱：名稱會被改，代碼不會，而他點名的是那幾台。
    """
    assert ss.infra_group("I-003", "工作站") == ss.GROUP_OTHER
    assert ss.infra_group("I-100", "SSIS 測試機") == ss.GROUP_OTHER
    assert ss.infra_group("I-021", "印表機周邊設備") == ss.GROUP_OTHER
    assert ss.infra_group("N-207", "虛擬化平台") == ss.GROUP_VMWARE
    assert ss.infra_group("I-004", "軟體測試環境") == ss.GROUP_SOFTWARE
    assert ss.infra_group("I-005", "軟體存放區") == ss.GROUP_SOFTWARE


def test_沒見過的系統要落在未分群_不可以硬塞():
    """安全網：以後新增的系統對不上任何規則時，要落在未分群並顯示出來等人點名。

    硬塞進最像的那一群，塞錯了沒人會發現，數字會一路錯下去。
    """
    for name in ("某某新設備", "???", "未來才會買的東西"):
        assert ss.infra_group("I-999", name) == ss.GROUP_UNGROUPED, name


def test_分群順序就是使用者給的編號():
    """他是用編號講的（1 網路設備、2 Storage…6 其他），畫面要照這個順序畫。

    未分群排最後，是安全網不是第七類。
    """
    assert list(ss.INFRA_GROUP_ORDER) == [
        ss.GROUP_NETWORK, ss.GROUP_STORAGE, ss.GROUP_VMWARE,
        ss.GROUP_IBM, ss.GROUP_SOFTWARE, ss.GROUP_OTHER, ss.GROUP_UNGROUPED]


# ---------- 交叉表某一格是哪幾台（2026-09-10）----------

def test_點一格要列得出是哪幾台(conn):
    """使用者：「板橋機房 DB 有 18 台，點進去要知道是哪 18 台」。"""
    _sys(conn, "N-1", "S")
    _hw(conn, "A", api_id="N-1", loc="01_板橋機房", env="正式", asset_purpose="交易資料庫",
        hostname="H-A")
    _hw(conn, "B", api_id="N-1", loc="01_板橋機房", env="正式", asset_purpose="Web 前端",
        hostname="H-B")
    _hw(conn, "C", api_id="N-1", loc="02_內湖機房", env="正式", asset_purpose="交易資料庫",
        hostname="H-C")
    conn.commit()

    items = ss.assets_in_cell(conn, "N-1", env="正式", location="板橋機房", role=ss.ROLE_DB)
    assert [x["hostname"] for x in items] == ["H-A"]
    assert items[0]["role"] == ss.ROLE_DB
    # 要能回答「為什麼這台算 DB」，不能只給結論
    assert items[0]["purpose"] == "交易資料庫"


def test_不篩就是整個系統(conn):
    _sys(conn, "N-1", "S")
    _hw(conn, "A", api_id="N-1", loc="01_板橋機房")
    _hw(conn, "B", api_id="N-1", loc="02_內湖機房")
    conn.commit()
    assert len(ss.assets_in_cell(conn, "N-1")) == 2


# ---------- 「除了 DB 以外都是 AP」與 LOG（2026-09-10）----------

def test_業務系統判不出來就當AP():
    """使用者 2026-09-10：「除了 DB 以外都是 AP 主機」。

    母體是業務系統時這個假設站得住——他截圖那批「大州經紀正式板一XX主機」
    用途說明寫的是業務名稱，看不出技術角色，但它們確實是應用主機。
    """
    assert ss.guess_role("大州經紀正式板一Instnet FIX主機", "", "", "",
                         default_ap=True) == ss.ROLE_AP
    # 基礎設施不套用——交換器不是 AP 主機
    assert ss.guess_role("某某設備", "", "", "", default_ap=False) == ss.ROLE_INCOMPLETE


def test_有依據的分類不可以被AP蓋掉():
    """**只在最後一步才套用**。

    把 OpenShift 節點標成 AP 之後，去問「這台 AP 是哪個系統的」會問不出答案。
    設備同理：交換器不是應用主機。
    """
    assert ss.guess_role("OCP Worker (paas-bq1)", "", "", "", default_ap=True) == ss.ROLE_PLATFORM
    assert ss.guess_role("", "", "網路設備", "", default_ap=True) == ss.ROLE_DEVICE
    assert ss.guess_role("交易資料庫", "", "", "", default_ap=True) == ss.ROLE_DB


def test_LOG機要獨立一類():
    """使用者 2026-09-10：「還有 LOG 機要獨立分出來」。"""
    for v in ("syslog server", "Splunk Indexer", "LOG 主機", "Graylog", "日誌伺服器"):
        assert ss.guess_role(v, "", "", "", default_ap=True) == ss.ROLE_LOG, v


def test_LOG要排在AP前面():
    """log server 上常常也跑 web 介面（Kibana／Graylog），順序反了會被標成 AP。"""
    assert ss.guess_role("Kibana web 介面", "", "", "", default_ap=True) == ss.ROLE_LOG


def test_LOG在角色順序表裡():
    assert ss.ROLE_LOG in ss.ROLE_ORDER


# ---------- 反查：知道機器，想知道它屬於哪個系統（2026-09-10）----------

def test_用主機名查得到它屬於哪個系統(conn):
    """使用者 2026-09-10：「SEC01 是哪個 APID 我怎麼找不到」。

    分佈統計本來只能由上往下鑽（系統 → 機房 → 哪幾台），
    知道機器卻找不到系統——這是下鑽的反方向。
    """
    _sys(conn, "N-003", "某某系統", dept="證券資訊部")
    _hw(conn, "HW-1", api_id="N-003", hostname="sec01", ip="10.9.9.1",
        loc="01_板橋機房", env="正式")
    conn.commit()
    got = ss.lookup_host(conn, "sec01")
    assert len(got) == 1
    assert got[0]["api_id"] == "N-003"
    assert got[0]["system_name"] == "某某系統"
    assert got[0]["ap_department"] == "證券資訊部"
    assert got[0]["location"] == "板橋機房"


def test_主機名查詢不分大小寫(conn):
    """真實資料裡是小寫 `sec01`，人會打 `SEC01`。

    查不到東西時，這是最常見也最沒必要的原因。
    """
    _sys(conn, "N-003", "某某系統")
    _hw(conn, "HW-1", api_id="N-003", hostname="sec01", ip="10.9.9.1")
    conn.commit()
    assert len(ss.lookup_host(conn, "SEC01")) == 1
    assert len(ss.lookup_host(conn, "Sec01")) == 1


def test_用IP也查得到(conn):
    _sys(conn, "N-003", "某某系統")
    _hw(conn, "HW-1", api_id="N-003", hostname="sec01", ip="10.9.9.1")
    conn.commit()
    assert len(ss.lookup_host(conn, "10.9.9.1")) == 1


def test_沒填系統的機器也要找得到_而且要看得出是沒填(conn):
    """「沒填 api_id」跟「對照表缺這個代碼」是兩件事，要補的地方不同。

    直接不回傳的話，使用者會以為這台機器不存在。
    """
    _hw(conn, "HW-X", api_id=None, hostname="orphan01", ip="10.9.9.9")
    conn.commit()
    got = ss.lookup_host(conn, "orphan01")
    assert len(got) == 1
    assert got[0]["api_id"] is None
    assert got[0]["system_name"] is None


def test_查空字串不要回整個資料庫(conn):
    _hw(conn, "HW-1", hostname="a", ip="10.9.9.1")
    conn.commit()
    assert ss.lookup_host(conn, "") == []
    assert ss.lookup_host(conn, "   ") == []


# ---------- 系統類別與排序（2026-09-10）----------
# 來源是使用者給的 APID 分級表（匯入到 business_system.sys_class），不是推的。
# 一開始用「正式機的可用性」推過一版，使用者隨即指出分級表上就有寫——有權威來源就用來源。

def _set_class(c, api_id, label):
    c.execute("UPDATE business_system SET sys_class = ? WHERE api_id = ?", (label, api_id))


def test_分級表原字串要認得出第幾類與核心():
    assert ss.class_from_label("S1-第一類系統軟體（核心）") == ("第一類", True)
    assert ss.class_from_label("S1-第一類系統軟體") == ("第一類", False)
    assert ss.class_from_label("S2-第二類系統軟體") == ("第二類", False)
    assert ss.class_from_label("S3-第三類系統軟體") == ("第三類", False)


def test_認不出來的類別字串不可以猜():
    for v in (None, "", "   ", "特殊等級", "X9"):
        assert ss.class_from_label(v) == (ss.CLASS_UNRATED, False), v


def test_系統類別照分級表_不在表上的是未分級(conn):
    _sys(conn, "N-1", "一類核心")
    _sys(conn, "N-2", "不在表上")
    _hw(conn, "A", api_id="N-1")
    _hw(conn, "B", api_id="N-2")
    _set_class(conn, "N-1", "S1-第一類系統軟體（核心）")
    conn.commit()
    got = {s["api_id"]: s for s in ss.by_system(conn, limit=None)["systems"]}
    assert got["N-1"]["sys_class"] == "第一類"
    assert got["N-1"]["sys_class_core"] is True
    assert got["N-2"]["sys_class"] == ss.CLASS_UNRATED
    assert got["N-2"]["sys_class_core"] is False


def test_排序是第一類先_同類裡正式機多的先(conn):
    """使用者 2026-09-10：「排序用第一類先排，再排序正式機最多的」。

    ⚠️ 排序要在後端做：清單只回前 10 大，前端才排的話那 10 個本身就挑錯了。
    所以這條測試直接測 limit 之後的結果。
    """
    _sys(conn, "N-A", "二類但台數最多")
    _sys(conn, "N-B", "一類正式少")
    _sys(conn, "N-C", "一類正式多")
    for i in range(10):
        _hw(conn, f"A{i}", api_id="N-A", env="正式")
    _hw(conn, "B0", api_id="N-B", env="正式")
    for i in range(20):
        _hw(conn, f"B{i + 1}t", api_id="N-B", env="測試")      # 總數多但正式少
    for i in range(3):
        _hw(conn, f"C{i}", api_id="N-C", env="正式")
    _set_class(conn, "N-A", "S2-第二類系統軟體")
    _set_class(conn, "N-B", "S1-第一類系統軟體")
    _set_class(conn, "N-C", "S1-第一類系統軟體（核心）")
    conn.commit()
    out = ss.by_system(conn, limit=2)
    assert [s["api_id"] for s in out["systems"]] == ["N-C", "N-B"],         "第一類要排在第二類前面；同是第一類時，正式機多的在前（不看總台數）"
    assert out["class_order"] == list(ss.CLASS_ORDER)
