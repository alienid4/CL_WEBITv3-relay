<script setup lang="ts">
// 7.1「照片／機櫃圖對帳」頁籤（V42，2.048.18 起 V2 原生；取代嵌入的舊頁 pages/rack-diagram.vue）。⚠D4。同一批 API，行為不變：
// GET /api/rack-diagram/imports、/imports/{id}/reconcile、/imports/{id}/diff、/imports/{id}/reviews、/api/racks/datacenter（匯出用機房清單）；
// POST /api/rack-diagram/import（匯入並對帳；只出報告，不寫 CIA、不改位置；限管理員，後端 ADMIN_WRITE）；
// POST /api/rack-diagram/rows/{row_id}/review（確認／判讀錯了，只增不改；一般帳號以上，後端 USER_WRITE；走 guardedFetch，428 時問管理密碼）；
// 匯出 export.xlsx、cia-fix.xlsx 限管理員（後端 _ADMIN_EXPORTS）。
// 網址：?tab=photo&rt=<小頁籤>&ri=<匯入 id>；對帳結果篩選 rr=<結果>｜rg=<差異群組>｜rsh+rrk(+rres)=<分頁＋櫃(＋結果)>｜rrow=<列>；rm=<沒拍到的櫃>；rdk=moved|added|removed。
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const { isAdmin, canWrite } = useAuth()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const SITES = ['內湖', '敦南', '板橋']
const RT = ['import', 'result', 'sysonly', 'missing', 'reviews', 'rate', 'diff', 'export'] as const

const imports = ref<any[]>([])
const impErr = ref('')
const rooms = ref<string[]>([])
async function loadImports() {
  try { imports.value = await apiFetch<any[]>('/api/rack-diagram/imports'); impErr.value = '' } catch (e: any) { impErr.value = `匯入紀錄載入失敗：${errMsg(e)}` }
}
const ready = ref(false)
onMounted(async () => {
  await loadImports()
  ready.value = true
  if (isAdmin.value) {        // 機房清單只給「匯出現場格式」用，匯出限管理員
    try { rooms.value = ((await apiFetch<any>('/api/racks/datacenter')).rooms).filter((r: any) => r.racks.length && SITES.some(s => r.room.includes(s))).map((r: any) => r.room) } catch (e: any) { showToast(`機房清單載入失敗：${errMsg(e)}`, 'error') }
  }
})
const curId = computed<number | null>(() => (q('ri') && /^\d+$/.test(q('ri')) ? Number(q('ri')) : imports.value[0]?.id ?? null))
const rt = computed(() => (RT.includes(q('rt') as any) ? q('rt') : (ready.value && !imports.value.length ? 'import' : 'result')))
const u = (extra: Record<string, string | undefined> = {}, keep = true) => ({ path: '/v2/inventory/rack', query: Object.fromEntries(Object.entries({ tab: 'photo',
  ...(keep && q('ri') ? { ri: q('ri') } : {}), ...extra }).filter(([, v]) => v !== undefined && v !== '')) })

// ---------- 對帳
const rc = ref<any>(null)
const diff = ref<any>(null)
const rcErr = ref('')
const loading = ref(false)
const begin = useLatest()
async function loadRc(iid: number | null) {
  const isLatest = begin()
  if (!iid) { rc.value = null; diff.value = null; return }
  loading.value = true
  try {
    const [a, b] = await Promise.all([apiFetch<any>(`/api/rack-diagram/imports/${iid}/reconcile`), apiFetch<any>(`/api/rack-diagram/imports/${iid}/diff`)])
    if (!isLatest()) return
    rc.value = a; diff.value = b; rcErr.value = ''
  } catch (e: any) { if (isLatest()) rcErr.value = `對帳失敗：${errMsg(e)}` } finally { if (isLatest()) loading.value = false }
}
watch(curId, id => loadRc(id), { immediate: true })

