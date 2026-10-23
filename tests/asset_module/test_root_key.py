"""決策 ROOTKEY1（2026-10-18，使用者選 A；暫時，待收斂）：沒有可用 sudo 的 Linux 與 AIX，
收集端用一把專用、受限的 root 金鑰收需要 root 的項目。

這裡守的是那幾條「不是選項」的收斂：
  · 只在沒有可用 sudo 的 Linux 分支、以及 AIX；有 sudo 的 Linux 不佈；Windows／VMware 永遠不佈
  · authorized_keys 帶 from=／no-pty／no-*-forwarding／no-user-rc 與辨識註解
  · 全域 PermitRootLogin 不動，只在 sshd_config 最後面加 Match Address 段（其他位址不受影響）
  · 取消納管照標記刪
  · 收集時 root 主機走 root 金鑰、只跑跟 sudo 時同一批固定指令
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state  # noqa: E402
import onboard_engine as eng  # noqa: E402
import os_rebuild_collector as orc  # noqa: E402
import root_key  # noqa: E402

PUB = "ssh-ed25519 AAAATESTKEY webit3 collector"
RPUB = "ssh-rsa AAAAROOTKEY"
IP = "10.99.0.21"
DR = "10.99.0.22"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _lin(**kw):
    return eng.build_linux_script(PUB, IP, root_pubkey=RPUB, extra_ips=kw.get("extra", []))


# ---------------------------------------------------------------------------
# 範圍：只有沒 sudo 的 Linux 分支、AIX
# ---------------------------------------------------------------------------

def test_Linux_root金鑰只在沒有sudo那個分支():
    s = _lin()
    sudo_if = s.index("if command -v sudo >/dev/null 2>&1 && [ -d /etc/sudoers.d ]; then")
    nosudo = s.index(eng.NO_SUDO_MARK)
    rk = s.index("ROOTKEY1（暫時，待收斂）：沒有可用的 sudo")
    assert sudo_if < nosudo < rk, "root 金鑰那段不在「沒有 sudo」分支裡"
    # 有 sudo 的分支（if 到 else 之間）不可以出現 root 金鑰的動作
    sudo_branch = s[sudo_if:nosudo]
    assert root_key.ROOT_KEY_COMMENT not in sudo_branch
    assert "Match Address" not in sudo_branch


def test_AIX一律帶root金鑰段_Windows永遠沒有():
    a = eng.build_aix_script(PUB, IP, root_pubkey=RPUB)
    assert root_key.ROOT_KEY_COMMENT in a and "Match Address" in a
    w = eng.build_windows_script(PUB, IP)
    assert root_key.ROOT_KEY_COMMENT not in w and "Match Address" not in w
    # build_script 對 windows 也不會帶
    assert root_key.ROOT_KEY_COMMENT not in eng.build_script("windows", PUB, IP, root_pubkey=RPUB)


def test_VMware守門在root金鑰之前():
    for s in (_lin(), eng.build_aix_script(PUB, IP, root_pubkey=RPUB)):
        assert s.index("VMkernel") < s.index(root_key.ROOT_KEY_COMMENT)
        assert s.index("/etc/photon-release") < s.index(root_key.ROOT_KEY_COMMENT)
    # 引擎在連線探測就擋掉 VMware，根本不會產腳本
    seen = {}
    r = eng.onboard("h", "linux", "root", "pw", IP, pubkey=PUB,
                    executor=lambda **kw: seen.setdefault("ran", True),
                    probe_runner=lambda *a: "Linux\n0\nNOSUDO\nVMWARE_APPLIANCE\n")
    assert not r.ok and "ran" not in seen


def test_沒有root公鑰或收集器不是IP就只印原因不動手():
    s = eng.build_linux_script(PUB, IP, root_pubkey=None)
    assert root_key.ROOT_KEY_SKIP_MARK in s and "\nMatch Address " not in s
    assert root_key.ROOT_KEY_COMMENT not in s
    s = eng.build_linux_script(PUB, "collector.example.org", root_pubkey=RPUB)
    assert root_key.ROOT_KEY_SKIP_MARK in s and "\nMatch Address " not in s


# ---------------------------------------------------------------------------
# authorized_keys 限制
# ---------------------------------------------------------------------------

def test_root金鑰那一行帶齊限制():
    line = root_key.authorized_keys_line(RPUB, [IP, DR])
    assert line.startswith(f'from="{IP},{DR}",')
    for opt in ("no-pty", "no-port-forwarding", "no-agent-forwarding", "no-X11-forwarding", "no-user-rc"):
        assert opt in line
    assert line.endswith(f"{RPUB} {root_key.ROOT_KEY_COMMENT}")
    assert line in _lin(extra=[DR])


def test_新金鑰是RSA3072不是ed25519(tmp_path):
    kp = str(tmp_path / ".root_collect_key")
    assert root_key.ensure_root_key(kp) is True
    pub = root_key.root_pubkey(kp, generate=False)
    assert pub.startswith("ssh-rsa ")
    from cryptography.hazmat.primitives import serialization
    k = serialization.load_ssh_public_key(pub.encode())
    assert k.key_size == 3072
    # 第二次不覆蓋
    before = Path(kp).read_bytes()
    assert root_key.ensure_root_key(kp) is False
    assert Path(kp).read_bytes() == before


# ---------------------------------------------------------------------------
# sshd：全域不動，只加 Match Address 段
# ---------------------------------------------------------------------------

def _effective(config: str, addr: str, user: str, keyword: str, default: str) -> str:
    """極簡的 `sshd -T -C user=…,addr=…` 模擬：全域取第一個值；Match 段（Address／User 條件全中）
    覆蓋全域（段內也取第一個）。"""
    glob, match_val, cur_match = None, None, None
    for raw in config.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        key, _, val = ln.partition(" ")
        if key.lower() == "match":
            toks = val.split()
            cond = dict(zip(toks[0::2], toks[1::2]))
            cur_match = True
            for k, v in cond.items():
                if k.lower() == "address":
                    cur_match &= addr in v.split(",")
                elif k.lower() == "user":
                    cur_match &= user in v.split(",")
                else:
                    cur_match = False
            continue
        if key.lower() == keyword.lower():
            if cur_match is None and glob is None:
                glob = val.strip()
            elif cur_match and match_val is None:
                match_val = val.strip()
    return match_val or glob or default


def _effective_permitrootlogin(config: str, addr: str) -> str:
    return _effective(config, addr, "root", "PermitRootLogin", "prohibit-password")


def test_Match段只放行收集器位址_全域原樣():
    original = "Port 22\nPermitRootLogin no\nPasswordAuthentication yes\n"
    after = original + root_key.sshd_match_block([IP, DR])
    assert _effective_permitrootlogin(after, IP) == "prohibit-password"
    assert _effective_permitrootlogin(after, DR) == "prohibit-password"
    assert _effective_permitrootlogin(after, "192.0.2.1") == "no", "別的位址被放寬了"
    assert "PermitRootLogin no" in after.split(root_key.SSHD_BEGIN)[0], "全域那行被改了"


def test_Match段只管root_收集器位址的其他帳號照樣能用密碼():
    """2026-10-18 審查：只寫 Match Address 的話 PasswordAuthentication no 會套到收集器位址來的所有帳號
    （收集器是跳板／做 SNAT 時會把別人鎖在外面）。"""
    original = "PermitRootLogin no\nPasswordAuthentication yes\n"
    after = original + root_key.sshd_match_block([IP])
    assert "User root" in root_key.sshd_match_block([IP])
    assert _effective(after, IP, "root", "PasswordAuthentication", "yes") == "no"
    assert _effective(after, IP, "alice", "PasswordAuthentication", "yes") == "yes"
    assert _effective(after, IP, "alice", "PermitRootLogin", "?") == "no"


def test_腳本驗證_收集器位址其他帳號的密碼登入不變():
    s = _lin()
    assert 'rootkey_pw()' in s and '[ "$AFTER_PW" = "$BEFORE_PW" ]' in s


def test_腳本從不把PermitRootLogin設成yes也不改全域那行():
    for s in (_lin(), eng.build_aix_script(PUB, IP, root_pubkey=RPUB)):
        assert not re.search(r"(?im)^\s*PermitRootLogin\s+yes", s)
        assert "sed -i" not in s.split("ROOTKEY1")[1], "不可以用 sed -i 改 sshd_config（AIX 沒有，也會改到全域）"
        # 唯一寫進 sshd_config 的 PermitRootLogin 在 Match 段裡、是 prohibit-password
        lines = [ln for ln in s.splitlines() if re.match(r"^\s*PermitRootLogin\s", ln)]
        assert lines and all("prohibit-password" in ln for ln in lines)
        # 加之前備份、加完 sshd -t 驗證、其他位址要不變、不過就還原
        assert 'cp -p "$SSHD_CONF" "$SSHD_CONF.webit3-$STAMP_RK"' in s
        # Match 段的驗證本身要有 sshd -t（突變測試：拿掉它這條要紅）
        assert 'if "$SSHD_BIN" -t && { [ "$AFTER_COL" = "without-password" ]' in s
        assert '[ "$AFTER_OTHER" = "$BEFORE_OTHER" ]' in s
        assert 'cp -p "$SSHD_CONF.webit3-$STAMP_RK" "$SSHD_CONF"' in s


def test_全域本來就允許root金鑰就不碰sshd():
    s = _lin()
    assert "yes|without-password|prohibit-password)" in s


def test_sshd重新載入不是重啟():
    s = _lin()
    assert "reload" in s and "systemctl restart" not in s and "stopsrc" not in s


# ---------------------------------------------------------------------------
# 取消納管
# ---------------------------------------------------------------------------

def test_取消納管照標記移除root金鑰與Match段():
    for s in (eng.build_linux_revoke_script(PUB), eng.build_aix_revoke_script(PUB)):
        assert f"grep -vF '{root_key.ROOT_KEY_COMMENT}'" in s
        assert root_key.SSHD_BEGIN in s and root_key.SSHD_END in s
        # 先斷 root 存取權（在刪收集帳號之前）
        assert s.index(root_key.ROOT_KEY_COMMENT) < s.index("刪除帳號")


def test_取消納管端點清掉root標記(tmp_path):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="S1", ip=IP)
    root_key.mark_host(conn, IP, True)
    assert root_key.is_root_host(conn, "S1")
    root_key.mark_host(conn, IP, False)
    assert not root_key.is_root_host(conn, "S1")


# ---------------------------------------------------------------------------
# 收集：root 主機走 root 金鑰、同一批固定指令
# ---------------------------------------------------------------------------

def test_root主機的需root收集器選root_其餘不變(tmp_path):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="S1", ip=IP)
    db.insert_hardware(conn, asset_serial="S2", ip="10.99.0.23")
    root_key.mark_host(conn, IP, True)
    conn.commit()
    assert root_key.account_for(conn, "S1", "linux") == "root"
    assert root_key.account_for(conn, "S1", "aix") == "root"
    assert root_key.account_for(conn, "S1", "windows") != "root"
    assert root_key.account_for(conn, "S2", "linux") != "root"


def test_全域收集帳號不可以是root(tmp_path):
    conn = _conn(tmp_path)
    from db import set_setting
    set_setting(conn, "collect_ssh_account", "root")
    set_setting(conn, "collect_ssh_account_aix", "root")
    assert manage_state.get_collect_account(conn, "linux") != "root"
    assert manage_state.get_collect_account(conn, "aix") != "root"


def test_ssh_runner遇到root就用root專用金鑰():
    r = manage_state._ssh_runner("/x/.collector_key", account="root")
    assert r.account == "root" and r.key_path == root_key.key_path()
    assert type(r).__name__ == "RootKeyRunner"
    r2 = manage_state._ssh_runner("/x/.collector_key", account="webit3sc")
    assert type(r2).__name__ == "SSHRunner"


def test_root的sudo_l回答的就是sudo主機那份白名單():
    text = root_key.synthetic_sudo_list()
    specs = orc.parse_sudo_list(text)
    assert specs
    want = [s.replace("\\,", ",") for s in root_key._split_specs(eng.linux_sudo_specs())]
    assert specs == want
    # OS 重建照樣只送清單上的指令
    assert orc.sudo_allows(specs, ["/usr/sbin/dmidecode", "-t", "0"])
    assert not orc.sudo_allows(specs, ["/bin/cat", "/etc/shadow"])


def test_root指令改寫_白名單照跑_其餘變false():
    allowed = root_key.allowed_binaries()
    assert root_key.rewrite_for_root("sudo -n /usr/sbin/dmidecode -t 0 2>/dev/null", allowed) \
        == "/usr/sbin/dmidecode -t 0 2>/dev/null"
    assert root_key.rewrite_for_root("sudo -n ss -tlnp", allowed) == "ss -tlnp"
    assert root_key.rewrite_for_root("sudo -n /bin/cat /etc/shadow", allowed) == "false /bin/cat /etc/shadow"
    assert root_key.rewrite_for_root('x=$(sudo -n passwd -S "$u")', allowed) == 'x=$(passwd -S "$u")'
    assert root_key.rewrite_for_root("sudo -n passwd root", allowed).startswith("false passwd")
    # 內嵌腳本（組態檢核）不改：腳本自己判 id -u
    body = "cat > a <<'__X__'\nsudo -n foo\n__X__\n"
    assert root_key.rewrite_for_root(body, allowed) == body


def test_RootKeyRunner回答sudo_l不連線():
    r = root_key.make_runner()
    out = r("10.99.0.21", "sudo -n -l")
    assert "may run the following commands" in out and r.last == {"rc": 0, "err": ""}
    # 收集端實際送的是 LC_ALL=C 版（2026-10-18），也要由收集端回答、不連線
    assert r("10.99.0.21", orc.SUDO_LIST_CMD) == out


def test_sudoers區塊跟linux_sudo_specs同一份():
    s = eng.build_linux_script(PUB, IP)
    for spec in eng.linux_sudo_specs()[1:]:
        # 動態產生的那幾行用 _sudoers_escape（bash 雙引號裡 \\ 會變成 \），寫死的那幾行原樣
        assert f"NOPASSWD: {eng._sudoers_escape(spec)}" in s or f"NOPASSWD: {spec}" in s, spec


def test_腳本說佈了但root金鑰登不進去就不記(tmp_path):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="S1", ip=IP)
    assert root_key.record_after_onboard(conn, IP, "linux", True, probe=lambda h: False) is False
    assert not root_key.is_root_host(conn, "S1")
    assert root_key.record_after_onboard(conn, IP, "linux", True, probe=lambda h: True) is True
    assert root_key.is_root_host(conn, "S1")
    # Windows 永遠不記
    root_key.mark_host(conn, IP, False)
    assert root_key.record_after_onboard(conn, IP, "windows", True, probe=lambda h: True) is False


def test_組態檢核root遠端登入標已備案例外_原始結果不改(tmp_path):
    import config_audit as ca
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="S1", ip=IP)
    root_key.mark_host(conn, IP, True)
    d = {"asset_serial": "S1", "items": [
        {"result": ca.NON_COMPLIANT, "name": "禁止 root 遠端登入", "standard": "PermitRootLogin no"},
        {"result": ca.NON_COMPLIANT, "name": "密碼長度", "standard": ">= 12"},
    ]}
    ca.annotate_root_key_exception(conn, d)
    assert d["items"][0]["exception"] == ca.ROOT_KEY_EXCEPTION
    assert d["items"][0]["result"] == ca.NON_COMPLIANT
    assert "exception" not in d["items"][1]


def test_接上Match段前先補換行_沒有結尾標記就不動():
    for s in (_lin(), eng.build_aix_script(PUB, IP, root_pubkey=RPUB)):
        assert '[ -n "$(tail -c 1 "$SSHD_CONF")" ]; then echo >> "$SSHD_CONF"; fi' in s
        assert s.index("tail -c 1") < s.index("<<'WEBIT3_SSHD_EOF'")
        assert "有我們的開頭標記卻沒有結尾標記" in s
    for r in (eng.build_linux_revoke_script(PUB), eng.build_aix_revoke_script(PUB)):
        assert "不自動刪（避免刪到檔尾）" in r
        # 先判斷缺結尾標記，才進到 awk 刪除那條路
        assert r.index("避免刪到檔尾") < r.index("awk 'index($0")


def test_restore_sh也會還原root金鑰與sshd與access_conf():
    s = eng.build_linux_script(PUB, IP, root_pubkey=RPUB)
    sec0 = s.split("# ---- 0.", 1)[1].split("useradd", 1)[0]
    for f in ('"$RHOME0/.ssh/authorized_keys" root_authorized_keys.orig',
              "/etc/ssh/sshd_config sshd_config.orig",
              "/etc/security/access.conf access.conf.orig"):
        assert f"backup_one {f}" in sec0, f
    assert "systemctl reload ssh" in sec0
    # 備份要在任何改動之前
    assert s.index("backup_one /etc/ssh/sshd_config") < s.index(root_key.ROOT_KEY_COMMENT + "'")
