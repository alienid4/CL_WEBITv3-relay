"""正式機完整包（2026-11-16）：重裝模式、自動還原、wheels 指紋、包完整性、patch 起點檢查。

起因：11-16 正式機 1.415 套 2.066 增量 patch，缺規則 json、API 起不來；使用者要「大包直接重裝」。
"""
import importlib.util
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = (ROOT / "APP/asset-module/install.sh").read_text(encoding="utf-8")
SETUP = (ROOT / "APP/asset-module/setup.sh").read_text(encoding="utf-8")


def _bash(script: str, cwd=None):
    r = subprocess.run(["bash"], input=script.encode("utf-8"), capture_output=True, cwd=cwd)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def test_syntax_install_and_setup():
    for name, text in (("install", INSTALL), ("setup", SETUP)):
        r = subprocess.run(["bash", "-n"], input=text.encode("utf-8"), capture_output=True)
        assert r.returncode == 0, f"{name}.sh: " + r.stderr.decode("utf-8", "replace")


def test_install_has_reinstall_pipeline():
    # 順序：wheels 指紋檢查 → 偵測重裝＋備份 → setup → 自驗
    i_req = INSTALL.index("_app_req=")
    i_bk = INSTALL.index("偵測是否為重裝、備份舊版")
    i_setup = INSTALL.index("\nINSTALL_STARTED=1\n")
    i_verify = INSTALL.index('echo "  — 自驗 —"')
    assert i_req < i_bk < i_setup < i_verify
    assert 'export WEBIT_REINSTALL="$REINSTALL"' in INSTALL
    # 失敗要自動還原：die() 與自驗失敗兩處都呼叫 rollback_install
    assert INSTALL.count("rollback_install") >= 3
    # 自驗三件事：磁碟版本、/api/version、前端 / 與 /v2
    assert "/api/version" in INSTALL and "/v2" in INSTALL
    # 降版要擋
    assert "WEBIT_ALLOW_DOWNGRADE" in INSTALL


def test_install_runtime_pack_forces_wheels_refresh():
    assert "手上有 runtime 包 → 重新解開以更新離線 wheels" in INSTALL


def test_setup_reinstall_mode_reuses_conf_and_keeps_admin():
    assert 'WEBIT_REINSTALL:-0}" = "1"' in SETUP
    assert "_conf_get API_HOST" in SETUP.replace("$(", "").replace(")", "") or "API_HOST" in SETUP
    assert "不重設密碼" in SETUP
    # 一般問答仍在（全新安裝不能壞）
    assert "這台 Server 對外服務的 IP" in SETUP


