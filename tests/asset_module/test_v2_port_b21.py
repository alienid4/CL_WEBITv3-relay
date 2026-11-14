"""V2 移植第 21 批（2.056.0）：V56 舊系統設定拆開搬——第 1 批「收集憑證」（⚠D5）改 V2 原生 ⚙ 頁籤。
純搬移：同一組 /api/credentials；密碼永不出現在 GET 回應與畫面上。全部假資料。"""
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import credential_store as cs  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

LOGIN_PW = "test-password-123"
FAKE_PW = "FAKE-TEST-PW-not-real-4412"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _calls(s, only=None):
    out = set()
    for m in re.finditer(r"apiFetch(?:<[^>]*>)?\(\s*[`']([^`']+)[`'](?:\s*,\s*\{([^}]*))?", s):
        path = re.sub(r"\$\{[^}]*\}", "{x}", m.group(1)).split("?")[0]
        if only and not path.startswith(only):
            continue
        mm = re.search(r"method:\s*'(\w+)'", m.group(2) or "")
        out.add((mm.group(1) if mm else "GET", path))
    return out


def test_端點集合跟舊頁的收集憑證一模一樣():
    new = _calls(_src("components/v2/Credentials.vue"))
    old = _calls(_src("pages/settings/index.vue"), only="/api/credentials")
    assert new == old, (new - old, old - new)
    assert new == {("GET", "/api/credentials"), ("POST", "/api/credentials"), ("PUT", "/api/credentials/{x}"),
                   ("DELETE", "/api/credentials/{x}"), ("POST", "/api/credentials/{x}/test")}


def test_畫面規則跟舊頁一樣_密碼送完就清():
    n = _src("components/v2/Credentials.vue")
    o = _src("pages/settings/index.vue")
    for frag in ("body: { username: newCred.username, scope: newCred.scope, note: newCred.note, password: newCred.password || null }",
                 "await apiFetch('/api/credentials', { method: 'POST', body: { ...newCred } })",
                 "body: credTest.kind === 'winrm' ? { ip: credTest.ip.trim() } : {}"):
        assert frag in n and frag in o, frag
    assert n.count("newCred.password = ''") >= 3                       # 成功、finally、取消編輯都清
    assert 'type="password" autocomplete="new-password"' in n


def test_設定頁_收集憑證自己一個頁籤_限管理員_連結改過來():
    s = _src("pages/v2/settings.vue")
    assert "{ k: 'cred', label: '收集憑證' }" in s and "'cred'," in s.split("const ADMIN_ONLY")[1].split("\n")[0]
    assert '<V2Credentials v-else-if="tab === \'cred\'" key="cred" />' in s
    for p in ("components/v2/IntegrationStorage.vue", "components/v2/IntegrationVrops.vue", "components/v2/IntegrationWhatsup.vue"):
        assert 'to="/v2/settings?st=cred"' in _src(p) and "sub=credentials" not in _src(p), p
    o = _src("pages/settings/index.vue")
    assert '<p v-if="props.view" class="credhint"' in o and '<div v-else class="card">' in o   # 嵌在 V2 時舊頁不再顯示憑證卡


@pytest.fixture()
def client(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    monkeypatch.setattr(cs, "_load_or_create_key", lambda path=cs.KEY_PATH_DEFAULT: key)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)

        def _dep():
            c = db.get_connection(p)
            try:
                yield c
            finally:
                c.close()
        api.app.dependency_overrides[api.get_db] = _dep
        c = db.get_connection(p)
        db.create_user(c, "admin", auth.hash_password(LOGIN_PW))
        c.close()
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "admin", "password": LOGIN_PW}).status_code == 200
        yield cl, p
        api.app.dependency_overrides.clear()


