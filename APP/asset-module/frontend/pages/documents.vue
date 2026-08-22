<script setup lang="ts">
// 單據檔案室：既有 Word 表單（主機及網路異動需求單／伺服器上線前檢查表）的歸檔與索引。
//
// 這頁的定位是**檔案室的索引卡，不是資料來源**——抽出來的東西不會自動寫進資產欄位。
// 勾選欄位與規格值（CPU/記憶體/硬碟）都抽，但規格值一律要人工確認過才算數：
// 自由填寫欄抓歪不會報錯，人看過才會報錯。
//
// 綁定資產的信心度三級，畫面要分得很清楚，因為「系統自己綁的」跟「人確認過的」
// 在稽核上的份量不一樣：
//   auto   三方 IP 一致且對得到資產 → 自動綁
//   review 有矛盾或對不到 → 排隊等人看
//   manual 人工指定
interface Doc {
  id: number
  doc_type: string
  file_name: string
  file_ext: string
  request_no: string | null
  ref_request_no: string | null
  form_date: string | null
  applicant_unit: string | null
  applicant: string | null
  system_name: string | null
  hostname: string | null
  ip: string | null
  asset_serial: string | null
  asset_hostname: string | null
  bind_confidence: string
  warnings: string[]
  imported_at: string | null
  snippet: string | null
  is_current: number
  has_secrets: number
  is_decommission: number
  review_status: string
  reviewed_by: string | null
  reviewed_at: string | null
  checkboxes: Record<string, { label: string; selected: string[]; confidence: string }>
  values: Record<string, { label: string; value: string; source: string }>
  sections: Record<string, { label: string; status: string; applicable: boolean }>
  checklist_summary: { total: number; done: number; na: number; blank: number } | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const config = useRuntimeConfig()

const docs = ref<Doc[]>([])
const summary = ref<any>(null)
const loading = ref(true)
const uploading = ref(false)
const lastResult = ref<any>(null)
const tab = ref<'' | 'review' | 'auto' | 'manual'>('')
const fileInput = ref<HTMLInputElement | null>(null)

const filtered = computed(() =>
  tab.value ? docs.value.filter((d) => d.bind_confidence === tab.value) : docs.value,
)
const { sortKey, sortDir, toggle, sorted } = useSort(filtered, 'form_date')

// 全文搜尋走後端（內文存在 DB 裡，不是前端有的資料）。debounce 300ms，
// 跟全域搜尋同一個節奏——邊打邊送會對後端打出一堆沒人要看的查詢。
const route = useRoute()
const q = ref(String(route.query.q ?? ''))
let timer: any = null
watch(q, () => {
  clearTimeout(timer)
  timer = setTimeout(load, 300)
})

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ documents: Doc[]; summary: any }>('/api/documents', {
      query: q.value.trim() ? { q: q.value.trim() } : {},
    })
    docs.value = r.documents
    summary.value = r.summary
  } finally {
    loading.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => clearTimeout(timer))

