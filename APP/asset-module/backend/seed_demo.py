"""天龍八部主題測試資料 seed（M1 資產 + M2 系統聯通圖）。

用途：填測試/展示資料，讓 M1 儀表板、資產查詢、納入管理、M2 系統聯通圖都有東西可測。
這是「明確要的測試資料」，不是 D31 說的「假裝成真的假資料」——僅供測試環境（.221）。

用法：
  python seed_demo.py          # 灌入（可重跑，冪等）
  python seed_demo.py --clear  # 清掉所有 demo 資料（不動真實資料）
  python seed_demo.py --recompare  # 灌完順便重跑比對，讓儀表板問題清單有資料

辨識標記：硬體/人員/軟體 asset_serial 皆以 'TLBB-' 開頭；M2 系統 id 在 SYSTEM_IDS 清單。
清除只刪這些，不碰任何真實資料。
"""
from __future__ import annotations

import sys

import db

# ── M2：AP 系統（application systems）+ 彼此關聯（依賴）─────────────────────
# A 依賴 B  =>  (A, B)。停 B 會波及 B 的所有上游。王語嫣客戶資料庫是 SPOF、波及最廣。
SYSTEMS = [
    # id, 名稱, 分類, 領域, 健康度, 是否SPOF
    ("portal_zhenlong", "珍瓏棋局入口", "對外", "數位通路", "ok", 0),
    ("app_lingjiu", "靈鷲宮行動App", "對外", "數位通路", "ok", 0),
    ("gw_liumai", "六脈神劍API閘道", "閘道", "數位通路", "ok", 0),
    ("core_tianlong", "天龍寺核心交易", "核心", "核心系統", "ok", 0),
    ("acct_dali", "大理段氏帳務", "核心", "核心系統", "warn", 0),
    ("mq_xiaoyao", "逍遙派訊息中介MQ", "基礎服務", "核心系統", "err", 0),
    ("db_wangyuyan", "王語嫣客戶資料庫", "基礎服務", "核心系統", "ok", 1),
    ("ad_shaolin", "少林達摩AD目錄", "基礎服務", "核心系統", "ok", 0),
    ("cache_gaibang", "丐幫降龍快取", "基礎服務", "核心系統", "ok", 0),
]
SYSTEM_IDS = [s[0] for s in SYSTEMS]

DEPS = [
    ("portal_zhenlong", "gw_liumai", "API 呼叫"),
    ("app_lingjiu", "gw_liumai", "API 呼叫"),
    ("gw_liumai", "core_tianlong", "API 呼叫"),
    ("gw_liumai", "cache_gaibang", "快取"),
    ("core_tianlong", "db_wangyuyan", "DB 連線"),
    ("acct_dali", "db_wangyuyan", "DB 連線"),
    ("core_tianlong", "mq_xiaoyao", "訊息"),
    ("core_tianlong", "ad_shaolin", "dependsOn"),
    ("acct_dali", "ad_shaolin", "dependsOn"),
    ("core_tianlong", "cache_gaibang", "快取"),
]

