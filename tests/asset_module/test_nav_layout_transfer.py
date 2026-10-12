"""選單設定匯出／匯入（2026-09-20 使用者：221 跟公司機順序不同，要能互相搬）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import pytest  # noqa: E402


ADMIN = {"username": "admin"}   # 這幾支端點限管理員，測試給一個管理員身分


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


LAYOUT = [
    {"id": "g0", "label": "總覽", "icon": "◈", "to": "/", "items": ["/issues", {"to": "/distribution", "label": "分佈"}]},
    {"id": "g1", "label": "納管", "icon": "＋", "to": "", "items": ["/adopt"]},
]


def test_匯出再匯入_設定原封不動(tmp_path):
    c = _conn(tmp_path)
    api.set_nav_layout(api.NavLayoutBody(layout=LAYOUT), session=ADMIN, conn=c)
    exported = api.export_nav_layout(session=ADMIN, conn=c)
    import json

    payload = json.loads(exported.body.decode("utf-8"))
    assert payload["kind"] == "webit3-nav-layout" and payload["layout"] == LAYOUT
    assert payload["exported_at"] and payload["source"], "要記錄哪一台、什麼時候匯出的"

    c2 = _conn(tmp_path / "b")
    r = api.import_nav_layout(api.NavLayoutImportBody(**payload), session=ADMIN, conn=c2)
    assert r["groups"] == 2 and r["items"] == 3 and r["replaced"] is False
    assert api.get_nav_layout(session=ADMIN, conn=c2)["layout"] == LAYOUT


def test_匯入空的等於回到內建預設(tmp_path):
    c = _conn(tmp_path)
    api.set_nav_layout(api.NavLayoutBody(layout=LAYOUT), session=ADMIN, conn=c)
    r = api.import_nav_layout(api.NavLayoutImportBody(kind="webit3-nav-layout", layout=None),
                              session=ADMIN, conn=c)
    assert r["replaced"] is True and "內建預設" in r["note"]
    assert api.get_nav_layout(session=ADMIN, conn=c)["layout"] is None


def test_壞掉的檔要擋下來_不可以把選單弄壞(tmp_path):
    c = _conn(tmp_path)
    for bad in ([{"items": ["/x"]}], [{"label": "群", "items": "不是陣列"}],
                [{"label": "群", "items": [123]}]):
        with pytest.raises(api.HTTPException) as e:
            api.import_nav_layout(api.NavLayoutImportBody(layout=bad), session=ADMIN, conn=c)
        assert e.value.status_code == 400
    with pytest.raises(api.HTTPException) as e:
        api.import_nav_layout(api.NavLayoutImportBody(kind="別的東西", layout=LAYOUT),
                              session=ADMIN, conn=c)
    assert e.value.status_code == 400
    assert api.get_nav_layout(session=ADMIN, conn=c)["layout"] is None, "擋下來就不可以留下半套設定"
