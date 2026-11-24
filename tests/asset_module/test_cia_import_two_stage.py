"""CIA 資產清冊匯入兩段式（2.055.0，V36，待決定 5）。⚠D4：
  · 預覽不寫正式庫；數字＝逐筆明細；
  · 同一份檔案走「舊一步式」與「預覽＋確認」兩條路，資料庫結果一模一樣；
  · 確認只寫預覽過的那一份：檔案雜湊、欄位對應、資料起點有任一個變了就 409；代號用一次就作廢。全部假資料。"""
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import cia_import_preview as cp  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _wb(path: Path, hw_rows, ppl_rows=(), sw_rows=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "硬體"
    ws.append(["資產序號", "主機名稱", "IP", "設備機型", "環境別", "資產狀態"])
    for r in hw_rows:
        ws.append(list(r))
    p = wb.create_sheet("人員")
    p.append(["資產序號", "人員姓名", "聯絡電話"])
    for r in ppl_rows:
        p.append(list(r))
    s = wb.create_sheet("軟體")
    s.append(["資產序號", "資料庫/軟體", "備份頻率"])
    for r in sw_rows:
        s.append(list(r))
    wb.save(path)
    return path.read_bytes()


def _files(tmp: Path):
    f1 = _wb(tmp / "f1.xlsx", [("HW-T-1", "fake01", "10.99.1.1", "Model X", "正式", "使用中"),
                               ("HW-T-2", "fake02", "10.99.1.2", "Model Y", "測試", "使用中")],
             [("HW-T-1", "假姓名", "0000")], [("HW-T-2", "PostgreSQL 14", "每日")])
    f2 = _wb(tmp / "f2.xlsx", [("HW-T-1", "fake01", "10.99.1.1", "Model X", "正式", "使用中"),        # 沒變
                               ("HW-T-2", "fake02-new", "10.99.1.2", "Model Y", "測試", "A02櫃"),      # 改名＋錯值狀態
                               ("HW-T-3", "fake03", "10.99.1.3", "Model Z", "正式", "使用中")],         # 新增
             [("HW-T-1", "假姓名", "1111"), ("HW-NOPE", "查無此資產", "0")], [("HW-T-2", "PostgreSQL 14", "每週")])
    return f1, f2


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "PREVIEW_DIR", tmp_path / "preview")
    monkeypatch.setenv("WEBIT_ENABLE_ONESTEP_CIA_IMPORT", "1")                  # 這個檔案要拿一步式當對照組
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "archive_root.db"))      # 原始檔存檔跟著測試的 DB 路徑，不寫到正式資料夾
    clients = {}

    def make(name):
        p = tmp_path / f"{name}.db"
        db.init_db(p)
        c = db.get_connection(p)
        db.create_user(c, "tester", auth.hash_password(_PW))
        db.create_user(c, "tester2", auth.hash_password(_PW))
        c.close()

        def _get_db():
            cc = db.get_connection(p)
            try:
                yield cc
            finally:
                cc.close()
        clients[name] = (p, _get_db)
        return p

    def client(name, user="tester"):
        p, g = clients[name]
        api.app.dependency_overrides[api.get_db] = g
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": user, "password": _PW}).status_code == 200
        return cl

    yield make, client
    api.app.dependency_overrides.clear()


def _state(p: Path):
    c = db.get_connection(p)
    try:
        out = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] for t in ("hardware", "personnel", "software")}
        for t in out.values():
            for r in t:
                r.pop("updated_at", None)
                r.pop("created_at", None)
        out["import_log"] = [{k: r[k] for k in ("hardware_count", "personnel_count", "software_count", "error_count", "source", "file_name")}
                             for r in c.execute("SELECT * FROM import_log ORDER BY id")]
        row = c.execute("SELECT value FROM app_settings WHERE key = ?", (cp.excel_import.lifecycle.INVALID_STATUS_SETTING,)).fetchone()
        out["invalid_status"] = row["value"] if row else None
        return out
    finally:
        c.close()