def test_GET回應永遠沒有密碼也沒有密文(client):
    cl, p = client
    for kind in ("winrm", "ssh", "whatsup", "vrops", "storage"):
        r = cl.post("/api/credentials", json={"name": f"c-{kind}", "kind": kind, "username": "svc_x", "password": FAKE_PW,
                                              "scope": None, "note": "t"})
        assert r.status_code in (200, 201), r.text
        assert FAKE_PW not in r.text
    r = cl.put("/api/credentials/c-winrm", json={"username": "svc_y", "scope": "", "note": "", "password": FAKE_PW + "2"})
    assert r.status_code == 200 and FAKE_PW not in r.text
    g = cl.get("/api/credentials")
    assert g.status_code == 200
    body = g.text
    assert FAKE_PW not in body and (FAKE_PW + "2") not in body
    c = db.get_connection(p)
    enc = [r[0] for r in c.execute("SELECT * FROM collect_credential")]
    blobs = [str(v) for row in c.execute("SELECT * FROM collect_credential") for v in row if isinstance(v, (str, bytes))]
    c.close()
    assert enc
    for b in blobs:                                                    # 連資料庫裡的密文都不可以出現在回應裡
        if len(b) > 40:
            assert b not in body
    for row in g.json():
        assert not {"password", "secret", "ciphertext", "secret_enc", "password_enc"} & set(row)
        assert row["has_secret"] is True


# ===== 2.056.1：V56 第 2 批「連線設定」（⚠D5）＋收集憑證刪除確認 =====

def test_連線設定_端點集合跟舊頁一模一樣():
    new = _calls(_src("components/v2/Connections.vue"))
    old = _calls(_src("pages/settings/index.vue"), only="/api/connections")
    old.discard(("PATCH", "/api/connections/{x}/scan-time"))             # 各網段排程時間在「掃描排程」分頁，不在這批
    assert new == old, (new - old, old - new)
    n = _src("components/v2/Connections.vue")
    assert "/api/connections/suggest-segments?fmt=txt" in n                 # txt 下載（fetch，不經 apiFetch）
    for frag in ("if (e.password) body.password = e.password",
                 "username: newRow.username || null, password: newRow.password || null",
                 "if (!confirm(`確定要刪除連線設定「${row.name}」？此操作無法復原。`)) return"):
        assert frag in n, frag
    assert "password: ''" in n and "password: c." not in n                  # 每列密碼框從空白開始，不預填


def test_設定頁_連線設定自己一個頁籤_舊書籤轉過來():
    s = _src("pages/v2/settings.vue")
    assert "{ k: 'conn', label: '連線設定' }" in s and "'conn'," in s.split("const ADMIN_ONLY")[1].split("\n")[0]
    assert "q('st') === 'dev' && q('sub') === 'connections' ? 'conn'" in s
    assert "dev: [{ k: 'modules', label: '功能模組管理' }]" in s
    assert "if (t === 'connections' || t === 'diagram') return to('/v2/settings', { st: 'conn' })" in _src("utils/v2LegacyMap.ts")


def test_收集憑證刪除前先確認():
    n = _src("components/v2/Credentials.vue")
    i = n.index("async function delCred(")
    body = n[i:n.index("\n}\n", i)]
    assert body.index("if (!confirm(") < body.index("method: 'DELETE'")


def test_連線設定_GET回應沒有密碼(client):
    cl, p = client
    r = cl.post("/api/connections", json={"name": "c1", "connection_type": None, "target": "10.99.9.0/24", "port": 22,
                                         "username": "svc_x", "password": FAKE_PW})
    assert r.status_code in (200, 201), r.text
    assert FAKE_PW not in r.text
    cid = cl.get("/api/connections").json()[0]["id"]
    r = cl.put(f"/api/connections/{cid}", json={"name": "c1", "connection_type": None, "target": "10.99.9.0/24", "port": 22,
                                              "username": "svc_x", "password": FAKE_PW + "2"})
    assert r.status_code == 200 and FAKE_PW not in r.text
    g = cl.get("/api/connections")
    assert FAKE_PW not in g.text and (FAKE_PW + "2") not in g.text
    for row in g.json():
        assert "password" not in row and row["has_password"] is True


# ===== 2.056.2：V56 第 3 批「備份與還原」（⚠D4＋D5 匯出金鑰）=====

