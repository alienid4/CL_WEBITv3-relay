"""2-1 待納管 2026-10-18 第二批（公司 v1.465.0 驗收回報）：

1. 結果欄露出原始代碼 `onboard_failed`——前端少了這個狀態的中文。
2. 「納管失敗」那列沒有一鍵納管、也勾不了——失敗的要能重試；VMware／設備照樣擋（fail-closed）。
3. 「上次失敗」短原因舊訊息都變「其他」——各版本實際寫進 onboard_audit 的訊息都要認得。
4. 搜尋只找目前頁簽——要跨全部頁簽；掃描結果找不到但資產庫有的要提示。
6. 改分類選單沒有 AIX——選單＝頁簽，一份定義（後端 CATEGORY_TABS）。

IP 一律範例位址（10.99.x／192.0.2.x），主機名是假的。
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import onboard_failures as of  # noqa: E402

FRONT = ROOT / "APP" / "asset-module" / "frontend"
ADOPT_VUE = (FRONT / "pages" / "adopt.vue").read_text(encoding="utf-8")
ASSET_VUE = (FRONT / "pages" / "assets" / "[serial].vue").read_text(encoding="utf-8")
ROUTER_SRC = (BACKEND / "routers" / "onboard.py").read_text(encoding="utf-8")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, ip, os_text, hostname):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os_text, hostname=hostname,
                       environment="測試", asset_status="使用中")
    conn.commit()


def _audit(conn, ip, stage, message, when="2026-09-22 10:00:00"):
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, ok, "
        "stage, message, created_at) VALUES (?, 'aix', 'root', 'manual', 'tester', 0, ?, ?, ?)",
        (ip, stage, message, when))
    conn.commit()


def _ssh_open(ip):
    return [22]


def _ssh_denied(ip):
    return False, "Permission denied (publickey,password)."


def _front_map(name):
    """把 adopt.vue 裡 `const NAME: Record<string, string> = { ... }` 解析成 dict。"""
    m = re.search(rf"const {name}: Record<string, string> = \{{(.*?)\n\}}", ADOPT_VUE, re.S)
    assert m, f"adopt.vue 找不到 {name}"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return dict(re.findall(r"(\w+): '([^']*)'", body))


# ===================== 1. 每個後端狀態都有中文 =====================

def test_後端每個狀態在前端都有中文_而且字一樣():
    front = _front_map("STATUS_LABEL")
    for key, text in cd.STATUS_LABEL.items():
        assert key in front, f"前端 STATUS_LABEL 少了 {key}——畫面會露出原始代碼"
        assert front[key] == text, f"{key}：前端「{front[key]}」≠ 後端「{text}」"
    assert front["onboard_failed"] == "納管失敗"


def test_後端每個狀態在前端都有顏色():
    tone = _front_map("STATUS_TONE")
    for key in cd.STATUS_LABEL:
        assert key in tone, f"STATUS_TONE 少了 {key}"


# ===================== 2. 納管失敗可重試、VMware 照樣擋 =====================

def _can_probe_src():
    m = re.search(r"function canProbe\(r: DispatchRow\): boolean \{(.*?)\n\}", ADOPT_VUE, re.S)
    assert m
    return m.group(1)


def test_前端_納管失敗可以勾也有一鍵納管():
    src = _can_probe_src()
    assert "'onboard_failed'" in src and "'needs_credential'" in src
    assert "r.route !== 'ssh'" in src, "只有走 SSH 的才能佈收集帳號"
    # 一鍵納管按鈕跟勾選走同一個條件（不會一個給、一個不給）
    assert re.search(r'<button v-else-if="canProbe\(r\)"\s*\n\s*class="btn small primary"', ADOPT_VUE)


def test_前端_fail_closed_VMware儲存網路AS400都不給():
    src = _can_probe_src()
    m = re.search(r"const NO_ONBOARD_TABS = \[(.*?)\]", ADOPT_VUE)
    tabs = set(re.findall(r"'(\w+)'", m.group(1)))
    assert {"esxi", "storage", "network", "nok_other", "import", "agent"} <= tabs
    assert "NO_ONBOARD_TABS.includes" in src
    assert "r.guide" in src, "有指路（另有納管方式）的一律不給"
    assert "!r.mainframe_onboardable" in src, "大型主機頁簽裡只有 AIX 可以（AS400 不行）"
    assert f"'{of.SHORT_VMWARE}'" in src, "上次被 VMware 天條擋下的不給重試"


def test_後端_上次失敗的機器banner自報VMware_仍判另有納管方式_不是納管失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _audit(conn, "192.0.2.31", "connect", "登入被拒——帳號或密碼不對")

        def banner(ip):
            return False, "Permission denied. Remote banner: VMware ESXi 8.0"
        out = cd.run_dispatch(conn, "192.0.2.31", prober=_ssh_open, ssh_prober=banner)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, r
        conn.close()


def test_後端_上次被VMware天條擋下_不給重試():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _audit(conn, "192.0.2.32", "refused", "ESXi 不可納管，請改由 vCenter／RVTools 取得")
        out = cd.run_dispatch(conn, "192.0.2.32", prober=_ssh_open, ssh_prober=_ssh_denied)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, r
        assert r["tab"] == cd.TAB_ESXI
        conn.close()


def test_後端_上次被平台白名單認出RHCOS_歸OCP_不給重試():
    """2026-10-21：不登 CIA 的新主機，連上去才認出是 OCP 節點（RHCOS／ostree）——歸 OCP 頁簽。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for ip, msg in (
                ("192.0.2.40", "這台是 Linux，但不是允許的發行版（偵測到：Red Hat Enterprise Linux CoreOS 414）——未納管"),
                ("192.0.2.41", "偵測到設備標記：ostree 系統（CoreOS／RHCOS／RHEL for Edge，由平台管理）——未納管")):
            _audit(conn, ip, "refused_platform", msg)
            out = cd.run_dispatch(conn, ip, prober=_ssh_open, ssh_prober=_ssh_denied)
            r = out["results"][0]
            assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, r
            assert r["tab"] == cd.TAB_OCP and r["not_onboardable_kind"] == "immutable"
        # 被白名單擋、但不是 RHCOS（例：Alma）→ 照舊是納管失敗，不會被誤歸 OCP
        _audit(conn, "192.0.2.42", "refused_platform", "這台是 Linux，但不是允許的發行版（偵測到：AlmaLinux 9）——未納管")
        r = cd.run_dispatch(conn, "192.0.2.42", prober=_ssh_open, ssh_prober=_ssh_denied)["results"][0]
        assert r["tab"] != cd.TAB_OCP and r["status"] == cd.STATUS_ONBOARD_FAILED
        conn.close()


