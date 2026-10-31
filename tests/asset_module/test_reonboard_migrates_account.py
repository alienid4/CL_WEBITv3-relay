"""重新納管＝收集帳號遷移（2026-10-18）。

公司機實例：Linux 重新納管後還是只有舊帳號 webit3scan、沒有 webit3sc。
真因：全域設定 collect_ssh_account 還是舊名，重新納管照設定建帳號 → 永遠換不過來。
使用者決定：「可以先刪掉再加入，因為真的有問題，我本來就有 sysinfra 可以進去」。
做法（同一次納管）：建 webit3sc（全套設定）→ 主機上驗新帳號 → 驗過才備份並移除 webit3scan；
收集器再用新帳號實際連一次，連得上才把這台的收集帳號記成 webit3sc。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import account_export as ax  # noqa: E402
import collect_account_migration as cam  # noqa: E402
import db  # noqa: E402
import manage_state as ms  # noqa: E402
import onboard_engine as oe  # noqa: E402

PUB = "ssh-ed25519 AAAATESTKEY webit3"


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def _legacy_setting(c):
    """公司機／221 的現況：全域設定還是舊名。"""
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('collect_ssh_account', ?)",
              (ms.LEGACY_LINUX_ACCOUNT,))
    c.commit()


# ═══ 1. 建哪個帳號：舊名一律換新名 ════════════════════════════════════

def test_設定是舊名_納管仍建新帳號(conn):
    """舊版（v1.463.0）：照設定建 webit3scan → 永遠換不過來。"""
    _legacy_setting(conn)
    assert ms.get_collect_account(conn, "linux") == "webit3scan"       # 收集仍照舊（還沒遷移的機器）
    assert ms.onboard_account(conn, "linux") == ms.READONLY_ACCOUNT    # 納管建新名
    assert ms.onboard_account(conn, "aix") == ms.READONLY_ACCOUNT      # AIX 本來就是新名（no-op）


def test_腳本與劇本拿到舊名也建新名():
    for body in (oe.build_script("linux", PUB, "192.0.2.1", "webit3scan"),
                 oe.build_linux_script(PUB, "192.0.2.1", "webit3scan")):
        assert 'ACCOUNT="webit3sc"' in body and 'ACCOUNT="webit3scan"' not in body
    yaml = pytest.importorskip("yaml")
    pb = yaml.safe_load(oe.build_linux_playbook(PUB, "192.0.2.1", "webit3scan"))
    assert pb[0]["vars"]["webit3_account"] == "webit3sc"


def test_bundle_用新帳號(conn, monkeypatch):
    _legacy_setting(conn)
    import onboard_bundle

    got = {}
    monkeypatch.setattr(onboard_bundle, "build_bundle", lambda *a, **k: got.update(a=a, k=k) or "x")
    monkeypatch.setattr(oe, "collector_pubkey", lambda: PUB)
    monkeypatch.setattr(oe, "resolve_collector_ip", lambda c: "192.0.2.1")
    onboard_bundle.bundle_for(conn)
    assert got["a"][2] == ms.READONLY_ACCOUNT


# ═══ 2. 換帳號那一段：同一份、驗過才刪、有備份 ══════════════════════════

def test_遷移段_同一份嵌進腳本與劇本_且無Jinja記號():
    frag = oe.legacy_migration_sh()
    sh = oe.build_linux_script(PUB, "192.0.2.1")
    head = frag.splitlines()[1]                     # for OLD in webit3scan; do
    assert head in sh
    # 放在帳號／金鑰／sudoers／AllowUsers 都佈完之後、「完成。」之前（with_log 只把 echo "[+] 改成 say）
    assert sh.index(head) > sh.index('acl_fix_user "$ACCOUNT"') and sh.index(head) < sh.index('echo "完成。')
    assert not any(t in frag for t in ("{{", "{%", "{#"))
    yaml = pytest.importorskip("yaml")
    pb = yaml.safe_load(oe.build_linux_playbook(PUB, "192.0.2.1"))
    task = pb[0]["tasks"][-1]
    assert "legacy" in task["register"] and frag.strip().splitlines()[0] in task["ansible.builtin.shell"]["cmd"]
    # 刪之前一定先驗：userdel 在「驗不過就 continue」之後；有備份到 /var/backups/webit3
    assert frag.index("userdel") > frag.index("continue\n  fi\n  BKL=")
    assert "/var/backups/webit3" in frag and "legacy_${OLD}_sudoers" in frag
    assert ms.LEGACY_LINUX_ACCOUNT in frag
    # AIX 不碰（本來就是 webit3sc）
    assert frag not in oe.build_aix_script(PUB, "192.0.2.1")


_BASH = shutil.which("bash")


def _p(path) -> str:
    """Git Bash（Windows）認的路徑：C:/x → /c/x（不然 getent 那行的冒號會切錯欄）。"""
    s = Path(path).as_posix()
    return f"/{s[0].lower()}{s[2:]}" if len(s) > 1 and s[1] == ":" else s


def _run_frag(tmp_path, *, pubkey_in_new=True, sudo_has_helper=True, allowusers=""):
    """用假指令（shell function）跑遷移段：不碰真帳號，只看它決定刪不刪、備份了什麼。"""
    home_new, home_old, bk = tmp_path / "new", tmp_path / "old", tmp_path / "bk"
    for h in (home_new, home_old):
        (h / ".ssh").mkdir(parents=True)
        os.chmod(h, 0o700)
        os.chmod(h / ".ssh", 0o700)
    (home_new / ".ssh" / "authorized_keys").write_text(
        (f'from="192.0.2.1" {PUB}\n' if pubkey_in_new else "ssh-rsa OTHER\n"), encoding="utf-8")
    (home_old / ".ssh" / "authorized_keys").write_text(f"{PUB}\n", encoding="utf-8")
    sudoers_d = tmp_path / "sudoers.d"
    sudoers_d.mkdir()
    (sudoers_d / "webit3scan").write_text("webit3scan ALL=(root) NOPASSWD: /x\n", encoding="utf-8")
    sshd = tmp_path / "sshd"
    sshd.write_text(f"#!/bin/sh\nprintf '%s\\n' '{allowusers}'\n", encoding="utf-8")
    os.chmod(sshd, 0o755)
    frag = oe.legacy_migration_sh().replace("/etc/sudoers.d/", f"{_p(sudoers_d)}/")
    gone = tmp_path / "gone"
    stub = f"""
