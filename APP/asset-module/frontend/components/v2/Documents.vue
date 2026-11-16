<script setup lang="ts">
// ⚙ 單據檔案室（V28，2.047.2 V2 原生；取代舊 pages/documents.vue）。同一批 API：
// GET /api/documents?q=（全文搜尋走後端）、POST /api/documents/import（每 25 份一批、逐批顯示進度、一批失敗不中止）、
// POST /api/documents/{id}/review（確認規格值／標記審核完成）、POST /api/documents/{id}/bind（改綁資產）、GET /api/documents/{id}/download。
// 定位是檔案室的索引卡、不是資料來源：抽出來的值不會自動寫進資產欄位，規格值要人工確認過才算數。
// 綁定方式（全部／待確認／自動綁定／人工綁定）是篩選＝下鑽；點「詳細」在表格下方看勾選欄位、規格值、區塊、檢核統計並審核／改綁。
interface Doc {
  id: number; doc_type: string; file_name: string; file_ext: string; request_no: string | null; ref_request_no: string | null; form_date: string | null
  applicant_unit: string | null; applicant: string | null; system_name: string | null; hostname: string | null; ip: string | null; asset_serial: string | null
  asset_hostname: string | null; bind_confidence: string; warnings: string[]; imported_at: string | null; snippet: string | null; is_current: number; has_secrets: number
  is_decommission: number; review_status: string; reviewed_by: string | null; reviewed_at: string | null
  checkboxes: Record<string, { label: string; selected: string[]; confidence: string }>; values: Record<string, { label: string; value: string; source: string }>
  sections: Record<string, { label: string; status: string; applicable: boolean }>; checklist_summary: { total: number; done: number; na: number; blank: number } | null
}
const TYPE_TEXT: Record<string, string> = { provision_form: '異動需求單', golive_form: '上線前檢查表' }
const CONF_TEXT: Record<string, string> = { auto: '自動綁定', review: '待確認', manual: '人工綁定' }
const UPLOAD_CHUNK = 25
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer, isAdmin } = useAuth()      // 2.047.4（GPT 審查）：批次匯入後端只准管理員（ADMIN_WRITE），前端也只給管理員；審核／改綁／下載一般帳號可用
const config = useRuntimeConfig()
const route = useRoute()
const router = useRouter()
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const docs = ref<Doc[]>([])
const summary = ref<any>(null)
const loading = ref(true)
const err = ref('')
const kw = ref(qs('q'))
const conf = computed(() => (qs('dc') in CONF_TEXT ? qs('dc') : ''))
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
const begin = useLatest()      // 打字搜尋時只套用最新那一次
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const r = await apiFetch<{ documents: Doc[]; summary: any }>('/api/documents', { query: kw.value.trim() ? { q: kw.value.trim() } : {} })
    if (!isLatest()) return
    docs.value = r.documents
    summary.value = r.summary
    err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `單據載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { if (isLatest()) loading.value = false }
}
let timer: any = null
watch(kw, (v) => { clearTimeout(timer); timer = setTimeout(() => { setQ({ q: v.trim() || undefined }); load() }, 300) })      // 打字停 300ms 才查
onMounted(load)
onBeforeUnmount(() => clearTimeout(timer))
const counts = computed(() => Object.fromEntries(Object.keys(CONF_TEXT).map(k => [k, docs.value.filter(d => d.bind_confidence === k).length])))
const rows = computed(() => docs.value.filter(d => !conf.value || d.bind_confidence === conf.value).map((d, i) => ({
  ...d, __no: i + 1, type: TYPE_TEXT[d.doc_type] ?? d.doc_type, conf: CONF_TEXT[d.bind_confidence] ?? d.bind_confidence,
  flags: [!d.is_current ? '歷史' : '', d.has_secrets ? '含帳密' : '', d.is_decommission ? '下線單' : '', d.review_status !== 'confirmed' ? '待審' : ''].filter(Boolean).join('、'),
  who: [d.applicant, d.applicant_unit].filter(Boolean).join('・'), req: d.request_no ? d.request_no + (d.ref_request_no ? `（對應 ${d.ref_request_no}）` : '') : '',
  warn: d.warnings.join('；'),
})))
// ---- 匯入（分批、逐批更新進度；一批失敗記下檔名、繼續下一批）
const uploading = ref(false)
const progress = ref<{ done: number; total: number; imported: number; failed: number } | null>(null)
const lastResult = ref<any>(null)
const fileInput = ref<HTMLInputElement | null>(null)
async function onFiles(e: Event) {
  const list = (e.target as HTMLInputElement).files
  if (!list?.length) return
  const files = Array.from(list)
  uploading.value = true
  lastResult.value = null
  const acc = { imported: 0, failed: 0, auto_bound: 0, need_review: 0, errors: [] as any[] }
  progress.value = { done: 0, total: files.length, imported: 0, failed: 0 }
  try {
    for (let i = 0; i < files.length; i += UPLOAD_CHUNK) {
      const batch = files.slice(i, i + UPLOAD_CHUNK)
      const fd = new FormData()
      for (const f of batch) fd.append('files', f)
      try {
        const r: any = await guardedFetch('/api/documents/import', { method: 'POST', body: fd })
        acc.imported += r.imported; acc.failed += r.failed; acc.auto_bound += r.auto_bound; acc.need_review += r.need_review
        acc.errors.push(...(r.errors ?? []))
      } catch (ex: any) {
        const d = ex?.data?.detail
        const msg = (typeof d === 'string' ? d : d?.message) ?? '這批送不出去（連線中斷或逾時）'
        acc.failed += batch.length
        acc.errors.push(...batch.map(f => ({ file_name: f.name, error: msg })))
      }
      progress.value = { done: Math.min(i + batch.length, files.length), total: files.length, imported: acc.imported, failed: acc.failed }
    }
    lastResult.value = acc
    showToast(`匯入 ${acc.imported} 份（自動綁定 ${acc.auto_bound}、待確認 ${acc.need_review}）` + (acc.failed ? `，${acc.failed} 份失敗（下面列出檔名，請重傳那幾份）` : ''), acc.failed ? 'warn' : 'success')
    await load()
  } finally {
    uploading.value = false
    progress.value = null
    if (fileInput.value) fileInput.value.value = ''
  }
}
// ---- 詳細：審核、改綁
const openId = computed(() => Number(qs('doc')) || 0)
const open = computed(() => docs.value.find(d => d.id === openId.value) ?? null)
const draft = reactive<Record<string, string>>({})
watch(open, (d) => { Object.keys(draft).forEach(k => delete draft[k]); for (const [k, v] of Object.entries(d?.values ?? {})) draft[k] = v.value; bindSerial.value = d?.asset_serial ?? '' })
const busy = ref(false)
const bindSerial = ref('')
async function saveReview(d: Doc) {
  busy.value = true
  try { await guardedFetch(`/api/documents/${d.id}/review`, { method: 'POST', body: { values: { ...draft } } }); showToast('已確認這份單的規格值', 'success'); await load() } catch (e: any) { showToast(`審核失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
