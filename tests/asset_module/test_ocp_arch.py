"""OCP 叢集架構圖（2026-09-20 使用者：「OCP 能做一個架構圖嗎？在架構圖就可以知道他的狀態」）。

使用者的模型：OCP 是「一台大設備，由很多小主機建立起來」——
所以節點要一台一台看得到，但要能以叢集為單位呈現；判不出叢集的標「未分群」由人自己指定。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import arch  # noqa: E402
import db  # noqa: E402
import ocp_cluster  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    rows = [
        # FQDN 分得出叢集
        ("HW-1", "n1.paas-a.ocp.example.com", "10.99.1.1", "CoreOS 4.12", "OCP Master"),
        ("HW-2", "n2.paas-a.ocp.example.com", "10.99.1.2", "CoreOS 4.12", "OCP Worker"),
        # 版本不一致（同叢集混版＝風險）
        ("HW-3", "n3.paas-a.ocp.example.com", "10.99.1.3", "RHCOS 4.10", "OCP Worker"),
        # 只有用途寫得出 paas-b
        ("HW-4", "shortname-1", "10.99.2.1", "CoreOS 4.18", "技術中台 paas-b AP 01 Infra"),
        # 兩個都判不出來 → 未分群
        ("HW-5", "shortname-2", "10.99.3.1", "CoreOS 4.18", "OCP 節點"),
    ]
    for sn, host, ip, os_, purpose in rows:
        db.insert_hardware(c, asset_serial=sn, hostname=host, ip=ip, os=os_,
                           asset_purpose=purpose, asset_status="使用中")
    c.commit()
    return c


def test_分群依據要標出來_判不出來就未分群(tmp_path):
    t = arch.ocp_topology(_conn(tmp_path))
    by = {c["cluster"]: c for c in t["clusters"]}
    assert by["paas-a"]["node_count"] == 3 and by["paas-a"]["basis"] == "FQDN"
    assert by["paas-b"]["node_count"] == 1 and by["paas-b"]["basis"] == "用途／名稱"
    assert by["未分群"]["node_count"] == 1, "判不出來就標未分群，不可以用機房或網段硬湊"
    assert t["totals"] == {"clusters": 2, "nodes": 5, "ungrouped": 1}


def test_同叢集混版要列成風險(tmp_path):
    t = arch.ocp_topology(_conn(tmp_path))
    a = next(c for c in t["clusters"] if c["cluster"] == "paas-a")
    assert any("2 種版本" in r for r in a["risks"]), a["risks"]
    assert a["by_role"] == {"Master": 1, "Worker": 2}


def test_人工指定叢集_優先且標明是誰指定(tmp_path):
    c = _conn(tmp_path)
    ocp_cluster.set_cluster(c, "HW-5", "paas-b", reason="問過平台組", by="tester")
    t = arch.ocp_topology(c)
    by = {x["cluster"]: x for x in t["clusters"]}
    assert "未分群" not in by, "指定完就不該還留在未分群"
    node = next(n for n in by["paas-b"]["nodes"] if n["asset_serial"] == "HW-5")
    assert node["cluster_basis"] == "人工指定" and node["cluster_by"] == "tester"
    assert node["cluster_reason"] == "問過平台組"
    # 清除指定就回到自動判斷
    ocp_cluster.set_cluster(c, "HW-5", "", by="tester")
    assert any(x["cluster"] == "未分群" for x in arch.ocp_topology(c)["clusters"])


def test_兄弟節點_出事時看得出關聯(tmp_path):
    s = arch.ocp_siblings(_conn(tmp_path), "HW-2")
    assert s["is_ocp"] and s["cluster"] == "paas-a" and s["node_count"] == 3
    assert [n["asset_serial"] for n in s["siblings"]] == ["HW-1", "HW-3"], "自己不列進兄弟"
    assert s["me"]["role"] == "Worker"
    assert arch.ocp_siblings(_conn(tmp_path / "b"), "沒這台")["is_ocp"] is False


def test_角色台數不算退役_汰換要標出來(tmp_path):
    """2026-09-21：221 的 paas-bq「Master 6 台」其實是現役 3 ＋ 已退役 3
    （SECSVR175-014/015/016 換過機器，主機名沿用、資產編號換新）。
    退役算進台數＝架構圖上的規模灌水一倍，而且會被誤讀成重複登記。"""
    c = _conn(tmp_path)
    # 同主機名、不同資產編號：舊的退役、新的還在 → 汰換
    db.insert_hardware(c, asset_serial="HW-OLD", hostname="n9.paas-a.ocp.example.com",
                       ip="10.99.1.9", os="CoreOS 4.12", asset_purpose="OCP Master",
                       asset_status="報廢")
    db.insert_hardware(c, asset_serial="HW-NEW", hostname="n9.paas-a.ocp.example.com",
                       ip="10.99.1.99", os="CoreOS 4.12", asset_purpose="OCP Master",
                       asset_status="使用中")
    c.commit()
    a = next(x for x in arch.ocp_topology(c)["clusters"] if x["cluster"] == "paas-a")
    m = a["role_tiers"]["Master"]
    assert m["count"] == 2, "現役 Master 是原本 1 台 ＋ 新的 1 台，退役那台不算"
    assert m["retired"] == 1, "退役的要另外報，不是消失"
    assert any("汰換" in r for r in a["risks"]), a["risks"]
    assert not any("多筆登記" in r for r in a["risks"]), "汰換不是重複登記，不可以混講"


def test_寫法不同不算混版_RHCOS就是CoreOS(tmp_path):
    """2026-09-21：221 上 13 座叢集的「混版」幾乎全是假警報——
    paas-bq 是「CoreOS 4.12 vs RHCOS4.12」（RHCOS 就是 Red Hat CoreOS），
    uat-nh 是「CoreOS 4.12 vs Coreos4.12」（只差大小寫）。
    假警報比沒有警報更糟：人查過一次發現是空的，下次真的混版就不看了。"""
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="HW-6", hostname="n6.paas-b.ocp.example.com",
                       ip="10.99.2.6", os="RHCOS4.18", asset_purpose="OCP Worker",
                       asset_status="使用中")
    db.insert_hardware(c, asset_serial="HW-7", hostname="n7.paas-b.ocp.example.com",
                       ip="10.99.2.7", os="Coreos 4.18", asset_purpose="OCP Worker",
                       asset_status="使用中")
    c.commit()
    b = next(x for x in arch.ocp_topology(c)["clusters"] if x["cluster"] == "paas-b")
    assert not any("種版本" in r for r in b["risks"]), b["risks"]
    assert any("寫法不同" in r for r in b["risks"]), "要改列成資料品質問題，不是默默吞掉"
    # 原字串照樣保留，不自作主張改資料
    assert set(b["versions"]) >= {"RHCOS4.18", "Coreos 4.18"}


def test_角色可人工指定_且中文用途也認得出來(tmp_path):
    """2026-09-21 使用者：「我要怎麼定義角色」「怎麼編輯」——本來只能靠英文關鍵字猜，
    猜不出來就永遠卡在「未標示」，人沒有地方訂正。"""
    c = _conn(tmp_path)
    # 中文用途要認得（資產用途是人填的，不是每個人都寫英文）
    db.insert_hardware(c, asset_serial="HW-ZH", hostname="zh.paas-a.ocp.example.com",
                       ip="10.99.1.20", os="CoreOS 4.12", asset_purpose="OCP 主控節點",
                       asset_status="使用中")
    c.commit()
    a = next(x for x in arch.ocp_topology(c)["clusters"] if x["cluster"] == "paas-a")
    zh = next(n for n in a["nodes"] if n["asset_serial"] == "HW-ZH")
    assert zh["role"] == "Master" and zh["role_basis"] == "用途／名稱"

    # 判不出來的那台可以人工指定，而且要標明是人工指定
    ocp_cluster.set_role(c, "HW-5", "Infra", reason="問過平台組", by="tester")
    node = next(n for x in arch.ocp_topology(c)["clusters"] for n in x["nodes"]
                if n["asset_serial"] == "HW-5")
    assert node["role"] == "Infra" and node["role_basis"] == "人工指定"
    # 只指定角色不該順手把它搬去別的叢集
    assert any(x["cluster"] == "未分群" for x in arch.ocp_topology(c)["clusters"])

    # 清除角色指定＝回到自動判斷，但不能把同一台的叢集指定一起抹掉
    ocp_cluster.set_cluster(c, "HW-5", "paas-b", by="tester")
    ocp_cluster.set_role(c, "HW-5", "", by="tester")
    node = next(n for x in arch.ocp_topology(c)["clusters"] for n in x["nodes"]
                if n["asset_serial"] == "HW-5")
    assert node["role"] == "未標示", "角色回到自動判斷"
    assert node["cluster_basis"] == "人工指定", "叢集指定不可以被順手刪掉"