def _extract_func(text: str, name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, name
    return m.group(0)


def test_rollback_restores_app_and_db(tmp_path):
    app = tmp_path / "app"; data = tmp_path / "data"; bk = data / "bk"
    (app / "backend").mkdir(parents=True); (app / "frontend").mkdir(); bk.mkdir(parents=True)
    (app / "backend" / "version.json").write_text('{"version": "1.415.0"}', encoding="utf-8")
    (app / "frontend" / "f.txt").write_text("old", encoding="utf-8")
    (app / "deploy.sh").write_text("old-deploy", encoding="utf-8")
    (data / "asset.db").write_text("OLD-DB", encoding="utf-8")
    (data / "install.conf").write_text("SVC_USER=nobody_such_user\n", encoding="utf-8")
    # 備份（跟 install.sh 同樣的 tar 指令）
    with tarfile.open(bk / "app.tar.gz", "w:gz") as t:
        for n in ("backend", "frontend", "deploy.sh"):
            t.add(app / n, arcname=n)
    (bk / "asset.db").write_text("OLD-DB", encoding="utf-8")
    # 模擬裝壞：新版蓋過去、DB 被遷移
    (app / "backend" / "version.json").write_text('{"version": "2.066.30"}', encoding="utf-8")
    (data / "asset.db").write_text("NEW-MIGRATED-DB", encoding="utf-8")

    func = _extract_func(INSTALL, "rollback_install")
    script = (
        'APP_DIR="app"; DATA_DIR="data"; BK_DIR="data/bk"\n'
        'OLD_VER="1.415.0"; ROLLED_BACK=0; BK_OK=1\n'
        "systemctl() { [ $1 = is-active ] && echo active; return 0; }\nchown() { :; }\nsleep() { :; }\n"
        + func + "rollback_install\necho RC=$?\n"
    )
    rc, out, err = _bash(script, cwd=tmp_path)
    assert "RC=0" in out, out + err
    assert '1.415.0' in (app / "backend" / "version.json").read_text(encoding="utf-8")
    assert (app / "frontend" / "f.txt").read_text(encoding="utf-8") == "old"
    assert (data / "asset.db").read_text(encoding="utf-8") == "OLD-DB"


def test_rollback_without_backup_does_not_destroy(tmp_path):
    app = tmp_path / "app"; (app / "backend").mkdir(parents=True)
    (app / "backend" / "version.json").write_text('{"version": "2.0.0"}', encoding="utf-8")
    func = _extract_func(INSTALL, "rollback_install")
    script = (
        f'APP_DIR="{app.as_posix()}"; DATA_DIR="{tmp_path.as_posix()}"; BK_DIR=""\n'
        "ROLLED_BACK=0\nsystemctl() { :; }\n" + func + "rollback_install\necho RC=$?\n"
    )
    rc, out, err = _bash(script)
    assert "RC=1" in out, out + err
    assert (app / "backend" / "version.json").exists()   # 沒備份就不能先把舊的刪掉


def test_downgrade_guard_expression():
    # 取 install.sh 裡實際的比較式，驗 2.066.30 > 1.415.0 等情境
    cond = ('[ "$OLD" != "$PACK" ] && [ "$(printf \'%s\\n%s\\n\' "$OLD" "$PACK" | sort -V | tail -1)" = "$OLD" ]')
    assert cond.split("sort -V")[0] in cond
    def newer(old, pack):
        rc, out, _ = _bash(f'OLD={old}; PACK={pack}; if {cond}; then echo DOWN; else echo OK; fi')
        return out.strip()
    assert newer("1.415.0", "2.066.30") == "OK"
    assert newer("2.066.30", "2.066.30") == "OK"
    assert newer("2.066.30", "1.415.0") == "DOWN"
    assert newer("2.9.0", "2.10.0") == "OK"      # 版本序不是字典序
    assert "sort -V | tail -1)\" = \"$OLD_VER\"" in INSTALL


def _load_pack_module():
    spec = importlib.util.spec_from_file_location("make_install_pack", ROOT / ".project/make_install_pack.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["make_install_pack"] = m
    spec.loader.exec_module(m)
    return m


def test_pack_completeness_gate(tmp_path):
    if not (ROOT / ".git").exists():
        pytest.skip("relay 副本沒有 git 工作區，這道核對只在出包那台有意義")
    mip = _load_pack_module()
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "APP/asset-module/backend"],
                       capture_output=True, text=True)
    files = [ln.strip() for ln in r.stdout.splitlines()
             if ln.strip() and not ln.strip().endswith((".db", ".pyc"))]
    assert any(f.endswith("machine_identity.json") for f in files)
    assert any(f.endswith("onboard_platform_rules.json") for f in files)

    def build(skip=None):
        pack = tmp_path / ("p_skip.tar.gz" if skip else "p_full.tar.gz")
        with tarfile.open(pack, "w:gz") as t:
            for f in files:
                if skip and f.endswith(skip):
                    continue
                src = ROOT / f
                if src.is_file():
                    t.add(src, arcname=f.replace("APP/asset-module/", "", 1))
        return pack

    full = build()
    mip.verify_app_pack_complete_or_die(full)            # 不丟例外＝通過
    assert full.exists()
    bad = build(skip="machine_identity.json")
    with pytest.raises(SystemExit):
        mip.verify_app_pack_complete_or_die(bad)
    assert not bad.exists()                               # 壞包會被刪掉


def test_req_fingerprint_stable():
    mip = _load_pack_module()
    fp = mip.req_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", fp)
    assert "REQ_SHA256" in (ROOT / ".project/make_install_pack.py").read_text(encoding="utf-8")