def _two_stage(cl, data: bytes, name: str):
    r = cl.post("/api/import/excel/preview", files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    pv = r.json()
    c = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert c.status_code == 200, c.text
    return pv, c.json()


def test_一步式與預覽加確認_資料庫結果一模一樣(env, tmp_path):
    make, client = env
    f1, f2 = _files(tmp_path)
    a, b = make("one_step"), make("two_stage")
    ca = client("one_step")
    for name, data in (("f1.xlsx", f1), ("f2.xlsx", f2)):
        assert ca.post("/api/import/excel", files={"file": (name, data, "application/octet-stream")}).status_code == 200
    cb = client("two_stage")
    s1 = _two_stage(cb, f1, "f1.xlsx")[1]
    s2 = _two_stage(cb, f2, "f2.xlsx")[1]
    assert s1["sheets"]["硬體"]["inserted"] == 2 and s2["sheets"]["硬體"]["inserted"] == 1
    sa, sb = _state(a), _state(b)
    assert sa == sb
    assert len(sa["hardware"]) == 3 and len(sa["import_log"]) == 2 and "A02櫃" in (sa["invalid_status"] or "")


def test_預覽不寫正式庫_數字等於明細(env, tmp_path):
    make, client = env
    f1, f2 = _files(tmp_path)
    p = make("db")
    cl = client("db")
    _two_stage(cl, f1, "f1.xlsx")
    before = _state(p)
    r = cl.post("/api/import/excel/preview", files={"file": ("f2.xlsx", f2, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert _state(p) == before                                    # 一個字都沒動
    t = r.json()["tables"]
    hw = t["hardware"]
    assert (hw["new"], hw["updated"], hw["unchanged"], hw["rejected"]) == (1, 1, 1, 0)
    assert len(hw["new_rows"]) == hw["new"] and len(hw["updated_rows"]) == hw["updated"]
    ch = hw["updated_rows"][0]
    assert ch["asset_serial"] == "HW-T-2" and ch["fields"]["hostname"] == {"before": "fake02", "after": "fake02-new"}
    assert "asset_status" not in ch["fields"]                     # 錯值不寫，保留原值（import_excel 原本的規則）
    assert r.json()["summary"]["invalid_status"] == [{"asset_serial": "HW-T-2", "value": "A02櫃"}]
    pp = t["personnel"]
    assert (pp["updated"], pp["rejected"]) == (1, 1)              # 電話改了；查無資產那列拒收
    sw = t["software"]
    # 軟體的自然鍵（序號＋資產名稱＋軟體）這份檔沒有資產名稱 → import_excel 照原本規則當新的一筆（寧可多列不誤蓋）；預覽照實講
    assert (sw["new"], sw["updated"], sw["unchanged"]) == (1, 0, 0)


def test_確認只寫預覽過的那一份(env, tmp_path, monkeypatch):
    make, client = env
    f1, f2 = _files(tmp_path)
    p = make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    # 檔案雜湊不對 → 409，資料沒動
    assert cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": "0" * 64}).status_code == 409
    # 找不到的代號 → 404
    assert cl.post("/api/import/excel/confirm", json={"token": "abc", "file_sha256": pv["file_sha256"]}).status_code == 404
    assert cl.post("/api/import/excel/confirm", json={"token": "../x", "file_sha256": pv["file_sha256"]}).status_code == 404
    assert _state(p)["hardware"] == []
    # 預覽之後資料有變動（例如別人一步式匯入）→ 409
    assert cl.post("/api/import/excel", files={"file": ("f2.xlsx", f2, "application/octet-stream")}).status_code == 200
    r = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 409 and "重新預覽" in r.json()["detail"]
    # 重新預覽 → 確認 → 成功；同一個代號再用一次 → 404
    pv2 = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    body = {"token": pv2["token"], "file_sha256": pv2["file_sha256"]}
    assert cl.post("/api/import/excel/confirm", json=body).status_code == 200
    assert cl.post("/api/import/excel/confirm", json=body).status_code == 404


def test_欄位對應改過_確認被擋(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    real = cp.read_mapping
    monkeypatch.setattr(cp, "read_mapping", lambda: (real()[0] + b" ", real()[1]))      # 對應檔多一個字＝雜湊不同
    r = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 409 and "欄位對應" in r.json()["detail"]


def test_過期的預覽不能確認(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    monkeypatch.setattr(cp, "TTL_SECONDS", -1)
    assert cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]}).status_code == 404


def test_不是CIA清冊_預覽回400_不留檔(env, tmp_path):
    make, client = env
    make("db")
    cl = client("db")
    wb = openpyxl.Workbook()
    wb.active.title = "vInfo"
    wb.save(tmp_path / "x.xlsx")
    r = cl.post("/api/import/excel/preview", files={"file": ("x.xlsx", (tmp_path / "x.xlsx").read_bytes(), "application/octet-stream")})
    assert r.status_code == 400 and "CIA" in r.json()["detail"]
    assert not list((tmp_path / "preview").glob("*.xlsx"))
    assert cl.post("/api/import/excel/preview", files={"file": ("x.txt", b"x", "text/plain")}).status_code == 400


def test_兩支新端點都要管理員_有登記():
    import access_policy as ap
    assert ap.level_for("POST", "/api/import/excel/preview") == ap.level_for("POST", "/api/import/excel") == "admin"
    assert ap.level_for("POST", "/api/import/excel/confirm") == "admin"


def test_畫面走兩段式_不再呼叫一步式():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    imp = (fe / "pages" / "import.vue").read_text(encoding="utf-8")
    assert "'/api/import/excel/preview'" in imp and "'/api/import/excel/confirm'" in imp
    assert "'/api/import/excel'," not in imp and "'/api/import/excel')" not in imp
    assert "body: { token: pv.token, file_sha256: pv.file_sha256 }" in imp
    assert "<V2CiaPreview" in imp
    pv = (fe / "components" / "v2" / "CiaPreview.vue").read_text(encoding="utf-8")
    assert "確認寫入" in pv and "emit('confirm')" in pv and "apiFetch" not in pv     # 元件只顯示，不自己打 API


def test_業務系統分類對照表_最後匯入有紀錄(env, tmp_path):
    make, client = env
    make("db")
    cl = client("db")
    assert cl.get("/api/reports/system-category/status").json() == {"last_import": None}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["api_id", "名稱", "台數", "分類"])
    ws.append(["N-999", "假系統", 1, "看不懂的分類"])
    wb.save(tmp_path / "cat.xlsx")
    r = cl.post("/api/reports/system-category/import",
                files={"file": ("cat.xlsx", (tmp_path / "cat.xlsx").read_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    last = cl.get("/api/reports/system-category/status").json()["last_import"]
    assert last["by"] == "tester" and last["file"] == "cat.xlsx" and last["accepted"] == r.json().get("accepted")


def test_一步式缺省關閉_回410_一筆都不寫(env, tmp_path, monkeypatch):
    """2.056.0：兩段式定案，一步式不可留成後門——沒設開關就 410。"""
    make, client = env
    f1, _ = _files(tmp_path)
    p = make("db")
    cl = client("db")
    monkeypatch.delenv("WEBIT_ENABLE_ONESTEP_CIA_IMPORT", raising=False)
    r = cl.post("/api/import/excel", files={"file": ("f1.xlsx", f1, "application/octet-stream")})
    assert r.status_code == 410 and "兩段式" in r.json()["detail"]
    assert _state(p)["hardware"] == [] and _state(p)["import_log"] == []
    # 兩段式照常
    _two_stage(cl, f1, "f1.xlsx")
    assert len(_state(p)["hardware"]) == 2


# ===== 2.055.2：第 20 批獨立審查 P1／P2 =====

def _routes_commit():
    from routers import imports as ri
    return ri._cia_commit


def test_同時兩個確認_只有一個寫_結果等於確認一次(env, tmp_path):
    """P1-1：兩個請求同時通過檢查再各寫一次（軟體 1→3、多一筆匯入紀錄）——現在搶代號是原子的，另一個 404。"""
    import threading
    make, client = env
    f1, f2 = _files(tmp_path)
    a, b = make("single"), make("double")
    ca = client("single")
    _two_stage(ca, f1, "f1.xlsx")
    _two_stage(ca, f2, "f2.xlsx")
    cb = client("double")
    _two_stage(cb, f1, "f1.xlsx")
    pv = cb.post("/api/import/excel/preview", files={"file": ("f2.xlsx", f2, "application/octet-stream")}).json()
    write = _routes_commit()
    results, barrier = [], threading.Barrier(2)

    def go():
        c = db.get_connection(b)
        try:
            barrier.wait()
            results.append(("ok", cp.confirm(c, pv["token"], pv["file_sha256"], "tester", lambda cc, fn, data, mapping: write(cc, "tester", fn, data, mapping))))
        except Exception as exc:  # noqa: BLE001
            results.append((type(exc).__name__, str(exc)))
        finally:
            c.close()
    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    kinds = sorted(k for k, _ in results)
    assert kinds == ["LookupError", "ok"], results
    assert _state(a) == _state(b)                                  # 跟只確認一次一樣（軟體、匯入紀錄都沒有多）


def test_預覽之後實測證據改了_確認409(env, tmp_path):
    """P1-2：source_record.resolved_hardware_id 被改（自動合併／掃描收編）會改變「CIA 作業系統要不要寫」——要擋。"""
    make, client = env
    f1, f2 = _files(tmp_path)
    p = make("db")
    cl = client("db")
    _two_stage(cl, f1, "f1.xlsx")
    c = db.get_connection(p)
    c.execute("INSERT INTO source_record (source, source_key, payload) VALUES ('dynassets', 'k1', '{}')")
    c.commit()
    c.close()
    pv = cl.post("/api/import/excel/preview", files={"file": ("f2.xlsx", f2, "application/octet-stream")}).json()
    c = db.get_connection(p)
    hid = c.execute("SELECT id FROM hardware WHERE asset_serial = 'HW-T-1'").fetchone()["id"]
    c.execute("UPDATE source_record SET resolved_hardware_id = ? WHERE source_key = 'k1'", (hid,))
    c.commit()
    c.close()
    r = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 409 and "重新預覽" in r.json()["detail"]


def test_收集成功的旗標改了_確認409(env, tmp_path):
    make, client = env
    f1, f2 = _files(tmp_path)
    p = make("db")
    cl = client("db")
    _two_stage(cl, f1, "f1.xlsx")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f2.xlsx", f2, "application/octet-stream")}).json()
    c = db.get_connection(p)
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'HW-T-2'")
    c.commit()
    c.close()
    assert cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]}).status_code == 409


def test_只有建立預覽的人能確認(env, tmp_path):
    make, client = env
    f1, _ = _files(tmp_path)
    p = make("db")
    c1 = client("db")
    pv = c1.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    c2 = client("db", user="tester2")
    r = c2.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 403 and _state(p)["hardware"] == []
    c1 = client("db")
    assert c1.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]}).status_code == 200


