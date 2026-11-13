"""壓縮版資料庫匯出＋納管失敗清單搜尋（2026-10-22）。

使用者：「326M 太大了」「這個重點先改，不然你連不到公司很多只能瞎猜」；
原本想拿掉 vCenter 明細分頁，使用者：「我覺得你都會用到」——所以**一筆都不拿**，只壓縮。
納管失敗清單：「沒搜尋」——畫面與匯出同一組欄位。

IP 一律範例位址（192.0.2.x）。
"""
import io
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import backup  # noqa: E402
import db  # noqa: E402
import onboard_failures as of  # noqa: E402

ADOPT_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")


def test_壓縮版_資料一筆不少_附各表大小_正式庫不變():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "asset.db"
        db.init_db(p)
        c = sqlite3.connect(p)
        rows = [("vcenter", "a"), ("vcenter_extra:vPartition", "b"), ("vcenter_extra:vNetwork", "c"),
                ("vcenter_extra:vCPU", "d"), ("dynassets", "e")]
        c.executemany("INSERT INTO source_record (source, source_key, payload) VALUES (?,?,'{}')", rows)
        c.commit()
        c.close()
        before = p.read_bytes()
        z = zipfile.ZipFile(io.BytesIO(backup.lite_dump(p)))
        assert set(z.namelist()) == {"asset.db", "各表大小.json"}
        info = json.loads(z.read("各表大小.json"))
        assert any(t["table"] == "source_record" and t["rows"] == 5 for t in info["tables"])
        out = Path(tmp) / "out.db"
        out.write_bytes(z.read("asset.db"))
        oc = sqlite3.connect(out)
        try:
            left = {r[0] for r in oc.execute("SELECT source FROM source_record")}
        finally:
            oc.close()
        assert left == {s for s, _ in rows}, "vCenter 明細一筆都不能少"
        assert p.read_bytes() == before, "正式庫一個位元組都不能動"


def test_匯出API與畫面有壓縮版():
    src = (BACKEND / "routers" / "system.py").read_text(encoding="utf-8")
    assert '"lite"' in src and "backup.lite_dump(db_path)" in src
    vue = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "settings" / "index.vue").read_text(encoding="utf-8")
    assert '<option value="lite">' in vue


def test_納管失敗搜尋_每個字都要中_畫面與匯出同一組欄位():
    item = {"ip": "192.0.2.70", "hostname": "SECSVR-X", "department": "資訊架構部", "contact": "王小明"}
    assert of.matches_q(item, None) and of.matches_q(item, "")
    assert of.matches_q(item, "secsvr 資訊架構")
    assert not of.matches_q(item, "secsvr 財務")
    front = re.search(r"const fSearchKeys = \[(.*?)\]", ADOPT_VUE, re.S).group(1)
    assert tuple(re.findall(r"'(\w+)'", front)) == of.SEARCH_KEYS
    assert "q=${encodeURIComponent(fQ.value.trim())}" in ADOPT_VUE, "匯出要帶搜尋字"