const REVIEWABLE = ['同櫃但U位不同', '機櫃不同', '機房不同', '機型不同', '系統沒有機櫃資料', '系統沒填機房', '照片有系統沒有']
const POSITION = ['同櫃但U位不同', '機櫃不同', '機房不同']
const pickLabel = computed(() => {
  if (q('rr')) return q('rr')
  if (q('rg')) return q('rg')
  if (q('rsh') || q('rrk')) return `${q('rsh')} ${q('rrk')}${q('rres') ? ' ' + q('rres') : ''}`
  if (q('rrow')) return `第 ${q('rrow')} 列`
  return ''
})
const rRows = computed(() => (rc.value?.rows ?? []).filter((r: any) => {
  if (q('rr')) return r.result === q('rr')
  if (q('rg')) return (q('rg') === '位置不同' ? POSITION : [q('rg')]).includes(r.result)
  if (q('rsh') || q('rrk')) return r.sheet === q('rsh') && r.rack === q('rrk') && (!q('rres') || r.result === q('rres'))
  if (q('rrow')) return String(r.row_id) === q('rrow')
  return true
}).map((r: any) => ({ ...r, __no: r.no, sys_where: [r.sys_room, r.sys_rack, r.sys_u].filter(Boolean).join(' '),
  src_t: `${r.source || r.sheet}・${r.shot_date || '日期未填'}・把握度 ${r.confidence || '未填'}`, text_t: r.state ? `${r.text}（${r.state}）` : r.text,
  result_t: r.detail ? `${r.result}（${r.detail}）` : r.result, review_t: r.review ? `${r.review.action_label}（${r.review.reviewed_by || '—'} ${r.review.reviewed_at.slice(5, 16)}）${r.review.note ? '：' + r.review.note : ''}` : '' })))
const groupLink = (g: string) => (g === '系統有照片沒有' ? u({ rt: 'sysonly' }) : g === '沒拍到的櫃' ? u({ rt: 'missing' }) : u({ rt: 'result', rg: g }))
const confirmedCount = computed(() => (rc.value?.rows ?? []).filter((r: any) => r.review?.action === 'confirm' && REVIEWABLE.includes(r.result) && r.result !== '照片有系統沒有').length)
const machine = (s: string | null | undefined) => (s ? `/v2/machine/${encodeURIComponent(s)}` : null)
const resCols = [{ k: 'result_t', label: '結果' }, { k: 'sheet', label: '分頁' }, { k: 'rack', label: '現場櫃' }, { k: 'u', label: '現場 U', mono: true },
  { k: 'text_t', label: '現場圖文字' }, { k: 'sys_name', label: '系統主機', link: (r: any) => machine(r.sys_serial) },
  { k: 'sys_where', label: '系統機房／櫃／U', link: (r: any) => (r.sys_serial && r.result !== '一致' ? `/v2/datacenter?room=${encodeURIComponent(r.sys_room || '')}` : null) },
  { k: 'match_by', label: '比對方式' }, { k: 'src_t', label: '來源／日期／把握度' }, { k: 'review_t', label: '確認' }]
const notes = reactive<Record<number, string>>({})
const reviewing = ref<number | null>(null)
async function review(x: any, action: 'confirm' | 'misread') {
  reviewing.value = x.row_id
  try {
    const rv = await guardedFetch<any>(`/api/rack-diagram/rows/${x.row_id}/review`, { method: 'POST', body: { action, note: notes[x.row_id] || null } })
    const orig = (rc.value?.rows ?? []).find((r: any) => r.row_id === x.row_id)
    if (orig) orig.review = rv
    showToast(action === 'confirm' ? '已記下：確認（照片對）' : '已記下：判讀錯了', 'success')
    if (rt.value === 'reviews') await loadReviews()
  } catch (e: any) { showToast(`記錄失敗：${errMsg(e)}`, 'error') } finally { reviewing.value = null }
}

// ---------- 其他小頁籤
const soRows = computed(() => (rc.value?.system_only ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, model_t: upText(x.model) })))
const reviews = ref<any[]>([])
const rvErr = ref('')
async function loadReviews() {
  if (!curId.value) return
  try { reviews.value = await apiFetch<any[]>(`/api/rack-diagram/imports/${curId.value}/reviews`); rvErr.value = '' } catch (e: any) { rvErr.value = `確認紀錄載入失敗：${errMsg(e)}` }
}
watch([rt, curId], ([t]) => { if (t === 'reviews') loadReviews() }, { immediate: true })
const rvRows = computed(() => reviews.value.map((x: any, i: number) => ({ ...x, __no: reviews.value.length - i, at: x.reviewed_at.slice(0, 16), src_t: `${x.source || '—'} ${x.shot_date || ''}`, row_t: `#${x.row_id}` })))
const rateRows = computed(() => (rc.value?.rate ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1,
  rate_t: x.rate === null ? '無法比' : `${x.rate}%`, u_rate_t: x.u_rate === null ? '無法比' : `${x.u_rate}%`,
  bad_t: Object.entries(x.counts ?? {}).filter(([k]) => k !== '一致').map(([k, n]) => `${k} ${n}`).join('、') })))
