<script setup lang="ts">
// 8.2「組態檢核」頁籤（V25，2.048.21 起 V2 原生；取代嵌入的舊頁 pages/config-audit.vue）。同一批 API，行為不變：
// GET /api/config-audit（每台一列＋summary）、/api/config-audit/items?platform&result（條目下鑽，後端同一份計算）、/api/config-audit/{id}（單機明細）、
// POST /api/config-audit/import（匯入腳本產出的 .txt；管理員，後端 ADMIN_WRITE）、GET /api/config-audit/export（DYN 原始格式；管理員，_ADMIN_EXPORTS）、
// GET /api/scripts＋/api/scripts/{name}/download（取得檢核腳本；下載限管理員，_ADMIN_EXPORTS）。
// 這頁不會連到目標機（只收腳本結果的文字檔），⚠D2 只在於腳本本身由人帶去目標機跑。
// 四件事照舊寫死：「未查到」自己一態（不算合規也不算不合規）；合規率算式跟數字一起顯示；平台看表頭不看檔名（後端）；每個數字點得進去。
// 網址：?tab=config&ct=overview|hosts|import；總覽下鑽 cd=hosts|untrust|unmatched|noend|platform|items（＋cp=平台、cr=結果）；單機 ch=<run id>（＋cf=結果）。
const { apiFetch } = useApi()
const route = useRoute()
const { isAdmin } = useAuth()
const runtimeConfig = useRuntimeConfig()
const apiBase = runtimeConfig.public.apiBase
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const u = (extra: Record<string, string | undefined> = {}) => {
  const query: Record<string, string> = { tab: 'config' }
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== '') query[k] = v
  return { path: '/v2/audit', query }
}
const machine = (s: string | null) => (s ? `/v2/machine/${encodeURIComponent(s)}` : null)