def test_備份與還原_端點集合跟舊頁一模一樣():
    n, o = _src("components/v2/Backup.vue"), _src("pages/settings/index.vue")
    new = _calls(n, only="/api/backup") | _calls(n, only="/api/export-key")
    old = _calls(o, only="/api/backup") | _calls(o, only="/api/export-key")
    assert new == old, (new - old, old - new)
    assert new == {("GET", "/api/backup/status"), ("POST", "/api/backup/run"), ("PUT", "/api/backup/offsite"),
                   ("GET", "/api/export-key"), ("POST", "/api/export-key/generate"), ("PUT", "/api/export-key/recipient"),
                   ("PUT", "/api/export-key/encryption")}
    for frag in ("/api/backup/dump?fmt=${dumpFormat.value}${splitQ}", "`${apiBase}/api/backup/restore`, { method: 'POST', credentials: 'include', body: form }"):
        assert frag in n, frag                                          # fetch 下載／上傳（不經 apiFetch）
    assert "/api/backup/dump?fmt=${dumpFormat.value}${splitQ}" in o and "/api/backup/restore`, {" in o


def test_還原照舊在功能開關與確認之後():
    n = _src("components/v2/Backup.vue")
    assert '<div v-if="isEnabled(\'restore\')"' in n
    assert "const RESTORE_CONFIRM_WORD = '覆蓋還原'" in n
    assert ':disabled="restoreRunning || !restoreFile || restoreConfirmText !== RESTORE_CONFIRM_WORD"' in n
    i = n.index("async function runRestore()")
    body = n[i:n.index("\n}\n", i)]
    assert body.index("restoreConfirmText.value !== RESTORE_CONFIRM_WORD") < body.index("/api/backup/restore")
    assert body.count("navigateTo('/login?reason=restored')") == 2      # 成功與 401（上一次已成功）都送去重新登入
    s = _src("pages/v2/settings.vue")
    assert '<V2Backup v-else-if="tab === \'backup\'" key="backup" />' in s and 'view="backup"' not in s


def test_匯出金鑰GET不回私鑰(client):
    cl, p = client
    r = cl.get("/api/export-key")
    assert r.status_code == 200
    assert "-----BEGIN" not in r.text and "private_key" not in r.json() and "private_key_pem" not in r.json()   # 只有路徑、公鑰與指紋
    b = cl.get("/api/backup/status")
    assert b.status_code == 200 and "-----BEGIN" not in b.text


# ===== 2.056.3：V56 第 4 批（功能模組、帳號備註、Windows 組態檢核開關、架構圖）=====

def test_功能模組_端點集合跟舊頁一樣_停用登入要打字確認():
    n, o = _src("components/v2/Modules.vue"), _src("pages/settings/index.vue")
    new = _calls(n, only="/api/admin")
    old = _calls(o, only="/api/admin/auth-disabled") | _calls(o, only="/api/admin/write-guard")
    assert new == old == {("GET", "/api/admin/auth-disabled"), ("PUT", "/api/admin/auth-disabled"),
                          ("GET", "/api/admin/write-guard"), ("PUT", "/api/admin/write-guard")}
    for frag in ("body: { enabled, confirm: enabled ? authOffConfirm.value : undefined }",
                 ":disabled=\"authOffBusy || authOffConfirm !== 'DISABLE-LOGIN'\"",
                 "if ((!on && wGuard.value?.enabled) || (on && wGuard.value?.auth_disabled)) {",
                 "body: { enabled: on, password }", "await setEnabled(moduleKey, enabled)"):
        assert frag in n, frag
    assert "body: { enabled, confirm: enabled ? authOffConfirm.value : undefined }" in o


def test_帳號備註與Windows組態檢核_端點集合跟舊頁一樣():
    n, o = _src("components/v2/CollectOptions.vue"), _src("pages/settings/index.vue")
    new = _calls(n)
    old = _calls(o, only="/api/accounts/collect-config") | _calls(o, only="/api/config-audit/windows-settings")
    assert new == old, (new - old, old - new)
    assert "body: { account: acAccount.value, account_comment: acEdit.value }" in n and "body: { enabled: want }" in n
    assert '<div v-if="!props.view" class="card" style="margin-bottom:16px">' in o          # 嵌在 V2 時舊頁不重複顯示
    assert '<div v-if="!props.view" class="card" style="margin-top:16px">' in o


def test_設定頁接線_架構圖在連線設定():
    s = _src("pages/v2/settings.vue")
    assert "<V2Modules v-else-if=\"tab === 'dev' && sub === 'modules'\"" in s
    assert "<V2CollectOptions key=\"collectopts\" />" in s
    c = _src("components/v2/Connections.vue")
    assert "diagramNodes" in c and "edge-green" in c and "資產盤點服務" in c


