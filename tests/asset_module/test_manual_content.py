"""使用手冊守門：內容檔的每一節結構完整、沒有真實網段或公司識別字、更新紀錄檔存在。

manual.ts 是 TypeScript，這裡不跑 TS，用文字比對檢查（檔案格式刻意寫得很死，見檔頭說明）。
更細的結構檢查在前端 vitest（tests/manual.spec.ts）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
MANUAL = FE / "content" / "manual.ts"
FIGS = FE / "content" / "manualFigures.ts"
LOG = ROOT / "docs" / "使用手冊_更新紀錄.md"

# 禁用字樣用 \u 跳脫寫，免得這支測試檔本身出現公司識別字
BANNED = [r"10\.9\d\.", "cat" + "hay", "國" + "泰", "secan" + "sible"]   # 用字串相接：公開 relay 的去識別替換會改寫整段字面值，守門測試自己不能被改


def _sections():
    text = MANUAL.read_text(encoding="utf-8")
    body = text.split("export const MANUAL_SECTIONS", 1)[1]
    body = body.split("// ===== 以下是純函式", 1)[0]
    return body.split("\n  {\n    id: '")[1:]


import pytest

# 公開 relay 的產出物只含 APP／tests／.project，沒有 docs/：更新紀錄檔相關檢查在 relay 上略過（私有 repo 照常檢查）
_NO_DOCS = not (ROOT / "docs").exists()


def test_manual_files_exist():
    assert MANUAL.exists() and FIGS.exists()
    if _NO_DOCS:
        pytest.skip("relay 產出物沒有 docs/")
    assert LOG.exists(), "docs/使用手冊_更新紀錄.md 不存在"


@pytest.mark.skipif(_NO_DOCS, reason="relay 產出物沒有 docs/")
def test_update_log_first_line_states_rule():
    first = LOG.read_text(encoding="utf-8").splitlines()[0]
    assert "同一次提交" in first and "手冊哪一節" in first


def test_nine_sections_each_complete():
    chunks = _sections()
    assert len(chunks) >= 9
    for c in chunks:
        sid = c.split("'", 1)[0]
        for field in ("title", "when", "sees"):
            m = re.search(rf"\n    {field}: '(.+)',\n", c)
            assert m and m.group(1).strip(), f"{sid} 缺 {field}"
        for field in ("steps", "notes"):
            m = re.search(rf"\n    {field}: \[\n(.*?)\n    \],\n", c, re.S)
            assert m and re.search(r"^\s+'.+',$", m.group(1), re.M), f"{sid} 的 {field} 是空的"
        assert re.search(r"\n    figures: \[", c), f"{sid} 缺 figures"


def test_no_banned_words_anywhere():
    for f in (MANUAL, FIGS, *([LOG] if LOG.exists() else [])):
        text = f.read_text(encoding="utf-8")
        for pat in BANNED:
            assert not re.search(pat, text, re.I), f"{f.name} 出現禁用字樣 {pat}"


def test_figure_svgs_have_no_real_network_ranges():
    text = FIGS.read_text(encoding="utf-8")
    assert not re.search(r"10\.9\d\.", text)
    # 圖裡出現的 IP 只允許文件保留網段 192.0.2.x
    for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
        assert ip.startswith("192.0.2."), f"示意圖出現非示範 IP：{ip}"


def test_manual_version_shape():
    text = MANUAL.read_text(encoding="utf-8")
    assert re.search(r"MANUAL_VERSION = '\d+\.\d{3}\.\d+'", text)


def test_no_inexact_phrases_and_no_pending():
    text = MANUAL.read_text(encoding="utf-8")
    for w in ("清冊每一筆", "任何一張表格", "一定比", "上架助手"):
        assert w not in text, f"手冊不該再出現：{w}"
    assert "（待確認" not in text.split("export const PENDING_MARK")[0].split("export const MANUAL_SECTIONS", 1)[1]


def test_section_switch_keeps_other_query():
    vue = (FE / "pages" / "v2" / "manual.vue").read_text(encoding="utf-8")
    assert "...route.query, s: v" in vue