const rackLink = (x: any, res?: string) => u({ rt: 'result', rsh: x.sheet, rrk: x.rack, rres: res })
// 不同機房可能有同名的櫃：鍵與下鑽一律用「機房|櫃」（審查 P2）
const missRows = computed(() => (rc.value?.missing_racks ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, rk: `${x.room}|${x.rack}` })))
const missOpen = computed(() => (rc.value?.missing_racks ?? []).find((x: any) => `${x.room}|${x.rack}` === q('rm')) ?? null)
const missNames = computed(() => (missOpen.value?.names ?? []).map((n: string, i: number) => ({ name: n, __no: i + 1 })))
const DK = ['moved', 'added', 'removed'] as const
const diffKind = computed(() => (DK.includes(q('rdk') as any) ? q('rdk') : 'moved'))
const diffRows = computed(() => ((diff.value?.[diffKind.value] ?? []) as any[]).map((x, i) => ({ ...x, __no: i + 1, where: x.from ? `${x.from} → ${x.to}` : `${x.rack} ${x.u_start}-${x.u_end}` })))
const impRows = computed(() => imports.value.map((x: any, i: number) => ({ ...x, __no: imports.value.length - i, at: x.imported_at.slice(0, 16), sha: x.sha256.slice(0, 12) })))

// ---------- 匯入（管理員）
const site = ref('內湖')
const file = ref<File | null>(null)
const dft = reactive({ source: '', shot_date: '', confidence: '' })
const uploading = ref(false)
async function upload() {
  if (!file.value) { showToast('先選檔案（.xlsx）', 'warn'); return }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    fd.append('site', site.value)
    if (dft.source) fd.append('default_source', dft.source)
    if (dft.shot_date) fd.append('default_shot_date', dft.shot_date)
    if (dft.confidence) fd.append('default_confidence', dft.confidence)
    const r = await apiFetch<any>('/api/rack-diagram/import', { method: 'POST', body: fd })
    showToast(`匯入完成：${r.sheets} 張分頁、${r.rows} 格設備`, 'success')
    if (r.skipped) showToast(r.skipped, 'warn')
    await loadImports()
    await navigateTo(u({ rt: 'result', ri: String(r.import_id) }, false))
  } catch (e: any) { showToast(`匯入失敗：${errMsg(e)}`, 'error') } finally { uploading.value = false }
}
const RTL = computed(() => [['import', `匯入 ${imports.value.length}`], ['result', `對帳結果${rc.value ? ' ' + rc.value.rows.length : ''}`],
  ['sysonly', `系統有照片沒有${rc.value ? ' ' + rc.value.system_only.length : ''}`], ['missing', `沒拍到的櫃${rc.value ? ' ' + rc.value.missing_racks.length : ''}`],
  ['reviews', '確認紀錄'], ['rate', '一致率（每櫃）'], ['diff', '跟上次比'], ['export', '匯出現場格式']])
</script>

