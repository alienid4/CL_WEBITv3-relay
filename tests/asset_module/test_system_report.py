"""系統組月報：三張表 + 每個數字都要能追。

使用者 2026-08-21 的兩個要求，這支測試守的就是這兩件：
1.「以後我就 COPY 畫面，不用再自己統計」——數字要對
2.「你每個數字我都要可以追」——格子上寫幾台，點進去就要看到幾台

第 2 點是這裡最重要的斷言：**加總與下鑽必須來自同一份計算**。
兩邊各寫一份查詢的話，遲早出現「格子寫 62、點進去列 58」，那時候沒人知道哪個
才對，整張報表就廢了。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import eos as eos_module  # noqa: E402
import normalize  # noqa: E402
import system_report  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


# ===== IBM i 版號（2026-08-21 使用者做月報時抓到的真實 bug）=====
#
# 泛用的版本正則是 `(\d+(?:\.\d+)*)\b`，而 V7R3 的 7 後面接著 R（也是單字字元），
# `\b` 不成立 → 跳過主版本、抓到結尾的 3 →「V7R3」被讀成「IBM i 3」。
# 連帶在 EOS 對照表查無此物，42 台 AS/400 全被標成「需確認」。

@pytest.mark.parametrize("raw,expect", [
    ("V7R3", "IBM i 7.3"),
    ("V7R5", "IBM i 7.5"),
    ("V7R1", "IBM i 7.1"),
    ("V5R4", "IBM i 5.4"),
])
def test_IBM_i_版號要讀成主版本點修訂版(raw, expect):
    assert normalize.normalize_os(raw)["canonical"] == expect


@pytest.mark.parametrize("raw", ["V10R3", "V8R8.6.0", "V9R2"])
def test_不是IBM_i的裸版號不可硬歸成IBM_i(raw):
    """V10R3 實際上是 HMC（硬體管理台）的版本，不是作業系統。
    IBM i 至今最高 7.5，沒有 V8 以上——認不出來就落到未分類讓人工判斷，
    不要為了讓數字好看硬猜（使用者月報因此多算 6 台）。"""
    info = normalize.normalize_os(raw)
    assert info["product"] != "IBM i"
    assert info["matched"] is False
    assert info["canonical"] == raw          # 原值原樣留著，進待處理清單


# ===== 分類規則 =====

def test_叢集用途依名稱判定():
    assert system_report.cluster_service("BQ_PROD_LOG_Cluster") == "Log服務"
    assert system_report.cluster_service("BQ_PROD_A_vSan_Cluster") == "交易服務"


def test_叢集環境依名稱判定():
    assert system_report.cluster_environment("NH_PROD_UAT_Cluster01") == "測試"
    assert system_report.cluster_environment("DN_PROD_Cluster01") == "正式"


def test_機房優先看叢集名稱前綴():
    """設定檔會進公開 relay，所以真實 vCenter IP → 機房的對照不寫在裡面。
    叢集命名本身帶著答案（BQ_＝板橋），前綴是代號，資訊量低得多。"""
    assert system_report.vcenter_location(None, cluster="BQ_PROD_A_vSan_Cluster") == "板橋"
    assert system_report.vcenter_location(None, cluster="NH_PROD_Cluster01") == "內湖"
    assert system_report.vcenter_location(None, cluster="DN_UAT_Cluster01") == "敦南"


def test_對不到機房要回未對應而不是猜一個(tmp_path):
    """對不到代表少了一筆對照，那要有人去補，不是讓它默默歸進某個機房——
    那會讓月報的機房分佈悄悄失真。"""
    assert system_report.vcenter_location("192.0.2.1", cluster="VCF-WLD02-UAT-CL01-DC") == "未對應"


def test_vCenter對照存資料庫不進版控(tmp_path):
    """真實 IP 與內部拓撲不外送：對照表存 app_settings，設定檔只留結構不留值。"""
    import json as _json

    from db import set_setting
    conn = _conn(tmp_path)
    try:
        assert system_report.vcenter_location("10.0.0.9", conn) == "未對應"
        set_setting(conn, system_report.VCENTER_LOCATION_KEY,
                    _json.dumps({"10.0.0.9": "某機房"}))
        assert system_report.vcenter_location("10.0.0.9", conn) == "某機房"
    finally:
        conn.close()


def test_Windows拆Server與Client_Win11算Client():
    """使用者 2026-08-21 明確指定 Win11 算 Client。"""
    assert system_report._report_platform("Windows", "Windows Server 2019") == "Windows Server"
    assert system_report._report_platform("Windows", "Windows 11") == "Windows Client"
    assert system_report._report_platform("Windows", "Windows 10") == "Windows Client"
    # 判不出來的不猜，獨立成一列——那正是要人去補資料的清單
    assert system_report._report_platform("Windows", None) == "Windows 未明版本"


def test_網路儲存設備不列入本報告():
    """使用者 2026-08-21：網路放網路組統計，這份報告只要圖上那幾類。"""
    for bucket in ("網路設備", "儲存設備", "管理韌體(BMC)", "未知"):
        assert system_report._report_platform(bucket, None) is None


# ===== 核心：數字可追 =====

def _seed(conn):
    rows = [
        ("HW-1", "win-a", "10.0.0.1", "Windows Server 2019", None, "在用"),
        ("HW-2", "win-b", "10.0.0.2", "Windows Server 2019", None, "在用"),
        ("HW-3", "cli-a", "10.0.0.3", "Windows 11", None, "在用"),
        ("HW-4", "as400", "10.0.0.4", "V7R3", None, "在用"),
        ("HW-5", "sw-a", "10.0.0.5", None, "Cisco Catalyst 9300", "在用"),
        ("HW-6", "old", "10.0.0.6", "Windows Server 2019", None, "報廢"),
    ]
    for serial, host, ip, os_, model, status in rows:
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip,
                           os=os_, device_model=model, asset_status=status)
    conn.commit()


def test_表1每一格的數字都等於點進去的筆數(tmp_path):
    """這是整份報表能不能用的關鍵。使用者原話：「你每個數字我都要可以追」。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        t1 = system_report.platform_lifecycle(conn)
        for row in t1["rows"]:
            plat = row["platform"]
            # 總量
            assert len(system_report.drill_platform(conn, platform=plat)) == row["total"], plat
            # 四態各自
            for st in system_report.STATUS_ORDER:
                got = system_report.drill_platform(conn, platform=plat, status=st)
                assert len(got) == row[st], f"{plat} / {st}"
    finally:
        conn.close()


