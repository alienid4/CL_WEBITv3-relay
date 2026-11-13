"""V2 移植第 23 批 第 7、8 片（2.058.9）：舊 /adopt「已納管主機」的收集全部、匯出、每列改分類搬到 6.1「已納管」；
「另有納管方式」的去處連結（SAN／HMC…）在 6.1「改分類與標籤」；/adopt 進 PORTED，舊網址（含 #頁簽）一律轉到 V2。
⚠D1 純搬移：收集全部同一支端點、同一個 body、確認句逐字；載入時只讀；改分類沿用共用 V2AdoptMove。"""
import re

from test_v2_port_b23 import _api_calls, _src

MAN = "components/v2/AdoptManaged.vue"


def _old_fn(name):
    s = _src("pages/adopt.vue")
    i = s.index(f"function {name}(")
    return s[i:s.index("\n}\n", i)]


def test_已納管_端點是舊頁原本就有的_載入只讀():
    s = _src(MAN)
    calls = _api_calls(s)
    assert calls == {("GET", "/api/manage-state"), ("GET", "/api/onboard/post-collect-queue"), ("POST", "/api/onboard/post-collect-batch"),
                     ("POST", "/api/asset-tags/by-serial"), ("POST", "/api/onboard/audit/latest-by-ip"), ("GET", "/api/onboard/windows/state"),
                     ("GET", "/api/onboard/managed/export-rows")}
    # 舊頁原本就有的＋export-rows（舊頁 Excel 匯出 /api/onboard/managed/export 的 JSON 版，同一份 managed_export.export_rows）
    old = _src("pages/adopt.vue")
    # _api_calls 抓不到舊頁那幾支帶巢狀泛型／換行的呼叫，改成逐支確認舊頁原本就打這支（同方法）
    for meth, path in calls - {("GET", "/api/onboard/managed/export-rows")}:
        i = old.index(f"'{path}'")
        assert (meth == "POST") == ("method: 'POST'" in old[i:i + 200]), (meth, path)
    assert "/api/onboard/managed/export" in _src("pages/adopt.vue")
    # 載入時只讀：onMounted／rows 變了只叫讀取（by-serial、latest-by-ip 是批量查的 POST，舊頁載入時也這樣查）
    assert "onMounted(() => { loadInfo(); loadPostQueue(); loadWin() })" in s
    assert "watch(() => props.rows, () => { loadTags(); loadLastFail() }, { immediate: true })" in s
    for bad in ("<OnboardModal", "<BatchOnboardModal", "<PasteOnboardModal", "<WinOnboardModal", "/api/onboard'", "/api/onboard/revoke"):   # 視窗在父頁，這裡只 emit
        assert bad not in s, bad


def test_收集全部_body與確認句跟舊頁一樣():
    old = _old_fn("collectAllSelected")
    assert "{ method: 'POST', body: { asset_serials: selectedSerials.value } }" in old
    s = _src(MAN)
    assert "{ method: 'POST', body: { asset_serials: list } }" in s
    assert "if (!window.confirm(collectAllConfirm(n, skipped))) return" in s
    u = _src("utils/adoptCollect.ts")
    conf = u[u.index("export function collectAllConfirm("):]
    conf = conf[:conf.index("\n}\n")]
    # 確認句三段逐字
    for frag in ("`對勾選的 ${n} 台各收一輪全部（硬體規格／服務／軟體／帳號／OS 重建／組態檢核）？\\n\\n`",
                 "'・唯讀收集，不會改動目標機。\\n・背景逐台排隊跑，一台約數十秒到幾分鐘；可以離開這頁。'",
                 "(skipped > 0 ? `\\n・另有 ${skipped} 台沒有資產序號，收不了，不會送。` : '')"):
        assert frag in old, frag
        assert frag in conf, frag
    # 成功／失敗訊息同舊頁
    assert "showToast(`已排入 ${r.added} 台${r.already ? `（${r.already} 台本來就在排隊）` : ''}`, 'success')" in old
    assert "showToast(`已排入 ${r.added} 台${r.already ? `（${r.already} 台本來就在排隊）` : ''}`, 'success')" in s
    # 送的＝這張表上勾了而且有序號的；表格重讀後不在表上的勾選丟掉
    assert "const serials = computed(() => collectAllSerials(picked.value, shown.value))" in s
    assert "watch(shown, (rs) => { picked.value = pruneKeys(picked.value, rs) })" in s
    # 按鈕可見度照舊頁：不分帳號等級（後端擋）；撤銷照舊只給管理員
    assert '<button type="button" class="btn" data-test="collect-all"' in s
    # 2.058.13 審查 P1／2.058.14 P2-3：Windows 納管狀態讀不到或還沒讀完（winReady）不給按——不然 Windows 納管的會開成 Linux 撤銷
    assert '<button v-if="isAdmin && winReady" type="button" class="btn2" data-test="managed-revoke-one" :disabled="!one"' in s
    assert "const winReady = computed(() => winLoaded.value && !winErr.value)" in s


