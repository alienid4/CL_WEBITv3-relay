"""全站「同一台」的判定只能有一把尺（2026-09-17）。

使用者原話：「你怎麼保證每次邏輯都對？以後改會不會再犯？」＋「檢視每一個公式，
確保不會因重複而有這種低級的錯誤。」

背景：同一個「幾台」的去重規則，先後在 system_report._dup_key（v1.204）、
pipeline._collapse_duplicates（v1.209）、api._duplicate_groups＋health_check（v1.210）
各自被寫過一份，其中好幾份忘了「ip=0.0.0.0 是佔位、不是同一台」，把兩台不同的
Cisco 交換器誤併／誤報成重複。

這支測試把**所有**去重／重複偵測路徑釘在同一個案例上：兩台同型號、都填 0.0.0.0 的
交換器＝兩台（不是重複）；同主機名＋同一個真實 IP＝一台（真重複）。任何一條路徑
以後又忘記排 0.0.0.0，這裡就會紅——這就是「不會再犯」的機制，不靠人記得。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_stats as ss  # noqa: E402
import system_report as sr  # noqa: E402
import pipeline  # noqa: E402
import api  # noqa: E402
import health_check  # noqa: E402

# 兩台不同交換器都填 0.0.0.0（佔位）＋同型號主機名
SW1 = ("HW-1", "cisco ws-c2960g-8tc-l", "0.0.0.0")
SW2 = ("HW-2", "cisco ws-c2960g-8tc-l", "0.0.0.0")
# 真重複：同主機名＋同一個真實 IP
DUP1 = ("HW-3", "realsrv", "10.99.1.1")
DUP2 = ("HW-4", "realsrv", "10.99.1.1")


def _seed(tmp_path):
    dbp = tmp_path / "d.db"
    db.init_db(dbp)
    conn = db.get_connection(dbp)
    for sn, host, ip in (SW1, SW2, DUP1, DUP2):
        db.insert_hardware(conn, asset_serial=sn, hostname=host, ip=ip, asset_status="使用中")
    conn.commit()
    return conn


def test_machine_key_把0000各算一台(tmp_path):
    _seed(tmp_path)
    assert ss.machine_key(SW1[1], SW1[2], SW1[0]) != ss.machine_key(SW2[1], SW2[2], SW2[0])
    assert ss.machine_key(DUP1[1], DUP1[2], DUP1[0]) == ss.machine_key(DUP2[1], DUP2[2], DUP2[0])


def test_system_report_dup_key_把0000視為獨立(tmp_path):
    _seed(tmp_path)
    # _dup_key 對 0.0.0.0 回 None（＝無法判定同台→各算一台）；真實 IP 回同一個 key
    assert sr._dup_key({"hostname": SW1[1], "ip": SW1[2]}) is None
    assert sr._dup_key({"hostname": DUP1[1], "ip": DUP1[2]}) == sr._dup_key(
        {"hostname": DUP2[1], "ip": DUP2[2]})


def test_pipeline去重不併0000(tmp_path):
    conn = _seed(tmp_path)
    s = pipeline.summarize(conn)
    hosts = [(it["hostname"], it["ip"]) for it in s["items"]]
    # 兩台 0.0.0.0 交換器要各自成列（2 筆），真重複那台收成 1 筆
    sw = [h for h in hosts if h[1] == "0.0.0.0"]
    dup = [h for h in hosts if h[1] == "10.99.1.1"]
    assert len(sw) == 2, "兩台都填 0.0.0.0 的交換器不可併成一台"
    assert len(dup) == 1, "同主機名＋同一真實 IP 才是真重複，收成一台"


def test_重複偵測不把0000列成重複(tmp_path):
    conn = _seed(tmp_path)
    groups = api._duplicate_groups(conn)
    keys = {(g["hostname"].lower().strip(), (g["ip"] or "").strip()) for g in groups}
    assert ("cisco ws-c2960g-8tc-l", "0.0.0.0") not in keys, "0.0.0.0 佔位不是重複，不可列出"
    assert ("realsrv", "10.99.1.1") in keys, "同主機名＋同真實 IP 才是真重複，要列出"


def test_health_check重複判定不含0000(tmp_path):
    conn = _seed(tmp_path)
    ctx = health_check._load_context(conn)
    assert ("cisco ws-c2960g-8tc-l", "0.0.0.0") not in ctx["dup_keys"]
    assert ("realsrv", "10.99.1.1") in ctx["dup_keys"]
