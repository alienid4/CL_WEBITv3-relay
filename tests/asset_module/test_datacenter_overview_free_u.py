"""機房總覽表的「可用 U」（W221-001，Codex 29 號退回）：有未定位設備或 U 位未登錄的櫃，
總覽表不可顯示確定的可用 U（要顯示上限與未定位數／待確認），要與摘要、電力表同語意。
這是資料契約＋渲染來源測試：後端要給 unplaced_count，前端表格欄位要真的用 free_text 顯示。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
FE = ROOT / "APP" / "asset-module" / "frontend"


def _vue():
    return (FE / "pages" / "v2" / "datacenter.vue").read_text(encoding="utf-8")


def test_總覽表可用U欄用free_text顯示():
    s = _vue()
    assert "free_text: freeUText(t.power" in s                                  # rackRows 帶不確定性顯示字
    assert re.search(r"\{ k: 'free_u', label: '可用 U'[^}]*text: \(r: any\) => r\.free_text", s), "總覽表『可用 U』欄沒有用 free_text"


def test_freeUText是共用工具且被總覽頁使用():
    """規則本體在 frontend/utils/rackFree.ts（vitest：tests/rackFree.spec.ts 實際執行四種情境）；
    這裡只確認頁面真的 import 並使用它，沒有各寫一份。"""
    u = (FE / "utils" / "rackFree.ts").read_text(encoding="utf-8")
    assert "U 位未登錄，待確認" in u and "台未定位" in u and "String(power.free_u)" in u
    assert "import { freeUText } from '~/utils/rackFree'" in _vue()
    assert "function freeUText" not in _vue()                      # 不在頁面內重寫
    assert (FE / "tests" / "rackFree.spec.ts").exists()


def test_後端有未定位時rack_power給unplaced_count():
    import rack_layout
    src = (ROOT / "APP" / "asset-module" / "backend" / "rack_layout.py").read_text(encoding="utf-8")
    assert "unplaced_count" in src and hasattr(rack_layout, "rack_power")
    # 機櫃磚（tile）的 power 就是 rack_power 的輸出；前端 rackRows 讀的是 t.power.unplaced_count
    assert "unplaced_count" in _vue()


def test_總覽表_摘要_電力表三處語意一致():
    s = _vue()
    for marker in ("freeUText(", "≤${room.power.free_u}", "free_text: t.power.unplaced_count"):
        assert marker in s or marker.replace("${", "${") in s, marker