def test_上限_檔案大小與活著的預覽數(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    monkeypatch.setattr(cp, "MAX_BYTES", 10)
    assert cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).status_code == 413
    monkeypatch.setattr(cp, "MAX_BYTES", 30 * 1024 * 1024)
    monkeypatch.setattr(cp, "MAX_PER_USER", 1)
    assert cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).status_code == 200
    assert cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).status_code == 429


def test_預覽檔在私有資料夾_確認與過期都刪檔(env, tmp_path, monkeypatch):
    import tempfile
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    monkeypatch.setattr(cp, "PREVIEW_DIR", None)                    # 預設：資料目錄底下（不是系統共用暫存）
    d = cp.preview_dir()
    assert d == (tmp_path / "archive_root.db").parent / cp.DIRNAME
    assert not str(d).startswith(tempfile.gettempdir() + "\\webit3") and d.name == "cia_preview"
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    assert {x.suffix for x in d.iterdir()} == {".json", ".xlsx"}
    assert cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]}).status_code == 200
    assert list(d.iterdir()) == []                                  # 確認完不留檔
    cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")})
    assert len(list(d.iterdir())) == 2
    assert cp.cleanup(now=__import__("time").time() + cp.TTL_SECONDS + 1) == 1
    assert list(d.iterdir()) == []                                  # 過期清掉


