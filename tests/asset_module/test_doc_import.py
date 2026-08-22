"""既有 Word 單據的歸檔與索引。

用使用者提供的 6 份真實樣本（DATA/IAD-*，3 個 .doc + 3 個 .docx）當黃金測試——
合成檔案測不出真實 Word 的樣子（跨 run 拆字、舊 .doc 的二進位編碼）。
樣本不在版控裡（DATA/ 在 .gitignore），所以檔案不在時整組 skip 而不是失敗：
CI 或別台機器上跑不該紅燈，但有樣本的機器上一定要通過。
"""
import io
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import doc_import  # noqa: E402
import api  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402

SAMPLES = sorted((ROOT / "DATA").glob("IAD-*"))
needs_samples = pytest.mark.skipif(not SAMPLES, reason="DATA/ 沒有 Word 樣本（不進版控）")


@needs_samples
def test_六份真實樣本的識別欄位命中率():
    """2026-08-15 實測基準：單號、日期、主機名、IP 各 6/6。低於這個就是抽取退步了。"""
    hit = {"no": 0, "date": 0, "host": 0, "ip": 0}
    for p in SAMPLES:
        f = doc_import.extract_fields(p.name, doc_import.extract_text(p))
        hit["no"] += bool(f["request_no"] or f["ref_request_no"] or f["file_serial"])
        hit["date"] += bool(f["form_date"])
        hit["host"] += bool(f["hostname"])
        hit["ip"] += bool(f["ip"])
    n = len(SAMPLES)
    assert hit == {"no": n, "date": n, "host": n, "ip": n}, hit


@needs_samples
def test_doc與docx都讀得到_不需要外部轉檔工具():
    exts = {p.suffix.lower() for p in SAMPLES}
    assert ".doc" in exts and ".docx" in exts, "樣本要同時包含新舊格式才測得出來"
    for p in SAMPLES:
        assert len(doc_import.extract_text(p)) > 500, f"{p.name} 幾乎讀不到內容"


@needs_samples
def test_三方交叉驗證全部一致():
    """檔名 IP／內文 IP／主機名編碼的 IP 後兩段——三個獨立來源要對得起來，
    這是能自動綁定資產的依據，不是憑單一來源猜。"""
    for p in SAMPLES:
        f = doc_import.extract_fields(p.name, doc_import.extract_text(p))
        assert f["triple_match"], f"{p.name} 三方對不起來：{f['ip_in_filename']} / {f['ip_in_content']} / {f['hostname_tail']}"


@needs_samples
def test_檢查表回指的單號要抽成ref不是自己的單號():
    """上線檢查表上的「伺服器申請單據表單編號」是回指申請單的，抽錯就串不起兩種單。"""
    golive = [p for p in SAMPLES if "上線前檢查" in p.name]
    assert golive, "樣本裡要有上線檢查表"
    got_ref = 0
    for p in golive:
        f = doc_import.extract_fields(p.name, doc_import.extract_text(p))
        assert f["doc_type"] == "golive_form"
        got_ref += bool(f["ref_request_no"])
    assert got_ref >= 1, "至少要有一份抽得到回指的申請單號"


@needs_samples
def test_單號尾碼不能被截掉():
    """INF-20260616-31 被抽成 INF-20260616 的話，兩種單就串不起來（實際踩過）。"""
    p = next((x for x in SAMPLES if "20260616-31" in x.name), None)
    assert p, "需要那份 INF- 開頭的樣本"
    f = doc_import.extract_fields(p.name, doc_import.extract_text(p))
    assert f["ref_request_no"] == "INF-20260616-31", f["ref_request_no"]


@needs_samples
def test_日期矛盾要列成警告而不是自己選一個():
    """有一份樣本檔名寫 2026、內文填表日期寫 2025。這種矛盾要讓人看到。"""
    found = False
    for p in SAMPLES:
        f = doc_import.extract_fields(p.name, doc_import.extract_text(p))
        if any("年份" in w for w in f["warnings"]):
            found = True
    assert found, "應該要抓到那份年份不一致的樣本"


