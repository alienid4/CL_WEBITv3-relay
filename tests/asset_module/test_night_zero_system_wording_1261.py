"""Codex 1261：夜間速查卡③「0 個業務系統・3 台相依主機」底下卻寫「也沒有任何系統或主機依賴它」。

零系統不等於零主機：說明要看 host_count；讀不到主機數不猜 0。
四種情況：0 系統＋有主機、0 系統＋0 主機、0 系統＋主機數未知、有系統（不出這句）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "night.vue").read_text(encoding="utf-8")
NONE_TEXT = "這台沒有登記業務系統，圖譜裡也沒有任何系統或主機依賴它。"


def _note_fn() -> str:
    m = re.search(r"const noSystemNote = computed\(\(\) => \{(.*?)\n\}\)", PAGE, re.S)
    assert m, "要有 noSystemNote（零系統說明依主機數決定）"
    return m.group(1)


def test_零系統的說明不再只看系統數():
    # 「沒有任何主機依賴它」那句不可以只靠 !allSystems.length 決定
    assert not re.search(r'v-if="!allSystems\.length"[^>]*>' + re.escape(NONE_TEXT), PAGE)
    assert '<div v-if="noSystemNote" class="sub">{{ noSystemNote }}</div>' in PAGE


def test_四種情況各有自己的說法():
    fn = _note_fn()
    assert "if (allSystems.value.length) return ''" in fn          # 有系統：不出零系統說明
    assert "typeof n !== 'number'" in fn and "讀不到" in fn         # 未知：不猜 0
    assert "n > 0" in fn and "台主機依賴它" in fn                   # 0 系統＋有主機
    # 「沒有任何主機依賴」只能出現在 n 是數字且不 > 0 之後（最後一個 return）
    assert fn.rstrip().splitlines()[-1].strip() == f"return '{NONE_TEXT}'"


def test_主機數讀不到時大字不顯示0():
    assert "card.impact.host_count ?? 0" not in PAGE
    assert "typeof card.impact.host_count === 'number' ? card.impact.host_count : '—'" in PAGE