set -euo pipefail
ACCOUNT=webit3sc; PUBKEY='{PUB}'; HELPER=/usr/local/libexec/webit3/account-facts
COLLECTOR_IPS=192.0.2.1; BK='{_p(bk)}'; SSHD_BIN='{_p(sshd)}'
id() {{ if [ "$1" = webit3scan ] && [ -f '{_p(gone)}' ]; then return 1; fi; return 0; }}
getent() {{ case "$2" in
  webit3sc) echo "webit3sc:x:1009:1009:WEBIT3 collector:{_p(home_new)}:/bin/bash" ;;
  webit3scan) echo "webit3scan:x:1008:1008:WEBIT3 collector:{_p(home_old)}:/bin/bash" ;; esac; }}
stat() {{ echo webit3sc; }}
sudo() {{ {'echo "(root) NOPASSWD: $HELPER"' if sudo_has_helper else 'echo "(root) NOPASSWD: /bin/true"'}; }}
pkill() {{ return 0; }}
install() {{ mkdir -p "${{@: -1}}"; }}   # Windows Git Bash 改不了權限；真機用真的 install
sleep() {{ return 0; }}
userdel() {{ echo "userdel $*" >> '{_p((tmp_path / "calls"))}'; : > '{_p(gone)}'; }}
{frag}
"""
    r = subprocess.run([_BASH, "-c", stub], capture_output=True, text=True, encoding="utf-8")
    calls = (tmp_path / "calls").read_text(encoding="utf-8") if (tmp_path / "calls").exists() else ""
    return r, calls, bk, sudoers_d


@pytest.mark.skipif(not _BASH, reason="沒有 bash")
def test_新帳號驗過_舊帳號備份後移除(tmp_path):
    r, calls, bk, sd = _run_frag(tmp_path)
    assert r.returncode == 0, r.stderr
    assert oe.LEGACY_REMOVED_MARK in r.stdout and "userdel -r webit3scan" in calls
    assert (bk / "legacy_webit3scan_sudoers").exists()
    assert (bk / "legacy_webit3scan_authorized_keys").exists()
    assert not (sd / "webit3scan").exists()
    restore = (bk / "restore.sh").read_text(encoding="utf-8", errors="replace")
    assert "useradd" in restore and "legacy_webit3scan_sudoers" in restore


@pytest.mark.skipif(not _BASH, reason="沒有 bash")
@pytest.mark.parametrize("kw,why", [
    ({"pubkey_in_new": False}, "收集公鑰不在"),
    ({"sudo_has_helper": False}, "sudo 白名單沒生效"),
    ({"allowusers": "allowusers sysinfra"}, "AllowUsers 沒放行"),
])
def test_新帳號驗不過_舊帳號不刪(tmp_path, kw, why):
    r, calls, _bk, sd = _run_frag(tmp_path, **kw)
    assert r.returncode == 0, r.stderr
    assert oe.LEGACY_KEPT_MARK in r.stdout and why in r.stdout
    assert calls == "" and (sd / "webit3scan").exists()


# ═══ 3. 收集器那端：用新帳號實際連一次，連得上才記成新帳號 ══════════════

def _host(c, serial="H-1", ip="10.99.0.7"):
    db.insert_hardware(c, asset_serial=serial, hostname=serial, ip=ip, os="Rocky Linux 9")
    c.commit()


def test_收集器連得上新帳號_這台記成新帳號(conn):
    _legacy_setting(conn)
    _host(conn)
    seen = []
    st = cam.settle_after_onboard(conn, "10.99.0.7", "linux",
                                  _probe=lambda ip, a: seen.append(a) or (True, None))
    assert st["ok"] and seen == [ms.READONLY_ACCOUNT]
    assert cam.account_for_host(conn, "H-1", "linux") == ms.READONLY_ACCOUNT
    assert ms.account_for_ip(conn, "10.99.0.7", "linux") == ms.READONLY_ACCOUNT


def test_收集器連不上新帳號_不改紀錄_講清楚(conn):
    _legacy_setting(conn)
    _host(conn)
    st = cam.settle_after_onboard(conn, "10.99.0.7", "linux",
                                  _probe=lambda ip, a: (False, "Permission denied (publickey)."))
    assert st["ok"] is False and "restore.sh" in st["error"] and "Permission denied" in st["error"]
    assert cam.account_for_host(conn, "H-1", "linux") == "webit3scan"


def test_AIX_Windows_不換(conn):
    for plat in ("aix", "windows"):
        assert cam.settle_after_onboard(conn, "10.99.0.7", plat, _probe=lambda *a: 1 / 0)["ok"] is None


def test_試連狀態照每台的帳號(conn, monkeypatch):
    """refresh_collect_status（已納管判定）不可以再讀全域設定——遷移過的那台舊帳號已刪。"""
    _legacy_setting(conn)
    _host(conn)
    conn.execute("UPDATE hardware SET collect_account = ? WHERE asset_serial = 'H-1'", (ms.READONLY_ACCOUNT,))
    conn.commit()
    used = []
    monkeypatch.setattr(ms, "local_ips", lambda: set())
    monkeypatch.setattr(ms, "probe_collect", lambda ip, kp, account=None, runner=None: used.append(account) or (True, None))
    ms.refresh_collect_status(conn, only_ip="10.99.0.7")
    assert used == [ms.READONLY_ACCOUNT]


def test_沒有收集器再寫死舊帳號():
    """grep：backend 程式（不含註解／說明字串）不可以再把 webit3scan 當收集帳號用。"""
    import re

    allowed = {"manage_state.py", "account_collector.py", "routers/accounts.py"}
    bad = []
    be = ROOT / "APP" / "asset-module" / "backend"
    for p in be.rglob("*.py"):
        rel = p.relative_to(be).as_posix()
        for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = ln.split("#", 1)[0]
            if re.search(r"""["']webit3scan["']""", code) and rel not in allowed:
                bad.append(f"{rel}:{n}: {ln.strip()}")
    assert not bad, bad


# ═══ 4. 帳號盤點：收集帳號不是「真人」══════════════════════════════════

@pytest.mark.parametrize("name", ["webit3sc", "webit3scan"])
def test_收集帳號分類是服務_匯出type5(name):
    acc = {"username": name, "uid": 1008, "shell": "/bin/bash", "gecos": "E123-someone_dept_sys"}
    assert ac.classify_account(acc, {}, set()) == "service"
    assert ax.classify_type({**acc, "kind": "service"}) == 5


def test_收集帳號名單跟常數一致():
    assert ac.OUR_COLLECT_ACCOUNTS == {ms.READONLY_ACCOUNT, ms.LEGACY_LINUX_ACCOUNT}


def test_備註寫收集帳號也算服務():
    acc = {"username": "scanbot", "uid": 1500, "shell": "/bin/bash", "gecos": "WEBIT3 collector"}
    assert ac.classify_account(acc, {}, set()) == "service"