async function saveBind(d: Doc) {
  busy.value = true
  try { await guardedFetch(`/api/documents/${d.id}/bind`, { method: 'POST', body: { asset_serial: bindSerial.value.trim() || null } }); showToast('已更新綁定', 'success'); await load() } catch (e: any) { const x = e?.data?.detail; showToast((typeof x === 'string' ? x : x?.message) ?? '綁定失敗', 'error') } finally { busy.value = false }
}
const downloadUrl = (d: Doc) => `${config.public.apiBase}/api/documents/${d.id}/download`
const COLS = [
  { k: 'form_date', label: '填表日期', mono: true }, { k: 'type', label: '類型' }, { k: 'req', label: '單據編號', mono: true },
  { k: 'hostname', label: '主機名稱', mono: true }, { k: 'ip', label: 'IP', mono: true }, { k: 'who', label: '申請人' },
  { k: 'asset_serial', label: '綁定資產', mono: true, link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) },
  { k: 'conf', label: '綁定方式', tone: (r: any) => (r.bind_confidence === 'review' ? 'test' : '') }, { k: 'flags', label: '標記' }, { k: 'warn', label: '提醒' },
  { k: 'snippet', label: '命中片段', brief: (r: any) => (r.snippet && r.snippet.length > 40 ? r.snippet.slice(0, 40) + '…' : '') },
  { k: 'act', label: '詳細', text: () => '' },
]
</script>