# ===== 2.056.4：V56 第 5 批（寄信設定 SMTP／弱點通知）=====

def _fn(s, head):
    i = s.index(head)
    return s[i:s.index("\n}\n", i)]


def test_寄信設定_端點集合跟舊頁一樣_body不改_立刻寄要先確認():
    n, o = _src("components/v2/VulnMail.vue"), _src("pages/settings/index.vue")
    new, old = _calls(n), _calls(o, only="/api/vuln-notify")
    assert new == old == {("GET", "/api/vuln-notify/settings"), ("PUT", "/api/vuln-notify/settings"), ("GET", "/api/vuln-notify/recipients"),
                          ("POST", "/api/vuln-notify/preview"), ("POST", "/api/vuln-notify/run")}, (new, old)
    assert _fn(n, "function mailBody()") == _fn(o, "function mailBody()")       # 送出的內容一字不改
    run = _fn(n, "async function runMailNow()")
    assert run.index("if (!confirm(") < run.index("/api/vuln-notify/run")      # 真的寄信之前先確認
    s = _src("pages/v2/settings.vue")
    assert "<V2VulnMail v-else-if=\"tab === 'notify' && sub === 'mail'\" key=\"mail\" />" in s


# ===== 2.056.5：第 21 批審查修正（伺服器端還原守門、連線帳號空白、export-key 限管理員）=====

@pytest.fixture()
def rclient(tmp_path, monkeypatch):
    """整庫還原會覆蓋 get_db_path() 那顆檔：一定要指到暫存庫（dependency_overrides 蓋不到它）。"""
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)
    monkeypatch.setattr(api, "get_db_path", lambda: p)

    def _dep():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _dep
    c = db.get_connection(p)
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, name, auth.hash_password(LOGIN_PW), role=role)
    db.insert_hardware(c, asset_serial="LIVE-1", hostname="live", ip="10.99.6.1")
    c.commit()
    c.close()
    up = tmp_path / "up.db"
    db.init_db(up)
    u = db.get_connection(up)
    db.create_user(u, "admin", auth.hash_password(LOGIN_PW), role="admin")
    db.insert_hardware(u, asset_serial="UPLOAD-1", hostname="up", ip="10.99.6.2")
    u.commit()
    u.close()

    def login(name="admin"):
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": name, "password": LOGIN_PW}).status_code == 200
        return cl
    yield login, p, up.read_bytes()
    api.app.dependency_overrides.clear()


def _serials(p):
    c = db.get_connection(p)
    try:
        return {r[0] for r in c.execute("SELECT asset_serial FROM hardware")}
    finally:
        c.close()


def _restore(cl, data, confirm):
    form = {} if confirm is None else {"confirm": confirm}
    return cl.post("/api/backup/restore", files={"file": ("up.db", data, "application/octet-stream")}, data=form)


def test_還原_伺服器端_開關關著一律拒絕_就算確認字對(rclient):
    login, p, data = rclient
    cl = login()
    r = _restore(cl, data, "覆蓋還原")
    assert r.status_code == 403, r.text
    assert _serials(p) == {"LIVE-1"}                                    # 一筆都沒動


@pytest.mark.parametrize("word", [None, "", "覆蓋", "覆蓋還原 ", "REPLACE"])
def test_還原_伺服器端_確認字不對或沒給一律拒絕(rclient, word):
    login, p, data = rclient
    cl = login()
    assert cl.put("/api/feature-flags/restore", json={"enabled": True}).status_code == 200
    r = _restore(cl, data, word)
    assert r.status_code == 400, r.text
    assert "覆蓋還原" in r.json()["detail"]
    assert _serials(p) == {"LIVE-1"}


def test_還原_伺服器端_開關開著且確認字對才會覆蓋_照舊先存證(rclient):
    login, p, data = rclient
    cl = login()
    assert cl.put("/api/feature-flags/restore", json={"enabled": True}).status_code == 200
    r = _restore(cl, data, "覆蓋還原")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["pre_restore_backup"]   # 覆蓋前存證照舊
    assert _serials(p) == {"UPLOAD-1"}