def test_後端_登記ESXi又失敗過_仍然不納管_不試連():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "E-1", "192.0.2.33", "VMware ESXi 8.0.3", "esx-example-33")
        _audit(conn, "192.0.2.33", "connect", "登入被拒——帳號或密碼不對")
        called = []
        out = cd.run_dispatch(conn, "192.0.2.33", prober=_ssh_open,
                              ssh_prober=lambda ip: called.append(ip) or (False, "x"))
        assert out["results"][0]["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert called == []
        conn.close()


def test_後端_一般失敗過的AIX_是納管失敗_在大型主機頁簽_可佈帳號():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _hw(conn, "A-1", "10.99.22.1", "AIX 7.2", "aix-example-01")
        _audit(conn, "10.99.22.1", "connect", "登入被拒——帳號或密碼不對")
        out = cd.run_dispatch(conn, "10.99.22.1", prober=_ssh_open, ssh_prober=_ssh_denied)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_ONBOARD_FAILED
        assert r["route"] == cd.ROUTE_SSH
        assert r["tab"] == cd.TAB_MAINFRAME and r["mainframe_onboardable"] is True
        conn.close()


# ===================== 3. 上次失敗短原因：各版本的真實訊息 =====================

REAL_MESSAGES = [
    # 現行 onboard_engine.classify_failure／_CONNECT_ERRORS
    ("connect", "登入被拒——帳號或密碼不對，或該帳號不允許 SSH 登入", of.SHORT_BAD_CRED),
    ("connect", "探測失敗，未執行納管：連不到這台的 22 埠（逾時）——確認機器開著、SSH 服務有跑、"
                "防火牆放行；Windows 通常沒開 22，該走 WinRM", of.SHORT_TIMEOUT),
    ("connect", "對方拒絕連線——22 埠沒有服務在聽", of.SHORT_UNREACHABLE),
    ("connect", "路由不通——網段之間可能沒開通", of.SHORT_UNREACHABLE),
    ("connect", "解析不到這個位址", of.SHORT_UNREACHABLE),
    ("connect", "主機金鑰驗證失敗", of.SHORT_HOSTKEY),
    ("connect", "Host key verification failed：10.99.1.5 的主機金鑰跟 known_hosts 記錄的不同"
                "（可能重灌過，也可能是中間人）", of.SHORT_HOSTKEY),
    ("connect", "登入方式不被接受——該機可能不允許密碼登入", of.SHORT_NO_PASSWORD_AUTH),
    ("connect", "22 埠有回應但讀不到 SSH 服務的開場訊息——可能不是 SSH、服務卡住，或連線被中間設備切斷",
     of.SHORT_NOT_SSH),
    ("connect", "逾時被中止——多半是登入帳號的 sudo 在等密碼，或連線卡住", of.SHORT_TIMEOUT),
    ("connect", "SSH 連線失敗：金鑰交換/加密演算法不相容（Incompatible ssh peer）——老設備常見，"
                "需啟用 legacy 演算法", of.SHORT_CIPHER),
    ("connect", "SSH 連線失敗：這台收集端的 paramiko 沒裝好（ImportError: x）", of.SHORT_COLLECTOR),
    ("execute", "進得去，但執行身分不是 root（腳本需要 root 才能建帳號）", of.SHORT_SUDO),
    ("execute", "進得去，但這台的 sudo 設了 requiretty（不給終端機就不准執行）", of.SHORT_SUDO),
    ("execute", "進得去，但 sudo 需要密碼——本流程沒辦法回答它", of.SHORT_SUDO),   # 2026-08 版
    ("execute", "進得去，但 sudo 這一關沒過（密碼或權限問題）", of.SHORT_SUDO),
    ("execute", "腳本執行未回報完成", of.SHORT_SCRIPT_PREFIX + "執行"),
    ("verify", "腳本執行未回報完成", of.SHORT_SCRIPT_PREFIX + "驗證"),
    ("refused", "ESXi 不可納管，請改由 vCenter／RVTools 取得（未建帳號、未佈金鑰、未改 sudoers）",
     of.SHORT_VMWARE),
    ("connect", "未知平台：hpux", of.SHORT_PLATFORM),
    ("connect", "帳號名稱 webit3scan 超過 AIX max_logname 8——mkuser 會直接拒絕。請改用較短的名字",
     of.SHORT_NAME_TOO_LONG),
    # 2026-08-16 公司機：收集金鑰還沒產生（收集端的問題，不是目標主機）
    ("connect", "讀不到收集端公鑰（/opt/webit3/.collector_key.pub）：[Errno 2] No such file or "
                "directory。這台還沒產生收集金鑰——請先完成安裝", of.SHORT_COLLECTOR),
    ("connect", "沒有設定收集器位址（ASSET_COLLECTOR_IP）——納管腳本需要它來限制金鑰來源。", of.SHORT_COLLECTOR),
    # 2026-08 sshpass 時代：「連線／執行失敗：<例外>」
    ("connect", "連線／執行失敗：Command '['sshpass', ...]' timed out after 60 seconds", of.SHORT_TIMEOUT),
    ("connect", "連線／執行失敗：[Errno 111] Connection refused", of.SHORT_UNREACHABLE),
    # 2026-09-22 起 paramiko 認證失敗（AIX 那批：publickey,password 都被拒，附 AIX 提示）
    ("connect", "Permission denied（帳號或密碼不對，或該帳號不允許 SSH 登入）｜原始錯誤："
                "AuthenticationException: Authentication failed.｜允許的方式：publickey,password"
                "｜AIX 常見原因：**收集帳號被鎖**。AIX 的 account_locked=true 會連公鑰登入一起擋",
     of.SHORT_BAD_CRED_OR_LOCKED),
    ("connect", "Permission denied（帳號或密碼不對，或該帳號不允許 SSH 登入）｜原始錯誤："
                "AuthenticationException: Authentication failed.", of.SHORT_BAD_CRED),
    ("connect", "SSH 連線失敗：SSHException: Error reading SSH protocol banner", of.SHORT_NOT_SSH),
    ("verify", "account_locked: true", of.SHORT_LOCKED),
    # 2026-09-22 起：納管後收集／本機執行後驗證（_collect_diagnosis）
    ("verify", "收集失敗：送出平台=aix、判定平台=linux、實際 uname -s=AIX、收到欄位=0 個。"
               "**判定平台與實際不符——拿了 linux 的指令去問 AIX，所以每個欄位都空**",
     of.SHORT_PLATFORM_MISMATCH),
    ("collect", "收集失敗：送出平台=aix、判定平台=aix、實際 uname -s=問不到、收到欄位=0 個。"
                "連 uname -s 都問不到，可能是收集帳號連不進去（而不是平台判錯）。原始錯誤："
                "Permission denied (publickey).", of.SHORT_COLLECT_LOGIN),
    ("verify", "收集失敗：送出平台=linux、判定平台=linux、實際 uname -s=Linux、收到欄位=0 個",
     of.SHORT_COLLECT_EMPTY),
    ("root_key", "root 收集金鑰標記失敗：database is locked", of.SHORT_ROOT_KEY),
    ("account", "新帳號 webit3sc 連不進去", of.SHORT_ACCOUNT_MIGRATE),
    # 批次統一密碼
    ("connect", "密碼都不對", of.SHORT_BAD_CRED),
    ("connect", "探測失敗，未執行納管：登入失敗或連線異常（帳密可能不對，或該台不支援這個探測方式）",
     of.SHORT_BAD_CRED),
]


def test_短原因_各版本真實訊息都認得():
    bad = [(s, m[:40], of.short_reason(s, m), want) for s, m, want in REAL_MESSAGES
           if of.short_reason(s, m) != want]
    assert not bad, bad


def test_短原因_其他只剩真的認不出來的():
    others = [m for s, m, _ in REAL_MESSAGES if of.short_reason(s, m) == of.SHORT_OTHER]
    assert others == []
    # 階段也不明、訊息也認不出來，才是「其他」
    assert of.short_reason(None, "something odd") == of.SHORT_OTHER
    assert of.short_reason("", "") == of.SHORT_OTHER


def test_AIX提示只是推測_不可以講成確定被鎖():
    msg = next(m for _, m, _ in REAL_MESSAGES if "AIX 常見原因" in m)
    assert "account_locked=true" in msg
    assert of.short_reason("connect", msg) != of.SHORT_LOCKED


# ===================== 4. 搜尋（v1.469.0 起只在目前頁簽）＋資產庫提示 =====================

def test_前端_搜尋只在目前頁簽_其他頁簽另列可跳():
    """v1.468.4 推翻 10-18 的「一輸入就跨全部頁簽」（使用者：「我都勾選 SSH 了，為什麼
    Push Agent、只能匯入…都在裡面？」）。搜尋只在目前頁簽；別的頁簽另列「其他頁簽還有 N 筆」。"""
    body = ADOPT_VUE.split("const dVisible = computed(() => dSearching.value")[1][:200]
    assert "dBase.value.filter" in body, "搜尋要從目前頁簽（dBase）找"
    assert "dSorted.value.filter" not in body
    assert "其他頁簽還有" in ADOPT_VUE and "jumpToHits(h.tab)" in ADOPT_VUE
    # 所在頁簽欄只在「全部」頁簽
    assert "const dShowTabCol = computed(() => dRoute.value === 'all' && !dStatus.value)" in ADOPT_VUE
    assert re.search(r'<SortTh v-if="dShowTabCol" k="tab"', ADOPT_VUE)


def test_前端_掃描結果沒有但資產庫有_提示還沒掃過():
    assert "資產裡有" in ADOPT_VUE and "還沒掃過" in ADOPT_VUE
    assert "'/api/assets'" in ADOPT_VUE
    assert "/assets/${dAssetHits[0].asset_serial}" in ADOPT_VUE


# ===================== 5. 隱藏已納管預設打勾 =====================

def test_前端_隱藏已納管_沒有記錄時預設打勾():
    assert "const dHideOnboarded = ref(true)" in ADOPT_VUE
    # 只有明確存過 '0'（使用者自己取消過）才不打勾
    assert re.search(r"if \(v === '0'\) dHideOnboarded\.value = false", ADOPT_VUE)


# ===================== 6. 改分類選單＝頁簽，一份定義 =====================

def test_分類頁簽是唯一一份_順序與名稱固定():
    tabs = [t["tab"] for t in cd.category_tabs()]
    assert tabs == ["ssh", "winrm", "agent", "mainframe", "storage", "network", "esxi",
                    "ocp", "nok_other", "import"]
    labels = [t["label"] for t in cd.category_tabs()]
    assert labels == ["SSH 收集", "WinRM 收集", "Push Agent", "大型主機（AIX／AS400）",
                      "儲存設備", "網路設備", "ESXi", "OCP", "其他（另有方式）", "只能匯入"]


def test_頁簽集合等於選單集合():
    chips = {t["tab"] for t in cd.category_tabs()}
    menu = {t["tab"] for t in cd.category_tabs()}      # 選單也是 category_tabs，一個不少
    assert chips == menu
    # 可搬的＝舊介面 move_choices；不能搬的（連線狀態）要講得出原因
    movable = {t["tab"] for t in cd.move_choices()}
    assert movable == set(cd.MOVABLE_TABS)
    for t in cd.category_tabs():
        if not t["movable"]:
            assert t["why_not"], t["tab"]


def test_大型主機有AIX與AS400兩個子選項_只有AIX會試連():
    mf = next(t for t in cd.category_tabs() if t["tab"] == cd.TAB_MAINFRAME)
    ch = {c["os_type"]: c for c in mf["choices"]}
    assert set(ch) == {"AIX", "IBM i"}
    assert ch["AIX"]["onboardable"] is True and ch["IBM i"]["onboardable"] is False
    assert "AS400" in ch["IBM i"]["label"]
    assert cd.os_type_for_tab_choice(cd.TAB_MAINFRAME, "IBM i") == "IBM i"
    assert cd.os_type_for_tab_choice(cd.TAB_MAINFRAME, None) == "AIX"
    assert cd.os_type_for_tab_choice(cd.TAB_MAINFRAME, "Linux") is None, "不屬於這個頁簽的子類別要拒絕"
    assert cd.os_type_for_tab_choice(cd.TAB_AGENT, None) is None
    # 往返：寫進去的子類別讀回來都在大型主機頁簽
    for os_type in ch:
        assert cd.tab_for_os_type(os_type) == cd.TAB_MAINFRAME


def test_前端_頁簽列與改分類選單都讀同一支_不各自寫死():
    assert "'/api/adopt/move-choices'" in ADOPT_VUE
    assert 'v-for="t in catTabs"' in ADOPT_VUE, "頁簽列要從後端那份定義產生"
    # 不可以再有寫死的分類頁簽按鈕
    for tab in ("ssh", "winrm", "agent", "mainframe", "import"):
        assert f"setRoute('{tab}')" not in ADOPT_VUE, f"頁簽 {tab} 還寫死在前端"
    assert "TAB_LABEL_MAP" not in ADOPT_VUE
    m = re.search(r"const moveTargetChoices = computed<MoveOpt\[\]>\(\(\) => \{(.*?)\n\}\)", ADOPT_VUE, re.S)
    assert "catTabs.value" in m.group(1)
    assert "os_type: restore ? null" in ADOPT_VUE, "大型主機的子類別要送給後端"


def test_資產明細頁搬移分類也讀同一支():
    assert "'/api/adopt/move-choices'" in ASSET_VUE
    assert "{ choices: string[] }>('/api/os-type-overrides')" not in ASSET_VUE


def test_搬移API吃子類別():
    assert "os_type_for_tab_choice(body.tab, body.os_type)" in ROUTER_SRC
    assert '"tabs": collect_dispatch.category_tabs()' in ROUTER_SRC