async function onFiles(e: Event) {
  const list = (e.target as HTMLInputElement).files
  if (!list?.length) return
  uploading.value = true
  lastResult.value = null
  try {
    const fd = new FormData()
    for (const f of Array.from(list)) fd.append('files', f)
    const r = await apiFetch<any>('/api/documents/import', { method: 'POST', body: fd })
    lastResult.value = r
    showToast(
      `匯入 ${r.imported} 份（自動綁定 ${r.auto_bound}、待確認 ${r.need_review}）` +
      (r.failed ? `，${r.failed} 份失敗` : ''),
      r.failed ? 'warn' : 'success',
    )
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '匯入失敗', 'error')
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

// 展開列：勾選欄位、規格值（可改）、區塊狀態、檢核統計。
// 規格值一定要能改——自由填寫欄抓歪不會報錯，人工審核是唯一的防線。
const openId = ref(0)
const draft = reactive<Record<string, string>>({})
const savingReview = ref(false)
function toggleOpen(d: Doc) {
  openId.value = openId.value === d.id ? 0 : d.id
  Object.keys(draft).forEach((k) => delete draft[k])
  for (const [k, v] of Object.entries(d.values ?? {})) draft[k] = v.value
}
async function saveReview(d: Doc) {
  savingReview.value = true
  try {
    await apiFetch(`/api/documents/${d.id}/review`, { method: 'POST', body: { values: { ...draft } } })
    showToast('已確認這份單的規格值', 'success')
    await load()
  } catch {
    showToast('審核失敗', 'error')
  } finally {
    savingReview.value = false
  }
}

const bindingId = ref(0)
const bindSerial = ref('')
function startBind(d: Doc) {
  bindingId.value = d.id
  bindSerial.value = d.asset_serial ?? ''
}
async function saveBind(d: Doc) {
  try {
    await apiFetch(`/api/documents/${d.id}/bind`, {
      method: 'POST', body: { asset_serial: bindSerial.value.trim() || null },
    })
    showToast('已更新綁定', 'success')
    bindingId.value = 0
    await load()
  } catch (err: any) {
    const e = err?.data?.detail
    showToast((typeof e === 'string' ? e : e?.message) ?? '綁定失敗', 'error')
  }
}

const TYPE_TEXT: Record<string, string> = {
  provision_form: '異動需求單',
  golive_form: '上線前檢查表',
}
const CONF_TEXT: Record<string, string> = {
  auto: '自動綁定', review: '待確認', manual: '人工綁定',
}
function downloadUrl(d: Doc) {
  return `${config.public.apiBase}/api/documents/${d.id}/download`
}
</script>

<template>
  <div>
    <div class="section-divider">資料治理</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>單據檔案室</b></div>

    <div class="card">
      <p class="rv-hint">
        既有的「主機及網路異動需求單」與「伺服器上線前檢查表」Word 檔（<b>.doc／.docx 都吃</b>）。
        抽<b>單據編號、日期、主機名、IP、勾選欄位、申請規格</b>並保存原檔。
        主機命名把 IP 編在裡面（<code>SECSVR195-059 ↔ 10.99.195.59</code>），
        所以檔名、內文、主機名三個來源可以互相對帳，<b>三方一致才自動綁定資產</b>。
        <b>抽出來的值不會自動寫進資產欄位</b>——規格值要人工確認過才算數（點任一列展開）。
        同一個 IP 有多張單時只有<b>最新那張算現行</b>（IP 會回收再分配，舊單描述的是別台機器）。
        含帳密的單，進資料庫的全文已遮罩；原始檔仍含帳密，<b>下載會留稽核紀錄</b>。
      </p>
      <div class="bar-row">
        <label class="upload">
          <input
            ref="fileInput" type="file" accept=".doc,.docx" multiple
            :disabled="uploading" @change="onFiles"
          />
          <span class="btn">{{ uploading ? '匯入中…' : '批次匯入 Word 單據' }}</span>
        </label>
        <div v-if="summary" class="stats">
          <span>共 <b>{{ summary.total }}</b> 份</span>
          <span>異動需求單 <b>{{ summary.by_type.provision_form ?? 0 }}</b></span>
          <span>上線檢查表 <b>{{ summary.by_type.golive_form ?? 0 }}</b></span>
          <span>待確認 <b :class="{ warn: summary.need_review }">{{ summary.need_review }}</b></span>
        </div>
      </div>

      <div v-if="lastResult" class="import-result">
        匯入 <b>{{ lastResult.imported }}</b> 份：自動綁定 {{ lastResult.auto_bound }}、
        待確認 {{ lastResult.need_review }}<span v-if="lastResult.failed">、失敗 {{ lastResult.failed }}</span>
        <div v-for="(e, i) in lastResult.errors" :key="i" class="warn-row">
          {{ e.file_name }}：{{ e.error }}
        </div>
      </div>

      <p class="rv-hint" style="margin:12px 0 0">
        <b>歷史檢查表不會變成系統裡的上線檢查表</b>。那些勾選是當年的狀態，
        轉成基線會立刻產生一堆過期的假 drift；基線只從今天以後在系統裡跑完的檢查表產生。
      </p>
    </div>

    <div class="card">
      <!-- 全文搜尋：這頁存在的主要理由。使用者原話「以前要找相關資料，我要翻所有的 Word 檔」 -->
      <div class="search-row">
        <input
          v-model="q" class="search" type="search"
          placeholder="搜單號、主機名、IP、申請人…也搜 Word 內文（例：F5、DMZ、記憶體）"
        />
        <span v-if="q.trim()" class="hit">
          找到 <b>{{ docs.length }}</b> 份
        </span>
      </div>

      <div class="tabs">
        <button :class="{ on: tab === '' }" @click="tab = ''">全部</button>
        <button :class="{ on: tab === 'review' }" @click="tab = 'review'">待確認</button>
        <button :class="{ on: tab === 'auto' }" @click="tab = 'auto'">自動綁定</button>
        <button :class="{ on: tab === 'manual' }" @click="tab = 'manual'">人工綁定</button>
      </div>

      <p v-if="loading" class="muted">載入中…</p>
      <p v-else-if="docs.length === 0" class="muted">
        還沒有單據。上面按「批次匯入 Word 單據」，可以一次選很多份。
      </p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="form_date" :active="sortKey" :dir="sortDir" @sort="toggle">填表日期</SortTh>
              <SortTh k="doc_type" :active="sortKey" :dir="sortDir" @sort="toggle">類型</SortTh>
              <SortTh k="request_no" :active="sortKey" :dir="sortDir" @sort="toggle">單據編號</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="applicant" :active="sortKey" :dir="sortDir" @sort="toggle">申請人</SortTh>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">綁定資產</SortTh>
              <SortTh k="bind_confidence" :active="sortKey" :dir="sortDir" @sort="toggle">綁定方式</SortTh>
              <th>原始檔</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="d in sorted" :key="d.id">
            <tr :class="{ warnrow: d.warnings.length, hist: !d.is_current }" class="clickable" @click="toggleOpen(d)">
              <td class="mono sm">
                {{ d.form_date ?? '—' }}
                <div class="badges">
                  <span v-if="!d.is_current" class="bd hist" title="同一個 IP 有更新的單，這張是歷史">歷史</span>
                  <span v-if="d.has_secrets" class="bd sec" title="原始檔含帳密，下載會留稽核紀錄">🔒 含帳密</span>
                  <span v-if="d.is_decommission" class="bd dec">下線單</span>
                  <span v-if="d.review_status !== 'confirmed'" class="bd pend">待審</span>
                </div>
                <!-- 命中片段：只給檔名的話，使用者還是得一份份開來看，等於沒解決問題 -->
                <div v-if="d.snippet" class="snip">{{ d.snippet }}</div>
              </td>
              <td>
                <span class="tag" :class="d.doc_type">{{ TYPE_TEXT[d.doc_type] ?? d.doc_type }}</span>
              </td>
              <td class="mono">
                {{ d.request_no ?? '—' }}
                <div v-if="d.ref_request_no" class="note">↳ 對應申請單 {{ d.ref_request_no }}</div>
              </td>
              <td class="mono">{{ d.hostname ?? '—' }}</td>
              <td class="mono">{{ d.ip ?? '—' }}</td>
              <td>
                {{ d.applicant ?? '—' }}
                <div v-if="d.applicant_unit" class="note">{{ d.applicant_unit }}</div>
              </td>
              <td>
                <template v-if="bindingId === d.id">
                  <input v-model="bindSerial" class="bind-in mono" placeholder="資產序號" />
                  <div class="btn-row">
                    <button class="chip" @click="saveBind(d)">儲存</button>
                    <button class="chip" @click="bindingId = 0">取消</button>
                  </div>
                </template>
                <template v-else>
                  <NuxtLink v-if="d.asset_serial" :to="`/assets/${d.asset_serial}`" class="lnk-in mono">
                    {{ d.asset_serial }}
                  </NuxtLink>
                  <span v-else class="muted">未綁定</span>
                  <button class="chip tiny" @click="startBind(d)">改</button>
                </template>
              </td>
              <td>
                <span class="st" :class="d.bind_confidence">{{ CONF_TEXT[d.bind_confidence] }}</span>
                <div v-for="(w, i) in d.warnings" :key="i" class="note warn">⚠ {{ w }}</div>
              </td>
              <td>
                <a :href="downloadUrl(d)" target="_blank" rel="noopener" class="lnk-in sm"
                   @click.stop>{{ d.file_ext }} ↓</a>
              </td>
            </tr>
            <tr v-if="openId === d.id" class="drill">
              <td colspan="9">
                <div class="panels">
                  <div v-if="Object.keys(d.checkboxes ?? {}).length" class="panel">
                    <div class="ph">單據上勾選的</div>
                    <div v-for="v in Object.values(d.checkboxes)" :key="v.label" class="kv">
                      <span>{{ v.label }}</span><b>{{ v.selected.join('、') || '（沒選）' }}</b>
                    </div>
                  </div>

                  <!-- 沒有規格欄位的單（上線檢查表）也要能標「看過了」，
                       不然它永遠掛在「待審」，待辦清單清不完（2026-08-15 使用者發現） -->
                  <div class="panel">
                    <div class="ph">
                      {{ Object.keys(d.values ?? {}).length ? '申請規格' : '審核' }}
                      <span class="ph-sub">
                        {{ d.review_status === 'confirmed'
                           ? `已由 ${d.reviewed_by} 確認` : '尚未有人確認過，請核對後按確認' }}
                      </span>
                    </div>
                    <div v-for="(v, k) in d.values" :key="k" class="kv">
                      <span>{{ v.label }}</span>
                      <input v-model="draft[k]" class="vin" @click.stop />
                    </div>
                    <div v-if="!Object.keys(d.values ?? {}).length" class="kv">
                      <span>這份沒有規格欄位，核對過內容後標記完成即可</span>
                    </div>
                    <button
                      v-if="d.review_status !== 'confirmed'"
                      class="chip ok" :disabled="savingReview" @click.stop="saveReview(d)"
                    >{{ savingReview ? '儲存中…'
                        : Object.keys(d.values ?? {}).length ? '確認這些值' : '標記審核完成' }}</button>
                    <div v-else class="kv done">✔ 已由 {{ d.reviewed_by }} 於 {{ d.reviewed_at }} 確認</div>
                  </div>

                  <div v-if="Object.keys(d.sections ?? {}).length" class="panel">
                    <div class="ph">第二頁區塊</div>
                    <div v-for="v in Object.values(d.sections)" :key="v.label" class="kv">
                      <span :class="{ appl: v.applicable }">{{ v.label }}</span>
                      <b :class="{ appl: v.applicable }">{{ v.status }}</b>
                    </div>
                  </div>

                  <div v-if="d.checklist_summary" class="panel">
                    <div class="ph">上線檢核項目</div>
                    <div class="kv"><span>共</span><b>{{ d.checklist_summary.total }} 列</b></div>
                    <div class="kv"><span>完成</span><b>{{ d.checklist_summary.done }}</b></div>
                    <div class="kv"><span>不需</span><b>{{ d.checklist_summary.na }}</b></div>
                    <div class="kv"><span>未填</span>
                      <b :class="{ warn: d.checklist_summary.blank > 0 }">{{ d.checklist_summary.blank }}</b>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-divider { margin: 0 0 16px; font-size: 11px; color: var(--brand-dark);
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong);
  padding: 8px 14px; font-size: 12.5px; color: var(--ink-soft); display: flex;
  align-items: center; gap: 8px; margin-bottom: 14px; }