<template>
  <div>
    <div class="chips">
      <NuxtLink v-for="t in RTL" :key="t[0]" class="chip" :class="{ on: rt === t[0] }" :to="u({ rt: t[0] })">{{ t[1] }}</NuxtLink>
      <InfoNote>現場手畫的 Excel 機櫃圖（內湖 3F／4F、敦南 21F、板橋 5F）跟系統比對。原檔不能出公司，所以在公司端匯入：每張分頁找櫃頭、讀每一格設備（合併格跨幾 U 就是幾 U），抓 IP、括號裡的序號、已拔電／已關機／閒置等標記；暫定／預留算「規劃中」不比；理線槽、panel、電源排插等略過。比對：先用 IP，沒有 IP 用序號；內湖 CIA 的「1-n／2-n／3-n」＝現場圖的 A／B／C n。只出報告，不改 CIA、不改位置。</InfoNote>
    </div>
    <p v-if="impErr" class="err">{{ impErr }}</p>
    <p v-if="rcErr" class="err">{{ rcErr }}</p>
    <p v-if="rc" class="cur">目前看：#{{ rc.import.id }} {{ rc.import.site }}｜{{ rc.import.file_name }}｜{{ rc.import.imported_at.slice(0, 16) }}（{{ rc.import.imported_by || '—' }}）
      <span v-if="rc.rates">｜兩邊都放得到 {{ rc.rates.placed }} 台：同櫃率 <b>{{ rc.rates.same_rack_rate ?? '—' }}%</b>（{{ rc.rates.same_rack }}）、U 位一致率 <b>{{ rc.rates.u_rate ?? '—' }}%</b>（{{ rc.rates.u_ok }}／{{ rc.rates.u_both }}）</span>
      <InfoNote>一致率只算「兩邊都放得到」的：照片那格配到系統某一台，而且兩邊都有機櫃。同櫃率＝同一櫃的比例；U 位一致率＝同櫃、兩邊都有 U 位的那幾台裡 U 位重疊的比例。沒有可比對編號、規劃中、未辨識、照片有系統沒有、系統沒填機房等都不算進分母。</InfoNote></p>
    <p v-else-if="loading" class="muted">載入中…</p>

    <template v-if="rt === 'import'">
      <div v-if="isAdmin" class="bar">
        <label>機房 <select v-model="site" class="sel"><option v-for="s in SITES" :key="s">{{ s }}</option></select></label>
        <input type="file" accept=".xlsx,.csv" aria-label="機櫃圖檔" @change="file = ($event.target as HTMLInputElement).files?.[0] ?? null">
        <input v-model="dft.source" class="inp" placeholder="預設來源（可空）" aria-label="預設來源">
        <label>預設拍攝日期 <input v-model="dft.shot_date" type="date" class="inp"></label>
        <label>預設把握度 <select v-model="dft.confidence" class="sel"><option value="">（不填）</option><option>高</option><option>中</option><option>低</option></select></label>
        <button type="button" class="btn" :disabled="uploading" @click="upload">{{ uploading ? '匯入中…' : '匯入並對帳' }}</button>
        <InfoNote>兩種檔都收：①現場格式（櫃並排、U 在兩側）；②清單格式（照片判讀結果，一列一台），表頭含「機櫃」「內容」，其他可選：機房、樓層、U起、U迄、IP、序號、來源（照片檔名）、拍攝日期、把握度（高／中／低／未辨識）。看不清楚的寫「未辨識」，系統不會硬配。照片本身不要上傳（敏感資料），只填檔名。配對順序：資產序號 → 主機名稱貼紙 → IP → 機型＋位置。</InfoNote>
      </div>
      <p v-else class="muted" data-test="admin-only">匯入限管理員</p>
      <V2Table :rows="impRows" prefix="I" row-key="id" unit="次" export-name="機櫃圖對帳_匯入紀錄" empty="還沒匯入過"
               :cols="[{ k: 'site', label: '機房' }, { k: 'file_name', label: '檔名', link: (r: any) => u({ rt: 'result', ri: String(r.id) }, false) }, { k: 'sheets', label: '分頁', mono: true },
                       { k: 'rows', label: '設備格', mono: true }, { k: 'at', label: '匯入時間', mono: true }, { k: 'imported_by', label: '誰' }, { k: 'sha', label: 'SHA256', mono: true }]" />
    </template>

    <!-- 確認紀錄只要有匯入 id、匯出現場格式用系統資料：沒有對帳結果也要進得去（審查 P1） -->
    <template v-else-if="!rc && rt !== 'export' && rt !== 'reviews'">
      <p v-if="ready && !loading && !rcErr" class="muted">先到「匯入」上傳一份現場圖。</p>
    </template>

    <template v-else-if="rt === 'result'">
      <div class="chips">
        <b>差異</b>
        <NuxtLink v-for="(n, g) in rc.groups" :key="g" class="chip" :class="{ warnchip: n, on: q('rg') === g }" :to="groupLink(String(g))">{{ g }} {{ n }}</NuxtLink>
        <a v-if="isAdmin" class="lnk" :href="`${apiBase}/api/rack-diagram/imports/${rc.import.id}/cia-fix.xlsx`">匯出 CIA 待更正（已確認 {{ confirmedCount }} 筆）</a>
        <NuxtLink class="lnk" to="/v2/datacenter">到 5.1 機櫃正面「依盤點結果更正」</NuxtLink>
      </div>
      <div class="chips">
        <NuxtLink class="chip" :class="{ on: !pickLabel }" :to="u({ rt: 'result' })">全部 {{ rc.rows.length }}</NuxtLink>
        <template v-for="(n, k) in rc.counts" :key="k">
          <NuxtLink v-if="n" class="chip" :class="{ on: q('rr') === k, warnchip: !['一致', '規劃中'].includes(String(k)) }" :to="u({ rt: 'result', rr: String(k) })">{{ k }} {{ n }}</NuxtLink>
          <span v-else class="chip off">{{ k }} 0</span>
        </template>
      </div>
      <p v-if="pickLabel" class="muted">只看：{{ pickLabel }}（{{ rRows.length }}）</p>
      <V2Table :rows="rRows" prefix="R" row-key="row_id" unit="格" :max="200" export-name="機櫃圖對帳_對帳結果" empty="沒有" :cols="resCols">
        <template #cell-review_t="{ row }">
          <template v-if="REVIEWABLE.includes(row.result)">
            <div v-if="row.review" :class="row.review.action === 'confirm' ? 'ok' : 'muted'">{{ row.review_t }}</div>
            <template v-if="canWrite">
              <input v-model="notes[row.row_id]" class="inp sm" placeholder="備註（可空）" aria-label="備註">
              <button type="button" class="lb" :disabled="reviewing === row.row_id" @click="review(row, 'confirm')">確認（照片對）</button>
              <button type="button" class="lb" :disabled="reviewing === row.row_id" @click="review(row, 'misread')">判讀錯了</button>
            </template>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </V2Table>
    </template>

    <template v-else-if="rt === 'sysonly'">
      <p class="muted">照片／現場圖有拍到這一櫃，但這幾台系統有登記、照片上找不到<InfoNote>不一定是錯：可能裝在背面、被擋住，或已下架——請現場確認。</InfoNote></p>
      <V2Table :rows="soRows" prefix="S" row-key="key" unit="台" export-name="機櫃圖對帳_系統有照片沒有" empty="沒有"
               :cols="[{ k: 'rack', label: '系統櫃' }, { k: 'u', label: '系統 U', mono: true }, { k: 'name', label: '主機', link: (r: any) => machine(r.serial) }, { k: 'model_t', label: '機型' }, { k: 'ip', label: 'IP', mono: true }]" />
    </template>

    <template v-else-if="rt === 'missing'">
      <template v-if="missOpen">
        <div class="bar"><NuxtLink :to="u({ rt: 'missing' })">‹ 返回</NuxtLink><b>{{ missOpen.room }} {{ missOpen.rack }}</b><span class="muted">{{ missOpen.count }} 台</span></div>
        <V2Table :rows="missNames" prefix="N" row-key="__no" unit="台" :export-name="`機櫃圖對帳_沒拍到_${missOpen.rack}`" :cols="[{ k: 'name', label: '設備' }]" />
      </template>
      <template v-else>
        <p class="muted">系統（CIA）在 {{ rc.import.site }} 有登記、但這份照片／現場圖沒涵蓋到的櫃<InfoNote>沒拍到不等於空櫃：可能只是沒拍，也可能是 CIA 櫃名寫錯。</InfoNote></p>
        <V2Table :rows="missRows" prefix="X" row-key="rk" unit="櫃" export-name="機櫃圖對帳_沒拍到的櫃" empty="沒有"
                 :cols="[{ k: 'room', label: '機房' }, { k: 'rack', label: '系統櫃名' }, { k: 'count', label: '設備', mono: true, link: (r: any) => u({ rt: 'missing', rm: r.rk }) }]" />
      </template>
    </template>

    <template v-else-if="rt === 'reviews'">
      <p class="muted">每一次按「確認（照片對）」／「判讀錯了」都留一筆，不改不刪<InfoNote>稽核用；同一筆以最新的為準。</InfoNote></p>
      <p v-if="rvErr" class="err">{{ rvErr }}</p>
      <V2Table :rows="rvRows" prefix="C" row-key="id" unit="筆" export-name="機櫃圖對帳_確認紀錄" empty="還沒有人確認過"
               :cols="[{ k: 'at', label: '時間', mono: true }, { k: 'reviewed_by', label: '誰' }, { k: 'action_label', label: '判定' }, { k: 'row_t', label: '哪一列', mono: true, link: (r: any) => u({ rt: 'result', rrow: String(r.row_id) }) },
                       { k: 'src_t', label: '來源照片' }, { k: 'note', label: '備註' }]" />
    </template>

    <template v-else-if="rt === 'rate'">
      <V2Table :rows="rateRows" prefix="G" :row-key="'__no'" unit="櫃" export-name="機櫃圖對帳_一致率" empty="沒有"
               :cols="[{ k: 'sheet', label: '分頁' }, { k: 'rack', label: '櫃' }, { k: 'total', label: '設備格', mono: true, link: (r: any) => rackLink(r) }, { k: 'comparable', label: '兩邊都放得到', mono: true },
                       { k: 'ok', label: '同櫃', mono: true, link: (r: any) => rackLink(r) }, { k: 'rate_t', label: '同櫃率', mono: true }, { k: 'u_rate_t', label: 'U 位一致率', mono: true }, { k: 'bad_t', label: '不一致的' }]">
        <template #cell-bad_t="{ row }">
          <template v-for="(n, k) in row.counts" :key="k"><NuxtLink v-if="k !== '一致'" class="lnk" :to="rackLink(row, String(k))">{{ k }} {{ n }}</NuxtLink>　</template>
        </template>
      </V2Table>
    </template>

    <template v-else-if="rt === 'diff'">
      <p v-if="!diff?.previous" class="muted">這是這個機房第一次匯入，沒有上一次可以比。</p>
      <template v-else>
        <p class="muted">跟上一次（#{{ diff.previous.id }} {{ diff.previous.file_name }}，{{ diff.previous.imported_at.slice(0, 16) }}）比：</p>
        <div class="chips">
          <NuxtLink class="chip" :class="{ on: diffKind === 'moved' }" :to="u({ rt: 'diff', rdk: 'moved' })">搬了 {{ diff.moved.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: diffKind === 'added' }" :to="u({ rt: 'diff', rdk: 'added' })">多了 {{ diff.added.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: diffKind === 'removed' }" :to="u({ rt: 'diff', rdk: 'removed' })">少了 {{ diff.removed.length }}</NuxtLink>
        </div>
        <V2Table :rows="diffRows" prefix="D" row-key="__no" unit="格" :export-name="`機櫃圖對帳_跟上次比_${diffKind}`" empty="沒有"
                 :cols="[{ k: 'where', label: '位置', mono: true }, { k: 'text', label: '現場圖文字' }]" />
      </template>
    </template>

    <template v-else-if="rt === 'export'">
      <p class="muted">從系統資料產生「跟現場同格式」的 Excel<InfoNote>一個機房＋排一張分頁、櫃並排、依類別上色（網路綠、虛擬環境橘、DB 藍、儲存淺黃、測試紫、關機未下架／待確認紅）。需要現場比對的格子紅框＋⚠＋註解（依這個機房最後一次匯入的現場圖）；第一張「需比對清單」先看。</InfoNote></p>
      <div v-if="isAdmin" class="chips">
        <a class="btn2" :href="`${apiBase}/api/rack-diagram/export.xlsx`">全部機房</a>
        <a v-for="r in rooms" :key="r" class="btn2" :href="`${apiBase}/api/rack-diagram/export.xlsx?room=${encodeURIComponent(r)}`">{{ r }}</a>
      </div>
      <p v-else class="muted">匯出限管理員</p>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.warnchip { border-color: var(--bad); color: var(--bad); }
.chip.off { opacity: .45; }
.cur { font-size: 13px; color: var(--ink-soft); margin: 0 0 8px; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.sel, .inp { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.inp.sm { font-size: 12px; width: 130px; margin: 2px 4px 2px 0; }
.btn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); text-decoration: none; }
.btn:disabled, .lb:disabled { opacity: .45; cursor: default; }
.lb { font-family: inherit; font-size: 12px; padding: 2px 8px; margin: 2px 4px 2px 0; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.lnk { color: var(--link); text-decoration: none; font-size: 12px; }
.ok { color: var(--good); font-weight: 600; }
.muted { color: var(--muted); font-size: 13px; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.err { color: var(--bad); }
</style>