# ── M1：硬體主機（api_id 綁到上面的 AP 系統 id，供 M2 未來「主機層」關聯測試）────
# (serial, hostname, ip, os, model, env, api_id(綁AP系統), owner, user, custodian)
HOSTS = [
    ("TLBB-0001", "liumai-gw01", "10.99.0.1", "Rocky Linux 9.7", "HPE ProLiant DL380", "正式", "gw_liumai", "喬峰", "段譽", "虛竹"),
    ("TLBB-0002", "liumai-gw02", "10.20.11.12", "Rocky Linux 9.7", "HPE ProLiant DL380", "備援", "gw_liumai", "喬峰", "段譽", "虛竹"),
    ("TLBB-0003", "tianlong-core01", "10.20.12.21", "RHEL 8.9", "IBM Power S1022 (AIX)", "正式", "core_tianlong", "段譽", "王語嫣", "保定帝"),
    ("TLBB-0004", "tianlong-core02", "10.20.12.22", "AIX 7.3", "IBM Power S1022", "正式", "core_tianlong", "段譽", "王語嫣", "保定帝"),
    ("TLBB-0005", "tianlong-app01", "10.99.0.101", "Ubuntu 22.04", "VMware VM", "正式", "core_tianlong", "段譽", "鍾靈", "保定帝"),
    ("TLBB-0006", "dali-acct01", "10.20.13.31", "Windows Server 2022", "Dell PowerEdge R760", "正式", "acct_dali", "段正淳", "阿朱", "褚萬里"),
    ("TLBB-0007", "dali-db01", "10.20.13.32", "RHEL 8.9", "Dell PowerEdge R760", "正式", "acct_dali", "段正淳", "阿朱", "褚萬里"),
    ("TLBB-0008", "wangyuyan-db01", "10.20.14.41", "RHEL 8.9", "IBM Power S1024", "正式", "db_wangyuyan", "王語嫣", "慕容復", "鄧百川"),
    ("TLBB-0009", "wangyuyan-db02", "10.20.14.42", "RHEL 8.9", "IBM Power S1024", "備援", "db_wangyuyan", "王語嫣", "慕容復", "鄧百川"),
    ("TLBB-0010", "xiaoyao-mq01", "10.20.15.51", "Rocky Linux 9.7", "VMware VM", "正式", "mq_xiaoyao", "無崖子", "李秋水", "蘇星河"),
    ("TLBB-0011", "shaolin-ad01", "10.20.16.61", "Windows Server 2022", "Dell PowerEdge R660", "正式", "ad_shaolin", "玄慈", "虛竹", "玄難"),
    ("TLBB-0012", "gaibang-cache01", "10.20.17.71", "Rocky Linux 9.7", "VMware VM", "正式", "cache_gaibang", "喬峰", "游坦之", "白世鏡"),
    ("TLBB-0013", "zhenlong-web01", "10.20.10.11", "Ubuntu 22.04", "VMware VM", "正式", "portal_zhenlong", "蘇星河", "丁春秋", "蘇星河"),
    ("TLBB-0014", "lingjiu-app01", "10.20.10.12", "Ubuntu 22.04", "VMware VM", "正式", "app_lingjiu", "虛竹", "梅劍", "天山童姥"),
    ("TLBB-0015", "dali-acct-test01", "10.30.13.31", "Windows Server 2019", "VMware VM", "測試", "acct_dali", "段正淳", "阿紫", "褚萬里"),
]

# ── M1：人員（天龍八部角色，綁到主機）────────────────────────────────
# (asset_serial, person_name, division, department, phone, job_desc, proxy1)
PEOPLE = [
    ("TLBB-0003", "段譽", "資訊處", "核心交易科", "0966-000-001", "核心交易系統負責人", "王語嫣"),
    ("TLBB-0006", "段正淳", "財務處", "帳務科", "0966-000-002", "帳務系統負責人", "阿朱"),
    ("TLBB-0008", "王語嫣", "資訊處", "資料庫科", "0966-000-003", "客戶資料庫DBA", "慕容復"),
    ("TLBB-0010", "無崖子", "資訊處", "中介平台科", "0966-000-004", "訊息中介MQ管理", "蘇星河"),
    ("TLBB-0011", "玄慈", "資安處", "目錄服務科", "0966-000-005", "AD目錄服務管理", "玄難"),
    ("TLBB-0001", "喬峰", "資訊處", "網路閘道科", "0966-000-006", "API閘道負責人", "段譽"),
]

# ── M1：軟體（AP 系統本身當軟體資產，api_id 綁 AP 系統）────────────────
# (asset_serial, api_id, asset_name, hostname, ip, os, db_software, handles_pii)
SOFTWARE = [
    ("TLBB-0003", "core_tianlong", "天龍寺核心交易系統", "tianlong-core01", "10.20.12.21", "AIX 7.3", "Oracle 19c", 1),
    ("TLBB-0006", "acct_dali", "大理段氏帳務系統", "dali-acct01", "10.20.13.31", "Windows Server 2022", "MSSQL 2022", 1),
    ("TLBB-0008", "db_wangyuyan", "王語嫣客戶資料庫", "wangyuyan-db01", "10.20.14.41", "RHEL 8.9", "Oracle 19c RAC", 1),
    ("TLBB-0010", "mq_xiaoyao", "逍遙派訊息中介平台", "xiaoyao-mq01", "10.20.15.51", "Rocky Linux 9.7", "RabbitMQ / Kafka", 0),
    ("TLBB-0013", "portal_zhenlong", "珍瓏棋局網銀入口", "zhenlong-web01", "10.20.10.11", "Ubuntu 22.04", "PostgreSQL 15", 1),
]