.breadcrumb-bar b { color: var(--brand-dark); }
.card { border: 1px solid var(--border); background: var(--card); padding: 16px; margin-bottom: 16px; }
.rv-hint { font-size: 12px; color: var(--muted); line-height: 1.8; margin: 0 0 12px; }
.rv-hint b { color: var(--ink-soft); }
.rv-hint code { color: var(--brand); }
.bar-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.upload input { display: none; }
.btn { display: inline-block; font-size: 12.5px; font-weight: 700; padding: 8px 18px;
  background: var(--brand); color: var(--ink); cursor: pointer; }
.btn:hover { background: var(--brand-dark); }
.stats { display: flex; gap: 16px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
.stats b { color: var(--ink); }
.stats b.warn, .warn { color: var(--warn, #d8a13a); }
.import-result { margin-top: 12px; border: 1px solid var(--border-strong);
  padding: 10px 14px; font-size: 12px; color: var(--ink-soft); line-height: 1.8; }
.warn-row { font-size: 11.5px; color: var(--warn, #d8a13a); }
.search-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.search { flex: 1; max-width: 520px; font-family: inherit; font-size: 12.5px;
  padding: 8px 12px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); }
.search:focus { outline: none; border-color: var(--brand); }
.hit { font-size: 12px; color: var(--muted); }
.hit b { color: var(--brand); }
.snip { font-size: 10.5px; color: var(--brand); margin-top: 4px; line-height: 1.6;
  font-family: inherit; max-width: 260px; }
.tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.tabs button { font-family: inherit; font-size: 12px; padding: 6px 14px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--muted); cursor: pointer; }
.tabs button.on { border-color: var(--brand); color: var(--ink); }
.muted { color: var(--muted); font-size: 12.5px; }
.lnk-in { color: var(--brand); text-decoration: none; }
.lnk-in:hover { text-decoration: underline; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 980px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); }
tr:last-child td { border-bottom: none; }
tr.warnrow td { background: rgba(216, 161, 58, 0.05); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.sm { font-size: 11.5px; }
.note { font-size: 10.5px; color: var(--muted); margin-top: 3px; line-height: 1.5; }
.note.warn { color: var(--warn, #d8a13a); }
.tag { font-size: 10.5px; padding: 2px 7px; border: 1px solid var(--border-strong); white-space: nowrap; }
.tag.provision_form { border-color: var(--brand); color: var(--brand); }
.st { font-size: 11.5px; white-space: nowrap; }
.st.auto { color: var(--brand); }
.st.review { color: var(--warn, #d8a13a); font-weight: 700; }
.st.manual { color: var(--ink-soft); }
.bind-in { font-size: 12px; padding: 4px 8px; width: 130px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.btn-row { display: flex; gap: 6px; margin-top: 4px; }
.chip { font-family: inherit; font-size: 11px; padding: 3px 9px; cursor: pointer;
  border: 1px solid var(--border-strong); background: none; color: var(--muted); }
.chip:hover { border-color: var(--brand); color: var(--brand); }
.chip.tiny { margin-left: 6px; padding: 1px 6px; font-size: 10px; }
.chip.ok { border-color: var(--brand); color: var(--brand); margin-top: 6px; }
.badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.bd { font-size: 9.5px; padding: 1px 5px; border: 1px solid var(--border-strong); color: var(--muted); }
.bd.hist { opacity: .8; }
.bd.sec { border-color: var(--bad); color: var(--bad); }
.bd.dec { border-color: var(--warn, #d8a13a); color: var(--warn, #d8a13a); }
.bd.pend { border-color: var(--warn, #d8a13a); color: var(--warn, #d8a13a); }
tr.hist td { opacity: .55; }
tr.clickable { cursor: pointer; }
tr.clickable:hover td { background: rgba(15,23,42,.03); }
.drill td { background: rgba(0,0,0,.18); }
.panels { display: flex; flex-wrap: wrap; gap: 18px; }
.panel { min-width: 200px; }
.ph { font-size: 11px; color: var(--brand); margin-bottom: 6px; }
.ph-sub { color: var(--muted); margin-left: 6px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 11.5px;
  padding: 2px 0; color: var(--muted); }
.kv b { color: var(--ink-soft); font-weight: 400; }
.kv .appl { color: var(--brand); }
.kv b.warn { color: var(--warn, #d8a13a); }
.kv.done { color: var(--brand); }
.vin { width: 110px; font-size: 11.5px; padding: 2px 6px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
</style>
