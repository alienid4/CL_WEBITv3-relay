"""本機舊檔保留（2.07x）：公司測試機根目錄曾滿到 100%（patch 備份、每日 DB 備份、中繼已匯入檔無限累積）。

釘住：保留份數／天數可設（預設 3／3／7）、空目錄、只有 1 份、名稱不符模式不刪、timer 被手動關掉不重開、
API 範圍檢查＋只有 admin 能改＋寫操作紀錄、本機磁碟使用率超過門檻。不連網路，全部在暫存資料夾。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import backup  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import retention  # noqa: E402

ROLE_AWARE_TESTS = True


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "asset.db"
    db.init_db(p)
    return p


def _set(p: Path, key: str, value: str) -> None:
    c = db.get_connection(p)
    db.set_setting(c, key, value)
    c.close()


def _files(d: Path, names, base=1_000_000):
    out = []
    for i, n in enumerate(names):
        f = d / n
        f.write_bytes(b"x")
        os.utime(f, (base + i * 100, base + i * 100))     # 越後面越新
        out.append(f)
    return out


# ---------------------------------------------------------------- 1. 每日 DB 備份
def test_DB備份預設保留3份(tmp_path):
    fs = _files(tmp_path, [f"asset_2026010{i}_000000.db" for i in range(1, 7)])
    removed = backup.prune_old_backups(tmp_path, datetime.now())
    assert backup.KEEP_LAST == 3
    assert sorted(removed) == fs[:3] and all(f.exists() for f in fs[3:])


def test_DB備份空目錄與只有1份都不刪(tmp_path):
    assert backup.prune_old_backups(tmp_path, datetime.now(), 1) == []
    f = _files(tmp_path, ["asset_20260101_000000.db"])[0]
    assert backup.prune_old_backups(tmp_path, datetime.now(), 1) == [] and f.exists()


def test_DB備份保留份數讀系統設定_run_backup用被備份的庫(tmp_path):
    p = _db(tmp_path)
    _set(p, retention.DB_BACKUP_KEEP_KEY, "2")
    bdir = tmp_path / "backups"
    bdir.mkdir()
    olds = _files(bdir, [f"asset_2026010{i}_000000.db" for i in range(1, 5)])
    r = backup.run_backup(p, bdir, datetime(2026, 11, 26, 2, 0, 0))
    assert r.ok, r.error
    left = sorted(x.name for x in bdir.glob("*.db"))
    assert len(left) == 2 and r.path.name in left and olds[3].name in left


def test_DB備份設定壞值或超出範圍_退回預設3(tmp_path):
    p = _db(tmp_path)
    for bad in ("0", "31", "abc", "-1"):
        _set(p, retention.DB_BACKUP_KEEP_KEY, bad)
        assert backup.keep_last_of(p) == 3, bad


def test_DB備份最新一份排程備份仍永遠保留(tmp_path):
    daily = _files(tmp_path, ["asset_20260101_020000.db"])[0]
    _files(tmp_path, [f"pre_deploy_{i}.db" for i in range(4)], base=2_000_000)
    backup.prune_old_backups(tmp_path, datetime.now(), 1)
    assert daily.exists()


def test_排程備份開關關閉_backup_py直接略過(tmp_path):
    p = _db(tmp_path)
    assert backup.scheduled_enabled(p) is True                        # 沒設過＝照舊備份
    _set(p, retention.TIMER_KEY, "0")
    assert backup.scheduled_enabled(p) is False
    env = {**os.environ, "ASSET_DB_PATH": str(p), "ASSET_BACKUP_DIR": str(tmp_path / "bk"), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(ROOT / "APP" / "asset-module" / "backend" / "backup.py")], env=env,
                       capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                       cwd=str(ROOT / "APP" / "asset-module" / "backend"))
    assert r.returncode == 0 and "略過" in r.stdout, r.stdout + r.stderr
    assert not (tmp_path / "bk").exists()


# ---------------------------------------------------------------- 2. timer 決策（deploy.sh）
@pytest.mark.parametrize("setting,state,expect", [
    (None, "enabled", "enable"),
    (None, "not-found", "enable"),           # 新裝
    (None, "disabled", "disable"),           # 有人手動關 → 不重開
    ("0", "enabled", "disable"),             # 畫面關掉
    ("1", "disabled", "enable"),             # 畫面明確打開
])
def test_timer決策(tmp_path, setting, state, expect):
    p = _db(tmp_path)
    if setting is not None:
        _set(p, retention.TIMER_KEY, setting)
    c = db.get_connection(p)
    assert retention.timer_decision(c, state) == expect
    c.close()


def test_timer沒設值且已關閉_寫回0_下次即使enabled也不重開(tmp_path):
    p = _db(tmp_path)
    c = db.get_connection(p)
    assert retention.timer_decision(c, "disabled") == "disable"
    assert db.get_setting(c, retention.TIMER_KEY) == "0"
    assert retention.timer_decision(c, "enabled") == "disable"
    c.close()


def test_timer讀不到庫_已關閉就維持關閉():
    assert retention.timer_decision(None, "disabled") == "disable"
    assert retention.timer_decision(None, "enabled") == "enable"


def test_timer決策CLI(tmp_path):
    p = _db(tmp_path)
    env = {**os.environ, "ASSET_DB_PATH": str(p)}
    py = str(ROOT / "APP" / "asset-module" / "backend" / "retention.py")
    r = subprocess.run([sys.executable, py, "timer-decision", "disabled"], env=env, capture_output=True, encoding="utf-8", timeout=60)
    assert r.stdout.strip() == "disable"
    c = db.get_connection(p)
    assert db.get_setting(c, retention.TIMER_KEY) == "0"
    c.close()
    r = subprocess.run([sys.executable, py, "timer-decision", "disabled"], env={**os.environ, "ASSET_DB_PATH": str(tmp_path / "none.db")},
                       capture_output=True, encoding="utf-8", timeout=60)
    assert r.stdout.strip() == "disable"


def test_deploy_sh_timer狀態要在寫unit之前記_新裝當not_found():
    """審查 H1：寫完 unit 後新裝的機器也回 disabled，若那時才讀狀態，新裝／重裝會被當成手動關掉、每日備份永遠不開。"""
    s = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    pre = s.index('_BK_STATE="not-found"')
    write = s.index("cat > /etc/systemd/system/webit3-backup.timer")
    assert pre < write
    assert s.count("_BK_STATE=\"$(systemctl is-enabled webit3-backup.timer") == 1
    assert s.index("systemctl is-enabled webit3-backup.timer") < write


def test_deploy_sh不再無條件enable備份timer():
    s = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    assert "retention.py\" timer-decision" in s
    i = s.index("systemctl enable --now webit3-backup.timer")
    assert 'if [ "$_BK_DECISION" = "enable" ]; then' in s[i - 200:i], "enable 必須包在決策判斷裡"
    assert s.count("enable --now webit3-backup.timer") == 1


# ---------------------------------------------------------------- 3. 中繼已匯入原始檔
@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "relay_in"
    drop.mkdir()
    db.set_setting(c, ri.DIR_KEY, str(drop))
    ri.ensure(c)
    yield c, drop
    c.close()


def _batch(c, drop: Path, name: str, status: str = "imported", days_old: float = 10, folder: str | None = None):
    d = drop / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"source": "whatsup", "batch_id": name}), encoding="utf-8")
    (d / "x.json").write_text("{}", encoding="utf-8")
    ts = time.time() - days_old * 86400
    for f in (d / "manifest.json", d / "x.json", d):
        os.utime(f, (ts, ts))
    ri._upsert_batch(c, "whatsup", name, status, "schedule", None, "2026-11-01 00:00:00", folder or str(d), "sig")
    c.commit()
    return d


def test_relay只刪已匯入且超過天數的(relay):
    c, drop = relay
    old_ok = _batch(c, drop, "b_old_ok")
    new_ok = _batch(c, drop, "b_new_ok", days_old=2)
    rej = _batch(c, drop, "b_rej", status="rejected")
    fail = _batch(c, drop, "b_fail", status="failed")
    moved = _batch(c, drop, "b_moved", folder=str(drop / "elsewhere"))   # 紀錄裡的資料夾不是這個 → 不刪
    unknown = drop / "b_unknown"
    unknown.mkdir()
    (unknown / "manifest.json").write_text("{}", encoding="utf-8")
    os.utime(unknown / "manifest.json", (1, 1))
    os.utime(unknown, (1, 1))
    (drop / "loose.txt").write_text("x", encoding="utf-8")
    out = ri.purge_imported(c, force=True)
    assert out["removed"] == ["b_old_ok"], out
    assert not old_ok.exists()
    for keep in (new_ok, rej, fail, moved, unknown, drop / "loose.txt"):
        assert keep.exists(), keep


def test_relay天數讀系統設定(relay):
    c, drop = relay
    d = _batch(c, drop, "b5", days_old=5)
    assert ri.purge_imported(c, force=True)["removed"] == []          # 預設 7 天
    db.set_setting(c, retention.RELAY_DAYS_KEY, "3")
    assert ri.purge_imported(c, force=True)["removed"] == ["b5"] and not d.exists()


def test_relay空目錄_沒設資料夾_節流(relay):
    c, drop = relay
    assert ri.purge_imported(c, force=True)["removed"] == []
    assert ri.purge_imported(c)["status"] == "not_due"                 # 6 小時內不重跑
    db.set_setting(c, ri.DIR_KEY, "")
    assert ri.purge_imported(c, force=True)["status"] == "no_dir"


def test_relay名稱不合法不刪(relay):
    c, drop = relay
    _batch(c, drop, "b ok")                                             # 有空白，_NAME_OK 不收
    assert ri.purge_imported(c, force=True)["removed"] == []
    assert (drop / "b ok").exists()


def test_relay正在匯入時不清(relay):
    c, drop = relay
    _batch(c, drop, "b1")
    assert ri._run_lock.acquire(blocking=False)
    try:
        assert ri.purge_imported(c, force=True)["status"] == "busy"
    finally:
        ri._run_lock.release()
    assert (drop / "b1").exists()


# ---------------------------------------------------------------- 4. patch.sh 的 prune 段
sys.path.insert(0, str(ROOT / ".project"))
try:
    import make_patch as mp
except ImportError:  # pragma: no cover - relay 快照沒有 make_patch
    mp = None

bash = shutil.which("bash")


def _bash_ok() -> bool:
    if not bash or mp is None:
        return False
    d = tempfile.mkdtemp()
    try:
        pr = Path(d) / "probe.sh"
        pr.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8", newline="\n")
        r = subprocess.run([bash, str(pr)], capture_output=True, timeout=30)
        return r.returncode == 0 and r.stdout.strip() == b"ok"
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


needs_bash = pytest.mark.skipif(not _bash_ok(), reason="沒有可跑原生路徑腳本的 bash 或沒有 make_patch")
PSTART, PEND = "# ===== 5.9 舊的 patch 備份只留最新 N 份 =====", "# ===== 5.9 end ====="


def _prune_block() -> str:
    s = mp.PATCH_SH
    return s[s.index(PSTART):s.index(PEND)]


@pytest.mark.skipif(mp is None, reason="沒有 make_patch")
def test_patch_sh成功後才清_且有安全網():
    s = mp.PATCH_SH
    blk = _prune_block()
    assert "^patch_backup_[0-9]{8}_[0-9]{6}$" in blk
    assert 'rm -rf -- "${DATA:?}/${name:?}"' in blk
    assert '[ -L "$DATA/$name" ] && continue' in blk
    call = s.index('prune_patch_backups "$(patch_backup_keep)"')
    ok_branch = s.rindex('if [ "$OK" = "1" ] && [ "${DEPLOY_RC:-0}" = "0" ]; then', 0, call)
    fail_branch = s.index('echo "!! 沒有完成', ok_branch)
    assert ok_branch < call < fail_branch, "只能在成功分支呼叫"
    assert "\nelse\n" not in s[ok_branch:call]
    assert s.index("prune_patch_backups()") < s.index("# ===== 5. 讓新程式碼生效 =====")


def _run_prune(tmp_path: Path, names, setting=None, data_override=None, backup_name=None):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    for n in names:
        (data / n).mkdir()
        (data / n / "f").write_text("x", encoding="utf-8")
    dbf = data / "asset.db"
    if setting is not None:
        cn = sqlite3.connect(dbf)
        cn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        cn.execute("INSERT INTO app_settings VALUES ('retention_patch_backup_keep', ?)", (setting,))
        cn.commit()
        cn.close()
    data_s = data.as_posix() if data_override is None else data_override
    script = textwrap.dedent(f"""\
        set -uo pipefail
        DATA="{data_s}"
        DB_FILE="{dbf.as_posix()}"
        VENV_PY="{Path(sys.executable).as_posix()}"
        BACKUP="{(data / backup_name).as_posix() if backup_name else ''}"
        """) + _prune_block() + '\nprune_patch_backups "$(patch_backup_keep)"\necho RC_OK\n'
    sh = tmp_path / "run.sh"
    sh.write_text(script, encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(sh)], capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    return r, data


@needs_bash
def test_patch備份預設留3份_名稱不符不刪(tmp_path):
    names = [f"patch_backup_2026110{i}_120000" for i in range(1, 6)] + ["patch_backup_x", "patch_backup_20261101_1200", "other"]
    r, data = _run_prune(tmp_path, names)
    assert "RC_OK" in r.stdout, r.stdout + r.stderr
    left = sorted(p.name for p in data.iterdir() if p.is_dir())
    assert left == sorted(["patch_backup_20261103_120000", "patch_backup_20261104_120000", "patch_backup_20261105_120000",
                           "patch_backup_x", "patch_backup_20261101_1200", "other"])


@needs_bash
def test_patch備份讀設定_壞值退回3(tmp_path):
    names = [f"patch_backup_2026110{i}_120000" for i in range(1, 6)]
    r, data = _run_prune(tmp_path, names, setting="1")
    assert [p.name for p in data.iterdir() if p.is_dir()] == ["patch_backup_20261105_120000"], r.stdout + r.stderr
    r, data2 = _run_prune(tmp_path / "b", names, setting="99")
    assert len([p for p in data2.iterdir() if p.is_dir()]) == 3


@needs_bash
def test_patch備份空目錄_只有1份(tmp_path):
    r, data = _run_prune(tmp_path, [])
    assert "RC_OK" in r.stdout and "清掉 0 份" in r.stdout
    r, data2 = _run_prune(tmp_path / "b", ["patch_backup_20261101_120000"], setting="1")
    assert (data2 / "patch_backup_20261101_120000").is_dir()


@needs_bash
def test_patch備份DATA為空不刪任何東西(tmp_path):
    r, data = _run_prune(tmp_path, [f"patch_backup_2026110{i}_120000" for i in range(1, 6)], data_override="")
    assert "RC_OK" in r.stdout and "略過" in r.stdout
    assert len(list(data.iterdir())) == 5


@needs_bash
def test_patch備份這次的一定保留(tmp_path):
    names = [f"patch_backup_2026110{i}_120000" for i in range(1, 4)]
    # 這次的 BACKUP 名稱較舊（時鐘回撥）也不可刪
    r, data = _run_prune(tmp_path, names, setting="1", backup_name="patch_backup_20261101_120000")
    left = sorted(p.name for p in data.iterdir() if p.is_dir())
    assert "patch_backup_20261101_120000" in left and "patch_backup_20261103_120000" in left


# ---------------------------------------------------------------- 5. 設定驗證、API、磁碟
def test_設定範圍驗證(tmp_path):
    p = _db(tmp_path)
    c = db.get_connection(p)
    for body in ({"db_backup_keep": 0}, {"patch_backup_keep": 31}, {"relay_in_days": 91}, {"relay_in_days": 0},
                 {"disk_alert_pct": 100}, {"db_backup_keep": "3"}, {"db_backup_keep": True}):
        with pytest.raises(ValueError):
            retention.set_config(c, body)
    cfg, changes = retention.set_config(c, {"db_backup_keep": 30, "patch_backup_keep": 1, "relay_in_days": 90, "disk_alert_pct": 50}, False)
    assert (cfg["db_backup_keep"], cfg["patch_backup_keep"], cfg["relay_in_days"], cfg["disk_alert_pct"]) == (30, 1, 90, 50)
    assert cfg["backup_timer_enabled"] is False and len(changes) == 5
    c.close()


def test_預設值(tmp_path):
    p = _db(tmp_path)
    c = db.get_connection(p)
    cfg = retention.get_config(c)
    assert (cfg["db_backup_keep"], cfg["patch_backup_keep"], cfg["relay_in_days"], cfg["disk_alert_pct"]) == (3, 3, 7, 85)
    assert cfg["backup_timer_enabled"] is True and cfg["backup_timer_decided"] is False
    c.close()


def test_本機磁碟使用率_超過門檻標high(tmp_path):
    rows = retention.local_disk(tmp_path, 99)
    assert rows and all(r["state"] in ("ok", "high") for r in rows)
    rows = retention.local_disk(tmp_path, 0)
    assert any(r["state"] == "high" for r in rows if r["pct"])


def _api(tmp, users):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(p)
    for name, role in users:
        db.create_user(cn, name, auth.hash_password("test-password-123"))
        cn.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    cn.commit()
    cn.close()

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        tok = cl.cookies.get("session_token")
        if tok:
            cl.headers["X-CSRF-Token"] = auth.csrf_token_for(tok)
        return cl
    return client, p, api


def test_API_只有admin能改_範圍檢查_寫稽核():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
        try:
            admin, alice, vera = client("admin"), client("alice"), client("vera")
            for who in (alice, vera):
                assert who.put("/api/backup/retention", json={"db_backup_keep": 4}).status_code == 403
                assert who.get("/api/backup/retention").status_code == 403         # /api/backup/ 前綴＝admin 才能讀
                r = who.get("/api/v2/local-disk")
                assert r.status_code == 200 and r.json()["alert_pct"] == 85 and r.json()["can_edit"] is False
            assert admin.put("/api/backup/retention", json={"db_backup_keep": 31}).status_code == 400
            assert admin.put("/api/backup/retention", json={"relay_in_days": 0}).status_code == 400
            r = admin.put("/api/backup/retention", json={"db_backup_keep": 4, "relay_in_days": 14, "backup_timer_enabled": False})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["db_backup_keep"] == 4 and j["relay_in_days"] == 14 and j["backup_timer_enabled"] is False
            cn = db.get_connection(p)
            row = cn.execute("SELECT username, path FROM activity_log WHERE action = 'backup_retention'").fetchone()
            cn.close()
            assert row and row["username"] == "admin" and "3→4" in row["path"] and "開→關" in row["path"], row
            assert admin.get("/api/backup/status").json()["local"]["retention_keep"] == 4
        finally:
            api.app.dependency_overrides.clear()