<template>
  <div>
    <div class="bar">
      <label v-if="isAdmin" class="upload">
        <input ref="fileInput" type="file" accept=".doc,.docx" multiple :disabled="uploading" @change="onFiles">
        <span class="btn">{{ uploading ? '匯入中…' : '批次匯入 Word 單據' }}</span>
      </label>
      <span v-if="!isAdmin" class="muted" data-testid="doc-import-admin-only">批次匯入 Word 單據限管理員<InfoNote>上傳新單據請洽管理員；查詢、看詳細<template v-if="!isViewer">、審核、改綁、下載</template>不受影響。</InfoNote></span>
      <span v-if="progress" class="muted">已處理 {{ progress.done }}／{{ progress.total }} 份（成功 {{ progress.imported }}<span v-if="progress.failed">、失敗 {{ progress.failed }}</span>）— 每 {{ UPLOAD_CHUNK }} 份送一批，已送出的那幾批會保留</span>
      <template v-if="summary">
        <span>共 <b>{{ summary.total }}</b> 份</span>
        <span>異動需求單 <b>{{ summary.by_type?.provision_form ?? 0 }}</b></span>
        <span>上線檢查表 <b>{{ summary.by_type?.golive_form ?? 0 }}</b></span>
      </template>
      <InfoNote>既有的「主機及網路異動需求單」與「伺服器上線前檢查表」Word 檔（.doc／.docx）。抽單據編號、日期、主機名、IP、勾選欄位、申請規格並保存原檔；檔名、內文、主機名三方一致才自動綁定資產。抽出來的值不會自動寫進資產欄位——規格值要人工確認過才算數（點「詳細」）。同一個 IP 有多張單時只有最新那張算現行（IP 會回收再分配）。含帳密的單，進資料庫的全文已遮罩；原始檔仍含帳密，下載會留稽核紀錄。歷史檢查表不會變成系統裡的上線檢查表。</InfoNote>
    </div>
    <div v-if="lastResult" class="note">匯入 {{ lastResult.imported }} 份：自動綁定 {{ lastResult.auto_bound }}、待確認 {{ lastResult.need_review }}<span v-if="lastResult.failed">、失敗 {{ lastResult.failed }}</span>
      <div v-for="(e, i) in lastResult.errors" :key="i" class="warn">{{ e.file_name }}：{{ e.error }}</div></div>
    <div class="bar">
      <input v-model="kw" class="in" type="search" placeholder="搜單號、主機名、IP、申請人…也搜 Word 內文（例：F5、DMZ、記憶體）" aria-label="搜尋單據">
      <button type="button" class="chip" :class="{ on: !conf }" @click="setQ({ dc: undefined })">全部 {{ docs.length }}</button>
      <button v-for="(t, k) in CONF_TEXT" :key="k" type="button" class="chip" :class="{ on: conf === k }" @click="setQ({ dc: k })">{{ t }} {{ counts[k] }}</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-else :rows="rows" prefix="W" row-key="id" unit="份" :cols="COLS" export-name="單據檔案室"
             :empty="loading ? '載入中…' : (docs.length ? '這個條件沒有單據' : '還沒有單據。按「批次匯入 Word 單據」，可以一次選很多份')">
      <template #cell-act="{ row }">
        <button type="button" class="lb" @click="setQ({ doc: openId === row.id ? undefined : String(row.id) })">{{ openId === row.id ? '收合' : '詳細' }}</button>
        <a v-if="!isViewer" :href="downloadUrl(row)" target="_blank" rel="noopener" class="lb">{{ row.file_ext }} ↓</a>
      </template>
    </V2Table>
    <div v-if="open" class="box" data-testid="doc-detail">
      <h3 class="h3">{{ open.file_name }}<button type="button" class="lb" @click="setQ({ doc: undefined })">關閉</button></h3>
      <div class="panels">
        <div v-if="Object.keys(open.checkboxes ?? {}).length" class="panel"><b>單據上勾選的</b>
          <div v-for="v in Object.values(open.checkboxes)" :key="v.label" class="kv"><span>{{ v.label }}</span><b>{{ v.selected.join('、') || '（沒選）' }}</b></div></div>
        <div class="panel"><b>{{ Object.keys(open.values ?? {}).length ? '申請規格' : '審核' }}</b>
          <span class="muted">{{ open.review_status === 'confirmed' ? `已由 ${open.reviewed_by} 於 ${open.reviewed_at} 確認` : '尚未有人確認過，請核對後按確認' }}</span>
          <div v-for="(v, k) in open.values" :key="k" class="kv"><span>{{ v.label }}</span><input v-model="draft[k]" class="vin" :disabled="isViewer || open.review_status === 'confirmed'" :aria-label="v.label"></div>
          <button v-if="open.review_status !== 'confirmed' && !isViewer" type="button" class="btn" :disabled="busy" @click="saveReview(open)">{{ Object.keys(open.values ?? {}).length ? '確認這些值' : '標記審核完成' }}</button></div>
        <div v-if="Object.keys(open.sections ?? {}).length" class="panel"><b>第二頁區塊</b>
          <div v-for="v in Object.values(open.sections)" :key="v.label" class="kv"><span>{{ v.label }}</span><b>{{ v.status }}</b></div></div>
        <div v-if="open.checklist_summary" class="panel"><b>上線檢核項目</b>
          <div class="kv"><span>共</span><b>{{ open.checklist_summary.total }} 列</b></div>
          <div class="kv"><span>完成</span><b>{{ open.checklist_summary.done }}</b></div>
          <div class="kv"><span>不需</span><b>{{ open.checklist_summary.na }}</b></div>
          <div class="kv"><span>未填</span><b>{{ open.checklist_summary.blank }}</b></div></div>
        <div class="panel"><b>綁定資產</b>
          <span class="muted">{{ CONF_TEXT[open.bind_confidence] }}</span>
          <template v-if="!isViewer">
            <input v-model="bindSerial" class="vin" placeholder="資產序號（留空＝解除綁定）" aria-label="資產序號">
            <button type="button" class="btn2" :disabled="busy" @click="saveBind(open)">儲存綁定</button>
          </template></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.upload input { display: none; }
.btn { display: inline-block; font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--type-on); cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.in { font-family: inherit; font-size: 13px; padding: 5px 9px; min-width: 360px; border: 1px solid var(--border-strong); border-radius: 8px; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; margin-right: 6px; }
.box { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.h3 { margin: 0 0 8px; font-size: 15px; display: flex; gap: 8px; align-items: center; }
.panels { display: flex; gap: 12px; flex-wrap: wrap; }
.panel { flex: 1 1 220px; display: flex; flex-direction: column; gap: 4px; padding: 8px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; }
.kv { display: flex; gap: 8px; justify-content: space-between; }
.vin { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.note { font-size: 13px; margin: 0 0 8px; }
.warn { color: var(--warn-text); font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