def test_改分類_列的轉換跟舊頁一樣_共用視窗():
    old, u, s = _src("pages/adopt.vue"), _src("utils/adoptCollect.ts"), _src(MAN)
    grab = lambda src, head: re.search(re.escape(head) + r"\s*=\s*\{(.*?)\n\}", src, re.S).group(1).split()
    assert grab(old, "const OS_TYPE_TAB: Record<string, string>") == grab(u, "export const OS_TYPE_TAB: Record<string, string>")
    assert "message: `已納管主機，目前 OS 類型：${m.os_type || '未填'}`" in old
    assert "message: `已納管主機，目前 OS 類型：${os || '未填'}`" in u
    assert "os_override: m.os_override ? { ...m.os_override, os_type: m.os_type || '', tab: OS_TYPE_TAB[m.os_type || ''] || '' } : null" in old
    assert "os_override: info?.os_override ? { ...info.os_override, os_type: os, tab: OS_TYPE_TAB[os] || '' } : null" in u
    assert '<V2AdoptMove v-if="move" :rows="move"' in s and '@click="move = [moveRowOf(row as any)]"' in s
    assert "canMove(moveRowOf(row as any))" in s


def test_匯出用V2Table_6_1已納管用新元件():
    s = _src(MAN)
    assert "<V2Table v-model:selected=\"picked\" :rows=\"shown\"" in s and "no-export" not in s
    assert "<table" not in s
    o = _src("pages/v2/onboard.vue")
    assert '<V2AdoptManaged :rows="onbRows"' in o
    assert '<RevokeModal v-if="revoke && !revoke.win"' in o and '<WinRevokeModal v-if="revoke && revoke.win"' in o
    # 舊 Excel 放不下畫面的欄：只在匯出時接上，名稱與順序＝managed_export.headers() 第 9 欄起（前 8 欄畫面上都有）
    import managed_export
    u = _src("utils/adoptCollect.ts")
    m = re.search(r"export const MANAGED_EXPORT_ONLY = \[(.*?)\]", u, re.S)
    assert re.findall(r"'([^']+)'", m.group(1)) == managed_export.headers()[8:]
    for lab in managed_export.headers()[1:8]:
        assert f"label: '{lab}'" in s or lab in ("主機名稱", "OS"), lab
    assert ":export-cols=\"exportCols\" :export-all=\"exportAll\"" in s
    assert "const exportCols = computed(() => (isAdmin.value ?" in s and "const exportAll = computed(() => (isAdmin.value" in s
    assert "...xc.map(c => c.label)]" in _src("components/v2/Table.vue")
    # 完整 Excel：舊頁同一支、同參數名
    assert "`${rtCfg.public.apiBase}/api/onboard/managed/export`" in _src("pages/adopt.vue")
    assert "/api/onboard/managed/export`" in s and "p.set('kw'," in s and "p.set('os'," in s and "p.set('tag'," in s


def test_export_rows_同一份資料_只給管理員():
    import access_policy
    assert "/api/onboard/managed/export-rows" in access_policy.ADMIN_READ_EXACT
    from pathlib import Path
    r = (Path(__file__).resolve().parents[2] / "APP/asset-module/backend/routers/onboard.py").read_text(encoding="utf-8")
    body = r[r.index('@router.get("/api/onboard/managed/export-rows")'):r.index('@router.get("/api/onboard/failures")')]
    assert 'return {"headers": h, "items": [dict(zip(h, r)) for r in managed_export.export_rows(conn)]}' in body
    for bad in ("INSERT", "UPDATE", "DELETE", "commit"):
        assert bad not in body, bad


def test_篩選跟舊頁一樣_網址():
    old, u, s = _src("pages/adopt.vue"), _src("utils/adoptCollect.ts"), _src(MAN)
    for frag in ("if (os && (m.os_type || '未填') !== os) return false",
                 "return [m.ip, m.hostname, m.asset_serial].some((v) => (v || '').toLowerCase().includes(k))"):
        assert frag in old, frag
    assert "if (f.os && (m.os_type || '未填') !== f.os) return false" in u
    assert "if (f.tag && !(m.tags ?? []).includes(f.tag)) return false" in u
    assert "return [m.ip, m.hostname, m.asset_serial].some((v) => (v || '').toLowerCase().includes(k))" in u
    assert "const filter = computed(() => ({ os: q('mos'), kw: q('mq'), tag: q('mtag') }))" in s