const RESULT_LABEL: Record<string, string> = { 'Compliant': '合規', 'Non-Compliant': '不合規', 'Not-Applicable': '不適用', 'Error': '未查到' }
// 跟後端 tally()／result_bucket() 同一條規則：認不得的結果值算「未查到」，不然點「未查到 3」進來可能只列出 2 條
const bucketOf = (r: string | null | undefined) => { const s = (r || '').trim(); return s === 'Compliant' || s === 'Non-Compliant' || s === 'Not-Applicable' ? s : 'Error' }
// 合規率分母是 0（全是不適用／未查到）＝算不出來，顯示「未知」，不可以顯示成 0% 或空白
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '未知' : `${v}%`)
const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`

const rows = ref<any[]>([])
const summary = ref<any>(null)
const loadErr = ref('')
const loaded = ref(false)
async function load() {
  try {
    const r = await apiFetch<any>('/api/config-audit')
    rows.value = r.items ?? []; summary.value = r.summary ?? null; loadErr.value = ''
  } catch (e: any) { loadErr.value = `讀不到檢核結果：${errMsg(e)}` }      // 不靜默：空白會被當成「還沒有人匯入過」
  finally { loaded.value = true }
}
const scripts = ref<any[]>([])
const scriptsErr = ref('')
async function loadScripts() {
  try { scripts.value = (await apiFetch<any>('/api/scripts')).items ?? []; scriptsErr.value = '' } catch (e: any) { scriptsErr.value = `讀不到腳本清單：${errMsg(e)}` }
}
onMounted(() => { load(); loadScripts() })

const ct = computed(() => (['overview', 'hosts', 'import'].includes(q('ct')) ? q('ct') : (q('ch') ? 'hosts' : 'overview')))
const CTL = computed(() => [['overview', '總覽'], ['hosts', `各主機 ${rows.value.length}`], ['import', '匯入與腳本']])

// 項次：照預設排序（主機名 A→Z，再 IP）編好、固定不變
const byHost = (a: any, b: any) => String(a.hostname ?? '').toLowerCase().localeCompare(String(b.hostname ?? '').toLowerCase())
  || String(a.ip ?? '').localeCompare(String(b.ip ?? ''), undefined, { numeric: true })
const numbered = computed(() => [...rows.value].sort(byHost).map((r, i) => ({ ...r, __no: i + 1, host_t: r.hostname || '（無主機名）',
  cov_t: pct(r.coverage), plat_t: r.platform || '未知', sv_t: [r.script_version, r.self_made_label, r.baseline_label].filter(Boolean).join('・') })))
const platRows = computed(() => (summary.value?.by_platform ?? []).map((p: any, i: number) => ({ ...p, __no: i + 1, c_ok: p['Compliant'], c_bad: p['Non-Compliant'],
  c_na: p['Not-Applicable'], c_err: p['Error'], cov_t: pct(p.coverage) })))

const hostLink = (r: any, cf?: string) => u({ ct: 'hosts', ch: String(r.id), cf })
const hostCols = [
  { k: 'host_t', label: '主機', link: (r: any) => hostLink(r) }, { k: 'ip', label: 'IP', mono: true }, { k: 'plat_t', label: '平台' },
  { k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => machine(r.asset_serial), text: (r: any) => r.asset_serial || '對不回資產清冊' },
  { k: 'os_version', label: 'OS 版本' }, { k: 'checked_at', label: '檢核時間', mono: true }, { k: 'sv_t', label: '腳本版本' },
  { k: 'total', label: '條數', mono: true, link: (r: any) => hostLink(r) },
  { k: 'compliant', label: '合規', mono: true, link: (r: any) => hostLink(r, 'Compliant') },
  { k: 'non_compliant', label: '不合規', mono: true, link: (r: any) => hostLink(r, 'Non-Compliant') },
  { k: 'not_applicable', label: '不適用', mono: true, link: (r: any) => hostLink(r, 'Not-Applicable') },
  { k: 'error', label: '未查到', mono: true, link: (r: any) => hostLink(r, 'Error') },
  { k: 'coverage', label: '合規率', mono: true, text: (r: any) => r.cov_t, link: (r: any) => hostLink(r), note: '分母＝總數 − 不適用 − 未查到；點主機看算式' }]
const sumLink = (cd: string, cp?: string, cr?: string) => u({ ct: 'overview', cd, cp, cr })
const platCols = [{ k: 'platform', label: '平台' }, { k: 'hosts', label: '主機', mono: true, link: (p: any) => sumLink('platform', p.platform) },
  { k: 'c_ok', label: '合規', mono: true, link: (p: any) => sumLink('items', p.platform, 'Compliant') },
  { k: 'c_bad', label: '不合規', mono: true, link: (p: any) => sumLink('items', p.platform, 'Non-Compliant') },
  { k: 'c_na', label: '不適用', mono: true, link: (p: any) => sumLink('items', p.platform, 'Not-Applicable') },
  { k: 'c_err', label: '未查到', mono: true, link: (p: any) => sumLink('items', p.platform, 'Error') },
  { k: 'coverage', label: '合規率', mono: true, text: (p: any) => p.cov_t, link: (p: any) => sumLink('platform', p.platform) },
  { k: 'coverage_formula', label: '算式' }]

// ===== 總覽下鑽（主機層級直接篩 rows——跟 summary 同一份 runs()；條目層級打 /items，後端同一份計算）=====
const DRILL = ['hosts', 'untrust', 'unmatched', 'noend', 'platform', 'items']
const cd = computed(() => (ct.value === 'overview' && DRILL.includes(q('cd')) ? q('cd') : ''))
const HOST_FILTER: Record<string, (r: any) => boolean> = {
  hosts: () => true, untrust: (r) => !r.trustworthy, unmatched: (r) => !r.asset_serial, noend: (r) => r.completeness === '無完成標記' }
const drillHosts = computed(() => {
  if (!cd.value || cd.value === 'items') return []
  if (cd.value === 'platform') return numbered.value.filter(r => (r.platform || '未知') === q('cp'))
  return numbered.value.filter(HOST_FILTER[cd.value])
})
const drillTitle = computed(() => ({ hosts: '有檢核結果的主機', untrust: '結果不可信（有未查到）', unmatched: '對不回資產清冊', noend: '無完成標記',
  platform: `${q('cp')} 的主機`, items: `${q('cp')}・${RESULT_LABEL[q('cr')] ?? q('cr')}` } as Record<string, string>)[cd.value] ?? '')
const items = ref<any[]>([])
const itemsErr = ref('')
const itemsLoading = ref(false)
const beginItems = useLatest()
watch(() => [cd.value, q('cp'), q('cr')], async () => {
  const isLatest = beginItems()
  items.value = []; itemsErr.value = ''; itemsLoading.value = false
  // 結果不可信＝有未查到：同時列出是哪幾條沒查到
  const result = cd.value === 'items' ? q('cr') : cd.value === 'untrust' ? 'Error' : ''
  if (!result) return
  itemsLoading.value = true
  try {
    const query: Record<string, string> = { result }
    if (cd.value === 'items' && q('cp')) query.platform = q('cp')
    const r = await apiFetch<any>('/api/config-audit/items', { query })
    if (!isLatest()) return
    items.value = [...(r.items ?? [])].sort((a, b) => byHost(a, b) || (a.seq ?? 0) - (b.seq ?? 0))
      .map((it, i) => ({ ...it, __no: i + 1, res_t: [RESULT_LABEL[it.bucket] || it.bucket, it.exception].filter(Boolean).join('・'), res_b: it.bucket, host_t: it.hostname || '（無主機名）' }))
  } catch (e: any) { if (isLatest()) itemsErr.value = `讀不到條目明細：${errMsg(e)}` } finally { if (isLatest()) itemsLoading.value = false }
}, { immediate: true })
const itemCols = [{ k: 'host_t', label: '主機', link: (it: any) => u({ ct: 'hosts', ch: String(it.run_id) }) }, { k: 'ip', label: 'IP', mono: true },
  { k: 'platform', label: '平台' }, { k: 'item_id', label: '編號', mono: true }, { k: 'ref_id', label: '基準條號', mono: true }, { k: 'res_t', label: '結果' },
  { k: 'category', label: '類別' }, { k: 'name', label: '條目' }, { k: 'expected', label: '建議值', mono: true }, { k: 'current', label: '目前值', mono: true },
  { k: 'note', label: '備註' }]

// ===== 單機明細 =====
const chId = computed(() => (ct.value === 'hosts' && /^\d+$/.test(q('ch')) ? q('ch') : ''))
const cf = computed(() => (RESULT_LABEL[q('cf')] ? q('cf') : ''))
const detail = ref<any>(null)
const detailErr = ref('')
const beginDetail = useLatest()
watch(chId, async (id) => {
  const isLatest = beginDetail()
  detail.value = null; detailErr.value = ''
  if (!id) return
  try { const d = await apiFetch<any>(`/api/config-audit/${id}`); if (isLatest()) detail.value = d } catch (e: any) { if (isLatest()) detailErr.value = `讀不到明細：${errMsg(e)}` }
}, { immediate: true })
// 項次照條目順序固定，篩選／排序都不重編
const detailItems = computed(() => ((detail.value?.items ?? []) as any[]).map((x, i) => ({ ...x, __no: i + 1, res_t: [RESULT_LABEL[x.result] || x.result, x.exception].filter(Boolean).join('・'), res_b: bucketOf(x.result) }))
  .filter(x => !cf.value || bucketOf(x.result) === cf.value))
const detailCols = computed(() => [{ k: 'item_id', label: '編號', mono: true }, { k: 'ref_id', label: '基準條號', mono: true },
  ...(detail.value?.platform === 'Windows' ? [{ k: 'role', label: '角色' }] : []), { k: 'res_t', label: '結果' }, { k: 'category', label: '類別' },
  { k: 'name', label: '條目' }, { k: 'standard', label: '說明', brief: (r: any) => String(r.standard ?? '').slice(0, 30) },
  { k: 'expected', label: '建議值', mono: true }, { k: 'current', label: '目前值', mono: true }, { k: 'note', label: '備註' }])
// 結果欄照舊依合規／不合規／未查到／不適用上色
const RES_CLS: Record<string, string> = { 'Compliant': 'ok-t', 'Non-Compliant': 'bad-t', 'Error': 'err-t', 'Not-Applicable': 'na-t' }
const LV: Record<string, string> = { fact: '事實', unknown: '無法確認' }

// ===== 匯入（管理員）=====
const busy = ref(false)
const upErr = ref('')
const upOk = ref('')
async function upload(ev: Event) {
  const input = ev.target as HTMLInputElement
  const f = input.files?.[0]
  if (!f) return
  upErr.value = ''; upOk.value = ''; busy.value = true
  try {
    const fd = new FormData(); fd.append('file', f)
    const r = await apiFetch<any>('/api/config-audit/import', { method: 'POST', body: fd })
    upOk.value = `已匯入 ${r.hostname || '(未知主機)'}（${r.platform || '未知平台'}） ${r.total} 條` + (r.match_by === '對不到' ? '　⚠ 這台對不回資產清冊' : '')
    await load()
  } catch (e: any) { upErr.value = `匯入失敗：${errMsg(e)}` } finally { busy.value = false; input.value = '' }
}
// 取得檢核腳本：走 apiFetch 下載（有執行中／成功／失敗三態；401 會統一處理），不寫死檔名（清單由後端掃 scripts/）
const scriptRows = computed(() => scripts.value.map((f, i) => ({ ...f, __no: i + 1, size_t: kb(f.size),
  sv_t: [f.script_version || '腳本裡沒寫版本', f.self_made_label].filter(Boolean).join('・') })))
const dlBusy = ref('')
const dlOk = ref('')
const dlErr = ref('')
async function downloadScript(f: any) {
  dlBusy.value = f.name; dlOk.value = ''; dlErr.value = ''
  try {
    const blob = await apiFetch<Blob>(`/api/scripts/${encodeURIComponent(f.name)}/download`, { responseType: 'blob' })
    const url = URL.createObjectURL(blob as Blob)
    const a = document.createElement('a'); a.href = url; a.download = f.name
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
    dlOk.value = `已下載 ${f.name}（${f.script_version || '腳本裡沒寫版本'}）　傳到主機後執行： ksh ${f.name}`
  } catch (e: any) { dlErr.value = `下載 ${f.name} 失敗：${errMsg(e)}` } finally { dlBusy.value = '' }
}
</script>

<template>
  <div>
    <p class="muted">各平台組態檢核腳本的結果：Linux、AIX、Windows。
      <InfoNote>「未查到」是獨立的一態，不會被算成合規，也不會被算成不合規——指令不存在、檔案讀不到、權限不足都落在這裡。一台如果「未查到」很多條，那份結果就不可信，不是「幾乎都合規」。合規率的分母是「總數 − 不適用 − 未查到」，算式會跟數字一起顯示。平台是讀檔案表頭的「平台:」判斷的，不看檔名。</InfoNote></p>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <div class="chips">
      <NuxtLink v-for="t in CTL" :key="t[0]" class="chip" :class="{ on: ct === t[0] }" :to="u({ ct: t[0] })">{{ t[1] }}</NuxtLink>
    </div>

    <!-- 總覽 -->
    <template v-if="ct === 'overview'">
      <template v-if="cd">
        <div class="bar"><NuxtLink :to="u({ ct: 'overview' })">‹ 返回</NuxtLink><b>{{ drillTitle }}</b>
          <span v-if="cd !== 'items'" class="muted">{{ drillHosts.length }} 台</span>
          <span v-if="cd === 'items' || cd === 'untrust'" class="muted">{{ itemsLoading ? '讀取中…' : itemsErr ? '—' : `${items.length} 條` }}</span></div>
        <V2Table v-if="cd !== 'items'" :rows="drillHosts" prefix="H" row-key="id" unit="台" :cols="hostCols" :export-name="`組態檢核_${drillTitle}`"
                 :empty="loaded ? '沒有符合的主機（0 台）' : '載入中…'" />
        <template v-if="cd === 'items' || cd === 'untrust'">
          <h3 v-if="cd === 'untrust'" class="h3">沒查到的條目</h3>
          <p v-if="itemsErr" class="err">{{ itemsErr }}</p>
          <V2Table v-else :rows="items" prefix="I" row-key="__no" unit="條" :max="200" :cols="itemCols" :export-name="`組態檢核_${drillTitle}_條目`"
                   :empty="itemsLoading ? '讀取中…' : '沒有符合的條目（0 條）'">
            <template #cell-res_t="{ row }"><span :class="RES_CLS[row.res_b]">{{ row.res_t }}</span></template>
          </V2Table>
        </template>
      </template>
      <template v-else-if="summary">
        <div class="cards">
          <NuxtLink class="scard" data-test="ca-card-hosts" :to="sumLink('hosts')"><span class="sv">{{ summary.hosts }}</span><span class="sl">K1 有檢核結果的主機</span></NuxtLink>
          <NuxtLink class="scard" :class="{ warn: summary.untrustworthy }" data-test="ca-card-untrust" :to="sumLink('untrust')"><span class="sv">{{ summary.untrustworthy }}</span>
            <span class="sl">K2 結果不可信（有未查到）<InfoNote>有未查到的項目，合規率不含它們。點進去同時列出是哪幾條沒查到。</InfoNote></span></NuxtLink>
          <NuxtLink class="scard" :class="{ warn: summary.unmatched }" data-test="ca-card-unmatched" :to="sumLink('unmatched')"><span class="sv">{{ summary.unmatched }}</span>
            <span class="sl">K3 對不回資產清冊<InfoNote>清冊少一台，或腳本跑在不該跑的機器。</InfoNote></span></NuxtLink>
          <NuxtLink class="scard" :class="{ warn: summary.no_end_marker }" data-test="ca-card-noend" :to="sumLink('noend')"><span class="sv">{{ summary.no_end_marker }}</span>
            <span class="sl">K4 無完成標記<InfoNote>該平台腳本沒有結尾標記，無法確認是否截斷。</InfoNote></span></NuxtLink>
        </div>
        <V2Table :rows="platRows" prefix="P" row-key="platform" unit="個平台" :cols="platCols" export-name="組態檢核_各平台" />
      </template>
      <p v-else-if="!loadErr" class="muted">載入中…</p>
    </template>

    <!-- 各主機 -->
    <template v-else-if="ct === 'hosts'">
      <template v-if="chId">
        <div class="bar"><NuxtLink :to="u({ ct: 'hosts' })">‹ 返回</NuxtLink>
          <b>{{ detail?.hostname || '' }}<template v-if="cf">・只看「{{ RESULT_LABEL[cf] }}」</template></b>
          <NuxtLink v-if="cf" :to="u({ ct: 'hosts', ch: chId })">看全部</NuxtLink>
          <span v-if="detail" class="muted">{{ detail.coverage_formula }}</span>
          <span v-if="detail?.self_made_label" class="tag warn">{{ detail.self_made_label }}<InfoNote>這份的判定邏輯是本專案自己寫的：官方 61 支子腳本拿不到，依官方標準文字逐條重寫。結果可參考，但尚未與官方腳本逐條比對（例如 0208 密碼長度門檻、0177／0144／0217 的嚴格程度），比對完成前不要當成官方判定結果。</InfoNote></span>
          <span v-if="detail?.baseline_label" class="tag">{{ detail.baseline_label }}</span>
          <span v-if="detail?.untrust_reason" class="err">{{ detail.untrust_reason }}</span>
          <span v-if="detail?.warning" class="err">{{ detail.warning }}</span>
          <span v-if="detail?.completeness === '無完成標記'" class="muted">此平台腳本沒有結尾標記，無法確認檔案是否被截斷</span>
        </div>
        <!-- 「這份報表的檢查本身有沒有在工作」：不影響合規率（合規率是要交出去的數字，提示歸提示） -->
        <div v-if="detail?.integrity?.length" class="integrity">
          <div v-for="(n, i) in detail.integrity" :key="i" class="inote" :class="'lv-' + n.level"><b class="tag">{{ LV[n.level] ?? '提示' }}</b><span>{{ n.text }}</span></div>
        </div>
        <p v-if="detailErr" class="err">{{ detailErr }}</p>
        <V2Table v-else-if="detail" :rows="detailItems" prefix="" row-key="seq" unit="條" :max="300" :cols="detailCols"
                 :export-name="`組態檢核_${detail.hostname || chId}`" empty="沒有符合的條目">
          <template #cell-res_t="{ row }"><span :class="RES_CLS[row.res_b]">{{ row.res_t }}</span></template>
        </V2Table>
        <p v-else class="muted">讀取中…</p>
      </template>
      <V2Table v-else :rows="numbered" prefix="" row-key="id" unit="台" :cols="hostCols" export-name="組態檢核_各主機"
               :empty="loaded ? '還沒有任何檢核結果。到「匯入與腳本」匯入腳本產出的 .txt' : '載入中…'" />
    </template>

    <!-- 匯入與腳本 -->
    <template v-else>
      <h3 class="h3">匯入檢核結果<InfoNote>Linux（fcb_rhelV###.sh）／AIX（fcb_aixV###.ksh）／Windows（fcb_windowsV###.ps1）產出的 .txt 都收；平台自動判讀。</InfoNote></h3>
      <div v-if="isAdmin" class="bar">
        <input type="file" accept=".txt,text/plain" :disabled="busy" aria-label="檢核結果 .txt" @change="upload">
        <span v-if="busy" class="muted">處理中…</span>
        <a v-if="rows.length" class="btn2" :href="`${apiBase}/api/config-audit/export`" download>匯出（DYN 格式）</a>
        <InfoNote>匯出的是原始檔全文（給 DYN 分析，逐字跟腳本產出相同）<template v-if="rows.some((r) => r.self_made_label)">；自製 Linux 腳本的檔案，表頭「腳本版本」一行已註明「自製判定，尚未與官方逐條比對」</template>。</InfoNote>
      </div>
      <p v-else class="muted">匯入與匯出限管理員</p>
      <p v-if="upErr" class="err">{{ upErr }}</p>
      <p v-if="upOk" class="ok">{{ upOk }}</p>

      <h3 class="h3">取得檢核腳本<InfoNote>下載後 scp 到目標主機執行（AIX：ksh &lt;檔名&gt;），產出的 .txt 再從上面匯入。換行已固定 LF。版本取不到就寫「腳本裡沒寫版本」，不拿檔案時間頂替。</InfoNote></h3>
      <p v-if="scriptsErr" class="err">{{ scriptsErr }}</p>
      <V2Table v-else :rows="scriptRows" prefix="S" row-key="name" unit="支" export-name="組態檢核_檢核腳本" empty="scripts/ 底下目前沒有可下載的腳本"
               :cols="[{ k: 'name', label: '腳本', mono: true }, { k: 'platform', label: '平台' }, { k: 'sv_t', label: '腳本版本', mono: true },
                       { k: 'size', label: '大小', mono: true, text: (f: any) => f.size_t }, { k: 'dl', label: '下載' }]">
        <template #cell-dl="{ row }">
          <button v-if="isAdmin" type="button" class="btn2" :disabled="dlBusy === row.name" @click="downloadScript(row)">{{ dlBusy === row.name ? '下載中…' : '下載' }}</button>
          <span v-else class="muted">限管理員</span>
        </template>
      </V2Table>
      <p v-if="dlErr" class="err">{{ dlErr }}</p>
      <p v-if="dlOk" class="ok">{{ dlOk }}</p>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin: 4px 0 10px; }
.scard { display: flex; flex-direction: column; gap: 2px; min-width: 150px; padding: 10px 14px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card); text-decoration: none; color: var(--ink); }
a.scard:hover { border-color: var(--brand); }
.scard .sv { font-family: var(--disp); font-size: 22px; font-weight: 700; }
.scard .sl { font-size: 12px; color: var(--muted); display: flex; gap: 4px; align-items: center; }
.scard.warn .sv { color: var(--warn-text); }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--sub); color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.integrity { display: flex; flex-direction: column; gap: 4px; margin: 0 0 10px; font-size: 13px; }
.inote { display: flex; gap: 8px; align-items: baseline; padding: 4px 10px; border-radius: 8px; background: var(--sub); }
.inote.lv-unknown { background: var(--warn-soft); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; cursor: pointer; }
.btn2:disabled { opacity: .45; cursor: default; }
.ok { color: var(--good); font-weight: 600; }
.ok-t { color: var(--brand-dark); } .bad-t { color: var(--bad); font-weight: 600; } .err-t { color: var(--warn-text); font-weight: 600; } .na-t { color: var(--muted); }
.muted { color: var(--muted); font-size: 13px; display: inline-flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.err { color: var(--bad); }
</style>