def test_還原_兩道守門在讀檔之前():
    s = (ROOT / "APP" / "asset-module" / "backend" / "routers" / "system.py").read_text(encoding="utf-8")
    body = s[s.index("def backup_restore_endpoint("):s.index('@router.get("/api/feature-flags")')]
    i_flag = body.index('get_feature_flag(_c, "restore")')
    i_word = body.index("if confirm != RESTORE_CONFIRM_WORD")
    i_read = body.index("file.file.read()")
    assert i_flag < i_word < i_read
    assert 'RESTORE_CONFIRM_WORD = "覆蓋還原"' in s
    for f in ("components/v2/Backup.vue", "pages/settings/index.vue"):
        assert "form.append('confirm', restoreConfirmText.value)" in _src(f), f


def test_export_key_GET限管理員(rclient):
    login, p, _ = rclient
    assert login("admin").get("/api/export-key").status_code == 200
    for name in ("alice", "vera"):
        assert login(name).get("/api/export-key").status_code == 403, name


def test_連線設定_PUT密碼空白或沒給都保留原本的密碼(rclient):
    login, p, _ = rclient
    cl = login()
    r = cl.post("/api/connections", json={"name": "scan-x", "connection_type": "網路掃描", "target": "10.99.9.0/24", "port": 22,
                                          "username": "svc_x", "password": FAKE_PW})
    assert r.status_code in (200, 201), r.text
    cid = next(x["id"] for x in cl.get("/api/connections").json() if x["name"] == "scan-x")

    def stored():
        c = db.get_connection(p)
        try:
            return dict(c.execute("SELECT * FROM connections WHERE id = ?", (cid,)).fetchone())
        finally:
            c.close()
    before = stored()
    pw_cols = [k for k in before if "pass" in k or "secret" in k]
    assert pw_cols and any(before[k] for k in pw_cols)
    base = {"name": "scan-x", "connection_type": "網路掃描", "target": "10.99.9.0/24", "port": 22, "username": None}
    for extra in ({}, {"password": ""}, {"password": None}):
        r = cl.put(f"/api/connections/{cid}", json={**base, **extra})
        assert r.status_code == 200, (extra, r.text)
        after = stored()
        assert {k: after[k] for k in pw_cols} == {k: before[k] for k in pw_cols}, extra
        assert FAKE_PW not in r.text
    assert next(x for x in cl.get("/api/connections").json() if x["id"] == cid)["has_password"] is True


# ===== 2.056.6：V56 第 6 批（掃描排程、每週整片收集、收集節奏）=====

def test_掃描排程_端點集合跟舊頁一樣_body不改():
    n, o = _src("components/v2/ScanSchedule.vue"), _src("pages/settings/index.vue")
    new = _calls(n)
    old = _calls(o, only="/api/scan/schedule") | _calls(o, only="/api/collect/dispatch")
    # 2.066.0（G3）：V2 多讀一支唯讀的 GET /api/scan/status 顯示「最近一次掃描」，舊頁的端點集合不變
    assert new - {("GET", "/api/scan/status")} == old == {("GET", "/api/scan/schedule"), ("PUT", "/api/scan/schedule"), ("GET", "/api/collect/dispatch/weekly"),
                          ("PUT", "/api/collect/dispatch/weekly"), ("GET", "/api/collect/dispatch/settings"),
                          ("PUT", "/api/collect/dispatch/settings")}, (new, old)
    for head in ("async function saveSchedule()", "async function saveDispatchCfg()"):
        nb, ob = _fn(n, head), _fn(o, head)
        a = nb[nb.index("await apiFetch"):nb.index("showToast(")]
        b = ob[ob.index("await apiFetch"):ob.index("showToast(")]
        assert re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b), head
    assert "{ method: 'PUT', body: { ...wEdit } }" in n and "{ method: 'PUT', body: { ...wEdit } }" in o
    assert "<ScanScope />" in n and "<ScanScope />" in o                # 掃描範圍：同一個共用元件
    assert "/scan-time'" not in n and "scan-time`" not in n          # 舊頁 v1.197.0 起就沒有畫面呼叫它，不加回來
    s = _src("pages/v2/settings.vue")
    assert "<V2ScanSchedule v-else-if=\"tab === 'identity' && sub === 'schedule'\" key=\"schedule\" />" in s


# ===== 2.056.7：V56 第 7 批（自動納管，⚠D1）=====

