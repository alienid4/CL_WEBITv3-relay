"""納管漏斗照主機算，不是照筆數算（2026-09-16）。

使用者看到同一台 SECSVR194-099T／10.92.194.99 在漏斗出現三次：
「這是不是同一台? 怎會出現多次。程式會自動合併嗎」。

資產庫裡確實有三筆（CIA／dynassets／RVTools 各登記一次）。要守的：

1. 主機名＋IP 都相同才收成一台（判準跟資產查詢頁的重複清單一致，不另立一套）
2. 只有一邊相同**不收**——只有 IP 相同可能是 DHCP 回收給了別台；
   只有主機名相同可能是換網段（搬遷）
3. **留最需要處理的那一關**。留成「資料齊全」等於把該做的事藏起來
4. **不自動合併資料**：只在顯示與統計上收成一台，並標 dup_count／dup_serials 讓人自己處理
5. 收完之後「各關加總 = 母體」的對帳仍然成立
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import pipeline  # noqa: E402


def item(host, ip, stage, serial, idx=None):
    return {
        "hostname": host, "ip": ip, "asset_serial": serial,
        "stage": stage, "stage_label": stage, "tone": "warn",
        "stage_index": pipeline.STAGE_INDEX[stage] if idx is None else idx,
        "next_action": "", "action": "", "environment": None, "env_group": "unset",
        "physical_location": None, "os": None, "os_type": "未知", "collect_ok": None,
        "onboard_block": None, "onboard_exempt": False, "last_check": None, "error": None,
    }


def test_同主機名同IP_收成一台_其餘序號留著():
    rows, merged = pipeline._collapse_duplicates([
        item("SECSVR194-099T", "192.0.2.99", "lost", "A-1"),
        item("secsvr194-099t ", " 192.0.2.99", "lost", "A-2"),   # 大小寫／空白不算不同台
        item("SECSVR194-099T", "192.0.2.99", "lost", "A-3"),
    ])
    assert len(rows) == 1 and merged == 2
    assert rows[0]["dup_count"] == 3
    assert set(rows[0]["dup_serials"]) == {"A-1", "A-2", "A-3"}


def test_兩台都填0000佔位不可誤併成一台():
    """2026-09-17 verify_numbers 抓到：兩台不同的 Cisco 交換器都填 ip=0.0.0.0（沒真 IP 的
    佔位），主機名又剛好同型號字串——pipeline 舊的自寫 (host,ip) 會把它們併成一台，
    害納管母體比正典 machine_key 多算/少算。machine_key 對 0.0.0.0 退回序號各算一台，
    這裡守住『佔位 IP 不當同一台』。"""
    rows, merged = pipeline._collapse_duplicates([
        item("cisco ws-c2960g-8tc-l", "0.0.0.0", "lost", "HW-00012622"),
        item("cisco ws-c2960g-8tc-l", "0.0.0.0", "lost", "HW-00012623"),
    ])
    assert len(rows) == 2 and merged == 0, "兩台都填 0.0.0.0 佔位是兩台不同機器，不可併成一台"


def test_只有一邊相同不收():
    rows, merged = pipeline._collapse_duplicates([
        item("h1", "192.0.2.1", "lost", "A-1"),
        item("h1", "192.0.2.2", "lost", "A-2"),     # 換網段＝搬遷，不是重複
        item("h2", "192.0.2.1", "lost", "A-3"),     # DHCP 回收給別台
    ])
    assert len(rows) == 3 and merged == 0


def test_缺主機名或IP不收():
    rows, merged = pipeline._collapse_duplicates([
        item(None, "192.0.2.5", "lost", "A-1"),
        item(None, "192.0.2.5", "lost", "A-2"),
        item("h9", "", "lost", "A-3"),
        item("h9", "", "lost", "A-4"),
    ])
    assert len(rows) == 4 and merged == 0, "少一邊就無法確定是同一台，寧可不收"


def test_留最需要處理的那一關_不可以藏起待辦():
    done = pipeline.STAGE_INDEX["complete"]
    todo = pipeline.STAGE_INDEX["not_onboarded"]
    assert todo < done, "前提：越前面的關卡 index 越小"
    rows, _ = pipeline._collapse_duplicates([
        item("h", "192.0.2.7", "complete", "A-1"),
        item("h", "192.0.2.7", "not_onboarded", "A-2"),
    ])
    assert len(rows) == 1
    assert rows[0]["stage"] == "not_onboarded", "留成『資料齊全』會把該做的事藏起來"
    assert rows[0]["asset_serial"] == "A-2", "代表列要換成被留下那一關的那一筆"


def test_對帳仍成立(tmp_path):
    import db

    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        for i in range(3):      # 同一台登記三次
            db.insert_hardware(conn, asset_serial=f"D-{i}", hostname="dup", ip="192.0.2.50",
                               asset_status="使用中")
        db.insert_hardware(conn, asset_serial="U-1", hostname="solo", ip="192.0.2.51",
                           asset_status="使用中")
        out = pipeline.summarize(conn)
    finally:
        conn.close()
    assert out["reconcile"]["ok"], "各關加總必須等於母體，收完重複也一樣"
    assert out["total"] == len(out["items"]) == 2, "三筆重複＋一台獨立 → 2 台"
    assert out["duplicates_merged"] == 2
