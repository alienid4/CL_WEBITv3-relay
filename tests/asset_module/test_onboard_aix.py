"""AIX 納管（方案 A，2026-08-16 定案）＋ Linux 的 Ansible playbook。

背景：資安／維運同意佈一次性 playbook，但那只解掉 Linux——AIX 不支援 Ansible，
而且照 Linux 腳本跑在 AIX 上一定失敗（useradd/passwd -l/visudo 都不對）。
公司有 8 台 AIX，定案走「一次性納管腳本」。

這裡守的是「照 Linux 那套跑會死在哪」——每一條都是 AIX 跟 Linux 真正不同的地方，
不是把同一份腳本換個名字就好。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state  # noqa: E402
import onboard_engine as eng  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"
COLLECTOR = "10.0.0.221"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


# ===== AIX 腳本：跟 Linux 真正不同的地方 =====

def test_AIX用mkuser不是useradd():
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert "mkuser" in s
    assert "useradd" not in s, "AIX 沒有 useradd，照 Linux 版跑一定失敗"


def test_AIX鎖密碼用chuser不是passwd_l():
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert "account_locked=true" in s
    assert "passwd -l" not in s


def test_AIX不佈sudoers():
    """Linux 版要 sudo 只為了讀 /sys/class/dmi/id/*（0400）。AIX 根本沒有 dmi，
    序號機型走 uname -M／-u 一般帳號就讀得到；而且 AIX 未必裝 sudo，
    硬寫 /etc/sudoers.d 會直接失敗。少一個不需要的權限也是好事。"""
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert "sudoers" not in s
    assert "visudo" not in s
    assert "/sys/class/dmi" not in s
    # 註解裡講「不需要 sudo」沒關係，但不能有任何一行真的去執行 sudo
    runnable = [ln for ln in s.splitlines() if not ln.strip().startswith("#")]
    assert all("sudo" not in ln for ln in runnable)
    # 但要證明它真的拿得到序號機型，不是「因為拿不到才不給權限」
    assert "uname -M" in s and "uname -u" in s


def test_AIX用ksh不假設有bash():
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert s.startswith("#!/usr/bin/ksh")


def test_AIX金鑰仍帶from限來源():
    """三道鎖是 C1 定案專用帳號的前提，AIX 版不能因為簡化就少一道。"""
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert f'from=\\"$COLLECTOR_IP\\"' in s or "from=" in s
    assert "no-port-forwarding" in s
    assert PUBKEY in s


def test_AIX帳號名超過8字元要當場擋下來():
    """AIX 的 max_logname 預設 9（可用 8 字元），webit3scan 是 10 字元，
    mkuser 會拒絕。與其讓人到現場才看到看不懂的錯，不如產腳本時就擋。"""
    try:
        eng.build_aix_script(PUBKEY, COLLECTOR, account="webit3scan")
    except ValueError as exc:
        assert "max_logname" in str(exc) or "字元" in str(exc)
        return
    raise AssertionError("10 字元的帳號名應該被擋")


def test_AIX預設帳號名剛好在上限內():
    assert len(eng.DEFAULT_ACCOUNT_AIX) <= eng.AIX_MAX_LOGNAME
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert eng.DEFAULT_ACCOUNT_AIX in s


def test_腳本自己會先檢查該機的max_logname():
    """就算我們這邊只有 8 字元，那台 AIX 若設得更短仍會失敗——
    腳本要自己先看一眼再動手，不要讓 mkuser 吐一句看不懂的錯。"""
    s = eng.build_aix_script(PUBKEY, COLLECTOR)
    assert "max_logname" in s


def test_build_script認得aix():
    s = eng.build_script("aix", PUBKEY, COLLECTOR)
    assert "mkuser" in s
    # 沒指定帳號時要自動用 AIX 的短名，不要沿用 webit3scan 然後被 mkuser 拒絕
    assert eng.DEFAULT_ACCOUNT_AIX in s


def test_onboard遇到不合法的AIX帳號名回失敗而不是丟例外():
    r = eng.onboard("10.0.0.9", "aix", "root", "x", COLLECTOR,
                    pubkey=PUBKEY, account="toolongname")
    assert r.ok is False
    assert r.stage == "connect"


# ===== Linux 的 Ansible playbook =====

def test_playbook含三道鎖():
    y = eng.build_linux_playbook(PUBKEY, COLLECTOR)
    assert "password_lock: true" in y                 # 鎖密碼
    assert f'from="{{{{ webit3_collector_ip }}}}"' in y or "from=" in y   # 限來源
    assert "/sys/class/dmi/id/*" in y                 # 唯讀 sudo 白名單
    # 白名單本身不能授權讀 /etc/shadow（註解裡寫「不含 /etc/shadow」是說明，不算授權）
    assert "cat /etc/shadow" not in y
    sudo_line = [ln for ln in y.splitlines() if "NOPASSWD" in ln]
    assert sudo_line and all("shadow" not in ln for ln in sudo_line)


def test_playbook帶的是當下的公鑰不是靜態檔():
    """換過金鑰之後，靜態的 yml 會安靜地變成錯的——每台都佈成功，
    但收集全部連不進來。所以 playbook 必須即時從引擎產。"""
    y = eng.build_linux_playbook("ssh-ed25519 NEWKEY x", COLLECTOR)
    assert "NEWKEY" in y
    assert PUBKEY not in y


def test_playbook是合法YAML且動作齊全():
    yaml = __import__("importlib").import_module("yaml") \
        if _has_yaml() else None
    y = eng.build_linux_playbook(PUBKEY, COLLECTOR)
    if yaml is not None:
        doc = yaml.safe_load(y)
        assert isinstance(doc, list) and doc[0]["hosts"] == "all"
        names = [t["name"] for t in doc[0]["tasks"]]
        assert len(names) == 4
    else:
        assert "hosts: all" in y and "become: true" in y


def _has_yaml() -> bool:
    try:
        __import__("yaml")
        return True
    except ImportError:
        return False


# ===== 收集鏈：AIX 主機不能被當成 Linux 收 =====

def test_已知OS是AIX時收集要走AIX指令集():
    """原本只看開放埠（3389→windows，其餘一律 linux），AIX 會被當成 Linux——
    拿 /etc/os-release、/sys/class/dmi 這種 AIX 沒有的路徑去收，結果是
    「連得上、但每個欄位都空的」，跟權限不足長得一模一樣，最難查。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                     "VALUES ('2026-08-16 01:00:00','10.0.0.9',1,'22')")
        conn.commit()
        assert manage_state.collect_platform_of(conn, "10.0.0.9", "AIX 7.2") == "aix"
        assert manage_state.collect_platform_of(conn, "10.0.0.9", "Rocky Linux 9") == "linux"
        assert manage_state.collect_platform_of(conn, "10.0.0.9", None) == "linux"
        conn.close()


def test_AIX的收集身分是另一個設定值():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        assert manage_state.get_collect_account(conn, "linux") == "webit3scan"
        assert manage_state.get_collect_account(conn, "aix") == manage_state.AIX_COLLECT_ACCOUNT
        assert len(manage_state.get_collect_account(conn, "aix")) <= eng.AIX_MAX_LOGNAME
        conn.close()


def test_該環境放寬過就能改回同名():
    """這不是兩個帳號，是同一個收集身分在不同平台的合法寫法。
    AIX 若已 chdev 放寬 max_logname，設定改回 webit3scan 就完全一致。"""
    from db import set_setting

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        set_setting(conn, "collect_ssh_account_aix", "webit3scan")
        assert manage_state.get_collect_account(conn, "aix") == "webit3scan"
        conn.close()


def test_金鑰產不出來時要說人話而不是500():
    """行為已改（2026-08-16）：金鑰不存在時系統自己產，不再要人跑腳本。
    但「連產都產不出來」仍要給看得懂的訊息——例如路徑不可寫。
    原本會噴 FileNotFoundError 變成 500，管理員只看到「伺服器錯誤」。"""
    # 用一個一定建不起來的路徑（把檔案當成目錄的父層）
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "notadir"
        blocker.write_text("x", encoding="utf-8")
        try:
            eng.collector_pubkey(str(blocker / "sub" / "k.pub"))
        except ValueError as exc:
            assert "收集金鑰" in str(exc) or "公鑰" in str(exc)
            return
    raise AssertionError("產不出來也讀不到時要丟 ValueError（呼叫端才翻得成 400）")


def test_只有公鑰存在時絕對不能重產(tmp_path):
    """私鑰放別處、或只把公鑰複製過來的機器會是這個狀態。重產會覆蓋公鑰，
    所有已納管主機的 authorized_keys 都對不上，當場全部失聯而且畫面不說原因。"""
    pub = tmp_path / ".collector_key.pub"
    pub.write_text("ssh-ed25519 EXISTINGKEY webit3 collector\n", encoding="utf-8")

    assert eng.ensure_collector_key(str(tmp_path / ".collector_key")) is False
    assert "EXISTINGKEY" in pub.read_text(encoding="utf-8")
    assert eng.collector_pubkey(str(pub)) == "ssh-ed25519 EXISTINGKEY webit3 collector"


def test_公鑰路徑常數改得動():
    """預設路徑要在呼叫當下才讀模組常數。綁在預設引數上會在 import 當下定值，
    之後改 COLLECTOR_KEY_PUB 完全無效——非標準安裝路徑就繞不過去。"""
    import tempfile as tf

    with tf.TemporaryDirectory() as tmp:
        p = Path(tmp) / "k.pub"
        p.write_text(PUBKEY, encoding="utf-8")
        orig = eng.COLLECTOR_KEY_PUB
        eng.COLLECTOR_KEY_PUB = str(p)
        try:
            assert eng.collector_pubkey() == PUBKEY
        finally:
            eng.COLLECTOR_KEY_PUB = orig


def test_AIX的一行指令不用base64也不用sudo():
    """AIX 沒有 GNU coreutils 的 base64，也未必裝 sudo。照 Linux 那行貼過去
    會噴 command not found——而人只看得到「指令沒反應」。"""
    import api
    import auth
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)

        def _override():
            c = db.get_connection(db_path)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _override
        # 收集公鑰在家裡不存在，注入一份假的（這支端點只是把它包進指令裡）
        orig = eng.collector_pubkey
        eng.collector_pubkey = lambda *a, **k: PUBKEY
        client = TestClient(api.app)
        c = db.get_connection(db_path)
        try:
            db.create_user(c, "t", auth.hash_password("test-password-123"))
        finally:
            c.close()
        client.post("/api/auth/login", json={"username": "t", "password": "test-password-123"})
        try:
            r = client.get("/api/onboard/script", params={"platform": "aix"})
            assert r.status_code == 200
            cmd = r.json()["command"]
            assert "openssl base64 -d" in cmd and "ksh" in cmd
            assert "sudo" not in cmd
            assert "root" in r.json()["note"]

            # playbook：Linux 有，AIX 沒有（Ansible 不支援 AIX），要講清楚不是默默失敗
            pb = client.get("/api/onboard/script",
                            params={"platform": "linux", "fmt": "ansible"})
            assert pb.status_code == 200
            assert "ansible.builtin.user" in pb.json()["content"]
            assert pb.json()["filename"].endswith(".yml")
            bad = client.get("/api/onboard/script",
                             params={"platform": "aix", "fmt": "ansible"})
            assert bad.status_code == 400
            assert "Ansible" in bad.json()["detail"]
        finally:
            eng.collector_pubkey = orig
            api.app.dependency_overrides.clear()


def test_試連AIX主機時用的是AIX的帳號名():
    """拿 webit3scan 去登一台上面只有 webit3sc 的機器，永遠 Permission denied，
    而錯誤訊息看不出是「名字對不上」而不是「金鑰沒佈」——8 台 AIX 會全部
    卡在未納管，人卻在查金鑰。"""
    seen = {}

    def fake_probe(host, key_path, account=None, timeout=8, runner=None):
        seen[host] = account
        return True, None

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        db.insert_hardware(conn, asset_serial="AIX-1", ip="10.0.0.9", os="AIX 7.2")
        db.insert_hardware(conn, asset_serial="LNX-1", ip="10.0.0.8", os="Rocky Linux 9")
        orig = manage_state.probe_collect
        manage_state.probe_collect = fake_probe
        try:
            manage_state.refresh_collect_status(conn, runner=object())
        finally:
            manage_state.probe_collect = orig
        assert seen["10.0.0.9"] == manage_state.AIX_COLLECT_ACCOUNT
        assert seen["10.0.0.8"] == "webit3scan"
        conn.close()
