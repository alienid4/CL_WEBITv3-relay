"""V2 移植第 23 批（2.058.x）：V17 6.1「開始收集」改 V2 原生（components/v2/AdoptCollect.vue）。
⚠D1 D2：純搬移——端點只能是舊頁 pages/adopt.vue 原本就有的那幾支，body 照抄；載入時只讀；
ESXi／vCenter 天條全在後端，V2 只照後端給的結果顯示、沒有納管入口。"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402

NEW = "components/v2/AdoptCollect.vue"


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _api_calls(s):
    """(method, path)：apiFetch('…') 與 apiFetch(`…${x}…`)；樣板變數一律換成 {x}；沒寫 method＝GET。"""
    out = set()
    for m in re.finditer(r"apiFetch(?:<[^>]*>)?\(\s*(['`])(/api/[^'`]+)\1(?:\s*,\s*\{([^}]*))?", s):
        mm = re.search(r"method:\s*'(\w+)'", m.group(3) or "")
        out.add((mm.group(1) if mm else "GET", re.sub(r"\$\{[^}]+\}", "{x}", m.group(2))))
    return out


# 這一片（只讀）用到的端點；第 2、3 片會把寫入加進來（每加一支都要是舊頁原本就有的）
READ_ONLY = {
    ("GET", "/api/segments/tree"), ("GET", "/api/collect/dispatch/gap"), ("GET", "/api/collect/dispatch/weekly"),
    ("GET", "/api/collect/dispatch/latest"), ("GET", "/api/collect/dispatch/progress"),
    ("GET", "/api/collect/dispatch/{x}/summary"), ("GET", "/api/collect/dispatch/coverage"),
    ("GET", "/api/collect/dispatch/coverage/segment"), ("GET", "/api/onboard/audit"),
    ("GET", "/api/onboard/post-collect-queue"),
}
# 第 2 片（⚠D2）：開始收集、批次間隔、停止、標記已中斷
WRITES = {
    ("POST", "/api/collect/dispatch"), ("PUT", "/api/collect/dispatch/gap"),
    ("POST", "/api/collect/dispatch/{x}/stop"), ("POST", "/api/collect/dispatch/{x}/mark-interrupted"),
    # 第 3 片（⚠D1）：納管後收集排隊取消、重新排入
    ("POST", "/api/onboard/post-collect-queue/cancel"), ("POST", "/api/onboard/post-collect-queue/requeue-lost"),
}


def test_排隊取消_body確認句照舊頁_只送畫面上還在的勾選():
    old, new = _src("pages/adopt.vue"), _src(NEW)
    for frag in ("body: { asset_serials: list }", "取消 ${n} 台的納管後收集？\\n\\n・排隊中的直接移出，這次不收。",
                 "'/api/onboard/post-collect-queue/requeue-lost', { method: 'POST' }"):
        assert frag in old and frag in new, frag
    fn = new[new.index("async function pqCancel"):new.index("async function pqRequeueLost")]
    assert "const list = all ? null : pqPicked.value" in fn and fn.index("confirm(") < fn.index("'/api/onboard/post-collect-queue/cancel'")
    assert "const pqPicked = computed(() => pqToSend(pqSel.value, pqItems.value))" in new
    assert "watch(pqItems, (items) => { pqSel.value = pqPrune(pqSel.value, items) })" in new
    assert '<template v-if="pqPanelOpen && pq.can_admin">' in new      # 舊頁：pq.can_admin 才有取消鍵
    assert 'v-model:selected="pqSel"' in new and "header-clears-all" in new


def test_6_1開始收集用V2原生_不再嵌入舊頁():
    o = _src("pages/v2/onboard.vue")
    assert "<V2AdoptCollect />" in o and "pages/adopt.vue" not in o and "AdoptCollect only" not in o


def test_開始收集_body與確認句跟舊頁一字不差():
    old, new = _src("pages/adopt.vue"), _src(NEW)
    u = _src("utils/adoptCollect.ts")
    for frag in ("body: { targets: targets.value, full: dFull.value, registered_only: dRegOnly.value }",
                 "body: { gap_seconds: Math.round(minutes * 60) }",
                 "停止這一輪收集？\\n\\n・目前那一台收完就停（最多等目前那台約 3 分鐘），剩下的台與批都不跑。",
                 "把這一輪標記為已中斷？"):
        assert frag in old and frag in new, frag
    for frag in ("請先輸入網段或 IP（空白不代表全部）", "還沒按「加入範圍」——先按「加入範圍」再開始收集",
                 "分 ${batches} 批，探測約 ${mins} 分鐘起跳（連得進去的還要另外收）。確定？"):
        assert frag in old and frag in u, frag
    # 送出前一定先過 dispatchGate；確認不過就不送
    run = new[new.index("async function runDispatch()"):new.index("function retryUnfiltered")]
    assert run.index("dispatchGate(") < run.index("'/api/collect/dispatch'")
    assert "if (g.kind === 'confirm' && !confirm(g.text)) return" in run


def test_按鈕可見度照舊頁():
    s = _src(NEW)
    assert """v-if="dispatching && canAdmin && runInfo?.id && runInfo?.status === 'running'\"""" in s
    assert 'v-if="canAdmin" type="button" class="btn2" data-test="dispatch-mark-interrupted"' in s
    # 開始收集鍵舊頁對所有人顯示（後端 ADMIN_WRITE 擋）：照舊
    assert ':disabled="dispatching || !targets.trim()" data-test="dispatch-start"' in s


