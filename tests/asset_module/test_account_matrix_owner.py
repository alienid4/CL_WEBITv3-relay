"""帳號合規表要帶部門／窗口／盤點時間（2026-09-18）。

使用者：「這個怎沒有對應部門跟窗口？這樣盤點誰要確認？」「盤點須加日期戳記嗎？」
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory as ai  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 帳號掛在 A-1，但 A-1 沒填部門／窗口；同 IP 的 A-2 有填 → 要補得到
    db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="192.0.2.1", asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-2", hostname="h1", ip="192.0.2.1", asset_status="使用中",
                       usage_unit="數位開發部", user_name="王小明", custodian="李小華")
    # 完全沒填的另一台
    db.insert_hardware(c, asset_serial="B-1", hostname="h2", ip="192.0.2.2", asset_status="使用中")
    for ip, sn in (("192.0.2.1", "A-1"), ("192.0.2.2", "B-1")):
        c.execute("INSERT INTO host_account (ip, asset_serial, username, uid, kind, is_sudoer, "
                  "never_logged_in, source, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ip, sn, "webit3", 1001, "human", 0, 0, "ssh", "2026-09-10", "2026-09-11 14:52:29"))
    c.commit()
    return c


def test_部門窗口保管者_同IP其他筆有填也要補得到(tmp_path):
    c = _conn(tmp_path)
    rows = {r["ip"]: r for r in ai.list_accounts(c)}
    a = rows["192.0.2.1"]
    assert a["department"] == "數位開發部" and a["contact"] == "王小明" and a["custodian"] == "李小華"
    b = rows["192.0.2.2"]
    assert b["department"] is None and b["contact"] is None


def test_匯出有部門窗口與盤點時間_沒填寫未填(tmp_path):
    c = _conn(tmp_path)
    cols = ai.MATRIX_EXPORT_COLS
    for k in ("department", "contact", "custodian", "collected"):
        assert k in cols, f"匯出少了 {k}"
    rows = {r["ip"]: r for r in ai.list_accounts(c)}
    assert cols["department"][1](rows["192.0.2.2"]) == "未填", "沒填要明講，不可以是空白"
    assert cols["collected"][1](rows["192.0.2.1"]) == "2026-09-11 14:52:29"


def test_合規表帶業務系統環境OS類型_跟盤點報告同一套(tmp_path):
    c = _conn(tmp_path)
    c.execute("UPDATE hardware SET api_id = 'N-001', environment = '正式', os = 'Red Hat Enterprise Linux 8' "
              "WHERE ip = '192.0.2.1'")
    c.commit()
    a = {r["ip"]: r for r in ai.list_accounts(c)}["192.0.2.1"]
    assert a["system_id"] == "N-001"
    assert a["env_group"] == "prod" and a["os_type"] == "Linux"
    assert a["needs_action"] is False and a["open_findings"] == 0


def test_搜尋0筆時說得出是不是還沒收集(tmp_path):
    """查人名得 0 筆：要分得出「他負責的機器還沒收集帳號」。"""
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="C-1", hostname="db7", ip="192.0.2.7", asset_status="使用中",
                       user_name="李泰益", usage_unit="數位開發部")
    c.commit()
    r = ai.explain_search(c, "李泰益")
    assert r["machines"] == 1 and r["not_collected"] == ["db7"]
    r2 = ai.explain_search(c, "王小明")            # 他負責的 h1 有收集帳號
    assert r2["machines"] == 1 and r2["collected"] == 1 and r2["not_collected_count"] == 0
    assert ai.explain_search(c, "數位開發部 李泰益")["machines"] == 1, "多個詞要全部符合"
    assert ai.explain_search(c, "查無此人")["machines"] == 0


def test_服務帳號看備註或帳號名_不可以落到真人():
    """221：ansible_svc 備註「Ansible Service Account」、有 bash、UID 夠大 → 以前判成真人。"""
    import account_collector as acn
    base = {"uid": 1500, "shell": "/bin/bash"}
    assert acn.classify_account(dict(base, username="ansible_svc", gecos="Ansible Service Account"), {}, set()) == "service"
    assert acn.classify_account(dict(base, username="svc_backup", gecos=None), {}, set()) == "service"
    assert acn.classify_account(dict(base, username="appuser", gecos="批次服務帳號"), {}, set()) == "service"
    assert acn.classify_account(dict(base, username="alice", gecos="Alice Wang"), {}, set()) == "human", "真人不可以被誤判"
    assert acn.classify_account(dict(base, username="servicedesk", gecos="Service Desk 王小明"), {}, set()) == "human", \
        "只認明確字樣：名字裡有 service 不算"
