"""收集器位址是佔位值時要大聲失敗，不要安靜佈一把永遠被拒的金鑰。

2026-08-16 在公司主機實際踩到（還沒按下去就先查出來）：
deploy.sh 從來沒設 ASSET_COLLECTOR_IP，程式退回原始碼裡的預設值；而 patch 走
去識別化管道送出去時，那個預設 IP 被替換表換成 `YOUR_SERVER_IP`。結果納管腳本會把
`from="YOUR_SERVER_IP"` 寫進目標主機的 authorized_keys——sshd 永遠比對不到，
金鑰等於被拒，但腳本印「完成」、畫面顯示已納管，之後每次收集都連不進去。

所有紅綠燈都說成功、只有資料永遠不進來，是這個專案反覆在防的那一類故障。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_engine as eng  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"


def _rejects(ip: str) -> bool:
    try:
        eng.validate_collector_ip(ip)
        return False
    except ValueError:
        return True


def test_去識別化留下的佔位值要擋掉():
    assert _rejects("YOUR_SERVER_IP")


def test_其他常見沒填值也要擋():
    for bad in ("", "   ", "your-collector", "changeme", "x.x.x.x",
                "example.com", "localhost", "127.0.0.1", "0.0.0.0"):
        assert _rejects(bad), f"{bad!r} 應該被擋"


def test_真的位址要放行():
    for good in ("10.92.198.1", "10.0.0.221", "172.16.0.5"):
        assert not _rejects(good), f"{good!r} 不該被擋"


def test_主機名也放行_有些環境用DNS名():
    assert not _rejects("webit3-collector.corp.local")


def test_四條佈金鑰的路都擋得到():
    """遠端納管、本機一行指令、排程自動納管都走 build_script；playbook 另一條。
    只要有一條漏擋，那條就會安靜地佈出壞金鑰。"""
    for platform in ("linux", "aix", "windows"):
        try:
            eng.build_script(platform, PUBKEY, "YOUR_SERVER_IP")
        except ValueError:
            continue
        raise AssertionError(f"{platform} 的 build_script 沒擋住佔位值")

    try:
        eng.build_linux_playbook(PUBKEY, "YOUR_SERVER_IP")
    except ValueError:
        return
    raise AssertionError("playbook 沒擋住佔位值")


def test_擋下來的訊息要講得出為什麼_不是一句失敗():
    """人看到訊息要知道去改哪裡，否則只會重按一次。"""
    try:
        eng.build_script("linux", PUBKEY, "YOUR_SERVER_IP")
    except ValueError as exc:
        msg = str(exc)
        assert "ASSET_COLLECTOR_IP" in msg
        assert "from=" in msg or "來源" in msg
        return
    raise AssertionError("應該被擋")


def test_onboard遇到佔位值回失敗而不是丟例外():
    """API 層要能翻成畫面上看得懂的錯誤，不是 500。"""
    r = eng.onboard("10.0.0.9", "linux", "sysctl", "x", "YOUR_SERVER_IP",
                    pubkey=PUBKEY)
    assert r.ok is False
    assert "ASSET_COLLECTOR_IP" in r.message


def test_正常位址仍然產得出腳本_而且from帶的是它():
    s = eng.build_script("linux", PUBKEY, "10.92.198.1")
    assert "10.92.198.1" in s
    assert "from=" in s


# ===== 收集金鑰由系統自己產（使用者 2026-08-16 指正：不該叫人 root 跑腳本）=====

def test_沒有金鑰就自己產一把(tmp_path):
    key = tmp_path / ".collector_key"
    assert eng.ensure_collector_key(str(key)) is True
    assert key.is_file() and (tmp_path / ".collector_key.pub").is_file()
    pub = (tmp_path / ".collector_key.pub").read_text(encoding="utf-8")
    assert pub.startswith("ssh-ed25519 ")


def test_已經有金鑰就絕對不覆蓋(tmp_path):
    """重新產一把會讓所有已納管主機當場失聯（它們 authorized_keys 裡是舊公鑰），
    而且沒有任何畫面會顯示原因。這條是寫死的底線。"""
    key = tmp_path / ".collector_key"
    eng.ensure_collector_key(str(key))
    original = key.read_bytes()
    pub_original = (tmp_path / ".collector_key.pub").read_bytes()

    assert eng.ensure_collector_key(str(key)) is False
    assert key.read_bytes() == original
    assert (tmp_path / ".collector_key.pub").read_bytes() == pub_original


def test_讀公鑰時沒有就順手產_按鈕不該因為缺金鑰而不能用(tmp_path):
    pub = tmp_path / ".collector_key.pub"
    got = eng.collector_pubkey(str(pub))
    assert got.startswith("ssh-ed25519 ")
    assert pub.is_file()


def test_產出的公鑰是合法的authorized_keys格式(tmp_path):
    """格式錯的話 sshd 會安靜忽略那一行——又是一個「看起來成功、其實沒用」。"""
    key = tmp_path / ".collector_key"
    eng.ensure_collector_key(str(key))
    line = (tmp_path / ".collector_key.pub").read_text(encoding="utf-8").strip()
    # 註解可以有空白（"webit3 collector"），所以最多切三段，不要用 split() 硬數欄位
    parts = line.split(maxsplit=2)
    assert len(parts) == 3 and parts[0] == "ssh-ed25519"
    import base64
    raw = base64.b64decode(parts[1])          # 解得開才是合法的
    assert raw[4:15] == b"ssh-ed25519"        # blob 開頭要自報同一種演算法


# ===== 收集器位址：自動偵測當底，畫面設定優先 =====

def test_自動偵測回得出一個真的位址():
    ip = eng.detect_collector_ip()
    assert ip and not _rejects(ip), f"偵測到的位址 {ip!r} 自己過不了驗證"


def test_沒有設定時用偵測值_不再退回寫死的預設():
    """寫死的預設值會被去識別化替換成佔位字串，是這次公司主機那個坑的根源。"""
    got = eng.resolve_collector_ip(None)
    assert "YOUR_" not in got.upper()
    assert not _rejects(got)


def test_畫面設定優先於環境變數與偵測(tmp_path):
    import db

    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        db.set_setting(conn, eng.COLLECTOR_IP_SETTING, "10.92.198.1")
        assert eng.resolve_collector_ip(conn) == "10.92.198.1"
        # 設成空字串＝沒設，要退回下一順位而不是回傳空字串
        db.set_setting(conn, eng.COLLECTOR_IP_SETTING, "  ")
        assert eng.resolve_collector_ip(conn) != ""
    finally:
        conn.close()


# ===== 執行中進度：講出目標主機做到哪一步（使用者 2026-08-16 要求）=====

def test_腳本印什麼階段就顯示什麼_不另外維護對照表():
    """另外維護一份「階段對照表」一定會跟腳本漂走，然後畫面講的跟實際做的
    不是同一件事。所以直接採用腳本自己印的話。"""
    eng.progress_start("10.0.0.9")
    eng.progress_note("10.0.0.9", "[+] 已建立帳號 webit3scan")
    assert eng.progress_of("10.0.0.9")["stage"] == "已建立帳號 webit3scan"
    eng.progress_note("10.0.0.9", "[*] 佈署收集公鑰")
    assert eng.progress_of("10.0.0.9")["stage"] == "佈署收集公鑰"
    eng.progress_note("10.0.0.9", "完成。10.0.0.221 現在可以收集這台。")
    assert eng.progress_of("10.0.0.9")["stage"] == "完成"


def test_非階段行只進log不蓋掉階段():
    """腳本會印很多雜訊行（驗證輸出等）。讓它們蓋掉階段，畫面就會停在無意義的一行。"""
    eng.progress_start("10.0.0.8")
    eng.progress_note("10.0.0.8", "[*] 佈署收集公鑰")
    eng.progress_note("10.0.0.8", "some random output")
    p = eng.progress_of("10.0.0.8")
    assert p["stage"] == "佈署收集公鑰"
    assert "some random output" in p["lines"]


def test_log不會無限長大():
    eng.progress_start("10.0.0.7")
    for i in range(200):
        eng.progress_note("10.0.0.7", f"line {i}")
    assert len(eng.progress_of("10.0.0.7")["lines"]) <= 40


def test_沒跑過的主機查進度不會炸():
    p = eng.progress_of("10.99.99.99")
    assert p["done"] is True and p["lines"] == []


def test_三個平台的腳本都印得出階段():
    """沒有階段訊息，畫面就只能顯示「已經幾秒」——正是使用者反映的問題。"""
    for platform in ("linux", "aix", "windows"):
        s = eng.build_script(platform, PUBKEY, "10.0.0.221")
        assert "[+] 已建立帳號" in s, f"{platform} 沒有建立帳號的階段訊息"


def test_linux腳本每個主要步驟都有回報():
    s = eng.build_script("linux", PUBKEY, "10.0.0.221")
    for step in ("已建立帳號", "佈署收集公鑰", "設定唯讀 sudo 白名單"):
        assert step in s, f"缺少階段訊息：{step}"


def test_串流讀取一定要有逾時看門狗():
    """`for line in proc.stdout` 本身沒有逾時：目標主機不吐東西又不結束時
    （sudo 在等密碼、SSH 半開連線）會**永遠阻塞**。改成串流時把原本
    subprocess.run(timeout=) 的上限弄丟過一次，症狀是畫面秒數一直跑、
    超過上限也不回來（2026-08-16 公司主機看到 90s+）。"""
    import inspect

    src = inspect.getsource(eng._sshpass_executor)
    assert "threading.Timer" in src, "串流迴圈沒有看門狗，會永遠卡住"
    assert "proc.kill" in src
    # 逾時要講得出人話，不能只是靜靜回一個失敗
    assert "sudo 需要密碼" in src or "強制中止" in src


# ===== 失敗階段要標對（2026-08-16 公司主機發現標錯）=====

def test_連不上要標connect不是execute():
    """畫面把 execute 解釋成「進去了但腳本沒跑完（多半是權限或 sudo）」。
    一台連 22 都連不上的機器被標成 execute，人會照提示去查 sudo——
    查一個根本不存在的問題。階段標錯比沒有階段更糟，它會主動把人引去錯方向。"""
    stage, why = eng.classify_failure(
        "ssh: connect to host 10.99.168.100 port 22: Connection timed out")
    assert stage == "connect"
    assert "22" in why or "逾時" in why


def test_各種連線層失敗都歸connect():
    for out in ("ssh: connect to host x port 22: Connection refused",
                "ssh: connect to host x port 22: No route to host",
                "Permission denied (publickey,password).",
                "ssh: Could not resolve hostname x"):
        assert eng.classify_failure(out)[0] == "connect", out


def test_進得去但權限不足才是execute():
    assert eng.classify_failure("需要 root")[0] == "execute"
    assert eng.classify_failure("sudo: a password is required")[0] == "execute"


def test_看不出來的預設仍是execute_但不要亂猜原因():
    stage, why = eng.classify_failure("something unexpected")
    assert stage == "execute"
    assert why == "腳本執行未回報完成"


def test_連線失敗的說明要指得出下一步():
    """Windows 通常沒開 22——這句話會直接省掉一輪瞎猜。"""
    _, why = eng.classify_failure("ssh: connect to host x port 22: Connection timed out")
    assert "WinRM" in why or "Windows" in why


# ===== 登入後問機器自己是什麼（2026-08-16 公司主機連續踩到兩次）=====

def test_登入後才判平台_不要相信畫面上選的():
    """AIX 與 Linux 從網路上分不出來（SSH banner 一樣），畫面只能請人自己選。
    但登進去一行 uname -s 就確定了——沒有理由讓人猜，猜錯就拿 useradd 去打 AIX。"""
    assert eng.parse_probe("AIX\n0\nHASSUDO")["os"] == "aix"
    assert eng.parse_probe("Linux\n1001\nNOSUDO")["os"] == "linux"
    assert eng.parse_probe("CYGWIN_NT-10.0\n0\nNOSUDO")["os"] == "windows"
    assert eng.parse_probe("")["os"] == ""          # 看不出來就誠實留空，不亂猜


def test_探測要看得出是不是root與有沒有sudo():
    p = eng.parse_probe("Linux\n0\nHASSUDO")
    assert p["uid"] == 0 and p["has_sudo"] is True
    p2 = eng.parse_probe("Linux\n1001\nNOSUDO")
    assert p2["uid"] == 1001 and p2["has_sudo"] is False


def test_探測結果可注入_測試不碰真網路():
    got = eng.probe_target("10.0.0.9", "u", "pw",
                           runner=lambda h, u, p: "AIX\n0\nNOSUDO")
    assert got == {"os": "aix", "uid": 0, "has_sudo": False}