# ===== 2.055.4：第 20 批第 2 輪審查 P2 =====

def _archive(tmp_path):
    d = tmp_path / "imports" / "cia_excel"
    return {p.name: p.read_bytes() for p in d.iterdir()} if d.exists() else {}


def test_匯入紀錄寫失敗_資料不進去_原始檔不被蓋(env, tmp_path, monkeypatch):
    """P2-1：資料＋匯入紀錄同一筆交易；原始檔在 commit 之後才存。"""
    from routers import imports as ri
    make, client = env
    f1, f2 = _files(tmp_path)
    p = make("db")
    cl = client("db")
    _two_stage(cl, f1, "f1.xlsx")
    before, arch = _state(p), _archive(tmp_path)
    assert arch                                                   # 上一次成功匯入有存原始檔
    pv = cl.post("/api/import/excel/preview", files={"file": ("f2.xlsx", f2, "application/octet-stream")}).json()

    def boom(*a, **kw):
        raise RuntimeError("模擬匯入紀錄寫失敗")
    monkeypatch.setattr(ri, "create_import_log", boom)
    cl2 = TestClient(api.app, raise_server_exceptions=False)
    assert cl2.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    r = cl2.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 500
    assert _state(p) == before                                    # 硬體／人員／軟體／匯入紀錄都沒變
    assert _archive(tmp_path) == arch                             # 存著的原始檔還是上一份


