"""AD 人員名單走中繼（2.060.x）：9.26 的 ad_export.ps1 -RelayDir 寫批次 → relay_import 的 ad_users 來源 → ad_import 整份取代。

示範資料只用 *.example.test 與假員編；不連網路。PowerShell 那一端只做靜態檢查（devkit 一致性另見 test_devkit_content）。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
from test_relay_import import SAT, write_batch  # noqa: E402

COLS = ["SamAccountName", "employeeID", "DisplayName", "UserPrincipalName", "mail", "department", "manager", "Enabled", "Phone", "Ext"]


def person(i, enabled="True"):
    return [f"{i:06d}", f"{i:06d}", f"測試人員{i}", f"u{i}@corp.example.test", f"u{i}@corp.example.test",
            "資訊處-系統部", "CN=測試主管,OU=IT,DC=corp,DC=example,DC=test", enabled, "", ""]


def users(n, start=1):
    return {"columns": COLS, "rows": [person(i) for i in range(start, start + n)]}


def _ai_script(name):
    """AI/ 不進公開中繼（去識別化快照裡沒有）：不在就跳過，正本一致性只在私有 repo 驗。"""
    p = ROOT / "AI" / "tools_relay" / name
    if not p.exists():
        pytest.skip("AI/ 不在這份快照（公開中繼）")
    return p.read_text(encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: SAT(10))
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    yield c, drop
    qw.set_clock(None)
    c.close()


def send(drop, name, content, fname="users.json"):
    return write_batch(drop, name, {fname: content}, source="ad_users",
                       collected=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"))


def count(c):
    return c.execute("SELECT COUNT(*) FROM ad_person").fetchone()[0]


def test_首次匯入整份寫進_前導零保留(env):
    c, drop = env
    send(drop, "adusers_1", users(60))
    res = ri.run_scan(c, "manual", "admin")
    assert any(b["status"] == "imported" and b["source"] == "ad_users" for b in res["results"]), res
    assert count(c) == 60
    assert c.execute("SELECT employee_id FROM ad_person WHERE upn='u7@corp.example.test'").fetchone()[0] == "000007"


def test_名單少超過兩成整批拒收_原名單不動(env):
    c, drop = env
    send(drop, "adusers_1", users(100))
    ri.run_scan(c, "manual", "admin")
    send(drop, "adusers_2", users(70))
    res = ri.run_scan(c, "manual", "admin")
    b = [x for x in res["results"] if x["batch_id"] == "adusers_2"][0]
    assert b["status"] == "rejected" and b["code"] == "shrink", b
    assert count(c) == 100


def test_少於兩成可以_整份取代(env):
    c, drop = env
    send(drop, "adusers_1", users(100))
    ri.run_scan(c, "manual", "admin")
    send(drop, "adusers_2", users(90, start=11))
    ri.run_scan(c, "manual", "admin")
    assert count(c) == 90
    assert c.execute("SELECT COUNT(*) FROM ad_person WHERE employee_id='000001'").fetchone()[0] == 0


@pytest.mark.parametrize("content,fname,code", [
    ({"columns": COLS, "rows": []}, "users.json", "bad_data"),
    ({"columns": ["mail"], "rows": [["a@corp.example.test"]]}, "users.json", "bad_data"),
    ({"columns": COLS, "rows": [["u1"]]}, "users.json", "bad_data"),
    (users(3), "other.json", "bad_data"),
])
def test_壞資料拒收(env, content, fname, code):
    c, drop = env
    send(drop, "adusers_bad", content, fname=fname)
    res = ri.run_scan(c, "manual", "admin")
    b = res["results"][0]
    assert b["status"] == "rejected" and b["code"] == code, b


def test_腳本_relay模式有防呆與清理():
    s = _ai_script("ad_export.ps1")
    assert s.startswith("# AD-EXPORT-SCRIPT v3")
    assert "source = 'ad_users'" in s and "'users.json'" in s and "-PurgeDir" in s
    # 腳本同時嵌在 devkit.ts 的模板字串裡：不可以有反斜線、反引號、${（會被 JS 當跳脫）
    assert "\\" not in s and "`" not in s and "${" not in s
    # manifest 最後寫（系統只看到完整的批次）
    assert s.index("'manifest.json'") > s.index("'users.json'")
