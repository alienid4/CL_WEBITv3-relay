"""2.059.3（V65 收尾）：5.1 機房平面圖批次標記（⚠D4）——同一支 PUT /api/racks/state/bulk、限管理員、先預覽再確認、只送看得到的勾選。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def test_批次標記同一支API_先預覽再確認():
    old = (FE / "pages" / "datacenter.vue").read_text(encoding="utf-8")
    new = (FE / "pages" / "v2" / "datacenter.vue").read_text(encoding="utf-8")
    assert "'/api/racks/state/bulk', { method: 'PUT', body: { keys: pickedKeys.value, state: kind === 'clear' ? null : kind, note: '現場盤點' } }" in old
    assert "guardedFetch('/api/racks/state/bulk', { method: 'PUT', body: { keys: p.keys, state: p.kind === 'clear' ? null : p.kind, note: '現場盤點' } })" in new
    assert "!!dc.value?.can_edit" in new                                   # 限管理員（後端 _can_edit_nav 另擋）
    assert 'data-test="bulk-confirm"' in new and "askBulk(k)" in new        # 兩步：先預覽、按確認才送
    assert "bulkPreview(kind, bulkKeys.value, drill.value?.keys ?? []" in new   # 只送目前清單看得到的勾選
    assert "/datacenter?legacy=1" not in new                                  # 舊版入口拿掉


def test_後端批次標記仍限管理員():
    import access_policy as ap
    assert ap.level_for("PUT", "/api/racks/state/bulk") == "admin"