@needs_samples
def test_匯入_三方一致且對得到資產才自動綁():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = next(x for x in SAMPLES if "10.99.195.59" in x.name)
            _insert_hardware(db_path, "DOC-001", "SECSVR195-059", "10.99.195.59")

            with io.open(p, "rb") as fh:
                r = client.post("/api/documents/import", files=[("files", (p.name, fh, "application/octet-stream"))])
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["imported"] == 1 and body["failed"] == 0
            res = body["results"][0]
            assert res["asset_serial"] == "DOC-001"
            assert res["bind_confidence"] == "auto"
            assert res["hostname"] == "SECSVR195-059"

            docs = client.get("/api/assets/DOC-001/documents").json()["documents"]
            assert len(docs) == 1 and docs[0]["doc_type"] == "provision_form"
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_匯入_對不到資產就標待確認不亂綁():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = SAMPLES[0]
            with io.open(p, "rb") as fh:
                r = client.post("/api/documents/import", files=[("files", (p.name, fh, "application/octet-stream"))])
            res = r.json()["results"][0]
            assert res["asset_serial"] is None
            assert res["bind_confidence"] == "review"
            assert any("找不到這台" in w for w in res["warnings"])
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_人工綁定標成manual_跟自動判定分得開():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = SAMPLES[0]
            _insert_hardware(db_path, "DOC-900", "somewhere", "192.0.2.1")
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import", files=[("files", (p.name, fh, "application/octet-stream"))])
            doc = client.get("/api/documents").json()["documents"][0]
            assert doc["bind_confidence"] == "review"

            assert client.post(f"/api/documents/{doc['id']}/bind",
                               json={"asset_serial": "DOC-900"}).status_code == 200
            doc = client.get("/api/documents").json()["documents"][0]
            assert doc["asset_serial"] == "DOC-900" and doc["bind_confidence"] == "manual"

            bad = client.post(f"/api/documents/{doc['id']}/bind", json={"asset_serial": "NOPE"})
            assert bad.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_一份壞檔不能讓整批中斷():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            good = SAMPLES[0]
            with io.open(good, "rb") as fh:
                r = client.post("/api/documents/import", files=[
                    ("files", (good.name, fh, "application/octet-stream")),
                    ("files", ("壞檔.pdf", io.BytesIO(b"not a word file"), "application/pdf")),
                ])
            body = r.json()
            assert body["imported"] == 1 and body["failed"] == 1
            assert ".doc" in body["errors"][0]["error"]
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_原始檔可下載_索引查到卻打不開等於沒歸檔():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = SAMPLES[0]
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import", files=[("files", (p.name, fh, "application/octet-stream"))])
            doc_id = client.get("/api/documents").json()["documents"][0]["id"]
            r = client.get(f"/api/documents/{doc_id}/download")
            assert r.status_code == 200
            assert len(r.content) == p.stat().st_size
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_重複匯入同一份不會變成兩筆():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = SAMPLES[0]
            for _ in range(2):
                with io.open(p, "rb") as fh:
                    client.post("/api/documents/import",
                                files=[("files", (p.name, fh, "application/octet-stream"))])
            assert client.get("/api/documents").json()["summary"]["total"] == 1
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_全文搜尋_搜得到Word內文並附命中片段():
    """使用者的原始痛點：以前要找一筆資料得把所有 Word 打開。
    只搜檔名/單號解決不了——內容也要搜得到，而且要顯示片段，不然還是得開檔。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            for p in SAMPLES:
                with io.open(p, "rb") as fh:
                    client.post("/api/documents/import",
                                files=[("files", (p.name, fh, "application/octet-stream"))])

            # 「主機名稱」是表單內文才有的字，檔名裡沒有——搜得到就證明搜的是內文
            r = client.get("/api/documents", params={"q": "主機名稱"}).json()["documents"]
            assert len(r) >= 3
            assert any(d["snippet"] for d in r), "命中要附片段"

            # 具體技術關鍵字：只出現在部分單子裡，不能全中
            f5 = client.get("/api/documents", params={"q": "F5"}).json()["documents"]
            assert 0 < len(f5) < len(SAMPLES)

            none_hit = client.get("/api/documents", params={"q": "絕不可能出現的字串xyz"}).json()
            assert none_hit["documents"] == []
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_全域搜尋涵蓋單據內文():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = next(x for x in SAMPLES if "10.99.195.59" in x.name)
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import",
                            files=[("files", (p.name, fh, "application/octet-stream"))])
            groups = client.get("/api/search", params={"q": "ETL"}).json()["groups"]
            doc_group = next((g for g in groups if g["key"] == "documents"), None)
            assert doc_group, "全域搜尋要涵蓋單據"
            assert doc_group["items"][0]["subtitle"], "要有命中片段或摘要"
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_全文有清理過_舊doc的雙解碼亂碼不會整包存進去():
    """.doc 為了相容用兩種編碼各解一次，原文一半是亂碼。不清理的話一份要存 200KB，
    而且搜尋會被亂碼誤命中。"""
    doc = next(p for p in SAMPLES if p.suffix.lower() == ".doc")
    raw = doc_import.extract_text(doc)
    clean = doc_import.searchable_text(raw)
    assert len(clean) < len(raw) * 0.6, f"{len(raw)} → {len(clean)}，清理效果不足"
    assert "主機" in clean or "檢核" in clean, "清理過頭把中文也砍掉了"


@needs_samples
def test_勾選欄位抽得出來_三種存法都要中():
    """2026-08-15 使用者指出「□WIN ☑LINUX，有☑的代表選 LINUX」。實測三種存法：
    .docx 直接打的 ☑、.docx 的 <w:sym> Wingdings 符號、舊 .doc 解碼成 '(' 的勾記號。
    比對截圖確認：這份需求單是 虛擬機/新增/一般/內湖機房/正式/內部服務/AP。"""
    p = next(x for x in SAMPLES if "10.99.195.59" in x.name)   # <w:sym> 那份
    cb = doc_import.extract_checkboxes(doc_import.extract_text(p))
    assert cb["host_type"]["selected"] == ["虛擬機"]
    assert cb["apply_kind"]["selected"] == ["新增"]
    assert cb["change_speed"]["selected"] == ["一般"]
    assert cb["datacenter"]["selected"] == ["內湖機房"]
    assert cb["environment"]["selected"] == ["正式"]
    assert cb["service_type"]["selected"] == ["內部服務"]
    assert all(v["confidence"] == "high" for k, v in cb.items()
               if k in ("host_type", "apply_kind", "environment"))

    # 舊 .doc：勾記號解碼成 '('
    d = next(x for x in SAMPLES if x.suffix == ".doc" and "異動申請單" in x.name)
    cb2 = doc_import.extract_checkboxes(doc_import.extract_text(d))
    assert cb2["host_type"]["selected"] == ["虛擬機"]
    assert cb2["environment"]["selected"] == ["正式"]

    # .docx 直接打 ☑ 的那份是實體機
    x = next(x for x in SAMPLES if "10.99.194.111" in x.name)
    assert doc_import.extract_checkboxes(doc_import.extract_text(x))["host_type"]["selected"] == ["實體機"]


@needs_samples
def test_上線檢查表不該被判出需求單才有的勾選欄位():
    """第一版沒有限定「標籤附近」，檢查表被判出「申請項目→新增」「服務類型→DMZ」——
    那些字只是剛好出現在檢查項目裡。"""
    for p in [x for x in SAMPLES if "上線前檢查" in x.name]:
        cb = doc_import.extract_checkboxes(doc_import.extract_text(p))
        assert "apply_kind" not in cb and "service_type" not in cb, (p.name, list(cb))


@needs_samples
def test_勾選結果會存進資料庫並回傳():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = next(x for x in SAMPLES if "10.99.195.59" in x.name)
            _insert_hardware(db_path, "CB-001", "SECSVR195-059", "10.99.195.59")
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import",
                            files=[("files", (p.name, fh, "application/octet-stream"))])
            docs = client.get("/api/assets/CB-001/documents").json()["documents"]
            cb = docs[0]["checkboxes"]
            assert cb["host_type"]["selected"] == ["虛擬機"]
            assert cb["host_type"]["asset_field"] == "is_vm"
            assert cb["host_type"]["asset_value"] == "1"
        finally:
            api.app.dependency_overrides.clear()


def _fake_doc(tmp, name, text):
    """用最小的 docx 造測試檔：IP 回收要測「同一 IP 不同時間的兩張單」，
    真實樣本沒有這種組合，只能自己造。"""
    import zipfile
    p = Path(tmp) / name
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return p


def test_IP回收_只有最新那張單算現行():
    """使用者 2026-08-15 指出的關鍵情境：10.99.0.1 三年前有人申請、後來釋放、
    上個月又發給別台。舊單描述的是當時另一台機器，拿它跟現在的資產比對只會製造假不一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "RC-001", "SECSVR001-001", "10.7.0.1")
            old = _fake_doc(tmp, "舊單_10.7.0.1.docx",
                            "※單據編號 A20230101001\n※填表日期 (西元) 2023 年 01 月 05 日\n"
                            "※主機名稱 SECSVR001-001 主機IP 10.7.0.1")
            new = _fake_doc(tmp, "新單_10.7.0.1.docx",
                            "※單據編號 A20260701001\n※填表日期 (西元) 2026 年 07 月 01 日\n"
                            "※主機名稱 SECSVR001-001 主機IP 10.7.0.1")
            for f in (old, new):
                with io.open(f, "rb") as fh:
                    r = client.post("/api/documents/import",
                                    files=[("files", (f.name, fh, "application/octet-stream"))])
                    assert r.json()["failed"] == 0, r.json()

            docs = {d["file_name"]: d for d in client.get("/api/documents").json()["documents"]}
            assert docs["新單_10.7.0.1.docx"]["is_current"] == 1
            assert docs["舊單_10.7.0.1.docx"]["is_current"] == 0, "三年前的單不該算現行"
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_人工審核_確認後重匯不會沖掉人工修正的值():
    """抽取邏輯改版後會重跑全部檔案。如果重匯直接覆蓋，審核就白做了而且沒有任何提示。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = next(x for x in SAMPLES if "10.99.195.59" in x.name)
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import",
                            files=[("files", (p.name, fh, "application/octet-stream"))])
            doc = client.get("/api/documents").json()["documents"][0]
            assert doc["review_status"] == "pending"
            assert doc["values"]["cpu_core"]["value"] == "16"

            r = client.post(f"/api/documents/{doc['id']}/review",
                            json={"values": {"cpu_core": "32", "memory_gb": "128"}})
            assert r.status_code == 200 and r.json()["review_status"] == "confirmed"

            with io.open(p, "rb") as fh:
                client.post("/api/documents/import",
                            files=[("files", (p.name, fh, "application/octet-stream"))])
            after = client.get("/api/documents").json()["documents"][0]
            assert after["review_status"] == "confirmed", "重匯不該把已審核打回未審"
            assert after["values"]["cpu_core"]["value"] == "32", "重匯不該沖掉人工修正"
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_帳密要被遮罩_全文檢索不能搜到密碼():
    """樣本裡 3 份寫著「帳密：arcsight / sys@8864」。把全文丟進 DB 做檢索，
    等於讓任何登入者搜三個字就拿到正式主機密碼。"""
    hit = 0
    for p in SAMPLES:
        raw = doc_import.extract_text(p)
        if "8864" not in raw:
            continue
        hit += 1
        assert "8864" not in doc_import.searchable_text(raw), f"{p.name} 密碼沒遮乾淨"
        assert "［已遮罩］" in doc_import.searchable_text(raw)
    assert hit >= 1, "樣本裡應該有含帳密的單"


@needs_samples
def test_下載原檔要留稽核紀錄():
    """遮罩只保護檢索；原始 Word 仍含帳密（那是稽核證據不能改），所以下載要留紀錄。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = SAMPLES[0]
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import",
                            files=[("files", (p.name, fh, "application/octet-stream"))])
            doc_id = client.get("/api/documents").json()["documents"][0]["id"]
            client.get(f"/api/documents/{doc_id}/download")
            import db as _db
            conn = _db.get_connection(db_path)
            try:
                rows = conn.execute("SELECT * FROM doc_download_audit").fetchall()
            finally:
                conn.close()
            assert len(rows) == 1 and rows[0]["username"] == "tester"
        finally:
            api.app.dependency_overrides.clear()


