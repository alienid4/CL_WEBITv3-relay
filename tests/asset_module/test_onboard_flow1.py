"""決策 ONBOARD-FLOW1（2026-10-18）：一支、跑一次的 webit3_onboard.sh，測試機與正式機同一套。

A1–A8：誰在跑（root／sudo 免密碼／sudo 要密碼／沒 sudo／VMware／Windows／重跑／sudo 不完整）
B1–B6：主機端坑（AllowUsers／pam_access、密碼效期、SELinux、遞送、主機端紀錄、驗證失敗分類）
外加：網頁一鍵納管跑的是同一份檔案；跑完系統自己偵測、自己觸發納管後六樣收集。

⚠️ 這裡**不實際執行**腳本（部署前的全套測試在 221 以 root 跑，執行它會真的改那台）：
只做文字層檢查＋ sh／dash／ksh 的語法檢查（有裝才做）。
"""
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
FRONT = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import onboard_bundle as ob  # noqa: E402
import onboard_engine as eng  # noqa: E402
import onboard_hostfix as hf  # noqa: E402
import paste_watch  # noqa: E402
import root_key  # noqa: E402

PUB = "ssh-ed25519 AAAATESTKEY webit3 collector"
RPUB = "ssh-rsa AAAAROOTKEY"
IP = "10.99.0.21"


def _bundle(**kw):
    return ob.build_bundle(PUB, IP, "webit3sc", None, root_pubkey=RPUB, extra_ips=kw.get("extra", []))


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


# ---------------------------------------------------------------------------
# 一支檔案：Linux／AIX 自己分流、最後一行一個結果
# ---------------------------------------------------------------------------

def test_一支檔案_自己依uname分流_最後一行一個結果():
    s = _bundle()
    assert s.startswith("#!/bin/sh")
    assert "Linux)\n    bash -s" in s and "AIX)\n    ksh -s" in s
    for line in (f'echo "{ob.RESULT_DONE}"', f'echo "{ob.RESULT_NEED_ROOT}"', f'echo "{ob.RESULT_STOP}：'):
        assert line in s
    # 內容跟目標主機無關：同一個收集器產兩次一模一樣（sha256 才有意義）
    assert ob.sha256_of(s) == ob.sha256_of(_bundle())
    assert "base64 -d" not in s and "base64 --decode" not in s   # 不把編碼過的東西餵給 shell


def _shells():
    return [sh for sh in ("sh", "dash", "ksh") if shutil.which(sh)]