def test_端點都是舊頁原本就有的_沒有新的():
    old = _api_calls(_src("pages/adopt.vue"))
    new = _api_calls(_src(NEW))
    assert new <= old, new - old
    assert new == READ_ONLY | WRITES, (new - (READ_ONLY | WRITES), (READ_ONLY | WRITES) - new)


def test_載入時只讀_不會自己開跑():
    s = _src(NEW)
    mount = s[s.index("onMounted(async () => {"):s.index("onBeforeUnmount(")]
    assert "method:" not in mount, "載入時不准有任何寫入"
    assert "runDispatch" not in mount and "pqCancel" not in mount
    # watch 只能讀（網段明細 cvs）
    for m in re.finditer(r"watch\(([^\n]*)", s):
        assert "method:" not in m.group(1), m.group(0)


def test_沒有納管入口_VMware照後端結果顯示():
    s = _src(NEW)
    for bad in ("OnboardModal", "BatchOnboardModal", "WinOnboardModal", "PasteOnboardModal", "/api/onboard'", "/api/onboard/windows"):
        assert bad not in s, bad
    # 結果與原因用後端原文（status_label／message），前端不自己重判類別
    assert "{ k: 'status_label', label: '結果' }" in s
    assert "not_onboardable: '另有納管方式'" in _src("utils/adoptCollect.ts") and "STATUS_LABEL" in s


def test_表格是V2Table_沒有V1連結():
    s = _src(NEW)
    assert "<V2Table" in s and "<table" not in s
    for old in ("'/adopt", "'/import'", "'/assets'", "to=\"/settings", "to=\"/import"):
        assert old not in s, old
    assert "/v2/settings?st=import" in s and "/v2/settings?st=identity&sub=schedule" in s


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_ESXi在V2結果卡_另有納管方式_沒有試連():
    """V2 結果卡讀的是 /api/collect/dispatch/{run_id}/summary（run_result_summary）：ESXi 那列要是「另有納管方式」、
    落在「其他」堆（不是待佈身分／失敗，不會被當成要去納管），而且分派時連試連都沒做。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        db.insert_hardware(conn, asset_serial="A-1", ip="10.99.0.11", os="VMware ESXi 8.0.3",
                           hostname="esx-example-01", environment="測試", asset_status="使用中")
        called = []
        out = cd.run_dispatch(conn, "10.99.0.11", prober=lambda ip: [22, 443],
                              ssh_prober=lambda ip: (called.append(ip), (True, None))[1])
        assert called == []
        sm = cd.run_result_summary(conn, out["run_id"])
        r = sm["rows"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["status_label"] == "另有納管方式"
        assert r["bucket"] == "other" and sm["counts"]["needs_credential"] == 0
        assert "VMware ESXi" in r["message"]
        conn.close()
