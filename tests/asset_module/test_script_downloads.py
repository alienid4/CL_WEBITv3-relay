"""單獨下載檢核腳本（網頁 → scp → 在 AIX 上 ksh 跑）。

為什麼要有這條路：之前要拿一支 60KB 的檢核腳本，得下載 3.3MB 的完整更新包再自己
翻出來。使用者的實際動線只有三步：網頁下載 → scp 到主機 → 跑。

這支測試釘的是四件事，每一件都有踩過的理由：

1. **不寫死檔名**：清單是掃 `scripts/` 算出來的。`fcbaixsh` 正在改名成
   `fcb_aix.ksh`，寫死那天會壞在「按鈕還在、點下去 404」這種最難聯想的地方。
2. **逐位元組相同**：下載到的內容要跟 repo 裡的檔一致（sha256 比對）。
   拿到一份被動過手腳的腳本，跑出來的檢核結果不可信。
3. **換行是 LF**：這些檔案要在 AIX/Linux 跑，CRLF 會讓 `#!/usr/bin/ksh` 帶著
   `\\r` 直接執行失敗（2026-07-28、2026-09-22 各踩過一次）。
4. **路徑穿越擋得掉、而且要登入**：這是會把伺服器上的檔案吐出去的端點。
"""
import hashlib
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import script_downloads as sd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402

SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"


def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    # sqlite3 的 connection 當 context manager 時只會 commit／rollback，**不會 close**。
    # 不自己關掉的話這個 handle 一直開著，Windows 上 TemporaryDirectory 收尾時就刪不掉
    # test.db，整個測試以 PermissionError(WinError 32) 收場——看起來像測試失敗，
    # 其實是檔案還被自己佔著（Linux 上刪得掉，所以只有在 Windows 開發機會紅）。
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "t", auth.hash_password("Passw0rd!x"))
        conn.commit()
    finally:
        conn.close()

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    return TestClient(api.app)


def _login(client):
    """登入，session cookie 會留在同一個 client 上。"""
    r = client.post("/api/auth/login", json={"username": "t", "password": "Passw0rd!x"})
    assert r.status_code == 200, r.text
    return r


# ─────────────────────────────────────────────────────────────────────────
# 一、清單：掃目錄算出來，不是寫死檔名
# ─────────────────────────────────────────────────────────────────────────

def test_清單來自掃目錄_改名不會壞(tmp_path: Path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir()
    (d / "fcb_aix.ksh").write_text(
        '#!/usr/bin/ksh\n# FCB AIX 組態檢核\nSCRIPTV="SV.AIX.202609301200"\n',
        encoding="utf-8", newline="\n")
    (d / "fcbrhelsh").write_text(          # 沒有副檔名，靠 shebang 認出來
        '#!/bin/sh\n# FCB RHEL 組態檢核\nSCRIPTV="SV.RHEL.202609301200"\n',
        encoding="utf-8", newline="\n")
    (d / "說明.md").write_text("# 不是腳本\n", encoding="utf-8", newline="\n")
    (d / "notes.txt").write_text("x\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(sd, "SCRIPTS_DIR", d)

    names = [x["name"] for x in sd.list_scripts()]
    assert names == ["fcb_aix.ksh", "fcbrhelsh"], "說明文件與雜項不該混進下載清單"

    aix = sd.list_scripts()[0]
    assert aix["script_version"] == "SV.AIX.202609301200", "版本戳要讀得出來"
    assert aix["platform"] == "AIX"
    assert aix["size"] > 0 and len(aix["sha256"]) == 64


def test_沒有版本戳就明講_不可以拿檔案時間頂替(tmp_path: Path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir()
    (d / "x.sh").write_text("#!/bin/sh\n# 沒有版本戳的腳本\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(sd, "SCRIPTS_DIR", d)
    assert sd.list_scripts()[0]["script_version"] is None


def test_目錄不存在時回空清單而不是炸掉(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sd, "SCRIPTS_DIR", tmp_path / "沒有這個目錄")
    assert sd.list_scripts() == []


# ─────────────────────────────────────────────────────────────────────────
# 二、內容：逐位元組相同 + LF
# ─────────────────────────────────────────────────────────────────────────

def test_repo裡的腳本在版控中就是LF():
    """`.gitattributes` 有規則，但規則會被繞過（新增沒副檔名的檔就中過一次）。"""
    bad = [p.name for p in SCRIPTS.iterdir()
           if p.is_file() and b"\r\n" in p.read_bytes()]
    assert not bad, f"這些檔案是 CRLF，拿到 AIX 會直接跑不動：{bad}"


@pytest.mark.parametrize("name", [p.name for p in sorted(SCRIPTS.iterdir())
                                  if sd._is_downloadable(p)])
def test_下載內容與repo裡的檔逐位元組相同(name: str):
    _, body = sd.read_script(name)
    want = (SCRIPTS / name).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(body).hexdigest() == hashlib.sha256(want).hexdigest()
    assert b"\r\n" not in body, "送出去的一定要是 LF"


def test_工作區是CRLF時下載仍然吐LF(tmp_path: Path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir()
    (d / "x.sh").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    monkeypatch.setattr(sd, "SCRIPTS_DIR", d)
    _, body = sd.read_script("x.sh")
    assert body == b"#!/bin/sh\necho hi\n"
    # 但這件事要看得見，不可以安靜地修掉就當沒事
    assert sd.list_scripts()[0]["crlf_in_repo"] is True


# ─────────────────────────────────────────────────────────────────────────
# 三、路徑穿越
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../api.py", "../../etc/passwd", "..", ".", "",
    "/etc/passwd", "C:/Windows/win.ini",
    "scripts/fcbaixsh", "fcbaixsh/../api.py",
    "fcbaix使用說明.md",           # 不是腳本，不在清單裡就不給
])
def test_路徑穿越與清單外的檔一律拒絕(bad: str):
    with pytest.raises(KeyError):
        sd.read_script(bad)


def test_端點層也擋得住路徑穿越():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        _login(client)
        for bad in ("..%2f..%2fapi.py", "%2Fetc%2Fpasswd", "..", "x.py"):
            r = client.get(f"/api/scripts/{bad}/download")
            assert r.status_code in (404, 405), f"{bad} 竟然回 {r.status_code}"
            assert b"def " not in r.content, "把伺服器上的程式碼吐出去了"


# ─────────────────────────────────────────────────────────────────────────
# 四、端點：要登入、以附件送出
# ─────────────────────────────────────────────────────────────────────────

def test_沒登入不給下載():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        assert client.get("/api/scripts").status_code == 401
        assert client.get("/api/scripts/fcbaixsh/download").status_code == 401


def test_登入後拿得到檔_而且是附件不是網頁():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        _login(client)
        items = client.get("/api/scripts").json()["items"]
        assert items, "repo 的 scripts/ 底下應該至少有一支可下載的腳本"
        name = items[0]["name"]
        r = client.get(f"/api/scripts/{name}/download")
        assert r.status_code == 200
        cd = r.headers["content-disposition"]
        assert cd.startswith("attachment"), "要存成檔案，不要讓瀏覽器直接渲染"
        assert name in cd
        assert r.headers["content-type"].startswith("application/octet-stream")
        assert hashlib.sha256(r.content).hexdigest() == items[0]["sha256"], \
            "下載到的內容跟清單上公告的 sha256 對不起來"
        assert b"\r\n" not in r.content
