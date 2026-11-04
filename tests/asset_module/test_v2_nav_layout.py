"""2.x 選單可拖拉（2.042.21，V43 移植）：/api/v2/nav-layout 限管理員、形狀檢查、回到預設、備份、操作紀錄，
而且存在新的 app_settings.nav_layout_v2，舊選單的 nav_layout 完全不動。合併規則（程式預設＋覆寫）在前端，
見 APP/asset-module/frontend/tests/navV2Merge.spec.ts。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import nav_prefs  # noqa: E402
from fastapi import HTTPException  # noqa: E402

ADMIN = {"username": "admin"}
USER = {"username": "alice"}
LAYOUT = [{"no": "2", "items": ["/v2/hosts"]}, {"no": "1", "items": ["/v2", "/v2/changes", "/v2/risk"]}]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _setting(c, key):
    r = c.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r and r[0] else None


def test_預設是null_管理員可改_非管理員不行(tmp_path):
    c = _conn(tmp_path)
    r = api.nav_v2_layout_get(session=ADMIN, conn=c)
    assert r["layout"] is None and r["can_edit"] is True
    assert api.nav_v2_layout_get(session=USER, conn=c)["can_edit"] is False
    for call in (lambda: api.nav_v2_layout_put(api.NavV2LayoutBody(layout=LAYOUT), session=USER, conn=c),
                 lambda: api.nav_v2_layout_reset(session=USER, conn=c)):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 403
    assert _setting(c, nav_prefs.V2_LAYOUT_KEY) is None, "被擋下來不可以留下任何設定"


def test_存_讀_回到預設_都有備份與操作紀錄(tmp_path):
    c = _conn(tmp_path)
    c.execute("INSERT INTO app_settings (key, value) VALUES ('nav_layout', ?)", (json.dumps([{"id": "old"}]),))
    c.commit()
    r = api.nav_v2_layout_put(api.NavV2LayoutBody(layout=LAYOUT), session=ADMIN, conn=c)
    assert r["layout"] == LAYOUT and r["updated_by"] == "admin" and r["updated_at"]
    assert api.nav_v2_layout_get(session=USER, conn=c)["layout"] == LAYOUT       # 全站共用，一般人讀得到
    r = api.nav_v2_layout_reset(session=ADMIN, conn=c)
    assert r["layout"] is None and r["had_override"] is True
    bk = _setting(c, nav_prefs.V2_BACKUP_KEY)
    assert bk[0]["layout"] == LAYOUT and bk[0]["reason"] == "回到預設前"           # 回預設前的版本留著
    assert _setting(c, "nav_layout") == [{"id": "old"}], "舊選單的設定不可以被動到"
    acts = [r[0] for r in c.execute("SELECT path FROM activity_log ORDER BY id")]
    assert "/api/v2/nav-layout" in acts and "/api/v2/nav-layout/reset" in acts


def test_備份只留5版(tmp_path):
    c = _conn(tmp_path)
    for i in range(8):
        api.nav_v2_layout_put(api.NavV2LayoutBody(layout=[{"no": "1", "items": [f"/v2/x{i}"]}]), session=ADMIN, conn=c)
    assert len(_setting(c, nav_prefs.V2_BACKUP_KEY)) == nav_prefs.MAX_BACKUPS


@pytest.mark.parametrize("bad", [
    [],                                                         # 空的（要回預設請用 reset）
    "不是清單",
    [{"items": ["/v2"]}],                                       # 沒有群組編號
    [{"no": "1", "items": "/v2"}],                              # items 不是清單
    [{"no": "1", "items": ["/assets"]}],                        # 不是 V2 網址
    [{"no": "1", "items": ["javascript:alert(1)"]}],
    [{"no": "1", "items": ["/v2/a b"]}],                        # 有空白
    [{"no": "1", "items": ["/v2"]}, {"no": "1", "items": []}],  # 群組重複
    [{"no": "1", "items": ["/v2"]}, {"no": "2", "items": ["/v2"]}],   # 同一項放兩組
    [{"no": "123456789", "items": []}],                         # 編號太長
    [{"no": str(i), "items": []} for i in range(31)],           # 群組太多
])
def test_形狀不對一律400_不留半套(tmp_path, bad):
    c = _conn(tmp_path)
    with pytest.raises(HTTPException) as e:
        api.nav_v2_layout_put(api.NavV2LayoutBody.model_construct(layout=bad), session=ADMIN, conn=c)
    assert e.value.status_code == 400
    assert _setting(c, nav_prefs.V2_LAYOUT_KEY) is None


def test_登入停用時放行(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    r = api.nav_v2_layout_put(api.NavV2LayoutBody(layout=LAYOUT), session=None, conn=c)
    assert r["layout"] == LAYOUT and r["updated_by"] == "（登入停用）"


def test_權限表登記為管理員才能寫():
    import access_policy
    src = Path(access_policy.__file__).read_text(encoding="utf-8")
    assert '"/api/v2/nav-layout"' in src and '"/api/v2/nav-layout/reset"' in src
    assert access_policy.level_for("PUT", "/api/v2/nav-layout") == "admin"
    assert access_policy.level_for("POST", "/api/v2/nav-layout/reset") == "admin"
    assert access_policy.level_for("GET", "/api/v2/nav-layout") != "admin"      # 讀：每個人的選單都要套


def test_外框用合併後的選單_設定頁用V2原生編輯器():
    layout = (FE / "layouts" / "v2.vue").read_text(encoding="utf-8")
    assert 'v-for="g in navGroups"' in layout and "useV2Nav()" in layout
    settings = (FE / "pages" / "v2" / "settings.vue").read_text(encoding="utf-8")
    assert "components/v2/NavEditor.vue" in settings and "pages/nav-editor.vue" not in settings
    ed = (FE / "components" / "v2" / "NavEditor.vue").read_text(encoding="utf-8")
    assert "/api/v2/nav-layout/reset" in ed and "draggable" in ed and "mergeNavV2" in ed