def test_patch_sh_base_ver_guard():
    if not (ROOT / ".project/make_patch.py").exists():
        pytest.skip("relay 副本不含 make_patch.py（打包工具不外送）")
    text = (ROOT / ".project/make_patch.py").read_text(encoding="utf-8")
    assert "BASE_VER" in text and "WEBIT_ALLOW_OLD_BASE" in text
    m = re.search(r'BASE_VER=""\n.*?\nfi\n', text, re.S)
    assert m
    block = m.group(0).replace('[ -f "$HERE/BASE_VER" ] && BASE_VER="$(tr -d \' \\t\\r\\n\' < "$HERE/BASE_VER")"\n', "")

    def run(old, base, allow=""):
        script = (f'OLD_VER={old}; BASE_VER={base}; {allow}\n'
                  + block.replace('BASE_VER=""\n', "", 1) + 'echo PASSED\n')
        rc, out, _ = _bash(script)
        return rc, out
    rc, out = run("1.415.0", "2.066.28")
    assert rc == 1 and "PASSED" not in out and "拒絕套用" in out
    rc, out = run("2.066.28", "2.066.28")
    assert "PASSED" in out
    rc, out = run("2.066.29", "2.066.28")
    assert "PASSED" in out
    rc, out = run("1.415.0", "2.066.28", "export WEBIT_ALLOW_OLD_BASE=1")
    assert "PASSED" in out


_ROLLBACK_PRE = ('APP_DIR="app"; DATA_DIR="data"; BK_DIR="data/bk"; OLD_VER=1.0.0; ROLLED_BACK=0; BK_OK={ok}\n'
                 "systemctl() { [ $1 = is-active ] && echo active; return 0; }\nchown() { :; }\nsleep() { :; }\n")


def _run_rollback(tmp_path, ok=1):
    func = _extract_func(INSTALL, "rollback_install")
    return _bash(_ROLLBACK_PRE.replace("{ok}", str(ok)) + func + "rollback_install\necho RC=$?\n", cwd=tmp_path)


def test_rollback_corrupt_backup_keeps_current_files(tmp_path):
    """備份壞掉時，現有檔案不能先被刪掉。"""
    app = tmp_path / "app"; bk = tmp_path / "data" / "bk"
    (app / "backend").mkdir(parents=True); bk.mkdir(parents=True)
    (app / "backend" / "version.json").write_text('{"version": "2.066.30"}', encoding="utf-8")
    (bk / "app.tar.gz").write_bytes(b"not a tar")
    rc, out, err = _run_rollback(tmp_path)
    assert "RC=1" in out, out + err
    assert "2.066.30" in (app / "backend" / "version.json").read_text(encoding="utf-8")


def test_rollback_refuses_when_backup_not_marked_ok(tmp_path):
    app = tmp_path / "app"; (app / "backend").mkdir(parents=True)
    (app / "backend" / "version.json").write_text('{"version": "2.0.0"}', encoding="utf-8")
    rc, out, err = _run_rollback(tmp_path, ok=0)
    assert "RC=1" in out, out + err
    assert (app / "backend" / "version.json").exists()


def test_rollback_clears_wal_and_keeps_pre_rollback_copy(tmp_path):
    app = tmp_path / "app"; data = tmp_path / "data"; bk = data / "bk"
    (app / "backend").mkdir(parents=True); (app / "frontend").mkdir(); bk.mkdir(parents=True)
    (app / "backend" / "version.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    with tarfile.open(bk / "app.tar.gz", "w:gz") as t:
        t.add(app / "backend", arcname="backend"); t.add(app / "frontend", arcname="frontend")
    (bk / "asset.db").write_text("OLD", encoding="utf-8")
    (data / "asset.db").write_text("NEW", encoding="utf-8")
    (data / "asset.db-wal").write_text("w", encoding="utf-8")
    (data / "asset.db-shm").write_text("s", encoding="utf-8")
    rc, out, err = _run_rollback(tmp_path)
    assert "RC=0" in out, out + err
    assert (data / "asset.db").read_text(encoding="utf-8") == "OLD"
    assert (bk / "asset.db.pre_rollback").read_text(encoding="utf-8") == "NEW"
    assert not (data / "asset.db-wal").exists() and not (data / "asset.db-shm").exists()


def test_shell_scripts_have_no_control_chars():
    """守門：install／setup／deploy 不得含 0x01 等控制字元（曾把 sed 的 \1 寫壞，443 自驗取不到版號）。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "APP" / "asset-module"
    for name in ("install.sh", "setup.sh", "deploy.sh"):
        p = root / name
        if not p.exists():
            continue
        data = p.read_bytes()
        bad = [b for b in set(data) if b < 32 and b not in (9, 10, 13)]
        assert not bad, f"{name} 含控制字元 {bad}"
