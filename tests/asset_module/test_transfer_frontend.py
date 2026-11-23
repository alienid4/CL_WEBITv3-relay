"""檔案傳送（Q94）前端的原始碼守門：頁籤只在「模組開＋登入啟用＋admin」出現、表格一律用 V2Table、
說明收進 InfoNote、顏色取 CSS 變數、前端呼叫的 API 後端都有。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

COMPS = sorted((FE / "components" / "v2").glob("FileTransfer*.vue"))


def _src(p):
    return p.read_text(encoding="utf-8")


def test_五個頁籤元件加外框都在():
    names = {p.name for p in COMPS}
    assert names == {"FileTransfer.vue", "FileTransferHosts.vue", "FileTransferFiles.vue", "FileTransferQueue.vue",
                     "FileTransferCmd.vue", "FileTransferLog.vue"}
    s = _src(FE / "components" / "v2" / "FileTransfer.vue")
    for label in ("① 主機", "② 傳檔", "③ 傳送佇列", "④ 指令", "⑤ 紀錄", "Linux（含 AIX）"):
        assert label in s, label


def test_設定頁籤只在模組開_admin時出現():
    s = _src(FE / "pages" / "v2" / "settings.vue")
    m = re.search(r"const showTransfer = computed\(\(\) => (.*)\)\n", s)
    assert m, "找不到 showTransfer"
    expr = m.group(1)
    assert "module_key === 'file_transfer'" in expr and "f.enabled === 1" in expr     # 明確開著才算（不用預設 true 的 isEnabled）
    assert "!authDisabled.value" not in expr and "isAdmin.value" in expr     # 2026-10-30 使用者決定：登入停用也放行（測試）
    assert "isEnabled(" not in expr
    assert "showTransfer.value ?" in s and "'transfer'" in s
    assert "<V2FileTransfer " in s


def test_沒有放進搜尋_選單_功能清單():
    for rel in ("composables/useNavV2.ts", "composables/useNav.ts", "components/v2/Search.vue", "components/GlobalSearch.vue"):
        assert "檔案傳送" not in _src(FE / rel), rel
        assert "transfer" not in _src(FE / rel).lower().replace("transferred", ""), rel


def test_表格都用V2Table_沒有自己畫table():
    for p in COMPS:
        assert "<table" not in _src(p), p.name
    assert "<V2Table" in _src(FE / "components" / "v2" / "FileTransferHosts.vue")
    assert "selectable" in _src(FE / "components" / "v2" / "FileTransferHosts.vue")
    log = _src(FE / "components" / "v2" / "FileTransferLog.vue")
    assert log.count("<V2Table") >= 5


def test_說明收進InfoNote_不攤在畫面上():
    for p in COMPS:
        s = _src(p)
        body = s.split("<template>", 1)[1].split("</template>\n\n<style", 1)[0]
        assert "<InfoNote>" in body or p.name in ("FileTransfer.vue", "FileTransferQueue.vue"), p.name
        assert "<p class=\"hint\"" not in body


def test_顏色取CSS變數_只有終端機區塊例外():
    for p in COMPS:
        style = _src(p).split("<style", 1)[1]
        for line in style.splitlines():
            if re.search(r"#[0-9a-fA-F]{3,6}\b", line):
                assert p.name == "FileTransferCmd.vue" and (".term" in line), (p.name, line)


def test_危險動作有確認_要輸入台數():
    s = _src(FE / "components" / "v2" / "FileTransferFiles.vue")
    assert "typed.value.trim() === String(ips.value.length)" in s and "confirm_count: Number(typed.value)" in s
    assert "needs_second_confirm" in s and "confirm_big" in s
    assert "overwrite" in s
    assert "X-Filename" in s and "FormData" not in s                      # 上傳走原始位元組＋檔名標頭（後端才擋得住上限）
    assert "props.box === 'windows' ? 1 : 2" in s                         # Windows 預設同時 1 台


def test_指令頁輸入列在最上面_最新輸出在最上面():
    s = _src(FE / "components" / "v2" / "FileTransferCmd.vue")
    assert s.index('class="cmdin"') < s.index('class="term"')
    assert "runs.value.unshift" in s and "ArrowUp" in s
    assert "PS>" in s and "'$'" in s


def test_catch都有交代():
    for p in COMPS:
        s = _src(p)
        for m in re.finditer(r"catch \(e: any\) \{([^}]*)\}", s):
            assert re.search(r"showToast|\.value = |err|Msg", m.group(1)), (p.name, m.group(0)[:80])


def test_前端呼叫的API路徑後端都有():
    import api
    routes = {(m, getattr(r, "path")) for r in api.app.routes for m in getattr(r, "methods", set())}
    paths = {p for _, p in routes}
    norm = lambda x: re.sub(r"\$\{[^}]*\}", "{x}", x)
    back = {re.sub(r"\{[^}]*\}", "{x}", p) for p in paths}
    seen = 0
    for p in COMPS:
        for m in re.finditer(r"apiFetch(?:<[^>]*>)?\(\s*([`'])(/api/transfer[^`']*)\1", _src(p)):
            seen += 1
            assert norm(m.group(2)) in back, (p.name, m.group(2))
    assert seen >= 15
