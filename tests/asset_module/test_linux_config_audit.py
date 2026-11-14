"""Q84 Linux 一鍵組態檢核＋納管後自動收集＋VMware（ESXi／vCenter）天條擴大（2026-10-18）。

守的重點：
1. 解析：去識別化的真實 Linux 樣本（61 條、上游舊版無收尾）→ 照實標「無完成標記」；
   本專案版本戳（WEBIT-RHEL-）沒有收尾 → 判「截斷」拒收；有收尾且條數對得上 → 完整。
2. 腳本：bash -n、LF、版本戳、無公司識別字、只用一條固定 sudo、不改系統；實跑一次產 61 條＋收尾。
3. 唯讀輔助程式 `rebuild-facts config-audit`：sudoers 一條固定子指令、無萬用字元；空白密碼只回帳號名。
4. 一鍵收集：Linux 走 fcb_rhel、SSH 全部是假的；Windows 講清楚不支援；VMware（含 vCenter）拒絕。
5. 納管後自動收集：OS 重建＋組態檢核都排進去；VMware／Windows 略過不算失敗；一樣失敗不拖垮其他樣。
6. vCenter（VCSA，Photon）：探測認得出來 → 不納管；腳本／playbook 開頭自擋；os_class 候選排除。

全部不連任何主機。
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import config_audit as ca  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
import onboard_eligibility as oe  # noqa: E402
import onboard_engine as eng  # noqa: E402
import os_rebuild_collector as R  # noqa: E402
import post_onboard  # noqa: E402

SCRIPT = ROOT / "APP" / "asset-module" / "scripts" / "fcb_rhelV001.sh"
FIXTURE = Path(__file__).parent / "fixtures" / "config_audit" / "fcb_rhel_sample_sanitized.txt"
PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"
COLLECTOR = "10.99.0.221"


def _conn(tmp, **hw):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    base = dict(asset_serial="L-1", hostname="demo-rhel01", ip="10.99.198.11",
                os="Red Hat Enterprise Linux 9.8 (Plow)", environment="測試", asset_status="使用中")
    base.update(hw)
    db.insert_hardware(c, **base)
    c.commit()
    return c


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _as_webit(text: str, *, summary: bool) -> str:
    """把樣本改成本專案腳本的版本戳；summary=True 時補上收尾兩行。"""
    t = text.replace("腳本版本: SV.202606290800", "腳本版本: WEBIT-RHEL-V001 (61 條)")
    if summary:
        t += ("Check ended at: 2026-10-18 10:00:00\n"
              "Check summary: 合計 61 項；Compliant 61；Non-Compliant 0；Not-Applicable 0；Error 0\n")
    return t


# ===== 1. 解析 =====

def test_去識別化樣本_61條_四態計數_舊版無收尾標無完成標記():
    d = ca.parse(_fixture_text().encode("utf-8"))
    assert d["header"]["platform"] == "Linux"
    assert len(d["items"]) == 61
    assert len({i["item_id"] for i in d["items"]}) == 61
    assert ca.tally(d["items"]) == {"Compliant": 61, "Non-Compliant": 0,
                                    "Not-Applicable": 0, "Error": 0}
    assert d["completeness"] == "無完成標記"


def test_改幾條結果_四態各自計數():
    lines = _fixture_text().splitlines()
    swap = {"TWGCB-01-012-0208": "Non-Compliant", "FCB-01-002-0022": "Not-Applicable",
            "TWGCB-01-012-0072": "Error"}
    out = []
    for ln in lines:
        k = ln.split(";", 1)[0]
        if k in swap:
            parts = ln.split(";")
            parts[1] = swap[k]
            ln = ";".join(parts)
        out.append(ln)
    d = ca.parse("\n".join(out).encode("utf-8"))
    assert ca.tally(d["items"]) == {"Compliant": 58, "Non-Compliant": 1,
                                    "Not-Applicable": 1, "Error": 1}


def test_本專案版本戳_沒收尾就是截斷_拒收():
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            with pytest.raises(ValueError, match="截斷"):
                ca.import_report(c, _as_webit(_fixture_text(), summary=False).encode("utf-8"), "x.txt")
            r = ca.import_report(c, _as_webit(_fixture_text(), summary=True).encode("utf-8"), "y.txt")
            assert r["completeness"] == "完整" and r["total"] == 61
            assert r["asset_serial"] == "L-1"          # 去識別化的主機名／IP 對得回清冊
        finally:
            c.close()


def test_本專案版本戳_收尾條數對不上也是截斷():
    t = _as_webit(_fixture_text(), summary=True)
    t = "\n".join(ln for ln in t.splitlines() if not ln.startswith("FCB-01-002-0026")) + "\n"
    assert ca.parse(t.encode("utf-8"))["completeness"] == "截斷"


def test_沒有root事實_提示權限不足不是不合規():
    h = {"platform": "Linux", "runas": "webit3scan(uid=1001) 特權事實來源=none",
         "script_version": "WEBIT-RHEL-V001"}
    notes = ca.integrity_notes(h, {"Compliant": 40, "Error": 16}, "完整")
    assert any("重新納管" in n["text"] for n in notes)


# ===== 2. 腳本本身 =====

def test_腳本_LF_版本戳_收尾():
    raw = SCRIPT.read_bytes()
    assert b"\r\n" not in raw, "腳本要 LF（.gitattributes *.sh eol=lf）"
    text = raw.decode("utf-8")
    assert re.search(r'^SCRIPTV="WEBIT-RHEL-V\d{3}', text, re.M)
    assert "WEBIT-RHEL-" in ca._GUARANTEES_SUMMARY_VERSIONS
    assert "Check summary: 合計" in text and "Check ended at:" in text


def test_腳本_bash語法():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    r = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr


def test_腳本_條號與上游樣本一字不差():
    text = SCRIPT.read_text(encoding="utf-8")
    ids = set(re.findall(r"\b((?:TWGCB-01-0(?:12|08)|FCB-01-002)-\d{4})\b", text))
    # 標準設定值文字裡會引用 0149／0156 自己的條號，那不影響集合
    want = {ln.split(";", 1)[0] for ln in _fixture_text().splitlines()
            if re.match(r"^(TWGCB|FCB)-\d", ln)}
    assert len(want) == 61
    assert ids == want


def test_腳本_唯讀_只用一條固定sudo():
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    sudo_calls = re.findall(r"\bsudo\s+-[^\n]*", code)
    assert len(sudo_calls) == 1, sudo_calls
    assert sudo_calls[0].startswith('sudo -n "$HELPER" config-audit 2>/dev/null)'), sudo_calls
    assert 'HELPER=/usr/local/libexec/webit3/rebuild-facts' in code
    # 不改系統：沒有這些會寫入／改設定的動作
    for bad in (r"sed\s+-i", r"systemctl\s+(start|stop|restart|enable|disable|mask|unmask)\b",
                r"(?m)^\s*(useradd|usermod|userdel|chpasswd|chown|dnf|yum|authselect\s+select)\b",
                r"\$\(\s*(useradd|usermod|chown|dnf|yum)\b", r">\s*/etc/", r"\brm\s+-"):
        assert not re.search(bad, code), bad
    # chmod 只准動自己的輸出檔
    assert re.findall(r"\bchmod\b[^\n]*", code) == ['chmod 644 "$OUT" 2>/dev/null']


def test_腳本與樣本_沒有公司識別字():
    pats = [(r"\b10\.9[0-8]\.\d{1,3}\.\d{1,3}\b", "公司網段"),
            (r"ISD-\d", "內部辦法編號"), (r"10\.9\"", "寫死網段前綴"),
            (r"egrep \"10\.", "寫死網段前綴")]
    try:
        sys.path.insert(0, str(ROOT / ".project"))
        from desensitize_rules import RESIDUAL_PATTERNS
        pats += list(RESIDUAL_PATTERNS)
    except ImportError:          # 公開 relay 不帶這支規則檔
        pass
    for f in (SCRIPT, FIXTURE):
        text = f.read_text(encoding="utf-8")
        for pat, why in pats:
            assert not re.search(pat, text), f"{f.name}：{why} {pat}"


def test_腳本實跑一次_61條加收尾_可匯入(tmp_path):
    """在本機（CI 的 Linux）實跑：唯讀、非 root、沒有輔助程式 → root 項目應為 Error，不是 Non-Compliant。"""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    import os
    env = dict(os.environ, FCBRHEL_OUTDIR=str(tmp_path))
    r = subprocess.run([bash, str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=240)
    outs = list(tmp_path.glob("FCB_*.txt"))
    assert len(outs) == 1, r.stderr[-500:]
    d = ca.parse(outs[0].read_bytes())
    assert len(d["items"]) == 61
    assert d["completeness"] == "完整"
    by = {i["item_id"]: i for i in d["items"]}
    if os.name == "posix" and os.geteuid() != 0 and "特權事實來源=none" in (d["header"].get("runas") or ""):
        # 拿不到 root 事實時，0072（/etc/shadow）一定是 Error＋權限不足，不可以被判成不合規
        assert by["TWGCB-01-012-0072"]["result"] == "Error"
        assert "權限不足" in by["TWGCB-01-012-0072"]["current"]


# ===== 3. 唯讀輔助程式 config-audit =====

def test_sudoers只多一條固定子指令_無萬用字元():
    specs = R.rebuild_sudoers_specs()
    assert f"{R.REBUILD_HELPER_PATH} config-audit" in specs
    assert "config-audit" in R.HELPER_SUBCOMMANDS
    assert not any("*" in s for s in specs)
    # 納管產出的 shell 與 playbook 都帶這一行
    sh = eng.build_linux_script(PUBKEY, COLLECTOR)
    assert f"NOPASSWD: {R.REBUILD_HELPER_PATH} config-audit" in sh


def test_輔助程式_空白密碼只回帳號名_不回雜湊():
    body = R.REBUILD_HELPER_SCRIPT[R.REBUILD_HELPER_SCRIPT.index("config-audit)"):]
    body = body[:body.index(";;\n  *)")]
    assert "awk -F: '$2==\"\"{print $1}' /etc/shadow" in body
    assert not re.search(r"(cat|head|base64)[^\n]*/etc/shadow", body)
    assert "print $2" not in body
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert "base64" not in code, "目標機上不做編碼／解碼（SOC 樣式）"


def test_腳本解析輔助程式輸出_內容與離開碼都對(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    text = SCRIPT.read_text(encoding="utf-8")
    fact_fn = text[text.index("fact() {"):text.index("\nNOPERM=")]
    hf = ("@@C sshd_t 0\n| maxauthtries 3\n| ignorerhosts yes\n@@E sshd_t\n"
          "@@C sudoers_stig 98\n@@E sudoers_stig\n"
          "@@C rules_d 0\n| -w /etc/passwd -p wa -k identity\n| @@E rules_d\n@@E rules_d\n")
    h = tmp_path / "h.sh"
    h.write_text("FACTSRC=helper\nHF=$(cat <<'X'\n" + hf + "X\n)\n" + fact_fn + "\n"
                 'v=$(fact sshd_t); echo "rc=$? [$v]"\n'
                 'v=$(fact sudoers_stig); echo "rc=$? [$v]"\n'
                 'v=$(fact rules_d); echo "rc=$? [$v]"\n'
                 'v=$(fact nope); echo "rc=$? [$v]"\n', encoding="utf-8", newline="\n")
    out = subprocess.run([bash, str(h)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout.splitlines()
    assert out[0] == "rc=0 [maxauthtries 3" and out[1] == "ignorerhosts yes]"
    assert "rc=98 []" in out
    assert any(ln.startswith("rc=0 [-w /etc/passwd") for ln in out)
    assert "@@E rules_d" in "\n".join(out)          # 內容裡的假標記照原樣當內容，沒有提早結束
    assert out[-1] == "rc=96 []"


def test_輔助程式_config_audit實跑_每個鍵都有一行():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, newline="\n",
                                     encoding="utf-8") as f:
        f.write(R.REBUILD_HELPER_SCRIPT)
        p = f.name
    r = subprocess.run([bash, p, "config-audit"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    keys = [ln.split(" ")[1] for ln in r.stdout.splitlines() if ln.startswith("@@C ")]
    ends = [ln.split(" ")[1] for ln in r.stdout.splitlines() if ln.startswith("@@E ")]
    assert ends == keys
    # 內容行一律前綴「| 」，偽造不了標記
    assert all(ln.startswith(("@@C ", "@@E ", "|")) for ln in r.stdout.splitlines())
    assert keys == ["shadow_empty", "ls_auditd_conf", "ls_audit_log", "ls_audit_dir",
                    "ls_audit_rules", "auditd_conf", "auditctl_l", "rules_d", "sudoers_stig",
                    "sshd_t", "sshd_config"]
    # 多給參數仍然拒絕
    assert subprocess.run([bash, p, "config-audit", "/etc/shadow"], capture_output=True).returncode == 2


# ===== 4. 一鍵收集（SSH 全部是假的）=====

def test_linux一鍵_送fcb_rhel腳本_自擋VMware_結果匯入(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
            seen = {}

            def runner(ip, cmd):
                seen["ip"], seen["cmd"] = ip, cmd
                return _as_webit(_fixture_text(), summary=True)
            r = ca.collect_from_host(c, "L-1", runner=runner, by="tester")
            assert seen["ip"] == "10.99.198.11"
            assert "__FCBRHEL_EOF__" in seen["cmd"] and "FCBRHEL_OUTDIR" in seen["cmd"]
            assert "WEBIT-RHEL-V" in seen["cmd"]                 # 腳本內容真的送上去
            assert seen["cmd"].index("VMkernel") < seen["cmd"].index("__FCBRHEL_EOF__")
            assert "/etc/photon-release" in seen["cmd"]          # vCenter 自擋在跑腳本之前
            assert "base64" not in seen["cmd"].split("__FCBRHEL_EOF__")[0]   # 純文字送，不 base64
            assert r["total"] == 61 and r["collected_platform"] == "linux"
            assert r["asset_serial"] == "L-1"
            got = ca.by_asset(c, "L-1")
            assert got["state"] == "有檢核結果" and len(got["items"]) == 61
        finally:
            c.close()


def test_遠端認出VMware就拒收(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
            with pytest.raises(ValueError, match="VMware"):
                ca.collect_from_host(c, "L-1", runner=lambda ip, cmd: ca.VMWARE_MARKER + "\n")
        finally:
            c.close()


@pytest.mark.parametrize("os_text", ["VMware ESXi 7.0.3", "VMware Photon OS 4.0",
                                     "vCenter Server Appliance 8.0", "Photon OS 3.0"])
def test_登記為VMware設備_一鍵收集連SSH都不送(os_text):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp, os=os_text)
        try:
            sent = []
            with pytest.raises(ValueError, match="VMware"):
                ca.collect_from_host(c, "L-1", runner=lambda ip, cmd: sent.append(cmd) or "x")
            assert sent == []
        finally:
            c.close()


def test_windows_不送Linux腳本_走Windows那條(monkeypatch):
    # 2026-10-21 起 Windows 走 WinRM 跑公司基準 286 條（win_fcb）：送出去的是 PowerShell 包裝，不是 bash 腳本
    import win_fcb

    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp, os="Microsoft Windows Server 2019")
        try:
            sent = []
            with pytest.raises(ValueError, match="Windows 組態檢核沒有結果"):
                ca.collect_from_host(c, "L-1", runner=lambda ip, cmd: sent.append(cmd) or "x")
            assert len(sent) == 1 and win_fcb.MARK_RUNAS in sent[0] and "#!/" not in sent[0]
        finally:
            c.close()


def test_客體VM不受影響_RHEL照常收(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp, os="Red Hat Enterprise Linux 9 (64-bit)", device_model="VMware Virtual Platform")
        try:
            monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
            r = ca.collect_from_host(c, "L-1",
                                     runner=lambda ip, cmd: _as_webit(_fixture_text(), summary=True))
            assert r["total"] == 61
        finally:
            c.close()


def test_端點_要登入_且回400講原因(monkeypatch):
    import api
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        c = _conn(tmp, os="Microsoft Windows Server 2019")
        c.close()

        def ov():
            cc = db.get_connection(p)
            try:
                yield cc
            finally:
                cc.close()
        api.app.dependency_overrides[api.get_db] = ov
        try:
            cl = TestClient(api.app)
            assert cl.post("/api/config-audit/collect", json={"asset_serial": "L-1"}).status_code in (401, 403)
            api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester", "role": "admin"}
            import win_onboard

            # 測的是「沒有憑證」那句：主機當成已納管（沒納管就不登入在 test_win_onboard.py 測）
            monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
            r = cl.post("/api/config-audit/collect", json={"asset_serial": "L-1"})
            # 沒有 WinRM 憑證 → 400 講清楚要去設定憑證
            assert r.status_code == 400 and "WinRM 收集憑證" in r.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()


# ===== 5. 納管後自動收集 =====

def _stub_all(monkeypatch, called, raise_job=None):
    import account_inventory
    import health_probe
    import host_spec_collector
    import service_inventory
    import software_inventory

    def mk(job):
        def fake(conn, *a, only_serial=None, trigger=None, **k):
            called.append(job)
            if raise_job == job:
                raise RuntimeError(f"{job} 炸了")
            return {"ok": 1}
        return fake
    monkeypatch.setattr(host_spec_collector, "run_collection", mk("spec"))
    monkeypatch.setattr(service_inventory, "collect_services", mk("service"))
    monkeypatch.setattr(software_inventory, "collect_software", mk("software"))
    monkeypatch.setattr(account_inventory, "collect_accounts", mk("account"))
    monkeypatch.setattr(R, "run_collection", mk("rebuild"))
    # Windows 排程（2026-10-20，WinRM）；Linux 這一樣略過、不呼叫
    import schedule_inventory
    monkeypatch.setattr(schedule_inventory, "collect_windows", mk("schedule"))
    # Windows 的「硬體規格」那一樣改走 WinRM 收主機事實（2026-10-20）
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, *a, only_serial=None, **k: (called.append("spec_facts"),
                                                                  {"updated": 1})[1])

    def fake_ca(conn, serial, runner=None, by=None):
        called.append("config_audit")
        if raise_job == "config_audit":
            raise ValueError("收不到檢核輸出")
        return {"total": 61, "counts": {"Non-Compliant": 2, "Error": 16}}
    monkeypatch.setattr(ca, "collect_from_host", fake_ca)
    monkeypatch.setattr(health_probe, "check_one", lambda *a, **k: {})
    monkeypatch.setattr(manage_state, "probe_uname", lambda *a, **k: None)


def test_納管後_OS重建與組態檢核都排進去(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            called = []
            _stub_all(monkeypatch, called)
            res = post_onboard.run_now(c, "L-1", by="tester")
            assert {"rebuild", "config_audit"} <= set(called)
            by = {r["job"]: r for r in res}
            assert by["rebuild"]["ok"] is True and by["config_audit"]["ok"] is True
            row = c.execute("SELECT ok FROM collect_attempt_log WHERE kind='post_onboard'").fetchone()
            assert row["ok"] == 1
        finally:
            c.close()


@pytest.mark.parametrize("os_text", ["VMware ESXi 8.0", "VMware vCenter Server 8.0 (Photon)"])
def test_納管後_VMware設備_兩樣都略過不算失敗(monkeypatch, os_text):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp, os=os_text)
        try:
            called = []
            _stub_all(monkeypatch, called)
            res = {r["job"]: r for r in post_onboard.run_now(c, "L-1")}
            assert "rebuild" not in called and "config_audit" not in called
            assert res["rebuild"]["skipped"] and res["config_audit"]["skipped"]
            assert "VMware" in res["config_audit"]["message"]
        finally:
            c.close()


def test_納管後_Windows也收組態檢核與OS重建(monkeypatch):
    # 2026-10-21：Windows 跑公司基準 286 條 → 組態檢核照收；OS 重建也有 Windows 版（WinRM）
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp, os="Microsoft Windows Server 2022")
        try:
            called = []
            _stub_all(monkeypatch, called)
            res = {r["job"]: r for r in post_onboard.run_now(c, "L-1")}
            assert "config_audit" in called and res["config_audit"]["ok"] is True
            assert "rebuild" in called and res["rebuild"]["ok"] is True
            assert {"spec_facts", "service", "software", "account"} <= set(called)
            row = c.execute("SELECT ok, message FROM collect_attempt_log WHERE kind='post_onboard'").fetchone()
            assert row["ok"] == 1
        finally:
            c.close()


def test_納管後_組態檢核失敗不拖垮其他樣_且看得到(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            called = []
            _stub_all(monkeypatch, called, raise_job="config_audit")
            res = {r["job"]: r for r in post_onboard.run_now(c, "L-1")}
            assert res["config_audit"]["ok"] is False and "收不到" in res["config_audit"]["message"]
            assert res["rebuild"]["ok"] is True and res["spec"]["ok"] is True
            row = c.execute("SELECT ok, message FROM collect_attempt_log WHERE kind='post_onboard'").fetchone()
            assert row["ok"] == 0 and "組態檢核" in row["message"]
        finally:
            c.close()


def test_排程納管成功的也跑納管後收集_一台失敗不擋下一台(monkeypatch):
    import auto_onboard as ao
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            db.insert_hardware(c, asset_serial="L-2", ip="10.99.198.12", hostname="demo-rhel02",
                               environment="測試", asset_status="使用中")
            c.commit()
            ran = []

            def fake_run_now(conn, serial, by=None, **k):
                ran.append(serial)
                if serial == "L-1":
                    raise RuntimeError("boom")
                return []
            monkeypatch.setattr(post_onboard, "run_now", fake_run_now)
            out = ao.run_post_onboard(c, [
                {"ip": "10.99.198.11", "asset_serial": "L-1", "action": "onboarded"},
                {"ip": "10.99.198.12", "action": "onboarded"},
                {"ip": "10.99.198.13", "action": "failed"}])
            assert ran == ["L-1", "L-2"]
            assert [o["ok"] for o in out] == [False, True]
            row = c.execute("SELECT ok, message FROM collect_attempt_log "
                            "WHERE kind='post_onboard' AND target='L-1'").fetchone()
            assert row["ok"] == 0 and "boom" in row["message"]
        finally:
            c.close()


def test_單台背景收集有並行上限():
    assert post_onboard.MAX_CONCURRENT >= 1
    assert post_onboard._slots._value == post_onboard.MAX_CONCURRENT   # noqa: SLF001


# ===== 6. vCenter（VCSA）天條 =====

RAW_VCSA = "Linux\n0\nHASSUDO\nVMWARE_APPLIANCE\n"


def test_探測指令會問VCSA標記_解析成vcsa():
    for marker in ("/etc/photon-release", "/etc/applmgmt/appliance", "/usr/lib/vmware-vpx", "vpxd"):
        assert marker in eng.PROBE_COMMAND
    assert eng.parse_probe(RAW_VCSA)["os"] == "vcsa"
    assert eng.parse_probe("Linux\n0\nHASSUDO\n")["os"] == "linux"


def test_引擎_探測到VCSA就拒絕_不產腳本不跑執行器():
    calls = []
    r = eng.onboard("10.99.7.21", "linux", "root", "x", COLLECTOR, pubkey=PUBKEY,
                    executor=lambda **kw: calls.append(kw) or eng.OnboardResult(True, "x", "ok"),
                    probe_runner=lambda h, u, p: RAW_VCSA)
    assert r.ok is False and r.stage == eng.ESXI_REFUSED_STAGE
    assert "vCenter" in r.message and calls == []


def test_批次_探測到VCSA不納管也不改密碼():
    onboard_calls, passwd_calls = [], []
    rows = eng.batch_auto_onboard(
        [{"ip": "10.99.7.22"}], "root", ["pwA"], COLLECTOR,
        account_of=lambda p: "webit3scan", pubkey=PUBKEY, unify_password=True,
        probe_runner=lambda h, u, p: RAW_VCSA,
        onboard_executor=lambda **kw: onboard_calls.append(kw),
        passwd_executor=lambda **kw: passwd_calls.append(kw))
    assert rows[0]["onboarded"] is False and "vCenter" in rows[0]["fail_reason"]
    assert onboard_calls == [] and passwd_calls == []


@pytest.mark.parametrize("platform", ["linux", "aix"])
def test_納管腳本開頭就擋VCSA(platform):
    text = eng.build_script(platform, PUBKEY, COLLECTOR)
    gi = text.find("/etc/photon-release")
    assert gi >= 0
    for m in ("useradd", "mkuser", "authorized_keys", "sudoers"):
        j = text.find(m, text.find("ACCOUNT="))
        if j >= 0:
            assert gi < j, m
    assert "exit 3" in eng.ESXI_SCRIPT_GUARD_SH


def test_playbook開頭就擋VCSA():
    text = eng.build_linux_playbook(PUBKEY, COLLECTOR)
    tasks = text[text.index("tasks:"):]
    gi = tasks.find("/etc/photon-release")
    assert 0 <= gi < tasks.find("ansible.builtin.user")


def test_VCSA守門_shell實跑會擋(tmp_path):
    """守門那段 shell 本身要真的能跑：沒有標記時放行（rc 0）。"""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    f = tmp_path / "g.sh"
    f.write_text(eng.ESXI_SCRIPT_GUARD_SH + "\necho PASSED\n", encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(f)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if Path("/etc/photon-release").exists():
        pytest.skip("這台本身就是 Photon")
    assert r.returncode == 0 and "PASSED" in r.stdout


@pytest.mark.parametrize("os_text", ["VMware Photon OS 4.0", "Photon OS 3.0",
                                     "vCenter Server Appliance 8.0", "VCSA 7.0"])
def test_os_class虛擬化平台_候選階段就判為不可納管(os_text):
    got = oe.judge_for_asset(None, {"os": os_text, "hostname": "vc01", "ip": "10.99.7.30"})
    assert got and got[0] == "esxi"


def test_客體VM不被當成VMware設備():
    assert oe.judge_for_asset(None, {"os": "Red Hat Enterprise Linux 9 (64-bit)",
                                     "device_model": "VMware Virtual Platform",
                                     "hostname": "app01", "ip": "10.99.7.31"}) is None


# ===== 7. 自製判定標示（Q84）=====
# 61 條判定是在拿不到官方子腳本下照標準文字重寫的；結果出現的每個地方都要標明。

SELF_MADE = "自製判定 V001（尚未與官方逐條比對）"


def test_自製標示_只認版本戳():
    assert ca.self_made_label("WEBIT-RHEL-V001 (61 條)") == SELF_MADE
    assert ca.self_made_label("WEBIT-RHEL-V002") == "自製判定 V002（尚未與官方逐條比對）"
    assert ca.self_made_label("SV.202606290800") is None          # 上游官方版不標
    assert ca.self_made_label(None) is None
    assert ca.self_made_label("xx WEBIT-RHEL-V001") is None        # 不是開頭就不算


def test_自製標示_清單_明細_單機頁都帶():
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            ca.import_report(c, _as_webit(_fixture_text(), summary=True).encode("utf-8"), "y.txt")
            assert [x["self_made_label"] for x in ca.runs(c)] == [SELF_MADE]
            assert ca.detail(c, ca.runs(c)[0]["id"])["self_made_label"] == SELF_MADE
            assert ca.by_asset(c, "L-1")["run"]["self_made_label"] == SELF_MADE
        finally:
            c.close()


def test_自製標示_上游檔不標():
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            ca.import_report(c, _fixture_text().encode("utf-8"), "up.txt")
            assert ca.runs(c)[0]["self_made_label"] is None
        finally:
            c.close()


def test_自製標示_腳本表頭與下載清單也帶_匯出原文看得到():
    text = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'^SCRIPTV="WEBIT-RHEL-V001[^"]*自製判定，尚未與官方逐條比對', text, re.M)
    import script_downloads
    meta = [m for m in script_downloads.list_scripts() if m["name"] == SCRIPT.name]
    assert meta and meta[0]["self_made_label"] == SELF_MADE


def test_自製標示_前端三處都有顯示():
    fe = ROOT / "APP" / "asset-module" / "frontend" / "pages"
    page = (fe / "config-audit.vue").read_text(encoding="utf-8")
    assert page.count("self_made_label") >= 4                     # 清單、明細、腳本下載、匯出註
    assert "self_made_label" in (fe / "assets" / "[serial].vue").read_text(encoding="utf-8")