def test_搶到代號後檔案不見了_回404不是500(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    p = make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    cp._file_path(pv["token"]).unlink()
    r = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 404 and _state(p)["hardware"] == []


def test_認領檔放太久_當作上次中斷_409(env, tmp_path):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    c = cp._claim_path(pv["token"])
    c.write_bytes(b"")
    body = {"token": pv["token"], "file_sha256": pv["file_sha256"]}
    assert cl.post("/api/import/excel/confirm", json=body).status_code == 404          # 剛建的認領檔＝別人正在寫
    old = __import__("time").time() - cp.CLAIM_STALE_SECONDS - 5
    __import__("os").utime(c, (old, old))
    r = cl.post("/api/import/excel/confirm", json=body)
    assert r.status_code == 409 and "上次寫入中斷" in r.json()["detail"]
    assert not c.exists() and not cp._file_path(pv["token"]).exists()


def test_全系統上限(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    monkeypatch.setattr(cp, "MAX_TOTAL", 1)
    c1 = client("db")
    assert c1.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).status_code == 200
    c2 = client("db", user="tester2")
    r = c2.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")})
    assert r.status_code == 429 and "系統上" in r.json()["detail"]


def test_計數與佔位在同一把鎖_同時預覽不會超過上限(env, tmp_path, monkeypatch):
    import threading
    make, client = env
    f1, _ = _files(tmp_path)
    p = make("db")
    monkeypatch.setattr(cp, "MAX_PER_USER", 1)
    real = cp.dry_run
    gate = threading.Event()

    def slow(*a, **kw):
        gate.wait(2)
        return real(*a, **kw)
    monkeypatch.setattr(cp, "dry_run", slow)
    out, barrier = [], threading.Barrier(4)            # 3 條預覽＋主執行緒

    def go():
        c = db.get_connection(p)
        try:
            barrier.wait()
            cp.preview(c, f1, "f1.xlsx", "tester")
            out.append("ok")
        except cp.TooMany:
            out.append("429")
        finally:
            c.close()
    ts = [threading.Thread(target=go) for _ in range(3)]
    for t in ts:
        t.start()
    barrier.wait()
    __import__("time").sleep(0.3)
    gate.set()
    for t in ts:
        t.join()
    assert sorted(out) == ["429", "429", "ok"], out


def test_指紋重驗在寫入鎖裡_別人插不進來(env, tmp_path, monkeypatch):
    """P2-7：檢查與寫入在同一個 BEGIN IMMEDIATE——檢查過後另一條連線想改資料會被鎖擋住，不會「檢查過了才被改」。"""
    import sqlite3
    make, client = env
    f1, f2 = _files(tmp_path)
    p = make("db")
    cl = client("db")
    _two_stage(cl, f1, "f1.xlsx")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f2.xlsx", f2, "application/octet-stream")}).json()
    real = cp.db_fingerprint
    side = []

    def fp_then_intrude(conn):
        v = real(conn)
        other = sqlite3.connect(p, timeout=0.2)
        try:
            other.execute("UPDATE hardware SET remark = 'intruder' WHERE asset_serial = 'HW-T-1'")
            other.commit()
            side.append("wrote")
        except sqlite3.OperationalError as exc:
            side.append(f"blocked:{exc}")
        finally:
            other.close()
        return v
    monkeypatch.setattr(cp, "db_fingerprint", fp_then_intrude)
    r = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 200, r.text
    assert side and side[0].startswith("blocked"), side
    c = db.get_connection(p)
    assert c.execute("SELECT remark FROM hardware WHERE asset_serial = 'HW-T-1'").fetchone()["remark"] != "intruder"
    c.close()


def test_欄位對應只讀一次_寫入用的就是雜湊的那份(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    called = []
    monkeypatch.setattr(cp.excel_import, "load_mapping", lambda: called.append(1) or {})
    assert cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]}).status_code == 200
    assert called == []                                           # 寫入沒有再自己讀一次對應檔


def test_私有資料夾建不起來就拒絕預覽(env, tmp_path, monkeypatch):
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    bad = tmp_path / "afile"
    bad.write_bytes(b"x")
    monkeypatch.setattr(cp, "PREVIEW_DIR", bad / "sub")           # 父路徑是檔案 → mkdir 失敗
    r = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")})
    assert r.status_code == 503 and "拒絕預覽" in r.json()["detail"]


def test_確認時私有資料夾出問題_回503不是500(env, tmp_path, monkeypatch):
    """第 20 批第 3 輪審查：confirm 路徑上 preview_dir() 丟 StorageError 也要 503（同預覽）。"""
    make, client = env
    f1, _ = _files(tmp_path)
    make("db")
    cl = client("db")
    pv = cl.post("/api/import/excel/preview", files={"file": ("f1.xlsx", f1, "application/octet-stream")}).json()
    bad = tmp_path / "afile2"
    bad.write_bytes(b"x")
    monkeypatch.setattr(cp, "PREVIEW_DIR", bad / "sub")
    r = cl.post("/api/import/excel/confirm", json={"token": pv["token"], "file_sha256": pv["file_sha256"]})
    assert r.status_code == 503, r.text