def test_排除與退役也要追得到(tmp_path):
    """排除的不是刪掉——總量對不起來時要講得出少的那些去哪了。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        t1 = system_report.platform_lifecycle(conn)
        for ex in t1["excluded"]:
            got = system_report.drill_platform(conn, bucket=ex["platform"])
            assert len(got) == ex["count"], ex["platform"]
        assert len(system_report.drill_platform(conn, retired=True)) == t1["retired_excluded"]
    finally:
        conn.close()


def test_每一列都講得出為什麼被分到這一類(tmp_path):
    """可追不只是「列得出是哪幾台」，還要「講得出為什麼是這一台」——
    口徑對不上時，看的人要能自己判斷是哪一步分錯。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        item = system_report.drill_platform(conn, platform="IBM i")[0]
        assert item["os_raw"] == "V7R3"
        assert item["os_canonical"] == "IBM i 7.3"
        assert "V7R3" in item["reason"] and "IBM i 7.3" in item["reason"]
    finally:
        conn.close()


def test_合計等於各列相加且排除數講得出來(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        t1 = system_report.platform_lifecycle(conn)
        assert t1["total"] == sum(r["total"] for r in t1["rows"])
        assert t1["excluded_total"] == sum(x["count"] for x in t1["excluded"])
        # 有效資產 = 列出的 + 排除的；再加退役 = 全部
        total_rows = conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
        assert t1["total"] + t1["excluded_total"] + t1["retired_excluded"] == total_rows
    finally:
        conn.close()


# ===== 備註與快照 =====

def test_備註存了跨月還在(tmp_path):
    """使用者 2026-08-21：像「7/10 為颱風假、無開單」這種話寫一次要留著。"""
    conn = _conn(tmp_path)
    try:
        system_report.set_note(conn, "platform:Windows Server", "2012/R2 已 EOS", "admin")
        assert system_report.get_notes(conn)["platform:Windows Server"] == "2012/R2 已 EOS"
        # 沒指定月份的備註，查任何一個月都要帶出來
        assert system_report.get_notes(conn, "2026-09")["platform:Windows Server"]
    finally:
        conn.close()


def test_同月快照重存是覆蓋不是累積(tmp_path):
    """同一個月本來就只該有一份定稿。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        system_report.save_snapshot(conn, "2026-08", "admin")
        system_report.save_snapshot(conn, "2026-08", "admin")
        assert len(system_report.list_snapshots(conn)) == 1
        assert system_report.get_snapshot(conn, "2026-08")["platform_lifecycle"]["total"] > 0
        assert system_report.get_snapshot(conn, "2026-07") is None
    finally:
        conn.close()


def test_報告一定帶資料新鮮度(tmp_path):
    """這一頁最大的風險不是算錯，是算得很精確但底層是三週前的快照，
    而看報告的人不知道。"""
    conn = _conn(tmp_path)
    try:
        r = system_report.build(conn)
        assert "rvtools_note" in r["meta"]
        # 沒有匯出日期時要明講「認不出」，不可以裝作資料是新的
        assert "認不出" in r["meta"]["rvtools_note"]
    finally:
        conn.close()


# ===== R1（2026-08-21 backlog，最終定案）：表3實體主機改成固定名單 =====
# 一開始走 classify_assets() 自動偵測，結果混進 LAN Console/HMC/測試環境
# 重複列，抓半天還是要一台一台核對。使用者最後直接拍板：「我只要這幾台的
# 資訊，總共10台主機就可以」，改成固定清單（見 PHYSICAL_HOSTS_KEY）。

def _seed_physical_hosts_cfg(conn, entries):
    from db import set_setting
    set_setting(conn, system_report.PHYSICAL_HOSTS_KEY, json.dumps(entries, ensure_ascii=False))


def test_表3固定名單只列出設定裡的機器(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-AIX", hostname="sec01",
                           ip="10.99.0.12", os="AIX 7.2", asset_status="使用中",
                           environment="正式")
        # 資料庫還有別的AIX/IBM i主機，但不在清單裡就不該出現
        db.insert_hardware(conn, asset_serial="HW-OTHER", hostname="other-aix",
                           ip="10.99.0.99", os="AIX 7.2", asset_status="使用中",
                           environment="正式")
        _seed_physical_hosts_cfg(conn, [
            {"location": "板橋", "environment": "正式", "service": "好麥證券經紀帳務",
             "ip": "10.99.0.12"},
        ])
        conn.commit()

        rows = system_report.physical_hosts_report(conn)
        assert len(rows) == 1
        assert rows[0]["hostname"] == "sec01"
        assert rows[0]["product"] == "AIX"
        assert rows[0]["os_canonical"] == "AIX 7.2"
        assert rows[0]["found"] is True
    finally:
        conn.close()


def test_表3固定名單裡IP查無登記要講出來不能悄悄消失(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_physical_hosts_cfg(conn, [
            {"location": "板橋", "environment": "正式", "service": "財管", "ip": "10.99.0.34"},
        ])
        conn.commit()

        rows = system_report.physical_hosts_report(conn)
        assert len(rows) == 1
        assert rows[0]["found"] is False
        assert rows[0]["hostname"] is None
        assert rows[0]["service"] == "財管"
    finally:
        conn.close()


def test_表3固定名單標出IBMi版本與EOS(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-AS400", hostname="futas01",
                           ip="10.99.0.18", os="V7R3", asset_status="使用中",
                           environment="正式")
        _seed_physical_hosts_cfg(conn, [
            {"location": "板橋", "environment": "正式", "service": "期貨", "ip": "10.99.0.18"},
        ])
        conn.commit()

        rows = system_report.physical_hosts_report(conn)
        assert rows[0]["product"] == "IBM i"
        assert rows[0]["os_canonical"] == "IBM i 7.3"
        # eos_data 認得「IBM i」這個產品，只是官方沒公布各版本日期——這跟系統
        # 根本不認識的產品是兩回事，2026-08-21 使用者要求分開標「尚未公布」。
        assert rows[0]["eos_status"] == "尚未公布"
        assert rows[0]["eos_date"] is None
    finally:
        conn.close()


# ===== R2（2026-08-21 backlog）：混版拆兩行各自標EOS =====

def _seed_vhost(conn, host, cluster, version, vcenter="10.9.0.1"):
    payload = {"Host": host, "Cluster": cluster, "Datacenter": "DC1",
               "ESX Version": version, "VI SDK Server": vcenter}
    conn.execute(
        "INSERT INTO source_record (source, source_key, payload) VALUES (?,?,?)",
        ("vcenter_extra:vHost", host, json.dumps(payload)),
    )


def test_表3混版拆成兩列各自算臺數與EOS(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_vhost(conn, "esxi-01", "PROD_Cluster", "VMware ESXi 7.0.3 build-21930508")
        _seed_vhost(conn, "esxi-02", "PROD_Cluster", "VMware ESXi 7.0.3 build-22348816")
        _seed_vhost(conn, "esxi-03", "PROD_Cluster", "VMware ESXi 8.0.3 build-24280767")
        conn.commit()

        env = system_report.virtualization_env(conn)
        rows = [c for c in env["clusters"] if c["cluster"] == "PROD_Cluster"]
        assert len(rows) == 2, "混版要拆成兩列，不是擠成一行"
        by_version = {r["version"]: r for r in rows}
        assert by_version["VMware ESXi 7.0.3"]["count"] == 2
        assert by_version["VMware ESXi 8.0.3"]["count"] == 1
        assert all(r["mixed_version"] for r in rows)
    finally:
        conn.close()


def test_表3版本欄的EOS判定(tmp_path):
    """7.0 官方已公告 EOS（2025-10-02，早於本測試的當下時間）；8.0 是已知產品
    但官方沒有公開可查的 EOS 日期（eos_data 刻意留白，不採第三方猜測值）——
    要標「尚未公布」，不是「支援中」（沒證據），也不是「需確認」（那是給
    EOS表裡根本沒收錄的產品用的，2026-08-21 使用者要求兩者分開）。"""
    conn = _conn(tmp_path)
    try:
        _seed_vhost(conn, "esxi-01", "OLD_Cluster", "VMware ESXi 7.0.3 build-21930508")
        _seed_vhost(conn, "esxi-02", "NEW_Cluster", "VMware ESXi 8.0.3 build-24280767")
        conn.commit()

        env = system_report.virtualization_env(conn)
        by_cluster = {c["cluster"]: c for c in env["clusters"]}
        assert by_cluster["OLD_Cluster"]["eos_status"] == "已EOS"
        assert by_cluster["OLD_Cluster"]["eos_date"] == "2025-10-02"
        assert by_cluster["NEW_Cluster"]["eos_status"] == "尚未公布"
        assert by_cluster["NEW_Cluster"]["eos_date"] is None
    finally:
        conn.close()


def test_EOS表完全查無此產品才顯示需確認(tmp_path):
    """跟「尚未公布」（查到產品、官方沒給日期）是兩種完全不同的情況——
    這裡驗證真的認不出的產品仍然是「需確認」，不會被誤標成尚未公布。"""
    conn = _conn(tmp_path)
    try:
        _seed_vhost(conn, "esxi-01", "GHOST_Cluster", "VMware ESXi 99.9.9 build-00000000")
        conn.commit()

        env = system_report.virtualization_env(conn)
        row = next(c for c in env["clusters"] if c["cluster"] == "GHOST_Cluster")
        assert row["eos_status"] == "需確認"
        assert row["eos_date"] is None
    finally:
        conn.close()


def test_表3每列的臺數等於下鑽帶version的筆數(tmp_path):
    """可追：格子上寫幾台，點進去（帶version）就要看到幾台。"""
    conn = _conn(tmp_path)
    try:
        _seed_vhost(conn, "esxi-01", "PROD_Cluster", "VMware ESXi 7.0.3 build-21930508")
        _seed_vhost(conn, "esxi-02", "PROD_Cluster", "VMware ESXi 8.0.3 build-24280767")
        conn.commit()

        env = system_report.virtualization_env(conn)
        for c in env["clusters"]:
            got = system_report.drill_cluster(
                conn, vcenter=c["vcenter"], cluster=c["cluster"], version=c["version"])
            assert len(got) == c["count"], c["version"]
    finally:
        conn.close()


# ===== OS版本明細（2026-08-21 使用者反饋，兩輪拍板） =====
# 第一輪：「只寫這樣子主管不會知道詳細資訊，譬如Red Hat Enterprise Linux 7.9、
# 26台」。第二輪看了實際畫面「真的太多了」，追加兩條規則：
# 1. 只列「一年內EOS」的，其他狀態不用列（這是行動清單，不是全量統計）
# 2. 小版本併進大版本（7.9併進7），不要每個小版本各自一列

def _with_eos_table(monkeypatch, entries):
    """跟 test_eos.py 同一招：直接換掉 eos._os_table，不依賴真實 eos_data
    裡的日期（那些日期會隨時間從「一年內」滑到「已過期」，測試會變成
    看當下日期決定生死的地雷）。"""
    monkeypatch.setattr(eos_module, "_os_table", entries)
    monkeypatch.setattr(eos_module, "_hw_table", [])


def _soon(days=30):
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _seed_versions(conn):
    rows = [
        ("HW-R1", "rhel-a", "10.2.0.1", "Red Hat Enterprise Linux 7.9"),
        ("HW-R2", "rhel-b", "10.2.0.2", "Red Hat Enterprise Linux 7.6"),
        ("HW-R3", "rhel-c", "10.2.0.3", "Red Hat Enterprise Linux 9.4"),
        ("HW-W1", "win-a", "10.2.0.4", "Windows Server 2019"),
    ]
    for serial, host, ip, os_ in rows:
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip,
                           os=os_, asset_status="使用中")
    conn.commit()


def test_版本明細小版本併進大版本(tmp_path, monkeypatch):
    """7.9跟7.6併成同一列「Red Hat Enterprise Linux 7」，不是各自一列。"""
    _with_eos_table(monkeypatch, [
        {"name": "Red Hat Enterprise Linux 7", "eos_date": _soon(), "source_url": "https://x", "note": ""},
    ])
    conn = _conn(tmp_path)
    try:
        _seed_versions(conn)
        rows = system_report.os_version_breakdown(conn)
        by_canonical = {r["os_canonical"]: r for r in rows}
        assert "Red Hat Enterprise Linux 7.9" not in by_canonical
        assert "Red Hat Enterprise Linux 7.6" not in by_canonical
        assert by_canonical["Red Hat Enterprise Linux 7"]["count"] == 2
    finally:
        conn.close()


def test_版本明細只列已EOS與一年內EOS_其他狀態不出現(tmp_path, monkeypatch):
    """第一輪拍板「把一年內EOS的列出來就好，其他的不用」——但使用者看了
    實際畫面反問「但有EOS的嗎」：已經過期的（已EOS）才是最急的，不該被
    濾掉。最終規則：已EOS＋一年內EOS都列，支援中/尚未公布/需確認都不列。"""
    _with_eos_table(monkeypatch, [
        {"name": "Red Hat Enterprise Linux 6", "eos_date": "2020-01-01", "source_url": "https://x", "note": ""},
        {"name": "Red Hat Enterprise Linux 7", "eos_date": _soon(), "source_url": "https://x", "note": ""},
        {"name": "Red Hat Enterprise Linux 9", "eos_date": _soon(900), "source_url": "https://x", "note": ""},
    ])
    conn = _conn(tmp_path)
    try:
        _seed_versions(conn)
        db.insert_hardware(conn, asset_serial="HW-R0", hostname="rhel-old", ip="10.2.0.5",
                           os="Red Hat Enterprise Linux 6.10", asset_status="使用中")
        conn.commit()

        rows = system_report.os_version_breakdown(conn)
        canonicals = {r["os_canonical"] for r in rows}
        assert canonicals == {"Red Hat Enterprise Linux 6", "Red Hat Enterprise Linux 7"}
        by_canonical = {r["os_canonical"]: r for r in rows}
        assert by_canonical["Red Hat Enterprise Linux 6"]["eos_status"] == "已EOS"
        assert by_canonical["Red Hat Enterprise Linux 7"]["eos_status"] == "一年內EOS"
    finally:
        conn.close()


def test_版本明細依台數排序_不是依急迫度(tmp_path, monkeypatch):
    """2026-08-21 使用者從畫面上發現：「已EOS一律排在一年內EOS前面」會把
    衝擊最大的項目埋掉——18個版本裡台數最多的Windows Server 2016(549台，
    一年內EOS)反而被擠到第10名之外。改成單純依台數排，不分急迫度先後。"""
    _with_eos_table(monkeypatch, [
        {"name": "Red Hat Enterprise Linux 6", "eos_date": "2020-01-01", "source_url": "https://x", "note": ""},
        {"name": "Windows Server 2016", "eos_date": _soon(), "source_url": "https://x", "note": ""},
    ])
    conn = _conn(tmp_path)
    try:
        # 已EOS但只有1台
        db.insert_hardware(conn, asset_serial="HW-OLD", hostname="rhel-old", ip="10.2.0.30",
                           os="Red Hat Enterprise Linux 6.10", asset_status="使用中")
        # 一年內EOS但有3台——台數比已EOS那筆多，應該排在前面
        for i in range(3):
            db.insert_hardware(conn, asset_serial=f"HW-W16-{i}", hostname=f"w16-{i}",
                               ip=f"10.2.0.{31+i}", os="Windows Server 2016", asset_status="使用中")
        conn.commit()

        rows = system_report.os_version_breakdown(conn)
        assert rows[0]["os_canonical"] == "Windows Server 2016"
        assert rows[0]["count"] == 3
        assert rows[1]["os_canonical"] == "Red Hat Enterprise Linux 6"
    finally:
        conn.close()


def test_版本明細排除退役與本報告不列入的平台(tmp_path, monkeypatch):
    _with_eos_table(monkeypatch, [
        {"name": "Red Hat Enterprise Linux 7", "eos_date": _soon(), "source_url": "https://x", "note": ""},
    ])
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-OLD", hostname="old", ip="10.2.0.9",
                           os="Red Hat Enterprise Linux 7.9", asset_status="報廢")
        db.insert_hardware(conn, asset_serial="HW-SW", hostname="switch1", ip="10.2.0.10",
                           os=None, device_model="Cisco Nexus 9300")
        conn.commit()

        rows = system_report.os_version_breakdown(conn)
        assert not rows  # 唯一一台是退役的，不該出現在任何一列
    finally:
        conn.close()


def test_版本明細同大版本群組取最急迫的狀態代表(tmp_path, monkeypatch):
    """AIX 的 EOS 是照小版本公告的（AIX 7.1／7.2 可能日期不同），併成
    「AIX 7」一列時，如果群組裡有一台快到期，這一列就該顯示「一年內EOS」，
    不能因為另一台還很久才到期就被平均掉、看不出風險。"""
    _with_eos_table(monkeypatch, [
        {"name": "AIX 7.1", "eos_date": _soon(), "source_url": "https://x", "note": ""},
        {"name": "AIX 7.2", "eos_date": _soon(900), "source_url": "https://x", "note": ""},
    ])
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-A1", hostname="aix-old", ip="10.2.0.20",
                           os="AIX 7.1", asset_status="使用中")
        db.insert_hardware(conn, asset_serial="HW-A2", hostname="aix-new", ip="10.2.0.21",
                           os="AIX 7.2", asset_status="使用中")
        conn.commit()

        rows = system_report.os_version_breakdown(conn)
        row = next(r for r in rows if r["os_canonical"] == "AIX 7")
        assert row["count"] == 2
        assert row["eos_status"] == "一年內EOS"
    finally:
        conn.close()


def test_版本明細每一列的台數等於下鑽帶os_canonical的筆數(tmp_path, monkeypatch):
    """可追：小版本併成大版本鍵之後，下鑽要用同一套併版邏輯比對，
    不然「Red Hat Enterprise Linux 7」點下去會找不到「7.9」「7.6」那兩台。"""
    _with_eos_table(monkeypatch, [
        {"name": "Red Hat Enterprise Linux 7", "eos_date": _soon(), "source_url": "https://x", "note": ""},
    ])
    conn = _conn(tmp_path)
    try:
        _seed_versions(conn)
        rows = system_report.os_version_breakdown(conn)
        for r in rows:
            got = system_report.drill_platform(conn, os_canonical=r["os_canonical"])
            assert len(got) == r["count"], r["os_canonical"]
    finally:
        conn.close()