def clear(conn) -> None:
    conn.execute("DELETE FROM system_deps WHERE source IN ({0}) OR target IN ({0})".format(
        ",".join("?" for _ in SYSTEM_IDS)), SYSTEM_IDS * 2)
    conn.execute("DELETE FROM systems WHERE id IN ({0})".format(",".join("?" for _ in SYSTEM_IDS)), SYSTEM_IDS)
    conn.execute("DELETE FROM software WHERE asset_serial LIKE 'TLBB-%'")
    conn.execute("DELETE FROM personnel WHERE asset_serial LIKE 'TLBB-%'")
    conn.execute("DELETE FROM hardware WHERE asset_serial LIKE 'TLBB-%'")
    conn.commit()
    print("已清除所有 TLBB- demo 資料（真實資料未動）")


def seed(conn) -> None:
    clear(conn)  # 先清再灌，確保冪等

    # M2 系統 + 依賴
    for sid, label, cat, dom, health, spof in SYSTEMS:
        conn.execute(
            "INSERT INTO systems (id, label, category, domain, health, is_spof) VALUES (?,?,?,?,?,?)",
            (sid, label, cat, dom, health, spof),
        )
    for src, tgt, dtype in DEPS:
        conn.execute("INSERT INTO system_deps (source, target, dep_type) VALUES (?,?,?)", (src, tgt, dtype))

    # M1 硬體
    for serial, host, ip, os_, model, env, api_id, owner, user, custodian in HOSTS:
        subnet = ".".join(ip.split(".")[:3]) + ".0/24"
        db.insert_hardware(
            conn, asset_serial=serial, hostname=host, ip=ip, os=os_, device_model=model,
            environment=env, api_id=api_id, asset_name=next((s[1] for s in SYSTEMS if s[0] == api_id), api_id),
            group_name=next((s[3] for s in SYSTEMS if s[0] == api_id), None),
            owner=owner, user_name=user, custodian=custodian, asset_status="使用中",
            is_vm=1 if "VM" in model else 0, subnet=subnet,
            integrity=3, confidentiality=3, availability=3, quantity=1,
            owning_company="天龍八部股份有限公司", request_no="TLBB-DEMO",
        )

    # M1 人員
    for serial, name, div, dept, phone, job, proxy in PEOPLE:
        conn.execute(
            "INSERT INTO personnel (asset_serial, person_name, belong_division, belong_department, phone, job_desc, proxy1, request_no) "
            "VALUES (?,?,?,?,?,?,?, 'TLBB-DEMO')",
            (serial, name, div, dept, phone, job, proxy),
        )

    # M1 軟體
    for serial, api_id, name, host, ip, os_, dbsw, pii in SOFTWARE:
        conn.execute(
            "INSERT INTO software (asset_serial, api_id, asset_name, hostname, ip, os, db_software, handles_pii, request_no) "
            "VALUES (?,?,?,?,?,?,?,?, 'TLBB-DEMO')",
            (serial, api_id, name, host, ip, os_, dbsw, pii),
        )

    conn.commit()
    print(f"已灌入 demo：{len(SYSTEMS)} AP系統 / {len(DEPS)} 關聯 / {len(HOSTS)} 硬體 / {len(PEOPLE)} 人員 / {len(SOFTWARE)} 軟體")
    print("提示：AP系統關聯圖看 /topology；王語嫣客戶資料庫是 SPOF，點它看 blast radius 波及最廣。")


def main() -> None:
    db.init_db()
    conn = db.get_connection()
    try:
        if "--clear" in sys.argv:
            clear(conn)
            return
        seed(conn)
        if "--recompare" in sys.argv:
            import scan_service
            scan_service._recompare(conn)
            print("已重跑比對（儀表板問題清單／venn 會反映 demo 硬體 vs 最新掃描）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
