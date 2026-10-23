"""DYN-AUTO-MERGE（決策 DYN2，2026-10-18）：證據確定是同一台的掃描空殼自動併回 CIA 正本。

使用者 2026-10-17（公司機 DYN-<MAC> 空殼＋HW- 正本，IP 換成 10.99.x）：
「確定是同一台就直接合併、畫面只留一台、不要那段黃色說明。」
條件（全部成立才併）：主機名＋IP 相同（IP 非空白／非 0.0.0.0）、CIA 非退役、兩邊都有填的
MAC／序號不衝突（MAC 不是必要）、候選 CIA 全屬同一台、沒被人還原過。其餘留 3-13 需人工。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import dyn_shell  # noqa: E402
import machine_identity  # noqa: E402

IP = "10.99.4.12"
HOST = "secsvr004-012t"
MAC = "00:50:56:ba:fe:76"
SHELL = f"DYN-{MAC}"
CIA = "HW-00012109"


def _conn(tmp, mode=None):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    if mode:
        c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                  (machine_identity.MODE_SETTING, mode))
        c.commit()
        machine_identity.refresh(c)
    return c, p


def _seed(c, *, cia_status="使用中", cia_ip=IP, shell_ip=IP, cia_mac=None, cia_serial=None,
          shell_serial_hw=None, shell_host=HOST, cia_host=HOST):
    db.insert_hardware(c, asset_serial=CIA, hostname=cia_host, ip=cia_ip, asset_name="證券阿發",
                       asset_purpose="阿發 AP 測試", api_id="N-113", environment="測試",
                       asset_status=cia_status, mac=cia_mac, hw_serial=cia_serial,
                       os="Red Hat Enterprise Linux 8.10", os_cia="Windows Server 2019")
    db.insert_hardware(c, asset_serial=SHELL, hostname=shell_host, ip=shell_ip, mac=MAC,
                       hw_serial=shell_serial_hw, is_vm=1, os="RedHat 8.10",
                       remark="來源=dynassets存活掃描")
    sid = c.execute("SELECT id FROM hardware WHERE asset_serial = ?", (SHELL,)).fetchone()[0]
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, "
              "resolved_hardware_id, collected_at) VALUES ('dynassets', ?, ?, 'matched', ?, '2026-10-17 10:00')",
              (shell_ip, f'{{"ip": "{shell_ip}", "hostname": "{shell_host}", "mac": "{MAC}", '
                         f'"os": "RedHat 8.10"}}', sid))
    c.commit()


def _status(c, serial):
    return c.execute("SELECT asset_status FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()[0]


def _merged(c) -> bool:
    """2026-10-20：併回不再把空殼標「停用」（資產狀態只有 CIA 可寫），改成搬進合併紀錄。"""
    import lifecycle

    gone = c.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (SHELL,)).fetchone() is None
    return gone and lifecycle.merged_into_of(c, SHELL) == CIA


def test_同一台_CIA沒有MAC_自動併回_留快照與紀錄_實測不符照樣標示():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c)
            sim = dyn_shell.simulate(c)
            assert sim["merged"] == 1 and dyn_shell.simulate_problems(sim) == []
            assert sim["before"]["off_book_total"] - sim["after"]["off_book_total"] == 1
            assert not _merged(c)                                  # simulate 不碰原庫
            r = dyn_shell.auto_merge(c)
            assert [m["shell_serial"] for m in r["merged"]] == [SHELL]
            assert _merged(c)
            h = dyn_shell.history(c)[0]
            assert h["reason"] == dyn_shell.AUTO_REASON and h["actor"] == dyn_shell.AUTO_ACTOR
            # 證據改掛正本，實測（掃描 OS）與 CIA 登記不符照樣標出來——防私接機沿用舊名＋IP 被藏起來
            hw = c.execute("SELECT * FROM hardware WHERE asset_serial = ?", (CIA,)).fetchone()
            obs = dyn_shell.observed_facts(c, [CIA], hw)
            f = {x["key"]: x for x in obs["fields"]}
            assert obs["via_serial"] == CIA and f["os"]["mismatch"] is True
            # 冪等：再跑一次沒有東西可併
            assert dyn_shell.auto_merge(c)["merged"] == []
            # 可逐筆還原；還原過的不再自動併
            dyn_shell.restore(c, h["id"], "tester")
            assert not _merged(c)
            ok, why = dyn_shell.auto_check(c, dict(c.execute(
                "SELECT * FROM hardware WHERE asset_serial = ?", (SHELL,)).fetchone()))
            assert not ok and "還原" in why
            assert dyn_shell.auto_merge(c)["merged"] == []
        finally:
            c.close()


def test_IP不同不自動併_主機名口徑下也一樣():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp, mode=machine_identity.MODE_HOSTNAME)
        try:
            _seed(c, shell_ip="10.99.4.13")
            assert dyn_shell.auto_merge(c)["merged"] == [] and not _merged(c)
            assert dyn_shell.auto_plan(c)["manual"][0]["shell_serial"] == SHELL
        finally:
            c.close()


def test_IP是0000或空白不自動併():
    for ip in ("0.0.0.0", ""):
        with tempfile.TemporaryDirectory() as tmp:
            c, _ = _conn(tmp, mode=machine_identity.MODE_HOSTNAME)
            try:
                _seed(c, cia_ip=ip, shell_ip=ip)
                assert dyn_shell.auto_merge(c)["merged"] == [] and not _merged(c)
            finally:
                c.close()


def test_CIA那筆退役不併():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c, cia_status="報廢")
            assert dyn_shell.auto_merge(c)["merged"] == [] and not _merged(c)
            assert _status(c, CIA) == "報廢"
        finally:
            c.close()


def test_兩邊都有MAC但不一樣不併():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c, cia_mac="00:50:56:aa:aa:aa")
            assert dyn_shell.auto_merge(c)["merged"] == [] and not _merged(c)
        finally:
            c.close()


def test_兩邊都有硬體序號但不一樣不併_一邊空白照併():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c, cia_serial="SN-AAA", shell_serial_hw="SN-BBB")
            ok, why = dyn_shell.auto_check(c, dict(c.execute(
                "SELECT * FROM hardware WHERE asset_serial = ?", (SHELL,)).fetchone()))
            assert not ok and "硬體序號" in why
            assert dyn_shell.auto_merge(c)["merged"] == []
        finally:
            c.close()
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c, cia_serial=None, shell_serial_hw="SN-BBB")
            assert len(dyn_shell.auto_merge(c)["merged"]) == 1
        finally:
            c.close()


def test_同IP在CIA另有別台的登記不自動併():
    """候選 CIA 不全屬同一台（同 IP 另一個主機名）：留給人判斷。"""
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c)
            db.insert_hardware(c, asset_serial="HW-OTHER", hostname="anotherbox", ip=IP,
                               asset_status="使用中", asset_purpose="另一台")
            c.commit()
            ok, why = dyn_shell.auto_check(c, dict(c.execute(
                "SELECT * FROM hardware WHERE asset_serial = ?", (SHELL,)).fetchone()))
            assert not ok and "別台" in why
            assert dyn_shell.auto_merge(c)["merged"] == []
        finally:
            c.close()


def test_通用主機名不自動併():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c, shell_host="localhost", cia_host="localhost")
            assert dyn_shell.auto_merge(c)["merged"] == []
        finally:
            c.close()


def test_CIA同一台多筆不自動併_也不碰CIA對CIA():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c)
            db.insert_hardware(c, asset_serial="HW-2ND", hostname=HOST, ip=IP, asset_status="使用中",
                               asset_purpose="同一台第二個業務系統")
            c.commit()
            assert dyn_shell.auto_merge(c)["merged"] == []
            assert _status(c, CIA) == "使用中" and _status(c, "HW-2ND") == "使用中"
        finally:
            c.close()


def test_CLI_dry_run不寫_正式跑才併(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, p = _conn(tmp)
        _seed(c)
        c.close()
        monkeypatch.setenv("ASSET_DB_PATH", str(p))
        assert dyn_shell._cli(["auto-merge", "--dry-run"]) == 0
        c = db.get_connection(p)
        assert not _merged(c)
        c.close()
        assert dyn_shell._cli(["auto-merge"]) == 0
        c = db.get_connection(p)
        try:
            assert _merged(c)
        finally:
            c.close()
        assert dyn_shell._cli(["auto-merge"]) == 0                 # 冪等


def test_verify_numbers_自動併回段全過():
    import verify_numbers as vn

    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c)
            before = vn.FAILS
            assert vn.check_dyn_auto_merge(c) == []
            assert vn.FAILS == before
            assert not _merged(c)                                  # verify 只在副本上跑
        finally:
            c.close()


def test_verify_numbers_自動併回模擬沒過只警告不擋部署(monkeypatch):
    """審查 2026-10-18（同 M3）：DYN 模擬沒過不可以讓 [2d] 整個部署中止；[3a2] 本來就會拒寫。"""
    import verify_numbers as vn

    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        try:
            _seed(c)
            real = dyn_shell.simulate

            def broken(conn):
                sim = real(conn)
                sim["left_auto"] = ["DYN-fake"]                    # 不冪等
                sim["preview_total"] += 1                          # 三類加總對不上
                return sim

            before = vn.FAILS
            monkeypatch.setattr(dyn_shell, "simulate", broken)
            bad = vn.check_dyn_auto_merge(c)
            assert len(bad) == 2 and vn.FAILS == before            # ⚠️ 不計 ❌

            def boom(conn):
                raise RuntimeError("模擬壞了")

            monkeypatch.setattr(dyn_shell, "simulate", boom)
            assert vn.check_dyn_auto_merge(c) and vn.FAILS == before
            assert any(r["ok"] is None and "模擬" in r["name"] for r in vn.RESULTS)
        finally:
            c.close()


def test_部署印的分類跟3_13同一份():
    """「留給人工 696 筆」其實全是 CIA 未登記；分開講。"""
    sim = {"planned": 2, "manual": [{"kind": "unregistered"}] * 5 + [{"kind": "review"}] * 3
           + [{"kind": "mergeable"}]}
    assert dyn_shell.summary_line(sim) == "可自動併 2／需人工 4／CIA 待補登 5"
    src = (ROOT / "APP" / "asset-module" / "backend" / "dyn_shell.py").read_text(encoding="utf-8")
    assert "留給人工 {len(" not in src


def test_部署與patch都在台數口徑判定之後跑自動併回():
    dep = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    assert 'dyn_shell.py" auto-merge' in dep
    assert dep.index("machine_identity.py\" decide-verify") < dep.index('dyn_shell.py" auto-merge')
    mp_path = ROOT / ".project" / "make_patch.py"
    if not mp_path.exists():   # 公開 relay 的快照沒有 make_patch.py（只帶 APP／tests）；deploy.sh 那半照驗
        return
    mp = mp_path.read_text(encoding="utf-8")
    assert 'dyn_shell.py" auto-merge' in mp
    assert mp.index("machine_identity.py\" decide-verify") < mp.index('dyn_shell.py" auto-merge')