@needs_samples
def test_檢查表的每一列都抽得出判定():
    """約 90 個勾選框 → 40+ 列檢核項目。三種判定都要分得出來，
    「未填」不能被當成「不需」——沒處理跟不用處理是兩件事。"""
    for p in [x for x in SAMPLES if "上線前檢查" in x.name]:
        rows = doc_import.extract_checklist(doc_import.extract_text(p))
        assert len(rows) >= 35, f"{p.name} 只抽到 {len(rows)} 列"
        assert all(r["verdict"] in ("完成", "不需", "未填") for r in rows)
        assert all(len(r["item"]) >= 3 for r in rows), "項目名稱不該有空的或亂碼殘留"


@needs_samples
def test_需求單第二頁的區塊狀態抽得出來():
    for p in [x for x in SAMPLES if "上線前檢查" not in x.name]:
        sec = doc_import.extract_sections(doc_import.extract_text(p))
        assert len(sec) >= 10, f"{p.name} 只抽到 {len(sec)} 個區塊"
        assert "dns" in sec and "waf" in sec and "database" in sec
        # 這幾份樣本第二頁都空著，狀態應該是「未填」而不是被當成不存在
        assert sec["dns"]["status"] in ("未填", "不適用", "新增", "異動", "刪除")


@needs_samples
def test_同一份單改檔名再上傳會被指出重複():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            p = next(x for x in SAMPLES if "10.99.195.59" in x.name)
            with io.open(p, "rb") as fh:
                client.post("/api/documents/import",
                            files=[("files", (p.name, fh, "application/octet-stream"))])
            with io.open(p, "rb") as fh:
                r = client.post("/api/documents/import",
                                files=[("files", ("改名副本.docx", fh, "application/octet-stream"))])
            assert any("單據編號與既有" in w for w in r.json()["results"][0]["warnings"])
        finally:
            api.app.dependency_overrides.clear()