@pytest.mark.parametrize("shell", _shells() or ["none"])
def test_產出的檔案在sh與ksh語法檢查都過(tmp_path, shell):
    if shell == "none":
        pytest.skip("這台沒有 sh／dash／ksh")
    f = tmp_path / ob.FILENAME
    f.write_bytes(_bundle().encode("utf-8"))
    r = subprocess.run([shell, "-n", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    # AIX 那段單獨用 POSIX／ksh 檢查（bash 的語法混進去在 AIX 上會壞）
    body = _bundle().split("<<'WEBIT3_AIX_BODY'\n", 1)[1].split("\nWEBIT3_AIX_BODY\n", 1)[0]
    g = tmp_path / "aix.ksh"
    g.write_bytes(body.encode("utf-8"))
    if shell in ("dash", "ksh"):
        r = subprocess.run([shell, "-n", str(g)], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_AIX那段不用bash專屬語法():
    body = _bundle().split("<<'WEBIT3_AIX_BODY'\n", 1)[1].split("\nWEBIT3_AIX_BODY\n", 1)[0]
    for bad in ("sed -i", "[[ ", "pipefail", "getent ", "install -d", "<(", "local "):
        assert bad not in body, bad


# ---------------------------------------------------------------------------
# A1–A8
# ---------------------------------------------------------------------------

def test_A1_root直接往下():
    s = _bundle()
    head = s.split("webit3_os=")[0]
    assert 'if [ "$(id -u)" -ne 0 ]; then' in head


def test_A2_有完整sudo免密碼就自己sudo重跑():
    s = _bundle()
    assert "sudo -n true" in s and 'exec sudo -n sh "$0" "$@"' in s


def test_A3_sudo要密碼就讓sudo問一次_只在有終端機時():
    s = _bundle()
    assert "elif [ -t 0 ]; then" in s
    # 認證與列權限同一次 sudo（timestamp_timeout=0 也判得對）
    assert 'SL="$(sudo -l 2>/dev/null)"' in s and "if sudo -v" not in s
    assert "sudo 認證沒過" in s


def test_A4_沒sudo或問失敗就停_請改用root():
    s = _bundle()
    assert hf.NEED_ROOT_MSG in s
    assert f'echo "{ob.RESULT_NEED_ROOT}"\n  exit {hf.NEED_ROOT_EXIT}' in s
    # 內層（網頁一鍵納管走 stdin）也一樣只認 root，離開碼相同 → 外層印同一句
    assert f"exit {hf.NEED_ROOT_EXIT}; fi" in eng.build_linux_script(PUB, IP)
    assert eng.classify_failure(hf.NEED_ROOT_MSG)[0] == "execute"


def test_A5_VMware最先擋_離開碼3_一行結果():
    s = _bundle()
    first = s.split("WEBIT3_SHA=")[0]
    assert "VMkernel" in first and "/etc/photon-release" in first
    assert f'{ob.RESULT_STOP}：VMware 設備（ESXi／vCenter）不可納管"; exit 3' in first


def test_A6_Windows不是這支():
    s = _bundle()
    assert "  *)\n    echo \"停止：這支只支援 Linux 與 AIX" in s


def test_A7_重跑只更新不重複():
    s = eng.build_linux_script(PUB, IP, root_pubkey=RPUB)
    assert 'if ! id "$ACCOUNT"' in s                       # 帳號在就不建
    assert 'grep -qxF "$LINE" "$AUTH"' in s                 # 同一行在就不動
    assert 'grep -vF "$PUBKEY" "$AUTH"' in s                # 收集器位址變了→換掉舊那行，不疊加
    assert "rootkey_strip" in s                             # root 那行也是先拿掉再加
    a = eng.build_aix_script(PUB, IP, root_pubkey=RPUB)
    assert 'if ! lsuser "$ACCOUNT"' in a and 'grep -xF "$LINE" "$AUTH"' in a


def test_A8_sudo不完整就停():
    s = _bundle()
    assert "sudo -n -l" in s and "(NOPASSWD:[[:space:]]*)?ALL" in s
    assert "sudo 不是完整權限" in s


# ---------------------------------------------------------------------------
# B1–B6
# ---------------------------------------------------------------------------

def test_B1_AllowUsers只加限收集器的一條_驗證不過還原_Deny不動():
    sh = hf.access_fix_sh()
    assert 'for ip in $COLLECTOR_IPS; do LINE="$LINE $U@$ip"; done' in sh
    # sshd -T 每條 AllowUsers 各一行：逐個位址用 grep -qx 確認（多個收集器位址也驗得過）
    assert """printf '%s\\n' "$NEW_AU" | grep -qx " *$U@$ip" || GOT=0""" in sh
    # $AU 可能有 *：迴圈前後關掉檔名展開
    assert sh.index("set -f") < sh.index("for e in $AU") < sh.index("set +f")
    assert hf.ALLOW_MARK in sh and '"$SSHD_BIN" -t' in sh
    assert 'cp -p "$SSHD_CONF.webit3-$STAMP_AC" "$SSHD_CONF"' in sh
    assert "DenyUsers／DenyGroups" in sh and "不改" in sh
    # 插在第一個 Match 之前（Match 之後的行只套用到那個 Match）
    assert "[Mm][Aa][Tt][Cc][Hh]" in sh
    # root 只有 root 金鑰佈成才處理
    assert 'if [ "${ROOTKEY_OK:-0}" = "1" ]; then\n  acl_fix_user root' in sh
    for s in (eng.build_linux_script(PUB, IP), eng.build_aix_script(PUB, IP)):
        assert "acl_fix_user \"$ACCOUNT\"" in s


def test_B1_pam_access只在有啟用時加_限收集器_放最前面():
    sh = hf.access_fix_sh()
    assert "pam_access" in sh and 'L="+ : $U : $COLLECTOR_IPS"' in sh
    assert '{ echo "' + hf.ACCESS_MARK + '"; echo "$L"; cat' in sh


def test_B2_收集帳號設成服務帳號_不過期():
    assert "chage -M -1 -I -1 -E -1" in eng.build_linux_script(PUB, IP)
    assert "chuser maxage=0 maxexpired=-1 expires=0" in eng.build_aix_script(PUB, IP)


def test_B3_SELinux有開才restorecon():
    s = eng.build_linux_script(PUB, IP)
    assert "selinuxenabled" in s and "restorecon -R" in s
    assert s.index("selinuxenabled") < s.index("restorecon -R")


def test_B4_一行指令_下載驗sha256再跑_失敗講停止():
    line = ob.one_liner("http://10.99.0.5:8000/api/onboard/fetch/TOK", "a" * 64)
    assert "\n" not in line
    assert "curl -fsS" in line and "wget -q" in line
    assert "sha256sum" in line and "openssl dgst -sha256" in line and "csum -h SHA256" in line
    # if／else：腳本本身失敗不可以被講成下載失敗
    assert f'[ "$S" = "{"a" * 64}" ]; then sh $F; else echo \'{ob.RESULT_STOP}：下載或 sha256 驗證失敗\'; fi' in line
    assert line.startswith("F=") and "|| echo" not in line
    assert "/tmp/" not in line, "固定檔名放 /tmp 會被別人預先放檔"


def test_B4_下載權杖30分鐘_只給這批主機():
    t = ob.issue_token([IP, "10.99.0.22"], now=1000.0)
    assert ob.token_valid(t, IP, now=1000.0 + ob.TOKEN_TTL - 1)
    assert not ob.token_valid(t, IP, now=1000.0 + ob.TOKEN_TTL + 1)
    assert not ob.token_valid(t, "10.99.0.99", now=1000.0), "別台拿得到"
    assert not ob.token_valid(t, None, now=1000.0)
    assert not ob.token_valid("nope", IP, now=1000.0)


def test_B5_每個改動都寫主機端紀錄_含版本與sha256():
    s = eng.build_linux_script(PUB, IP)
    assert hf.HOST_LOG in s and 'say "[+]' in s
    assert "SCRIPT_VER=" in s and "sha256" in s and "RUNBY=" in s
    # heredoc 裡的輔助程式本體不能被改成 say（那邊沒有 say）
    helper = s.split("<<'WEBIT3_HELPER_EOF'\n", 1)[1].split("WEBIT3_HELPER_EOF", 1)[0]
    assert "say " not in helper
    # say 在用之前就定義好
    assert s.index("say() {") < s.index('say "[+]')


def test_B6_驗證失敗分得出是哪一種():
    assert hf.classify_collect_error("ssh: connect to host x port 22: Connection timed out")["text"] == hf.NET_FAIL
    k = hf.classify_collect_error("Permission denied (publickey).")
    assert k["text"] == hf.KEY_REJECTED and any("AllowUsers" in h for h in k["hints"])
    assert any("from=" in h for h in k["hints"]) and any("SELinux" in h for h in k["hints"])
    assert hf.classify_collect_error("REMOTE HOST IDENTIFICATION HAS CHANGED host key verification failed")["kind"] == "hostkey"


def test_B6_來源IP與登記不符_設定被還原(tmp_path):
    conn = _conn(tmp_path)
    d = paste_watch.diagnose(conn, IP, "Permission denied (publickey)", was_ok=False,
                             source_ip=lambda ip: "10.99.9.9")
    assert d["state"] == "source_ip"
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('collector_ip', '10.99.9.9')")
    d = paste_watch.diagnose(conn, IP, "Permission denied (publickey)", was_ok=True,
                             source_ip=lambda ip: "10.99.9.9")
    assert d["state"] == "reverted" and "組態管理" in d["reason"]


# ---------------------------------------------------------------------------
# 跑完系統自己偵測 → 觸發納管後六樣收集
# ---------------------------------------------------------------------------

def test_貼上執行後系統自己偵測並觸發納管後收集(tmp_path):
    conn = _conn(tmp_path)
    paste_watch.issue(conn, [IP, "10.99.0.22"], "tester", platform_of=lambda ip: "linux")
    started = []
    res = paste_watch.check(
        conn, force=True,
        probe=lambda ip, acct: (True, None) if ip == IP else (False, "Connection timed out"),
        root_probe=lambda ip: True,
        collect=lambda serial: {"updated": 3},
        post_start=lambda serial, by: started.append((serial, by)))
    by_ip = {r["ip"]: r for r in res}
    assert by_ip[IP]["state"] == "ok" and by_ip[IP]["root_key"] is True
    assert by_ip["10.99.0.22"]["state"] == "network"
    assert started == [(f"AUTO-{IP}", "paste:tester")], "沒觸發納管後收集"
    row = conn.execute("SELECT collect_ok, root_key_at FROM hardware WHERE ip=?", (IP,)).fetchone()
    assert row["collect_ok"] == 1 and row["root_key_at"]
    assert conn.execute("SELECT COUNT(*) FROM onboard_audit WHERE trigger='paste'").fetchone()[0] == 1
    # 已偵測到的不再試；2 分鐘內不重試
    again = paste_watch.check(conn, probe=lambda ip, acct: (_ for _ in ()).throw(AssertionError("不該再試")))
    assert again == []


def test_等太久就停止自動試連(tmp_path):
    conn = _conn(tmp_path)
    paste_watch.issue(conn, [IP], None)
    later = datetime.now() + timedelta(days=paste_watch.PENDING_DAYS + 1)
    paste_watch.check(conn, now=later, probe=lambda *a: (_ for _ in ()).throw(AssertionError("過期不該試")))
    assert paste_watch.status(conn, [IP])[0]["state"] == "expired"


# ---------------------------------------------------------------------------
# 網頁一鍵納管跑的是同一份檔案
# ---------------------------------------------------------------------------

def test_網頁一鍵納管跟下載是同一份腳本(monkeypatch):
    monkeypatch.setattr(eng, "resolve_extra_collector_ips", lambda conn=None: [])
    monkeypatch.setattr(root_key, "read_pubkey", lambda path=None: RPUB)
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return eng.OnboardResult(True, "execute", "ok", "")

    for plat in ("linux", "aix"):
        r = eng.onboard(IP, plat, "root", "pw", IP, pubkey=PUB, executor=fake, account="webit3sc")
        assert r.ok
        want = ob.build_bundle(PUB, IP, "webit3sc", None)
        assert seen["script"] == want, f"{plat} 網頁一鍵納管跑的不是下載那份"
        assert ob.sha256_of(seen["script"]) == ob.sha256_of(want)


def test_取得納管指令端點_一行指令與下載同一個sha256_權杖錯就404(tmp_path, monkeypatch):
    import api
    import auth
    from fastapi.testclient import TestClient

    monkeypatch.setattr(eng, "collector_pubkey", lambda path=None: PUB)
    monkeypatch.setattr(root_key, "read_pubkey", lambda path=None: RPUB)
    db_path = Path(tmp_path) / "t.db"
    db.init_db(db_path)

    def _override():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        c = db.get_connection(db_path)
        db.create_user(c, "tester", auth.hash_password("s3cure-pass!"))
        c.execute("INSERT INTO app_settings (key, value) VALUES ('collector_ip', ?)", (IP,))
        c.commit(); c.close()
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"}).status_code == 200
        info = cl.get("/api/onboard/bundle").json()
        dl = cl.get("/api/onboard/bundle/download")
        assert dl.status_code == 200 and ob.sha256_of(dl.content.decode("utf-8")) == info["sha256"]
        # TestClient 的來源位址是 "testclient"：拿它當這批主機之一，才測得到「綁來源」
        iss = cl.post("/api/onboard/paste/issue", json={"ips": [IP, "testclient"]}).json()
        assert iss["sha256"] == info["sha256"] and info["sha256"] in iss["one_liner"]
        tok = re.search(r"/api/onboard/fetch/([\w-]+)", iss["one_liner"]).group(1)
        anon = TestClient(api.app)
        got = anon.get(f"/api/onboard/fetch/{tok}")
        assert got.status_code == 200 and ob.sha256_of(got.content.decode("utf-8")) == info["sha256"]
        assert anon.get("/api/onboard/fetch/wrong-token").status_code == 404
        st = cl.get(f"/api/onboard/paste/status?ips={IP}").json()
        assert st["rows"][0]["state"] == "waiting"
    finally:
        api.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 畫面：2-1 兩個對等的動作；取得納管指令只有兩個單一動作
# ---------------------------------------------------------------------------

def test_畫面_取得納管指令只有複製與下載兩個動作():
    v = (FRONT / "components" / "PasteOnboardModal.vue").read_text(encoding="utf-8")
    tpl = v.split("<template>", 1)[1]
    buttons = re.findall(r"<button[^>]*>\s*([^<]+?)\s*</button>", tpl)
    actions = [b for b in buttons if b not in ("關閉",)]
    assert actions == ["複製", "下載"], actions
    a = (FRONT / "pages" / "adopt.vue").read_text(encoding="utf-8")
    assert "我有密碼：網頁一鍵納管" in a and "我沒有密碼：取得納管指令" in a


def test_AllowUsers驗證_兩個收集器位址也過_模擬sshd_T輸出():
    """把那段 shell 的判斷照抄成 Python：sshd -T 的 allowusers 一條一行。"""
    new_au = " webit3sc@10.99.0.21\n webit3sc@10.99.0.22\n alice"
    got = all(any(re.fullmatch(rf" *webit3sc@{re.escape(ip)}", ln) for ln in new_au.split("\n"))
              for ip in ("10.99.0.21", "10.99.0.22"))
    assert got


def test_超過20台_剛試過的不佔名額_後面的輪得到(tmp_path):
    conn = _conn(tmp_path)
    ips = [f"10.99.1.{i}" for i in range(1, 31)]
    paste_watch.issue(conn, ips, None, platform_of=lambda ip: "linux")
    t0 = datetime.now()
    tried = []
    probe = lambda ip, acct: (tried.append(ip), (False, "Connection timed out"))[1]  # noqa: E731
    paste_watch.check(conn, probe=probe, now=t0)
    assert len(tried) == paste_watch.MAX_PER_TICK
    first = set(tried)
    tried.clear()
    paste_watch.check(conn, probe=probe, now=t0 + timedelta(seconds=30))   # 2 分鐘內
    assert set(tried) == set(ips) - first, "第 21 台以後輪不到"


def test_畫面輪詢不會觸發SSH(tmp_path, monkeypatch):
    import inspect
    from routers import onboard as r
    src = inspect.getsource(r.onboard_paste_status)
    assert "paste_watch.check(" not in src
    v = (FRONT / "components" / "PasteOnboardModal.vue").read_text(encoding="utf-8")
    assert "check=1" not in v
    # 排程走背景執行緒，上一輪沒跑完不疊加
    monkeypatch.setattr(paste_watch, "check", lambda c: __import__("time").sleep(0.3))
    assert paste_watch.tick_in_background(Path(tmp_path) / "t.db") is True
    assert paste_watch.tick_in_background(Path(tmp_path) / "t.db") is False


def test_網頁一鍵納管端點跑的sha256等於bundle端點(tmp_path, monkeypatch):
    """設定不是預設值（AIX 帳號另設、有備援位址）時，兩條路還是同一份。"""
    import api
    import auth
    from fastapi.testclient import TestClient

    monkeypatch.setattr(eng, "collector_pubkey", lambda path=None: PUB)
    monkeypatch.setattr(root_key, "read_pubkey", lambda path=None: RPUB)
    seen = {}

    def fake_exec(**kw):
        seen["script"] = kw["script"]
        return eng.OnboardResult(False, "execute", "stop here", "")

    monkeypatch.setattr(eng, "_ssh_executor", fake_exec)
    monkeypatch.setattr(eng, "probe_target", lambda *a, **k: {"os": "linux", "uid": 0, "has_sudo": False})
    db_path = Path(tmp_path) / "t.db"
    db.init_db(db_path)

    def _override():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        c = db.get_connection(db_path)
        db.create_user(c, "tester", auth.hash_password("s3cure-pass!"))
        for k, v in (("collector_ip", IP), ("collector_ips_extra", "10.99.0.30"),
                     ("collect_ssh_account_aix", "w3aix")):
            c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (k, v))
        c.commit(); c.close()
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"}).status_code == 200
        want = cl.get("/api/onboard/bundle").json()["sha256"]
        cl.post("/api/onboard", json={"ip": "10.99.0.40", "platform": "linux", "username": "root", "password": "x"})
        assert ob.sha256_of(seen["script"]) == want
        assert "w3aix" in seen["script"] and "10.99.0.30" in seen["script"]
    finally:
        api.app.dependency_overrides.clear()


def test_B5_sudo白名單變更要寫進主機紀錄檔_沒變不換():
    """2026-10-18 在 221 演練：白名單換了新版，/var/log/webit3_onboard.log 卻沒有這一筆。"""
    s = eng.build_linux_script(PUB, "10.99.0.221")
    assert 'if [ -f "$SUDOERS" ] && cmp -s "$SUDOERS.new" "$SUDOERS"; then' in s
    assert 'say "[+] sudo 唯讀白名單已更新' in s
    assert 'say "[=] sudo 唯讀白名單未變' in s


def test_備份目錄帶PID_同一秒重跑不互相覆蓋():
    """2026-10-18 演練：root 跑完、sysinfra 同一秒再跑，兩次共用同一個備份目錄，restore.sh 被覆蓋。"""
    s = eng.build_linux_script(PUB, "10.99.0.221")
    assert 'BK="/var/backups/webit3/$(date +%Y%m%d_%H%M%S)_$$"' in s