def test_自動納管_端點集合跟舊頁一樣_body不改():
    n, o = _src("components/v2/AutoOnboard.vue"), _src("pages/settings/index.vue")
    new, old = _calls(n), _calls(o, only="/api/auto-onboard")
    assert new == old == {("GET", "/api/auto-onboard"), ("PATCH", "/api/auto-onboard/enabled"), ("POST", "/api/auto-onboard/segments"),
                          ("PATCH", "/api/auto-onboard/segments/{x}/enabled"), ("DELETE", "/api/auto-onboard/segments/{x}"),
                          ("POST", "/api/auto-onboard/run")}, (new, old)
    for frag in ("method: 'PATCH', body: { enabled: next },",
                 "method: 'POST', body: { prefix: newSeg.prefix.trim(), enabled: true, note: newSeg.note || null },",
                 "await apiFetch<any>('/api/auto-onboard/run', { method: 'POST' })"):
        assert frag in n and frag in o, frag
    assert n.count("runAutoOnboardNow") == 2 and n.count("apiFetch<any>('/api/auto-onboard/run'") == 1                       # 沒有新增任何觸發點（只有「立即執行」一處）
    s = _src("pages/v2/settings.vue")
    assert "<V2AutoOnboard v-else-if=\"tab === 'identity' && sub === 'autoonboard'\" key=\"autoonboard\" />" in s


def test_自動納管_授權網段與稽核_GET不帶密碼(rclient):
    login, p, _ = rclient
    cl = login()
    r = cl.post("/api/credentials", json={"name": "ssh-a", "kind": "ssh", "username": "svc_x", "password": FAKE_PW, "scope": None, "note": None})
    assert r.status_code in (200, 201), r.text
    assert cl.post("/api/auto-onboard/segments", json={"prefix": "10.99.1.", "enabled": True, "note": None}).status_code == 200
    g = cl.get("/api/auto-onboard")
    assert g.status_code == 200 and FAKE_PW not in g.text
    assert {"enabled", "segments", "recent"} <= set(g.json())
    for name in ("alice", "vera"):                                     # 只有管理員能動納管設定
        assert login(name).patch("/api/auto-onboard/enabled", json={"enabled": True}).status_code == 403, name
        assert login(name).post("/api/auto-onboard/run").status_code == 403, name


# ===== 2.056.8：V56 第 8 批（收集金鑰 Linux，⚠D5 D1）＋ V56 收尾 =====

def test_收集金鑰_端點集合跟舊頁一樣_body不改():
    n, o = _src("components/v2/CollectorKey.vue"), _src("pages/settings/index.vue")
    new, old = _calls(n), _calls(o, only="/api/collector-key")
    assert new == old == {("GET", "/api/collector-key"), ("POST", "/api/collector-key/import")}, (new, old)
    for frag in ("const body: any = { confirm: ckConfirm.value }",
                 "if (ckMode.value === 'paste') body.private_key = ckPrivate.value",
                 "else body.from_path = ckPath.value.trim()",
                 "!ckBusy.value && ckConfirm.value === 'REPLACE'"):
        assert frag in n and frag in o, frag
    fin = _fn(n, "async function importCollectorKey()")
    assert "ckPrivate.value = ''" in fin[fin.index("} finally {"):]           # 成功或失敗都清私鑰
    assert "const ckPrivate = ref('')" in n                              # 不預填（畫面行為另由 vitest 驗）


def _ed25519_openssh():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    return priv, pub