def test_每列重新納管_沿用共用視窗_只給管理員():
    s, o, old = _src(MAN), _src("pages/v2/onboard.vue"), _src("pages/adopt.vue")
    # 舊頁：Windows 納管的開 Windows 視窗（opted_in: true），其餘開一鍵納管視窗
    assert "@click=\"openWinOnboard([{ ip: m.ip, hostname: m.hostname, opted_in: true }])\">↻ 重新納管</button>" in old
    assert "@click=\"openOnboard({ ip: m.ip, os_guess: null } as any)\">↻ 重新納管</button>" in old
    assert "if (isWin(r.ip)) emit('win-onboard', [{ ip: r.ip, hostname: info(r)?.hostname ?? null, opted_in: true }])" in s
    assert "else emit('onboard', r.ip)" in s
    assert '<template v-if="isAdmin && (row as any).ip && winReady">' in s        # 收緊：只給管理員；Windows 狀態讀不到不給按
    assert '@onboard="(ip: string) => { onboardIp = ip }" @batch-onboard="(ips: string[]) => { batchIps = ips }" @win-onboard="(rs: any[]) => { winRows = rs }"' in o
    assert '<BatchOnboardModal v-if="batchIps" :ips="batchIps" @done="after(tab)"' in o
    assert '<OnboardModal v-if="onboardIp" :ip="onboardIp" @done="after(tab)"' in o
    assert '<WinOnboardModal v-if="winRows" :rows="winRows"' in o
    for bad in ("'/api/onboard'", "/api/onboard/windows'", "password"):
        assert bad not in s, bad


def test_另有納管方式_去處連到V2():
    c = _src("components/v2/AdoptClassify.vue")
    assert "{ k: 'guide_text', label: '另有納管方式', link: (r: any) => guideHref(r.guide) }" in c
    assert "guide_text: guideText(r.guide)" in c
    import collect_dispatch as cd
    for g in cd._NOK_GUIDE.values():
        assert g["page"] is None or g["page"].startswith("/v2/"), g


def test_adopt進PORTED_舊網址轉到V2():
    m = _src("utils/v2LegacyMap.ts")
    assert "  '/adopt',  " in m
    assert "'/adopt': q => to('/v2/onboard', drop(q, ['tab']))," in m
    # middleware 帶 hash 轉址、6.1 掛載時轉換：行為測試在 frontend/tests/v2AdoptHashRedirect.spec.ts（審查 P2-5，不再比對原始碼字串）
    # 舊頁 #頁簽 全部都有對應（舊頁 ADOPT_TABS＋#managed-hosts）
    old = _src("pages/adopt.vue")
    assert "const ADOPT_TABS: AdoptTab[] = ['collect', 'pending', 'unregistered', 'failures', 'onboarded']" in old
    for t in ("collect", "pending", "failures", "onboarded", "'managed-hosts'"):
        assert f"{t}: '" in m or f"{t}:" in m, t
    assert "t === 'unregistered' ? to('/v2/hosts', { tab: 'unreg' })" in m


def test_每列取消納管_狀態標記_編輯_跟舊頁一樣():
    """2.058.12：取消納管（⚠D1）沿用同一組共用視窗（端點、body、確認都在視窗裡）、同一個 Windows／Linux 分法；標記只顯示；編輯連 V2 表單。"""
    s, o, old = _src(MAN), _src("pages/v2/onboard.vue"), _src("pages/adopt.vue")
    assert '@click="winRevoke = { ip: m.ip, hostname: m.hostname }">取消納管</button>' in old
    assert '@click="revokeIp = m.ip">取消納管</button>' in old
    assert '<RevokeModal v-if="revokeIp" :ip="revokeIp" platform="linux"' in old
    assert "function revokeRow(r: any) { emit('revoke', { ...r, win: isWin(r.ip), name: r.hostname ?? null }) }" in s
    assert '<RevokeModal v-if="revoke && !revoke.win" :ip="revoke.ip" :platform="revokePlatform(revoke)"' in o   # Codex 1019：依登記平台，不寫死 linux
    assert '<WinRevokeModal v-if="revoke && revoke.win" :ip="revoke.ip" :hostname="revoke.name"' in o
    i = s.index('data-test="managed-revoke"')
    assert '<template v-if="isAdmin && (row as any).ip && winReady">' in s[s.rfind("<template", 0, i) - 1:i]
    for frag in ("root 金鑰（暫時）", "本機管理員：", "已納管但失聯"):
        assert frag in old and frag in s, frag
    assert '`/assets/${m.asset_serial}?edit=1`">✎ 編輯</NuxtLink>' in old
    assert '`/v2/asset/${encodeURIComponent((row as any).serial)}?edit=1`">✎ 編輯</NuxtLink>' in s
