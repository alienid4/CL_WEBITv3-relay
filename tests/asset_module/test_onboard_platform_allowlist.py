"""平台白名單（使用者 2026-10-19：「很害怕納管到網路設備，或者是非 Red Hat、非 CentOS、Debian 的設備」；
同日拍板「Alma，Oracle Linux 擋」）。

fail-closed：uname=Linux 的主機，只有 /etc/os-release 的 ID 在 onboard_platform_rules.json 的
allowed_ids（rhel／centos／rocky／debian／ubuntu），且沒有命中任何設備標記，才會納管。
守的路徑：引擎探測（onboard，含重新納管）、批次（batch_auto_onboard）、腳本本身（Linux／AIX 腳本、
webit3_onboard.sh 外層、Ansible playbook）。拒絕一律在任何副作用（產腳本、執行器、改密碼）之前。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_bundle  # noqa: E402
import onboard_engine as eng  # noqa: E402
import onboard_failures as of  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"
COLLECTOR = "10.99.0.221"
HOST = "10.99.7.11"


def _osrel(id_, pretty, like=None, ver="9"):
    s = f'OSREL_ID={id_}\nOSREL_VERSION_ID="{ver}"\nOSREL_PRETTY_NAME="{pretty}"\n'
    if like:
        s += f'OSREL_ID_LIKE="{like}"\n'
    return s


LINUX_HEAD = "Linux\n0\nHASSUDO\n"
RAW = {
    "rhel": LINUX_HEAD + _osrel('"rhel"', "Red Hat Enterprise Linux 9.4 (Plow)", "fedora"),
    "rocky": LINUX_HEAD + _osrel('"rocky"', "Rocky Linux 9.4 (Blue Onyx)", "rhel centos fedora"),
    "centos": LINUX_HEAD + _osrel('"centos"', "CentOS Linux 7 (Core)", "rhel fedora", "7"),
    "debian": LINUX_HEAD + _osrel("debian", "Debian GNU/Linux 12 (bookworm)", ver="12"),
    "ubuntu": LINUX_HEAD + _osrel("ubuntu", "Ubuntu 22.04.4 LTS", "debian", "22.04"),
    "rhel6_legacy": LINUX_HEAD + "LEGACYREL_Red Hat Enterprise Linux Server release 6.10 (Santiago)\n",
    "centos6_legacy": LINUX_HEAD + "LEGACYREL_CentOS release 6.10 (Final)\n",
}
REFUSED_RAW = {
    "almalinux": LINUX_HEAD + _osrel('"almalinux"', "AlmaLinux 9.4 (Seafoam Ocelot)", "rhel centos fedora"),
    "oracle_linux": LINUX_HEAD + _osrel('"ol"', "Oracle Linux Server 8.9", "fedora", "8.9"),
    "ol6_legacy": LINUX_HEAD + "LEGACYREL_Red Hat Enterprise Linux Server release 6.10 (Santiago)\n"
                               "LEGACYDENY=/etc/oracle-release\n",
    "f5_bigip": LINUX_HEAD + _osrel('"centos"', "CentOS Linux 7 (Core)", "rhel fedora", "7")
                + "APPLIANCE_MARKER=f5_bigip\n",
    "nutanix": LINUX_HEAD + _osrel('"centos"', "CentOS Linux 7 (Core)", "rhel fedora", "7")
               + "APPLIANCE_MARKER=nutanix\n",
    "no_os_release": LINUX_HEAD,
    "suse": LINUX_HEAD + _osrel('"sles"', "SUSE Linux Enterprise Server 15 SP5", "suse", "15.5"),
    "rhcos_via_id_like": LINUX_HEAD + _osrel("rhcos", "Red Hat Enterprise Linux CoreOS 414", "rhel fedora"),
    "fedora": LINUX_HEAD + _osrel("fedora", "Fedora Linux 40"),
}


# ===== 純判斷 =====

@pytest.mark.parametrize("name", sorted(RAW))
def test_白名單發行版可以納管(name):
    p = eng.parse_probe(RAW[name])
    assert p["os"] == "linux"
    assert p["refusal"] is None, p["refusal"]


@pytest.mark.parametrize("name", sorted(REFUSED_RAW))
def test_非白名單或設備一律拒絕(name):
    p = eng.parse_probe(REFUSED_RAW[name])
    assert p["os"] == "linux"
    assert p["refusal"] and p["refusal"].endswith("——未納管")


def test_拒絕訊息講出偵測到什麼():
    assert eng.parse_probe(REFUSED_RAW["almalinux"])["refusal"] == \
        "這台是 Linux，但不是允許的發行版（偵測到：AlmaLinux 9.4 (Seafoam Ocelot)）——未納管"
    assert "無 os-release" in eng.parse_probe(REFUSED_RAW["no_os_release"])["refusal"]
    assert eng.parse_probe(REFUSED_RAW["f5_bigip"])["refusal"] == "偵測到設備標記：F5 BIG-IP——未納管"
    assert "Nutanix" in eng.parse_probe(REFUSED_RAW["nutanix"])["refusal"]


def test_AlmaLinux與OracleLinux_使用者拍板要擋():
    rules = eng.load_platform_rules()
    assert rules["allowed_ids"] == ["rhel", "centos", "rocky", "debian", "ubuntu"]
    assert "almalinux" not in rules["allowed_ids"] and "ol" not in rules["allowed_ids"]


def test_AIX_ESXi_VCSA_不受白名單影響():
    aix = eng.parse_probe("AIX\n0\nNOSUDO\n")
    assert aix["os"] == "aix" and aix["refusal"] is None
    assert eng.parse_probe("VMkernel\n0\nHASSUDO\n")["os"] == "esxi"
    vcsa = eng.parse_probe("Linux\n0\nHASSUDO\nVMWARE_APPLIANCE\n" + _osrel("photon", "VMware Photon OS/Linux"))
    assert vcsa["os"] == "vcsa"


def test_PRETTY_NAME含Linux字樣不影響平台與uid判斷():
    p = eng.parse_probe("AIX\n1001\nHASSUDO\nOSREL_PRETTY_NAME=\"Linux 9\"\nOSREL_VERSION_ID=\"9\"\n")
    assert p["os"] == "aix" and p["uid"] == 1001


def test_探測指令只做唯讀測試():
    cmd = eng.PROBE_COMMAND
    assert "/etc/os-release" in cmd and "APPLIANCE_MARKER=" in cmd
    for bad in ("useradd", "mkuser", "sudoers", "authorized_keys", "rm ", "> /", ">>"):
        assert bad not in cmd


def test_設定字元不安全就拒絕載入(tmp_path):
    good = json.loads(eng.PLATFORM_RULES_PATH.read_text(encoding="utf-8"))
    for mutate in (
        lambda c: c["appliance_markers"].append({"id": "x", "name": "a'b", "path": "/x"}),
        lambda c: c["appliance_markers"].append({"id": "x", "name": "n", "path": "/x;rm -rf /"}),
        lambda c: c["appliance_markers"].append({"id": "x", "name": "n", "path": "/x", "contains": "a'b"}),
        lambda c: c.__setitem__("allowed_ids", ["rhel; reboot"]),
        lambda c: c.__setitem__("allowed_ids", []),
    ):
        c = json.loads(json.dumps(good))
        mutate(c)
        f = tmp_path / "r.json"
        f.write_text(json.dumps(c), encoding="utf-8")
        with pytest.raises(ValueError):
            eng.load_platform_rules(f)


def test_設定讀不到_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "PLATFORM_RULES_PATH", tmp_path / "missing.json")
    assert "讀不到" in eng.platform_refusal(eng.parse_probe(RAW["rocky"]))


# ===== 引擎：拒絕在任何副作用之前 =====

def _fake_ssh_layer(monkeypatch, probe_raw):
    sent, side = [], []

    def fake_exec(host, username, password, command, stdin_text=None, timeout=20, **kw):
        sent.append(command)
        return 0, probe_raw

    monkeypatch.setattr(eng, "_ssh_exec", fake_exec)
    monkeypatch.setattr(eng, "_ssh_executor",
                        lambda **kw: side.append("executor") or eng.OnboardResult(True, "execute", "ok", "完成。"))
    monkeypatch.setattr(eng, "build_script", lambda *a, **k: side.append("build_script") or "echo")
    monkeypatch.setattr(eng, "collector_pubkey", lambda *a, **k: side.append("collector_pubkey") or PUBKEY)
    monkeypatch.setattr(onboard_bundle, "build_bundle", lambda *a, **k: side.append("build_bundle") or "echo")
    return sent, side


@pytest.mark.parametrize("name", sorted(REFUSED_RAW))
def test_引擎_拒絕時只送了唯讀探測(monkeypatch, name):
    sent, side = _fake_ssh_layer(monkeypatch, REFUSED_RAW[name])
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR)
    assert r.ok is False and r.stage == eng.PLATFORM_REFUSED_STAGE
    assert r.message.endswith("——未納管")
    assert sent == [eng.PROBE_COMMAND]
    assert side == [], "不准產腳本／讀金鑰／跑執行器"


@pytest.mark.parametrize("name", ["rhel", "rocky", "debian", "ubuntu", "centos6_legacy"])
def test_引擎_白名單照常納管(monkeypatch, name):
    sent, side = _fake_ssh_layer(monkeypatch, RAW[name])
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR)
    assert r.ok is True
    assert "executor" in side


def test_引擎_畫面選aix但實際是不允許的Linux_照樣擋(monkeypatch):
    sent, side = _fake_ssh_layer(monkeypatch, REFUSED_RAW["almalinux"])
    r = eng.onboard(HOST, "aix", "root", "x", COLLECTOR)
    assert r.stage == eng.PLATFORM_REFUSED_STAGE and side == []


def test_引擎_AIX不受影響(monkeypatch):
    sent, side = _fake_ssh_layer(monkeypatch, "AIX\n0\nNOSUDO\n")
    r = eng.onboard(HOST, "aix", "root", "x", COLLECTOR)
    assert r.ok is True and "executor" in side


def test_重新納管_已納管過的主機也照樣拒絕且不收回(monkeypatch):
    """重新納管跟第一次納管走同一個 onboard()；拒絕時只回原因，不呼叫 revoke。"""
    sent, side = _fake_ssh_layer(monkeypatch, REFUSED_RAW["oracle_linux"])
    monkeypatch.setattr(eng, "revoke", lambda *a, **k: side.append("revoke"))
    r = eng.onboard(HOST, "linux", "root", "x", COLLECTOR, account="webit3sc")
    assert r.stage == eng.PLATFORM_REFUSED_STAGE and "Oracle Linux" in r.message
    assert side == []


def test_批次_拒絕時不呼叫onboard也不改密碼():
    def runner(h, u, p):
        return REFUSED_RAW["nutanix"]

    def boom(**kw):
        raise AssertionError("不該執行")

    rows = eng.batch_auto_onboard([{"ip": HOST}], "root", ["A", "B"], COLLECTOR,
                                  lambda p: "webit3sc", pubkey=PUBKEY, unify_password=True,
                                  probe_runner=runner, onboard_executor=boom, passwd_executor=boom)
    assert rows[0]["onboarded"] is False
    assert rows[0]["fail_stage"] == eng.PLATFORM_REFUSED_STAGE
    assert "Nutanix" in rows[0]["fail_reason"]


def test_失敗分類認得白名單拒絕():
    msg = eng.parse_probe(REFUSED_RAW["f5_bigip"])["refusal"]
    assert of.short_reason(eng.PLATFORM_REFUSED_STAGE, msg) == of.SHORT_PLATFORM_BLOCKED
    assert of.short_reason("connect", msg) == of.SHORT_PLATFORM_BLOCKED
    assert of.classify_reason(eng.PLATFORM_REFUSED_STAGE, msg) == of.REASON_NOT_ONBOARDABLE


# ===== 腳本本身的守門（縱深防禦）=====




def _guard_before_changes(text, guard):
    gi = text.find(guard)
    assert gi >= 0, "腳本缺平台白名單守門"
    body = text[text.index("\n") + 1:]          # 跳過檔頭註解第一行
    firsts = [body.find(m) for m in ("useradd ", "mkuser ", "install -d") if body.find(m) >= 0]
    assert firsts and body.find(guard) < min(firsts), "白名單守門必須在任何建帳號／建目錄之前"


@pytest.mark.parametrize("platform", ["linux", "aix"])
def test_shell腳本有白名單守門且在變更之前(platform):
    text = eng.build_script(platform, PUBKEY, COLLECTOR, root_pubkey=None, extra_ips=[])
    _guard_before_changes(text, eng.platform_guard_sh())
    assert text.find(eng.ESXI_SCRIPT_GUARD_SH) < text.find(eng.platform_guard_sh())


def test_bundle外層有白名單守門_且最後一行講停止():
    s = onboard_bundle.build_bundle(PUBKEY, COLLECTOR, root_pubkey=None, extra_ips=[])
    g = eng.platform_guard_sh(result_prefix=onboard_bundle.RESULT_STOP + "：")
    assert g in s
    assert s.index(g) < s.index("WEBIT3_LINUX_BODY")
    assert f"  {eng.PLATFORM_REFUSED_EXIT}) echo \"停止：" in s


def test_playbook有白名單task且在建帳號之前():
    text = eng.build_linux_playbook(PUBKEY, COLLECTOR)
    tasks = text[text.index("tasks:"):]
    assert tasks.index("平台白名單") < tasks.index("建立收集帳號")
    assert "rhel|centos|rocky|debian|ubuntu" in tasks


# ===== 真的跑 shell 守門（Linux 上；221 部署前的全套會跑到）=====

def _sh():
    if os.environ.get("WEBIT_TEST_SH"):        # Windows 本機手動驗：指向 Git Bash 的 sh.exe
        return os.environ["WEBIT_TEST_SH"]
    if sys.platform == "win32":
        return None
    return shutil.which("sh")


def _run_guard(tmp_path, os_release=None, markers=(), legacy=None, deny=False, prefix=""):
    root = tmp_path / "fs"
    (root / "etc").mkdir(parents=True)
    rp = root.as_posix()
    rules = eng.load_platform_rules()
    rules["markers"] = [dict(m, path=rp + m["path"]) for m in rules["markers"]]
    rules["legacy_file"] = rp + "/etc/redhat-release"
    rules["legacy_deny_files"] = [rp + d for d in rules["legacy_deny_files"]]
    for m in rules["markers"]:
        if m["id"] in markers:
            p = Path(m["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"Product: {m['contains']}\n" if m["contains"] else "", encoding="utf-8")
    if os_release is not None:
        (root / "etc" / "os-release").write_text(os_release, encoding="utf-8")
    if legacy:
        Path(rules["legacy_file"]).write_text(legacy + "\n", encoding="utf-8")
    if deny:
        Path(rules["legacy_deny_files"][0]).write_text("x\n", encoding="utf-8")
    g = eng.platform_guard_sh(rules, prefix).replace(
        "/etc/os-release /usr/lib/os-release", f"{rp}/etc/os-release {rp}/usr/lib/os-release")
    script = "uname() { echo Linux; }\n" + g + "\necho PASSED_GUARD\n"
    return _sh_run(script)


def _sh_run(script):
    return subprocess.run([_sh(), "-s"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=20)


@pytest.mark.skipif(_sh() is None, reason="需要 POSIX sh（Linux 上跑）")
@pytest.mark.parametrize("osr,ok", [
    ('ID="rocky"\nPRETTY_NAME="Rocky Linux 9.4"\n', True),
    ('ID="rhel"\nPRETTY_NAME="Red Hat Enterprise Linux 9"\n', True),
    ('ID=debian\nPRETTY_NAME="Debian GNU/Linux 12"\n', True),
    ('ID=ubuntu\nPRETTY_NAME="Ubuntu 22.04"\n', True),
    ('ID="almalinux"\nID_LIKE="rhel centos fedora"\nPRETTY_NAME="AlmaLinux 9"\n', False),
    ('ID="ol"\nPRETTY_NAME="Oracle Linux Server 8.9"\n', False),
    ('ID="sles"\nPRETTY_NAME="SLES 15"\n', False),
    (None, False),
])
def test_shell守門_實跑發行版(tmp_path, osr, ok):
    r = _run_guard(tmp_path, os_release=osr)
    if ok:
        assert r.returncode == 0 and "PASSED_GUARD" in r.stdout, r.stderr
    else:
        assert r.returncode == eng.PLATFORM_REFUSED_EXIT and "PASSED_GUARD" not in r.stdout
        assert "不是允許的發行版" in r.stderr


@pytest.mark.skipif(_sh() is None, reason="需要 POSIX sh（Linux 上跑）")
@pytest.mark.parametrize("marker", ["f5_bigip", "nutanix", "exadata"])
def test_shell守門_實跑設備標記(tmp_path, marker):
    r = _run_guard(tmp_path, os_release='ID="centos"\nPRETTY_NAME="CentOS Linux 7"\n', markers=(marker,))
    assert r.returncode == eng.PLATFORM_REFUSED_EXIT and "偵測到設備標記" in r.stderr


@pytest.mark.skipif(_sh() is None, reason="需要 POSIX sh（Linux 上跑）")
def test_shell守門_舊版RHEL6可以_OL6不行(tmp_path):
    ok = _run_guard(tmp_path, legacy="Red Hat Enterprise Linux Server release 6.10 (Santiago)")
    assert ok.returncode == 0, ok.stderr
    bad = _run_guard(tmp_path / "b", legacy="Red Hat Enterprise Linux Server release 6.10 (Santiago)", deny=True)
    assert bad.returncode == eng.PLATFORM_REFUSED_EXIT


@pytest.mark.skipif(_sh() is None, reason="需要 POSIX sh（Linux 上跑）")
def test_shell守門_bundle外層印停止到stdout(tmp_path):
    r = _run_guard(tmp_path, os_release='ID="ol"\nPRETTY_NAME="Oracle Linux 8"\n', prefix="停止：")
    assert r.returncode == eng.PLATFORM_REFUSED_EXIT
    assert r.stdout.strip().splitlines()[-1].startswith("停止：這台是 Linux")


@pytest.mark.skipif(_sh() is None or os.environ.get("CI_NO_AIX"), reason="需要 POSIX sh")
def test_shell守門_非Linux直接通過(tmp_path):
    script = "uname() { echo AIX; }\n" + eng.platform_guard_sh() + "\necho PASSED_GUARD\n"
    r = _sh_run(script)
    assert r.returncode == 0 and "PASSED_GUARD" in r.stdout


# ===== 審查後補（2026-10-19 獨立審查 D1）=====

@pytest.mark.parametrize("marker", ["vyos", "sonic", "truenas", "proxmox_backup", "openmediavault"])
def test_Debian底的設備靠標記擋(marker):
    raw = LINUX_HEAD + _osrel("debian", "Debian GNU/Linux 12", ver="12") + f"APPLIANCE_MARKER={marker}\n"
    assert "偵測到設備標記" in eng.parse_probe(raw)["refusal"]


def test_一般主機常見的備份代理目錄不當設備標記():
    paths = {m["path"] for m in eng.load_platform_rules()["markers"]}
    assert "/opt/rubrik" not in paths and "/home/cohesity/bin" not in paths


def test_os_release檔在但沒有ID_不走舊版規則():
    """Python 與腳本一致：有 os-release 檔就不看 redhat-release（避免壞掉的 os-release 被舊規則放行）。"""
    raw = LINUX_HEAD + "OSREL_FILE=/etc/os-release\nLEGACYREL_Red Hat Enterprise Linux Server release 6.10\n"
    assert eng.parse_probe(raw)["refusal"]


@pytest.mark.skipif(_sh() is None, reason="需要 POSIX sh（Linux 上跑）")
def test_shell守門_os_release檔在但沒有ID_不走舊版規則(tmp_path):
    r = _run_guard(tmp_path, os_release="", legacy="Red Hat Enterprise Linux Server release 6.10 (Santiago)")
    assert r.returncode == eng.PLATFORM_REFUSED_EXIT


@pytest.mark.skipif(_sh() is None, reason="需要 POSIX sh（Linux 上跑）")
def test_shell守門_重複ID行取第一個(tmp_path):
    r = _run_guard(tmp_path, os_release='ID="rocky"\nID="dup"\nPRETTY_NAME="Rocky Linux 9.4"\n')
    assert r.returncode == 0, r.stderr


def test_探測印出os_release檔位置():
    assert "echo OSREL_FILE=" in eng.PROBE_COMMAND


def test_排程_上次被白名單拒絕的不再每輪去試(tmp_path):
    import auto_onboard as ao
    import db

    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        db.insert_hardware(conn, asset_serial="L-81", ip="192.168.1.81", collect_ok=0,
                           environment="正式", asset_status="使用中")
        conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) "
                     "VALUES ('2026-07-20 01:00:00','192.168.1.81',NULL,1,'22')")
        conn.execute("INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, "
                     "ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("192.168.1.81", "linux", "root", "manual", "t", 0, eng.PLATFORM_REFUSED_STAGE,
                      "偵測到設備標記：F5 BIG-IP——未納管", ""))
        conn.commit()
        ao.save_segment(conn, "192.168.1.")

        def boom(**kw):
            raise AssertionError("不該再去納管")

        r = ao.run_auto_onboard(conn, "10.0.0.221", pubkey=PUBKEY, executor=boom,
                                cred_lookup=lambda ip: ("c", "root", "x"))
        assert r["onboarded"] == 0
        d = [x for x in r["details"] if x["ip"] == "192.168.1.81"][0]
        assert d["action"] == "skipped" and "F5" in d["message"]
    finally:
        conn.close()