def test_收集金鑰_私鑰不出現在任何GET_匯入回應_紀錄_log(rclient, monkeypatch, tmp_path, caplog):
    import collector_key
    import onboard_engine
    kdir = tmp_path / "keys"
    kdir.mkdir()
    monkeypatch.setattr(onboard_engine, "COLLECTOR_KEY_PUB", str(kdir / "ck") + ".pub")   # 不碰這台真正的收集金鑰
    monkeypatch.setattr(collector_key, "_chown_to_service_user", lambda *a: None)
    priv, pub = _ed25519_openssh()
    body_lines = [ln for ln in priv.splitlines() if ln and not ln.startswith("-----")]
    login, p, _ = rclient
    cl = login()
    caplog.set_level("DEBUG")
    assert cl.post("/api/collector-key/import", json={"confirm": "replace", "private_key": priv}).status_code == 400   # 確認字照舊
    r = cl.post("/api/collector-key/import", json={"confirm": "REPLACE", "private_key": priv})
    assert r.status_code == 200, r.text
    texts = {"import": r.text}
    texts["GET collector-key"] = cl.get("/api/collector-key").text
    texts["GET collect-config"] = cl.get("/api/accounts/collect-config").text
    texts["GET activity"] = cl.get("/api/activity").text
    c = db.get_connection(p)
    try:
        texts["db"] = "\n".join(str(v) for t in ("app_settings", "activity_log") for row in c.execute(f"SELECT * FROM {t}") for v in row)
    finally:
        c.close()
    texts["log"] = caplog.text
    for where, t in texts.items():
        assert "PRIVATE KEY" not in t, where
        for ln in body_lines:
            assert ln not in t, (where, ln[:8])
    assert pub.split()[1] in texts["GET collector-key"]                  # 公鑰可以顯示（公開資訊）
    assert (kdir / "ck").read_text(encoding="utf-8").strip() == priv.strip()   # 真的寫進去了


def test_收集金鑰_GET限管理員(rclient):
    login, p, _ = rclient
    for name in ("alice", "vera"):
        assert login(name).get("/api/collector-key").status_code == 403, name
        assert login(name).post("/api/collector-key/import", json={"confirm": "REPLACE", "from_path": "/tmp/x"}).status_code == 403, name


def test_V56收尾_設定頁不再嵌入舊頁_舊網址轉到V2():
    s = _src("pages/v2/settings.vue")
    assert "pages/settings/index.vue" not in s.split("// 舊系統設定頁")[0] and "<OldSettings" not in s
    assert "import('~/pages/settings/index.vue')" not in s
    assert '<V2CollectorKey key="collectorkey" /><V2CollectOptions key="collectopts" />' in s
    m = _src("utils/v2LegacyMap.ts")
    assert "  '/settings',  " in m.split("export const PORTED")[1].split("]")[0]
    assert "t === 'diagram'" in m


# ===== 2.057.1：V56 審查補測試 =====

def test_還原_restore開關那一列不見了也一律拒絕(rclient):
    login, p, data = rclient
    cl = login()
    c = db.get_connection(p)
    c.execute("DELETE FROM feature_flags WHERE module_key = 'restore'")
    c.commit()
    c.close()
    r = _restore(cl, data, "覆蓋還原")
    assert r.status_code == 403, r.text                                 # 查不到開關＝當成關著（fail-closed）
    assert _serials(p) == {"LIVE-1"}


def test_收集金鑰_匯入失敗時原本的金鑰不動(rclient, monkeypatch, tmp_path):
    import collector_key
    import onboard_engine
    kdir = tmp_path / "keys"
    kdir.mkdir()
    monkeypatch.setattr(onboard_engine, "COLLECTOR_KEY_PUB", str(kdir / "ck") + ".pub")
    monkeypatch.setattr(collector_key, "_chown_to_service_user", lambda *a: None)
    priv, pub = _ed25519_openssh()
    login, p, _ = rclient
    cl = login()
    assert cl.post("/api/collector-key/import", json={"confirm": "REPLACE", "private_key": priv}).status_code == 200
    before = ((kdir / "ck").read_bytes(), (kdir / "ck.pub").read_bytes(), sorted(x.name for x in kdir.iterdir()))
    other_priv, other_pub = _ed25519_openssh()
    bad = [
        {"confirm": "REPLACE", "private_key": "-----not a key-----"},                       # 讀不出來
        {"confirm": "REPLACE", "private_key": other_priv, "public_key": pub},              # 公私鑰不是一對
        {"confirm": "REPLACE", "from_path": str(tmp_path / "missing_key")},               # 路徑不存在
        {"confirm": "replace", "private_key": other_priv},                                 # 確認字不對
    ]
    for body in bad:
        r = cl.post("/api/collector-key/import", json=body)
        assert r.status_code == 400, (body.get("confirm"), r.text)
        after = ((kdir / "ck").read_bytes(), (kdir / "ck.pub").read_bytes(), sorted(x.name for x in kdir.iterdir()))
        assert after == before, body.get("confirm")                    # 私鑰、公鑰、備份檔都沒動
    assert other_priv.splitlines()[1] not in cl.get("/api/collector-key").text
