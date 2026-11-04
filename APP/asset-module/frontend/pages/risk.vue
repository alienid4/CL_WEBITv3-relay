<script setup lang="ts">
// 4-7 設備風險清單（v1.499.0）：每天開一頁，看今天哪些機器要注意；點一列就看到出事要打給誰。
// 數字全由後端 daily_risk.py 算（verify_numbers 同一支），這裡只排版、搜尋、排序、匯出。
// 單位一律是「台」（machine_key）；單點分頁的單位是「系統」。
interface Brief { key: string; serial: string; hostname: string | null; ip: string | null; os: string | null
  room: string | null; rack: string | null; model: string | null; env: string; api_ids: string[]; systems: string | null }
type Row = Brief & Record<string, any>

const { apiFetch } = useApi()
const { exportTable, busy: exporting } = useTableExport()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const data = ref<any>(null)
const err = ref('')
async function load() {
  err.value = ''
  try {
    data.value = await apiFetch('/api/risk')
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
await load()

type Tab = 'uptime' | 'hardware' | 'spof' | 'contract'
const TABS: { k: Tab; label: string; prefix: string }[] = [
  { k: 'uptime', label: '長時間未重開機', prefix: 'U' },
  { k: 'hardware', label: '硬體異常', prefix: 'H' },
  { k: 'spof', label: '單點（無備援）', prefix: 'S' },
  { k: 'contract', label: '保固／合約到期', prefix: 'C' },
]
const tab = computed<Tab>({
  get: () => (TABS.some((t) => t.k === route.query.tab) ? route.query.tab as Tab : 'uptime'),
  set: (v) => { router.replace({ query: { ...route.query, tab: v, view: undefined } }); view.value = 'main' },
})
// 分頁裡的子清單（例如「讀不到開機時間」「無法判定」「無合約資料」）——每個數字都點得進去
const view = ref<string>(typeof route.query.view === 'string' ? route.query.view : 'main')
const q = ref('')
const open = ref<Set<string>>(new Set())
function toggleOpen(k: string) {
  const n = new Set(open.value)
  n.has(k) ? n.delete(k) : n.add(k)
  open.value = n
}

const MACHINE_COLS = [
  { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP' }, { k: 'os', label: 'OS' },
  { k: 'room', label: '機房' }, { k: 'rack', label: '機櫃' }, { k: 'systems', label: '系統' },
]
const COLS: Record<string, { k: string; label: string }[]> = {
  'uptime:main': [{ k: 'days', label: '未重開機（天）' }, { k: 'last_boot', label: '上次開機' }, ...MACHINE_COLS,
    { k: 'collected_at', label: '收集時間' }],
  'uptime:unreadable': [...MACHINE_COLS, { k: 'collected_at', label: '收集時間' }, { k: 'raw', label: '收到的原文' }],
  'hardware:main': [{ k: 'items', label: '項目' }, { k: 'detail', label: '內容' }, { k: 'sources', label: '來源' },
    ...MACHINE_COLS],
  'spof:main': [{ k: 'reason', label: '類型' }, { k: 'api_id', label: 'APID' }, { k: 'name', label: '系統' },
    { k: 'prod_count', label: '正式（台）' }, { k: 'rooms', label: '機房' }, { k: 'dr_other_room', label: '他機房備援（台）' }],
  'spof:undetermined': [{ k: 'reason', label: '狀態' }, { k: 'api_id', label: 'APID' }, { k: 'name', label: '系統' },
    { k: 'prod_count', label: '正式（台）' }, { k: 'rooms', label: '機房' }],
  'contract:main': [{ k: 'band_label', label: '狀態' }, { k: 'contract_end', label: '合約到期' },
    { k: 'days_left', label: '剩餘天數' }, { k: 'vendor', label: '廠商' }, { k: 'phone', label: '維護電話' },
    { k: 'end_source', label: '來源' }, ...MACHINE_COLS, { k: 'model', label: '型號' }],
  'contract:none': [...MACHINE_COLS, { k: 'model', label: '型號' }],
  'contract:ok': [{ k: 'contract_end', label: '合約到期' }, { k: 'days_left', label: '剩餘天數' },
    { k: 'vendor', label: '廠商' }, { k: 'end_source', label: '來源' }, ...MACHINE_COLS],
}

function srcRows(t: Tab, v: string): Row[] {
  const d = data.value
  if (!d) return []
  if (t === 'uptime') return v === 'unreadable' ? d.uptime.unreadable : d.uptime.rows
  if (t === 'hardware') return d.hardware.rows
  if (t === 'spof') return v === 'undetermined' ? d.spof.undetermined : d.spof.rows
  if (v === 'none') return d.contract.none_rows
  if (v === 'ok') return d.contract.ok_rows
  return d.contract.rows
}
const viewKey = computed(() => `${tab.value}:${COLS[`${tab.value}:${view.value}`] ? view.value : 'main'}`)
// 項次照後端預設順序固定（U1、H1…；子清單再加一個字母），重排後同一列仍是同一個號碼
const numbered = computed<Row[]>(() => {
  const t = TABS.find((x) => x.k === tab.value)!
  const v = viewKey.value.split(':')[1]
  const pre = t.prefix + (v === 'main' ? '' : v[0].toUpperCase())
  return srcRows(tab.value, v).map((r, i) => ({ ...r, no: `${pre}${i + 1}`, _i: i }))
})
const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  if (!s) return numbered.value
  return numbered.value.filter((r) => JSON.stringify(r).toLowerCase().includes(s))
})
const { sortKey, sortDir, toggle, sorted } = useSort(filtered, '')
const cols = computed(() => COLS[viewKey.value])

function cell(r: Row, k: string) {
  const v = r[k]
  if (v === null || v === undefined || v === '') return '—'
  return v
}
function doExport() {
  const title = `${TABS.find((t) => t.k === tab.value)!.label}${view.value === 'main' ? '' : `_${subLabel.value}`}`
  exportTable(title, ['項次', ...cols.value.map((c) => c.label)],
    sorted.value.map((r) => [r.no, ...cols.value.map((c) => r[c.k] ?? '')]))
}

const subs = computed(() => {
  const d = data.value
  if (!d) return []
  if (tab.value === 'uptime') return [
    { v: 'main', n: d.uptime.count, l: `≥ ${d.uptime.threshold} 天` },
    { v: 'unreadable', n: d.uptime.unreadable_count, l: '讀不到開機時間' }]
  if (tab.value === 'spof') return [
    { v: 'main', n: d.spof.count, l: '單點（系統）' },
    { v: 'undetermined', n: d.spof.undetermined_count, l: '機房未填，無法判定' }]
  if (tab.value === 'contract') return [
    { v: 'main', n: d.contract.counts.expired + d.contract.counts.d90 + d.contract.counts.d180, l: '180 天內到期／已過期' },
    { v: 'ok', n: d.contract.counts.ok, l: '180 天以上' },
    { v: 'none', n: d.contract.counts.none, l: '無合約資料' }]
  return []
})
const subLabel = computed(() => subs.value.find((s) => s.v === view.value)?.l ?? '')

function go(t: Tab, v = 'main') {
  router.replace({ query: { ...route.query, tab: t, view: v === 'main' ? undefined : v } })
  view.value = v
  q.value = ''
  open.value = new Set()
}

const cards = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { t: 'uptime' as Tab, v: 'main', n: d.uptime.count, l: `未重開機 ≥ ${d.uptime.threshold} 天（台）`, cls: 'warn' },
    { t: 'hardware' as Tab, v: 'main', n: d.hardware.count, l: '硬體異常（台）', cls: 'bad' },
    { t: 'spof' as Tab, v: 'main', n: d.spof.count, l: '單點（系統）', cls: 'warn' },
    { t: 'contract' as Tab, v: 'main', n: d.contract.counts.expired, l: '合約已過期（台）', cls: 'bad' },
    { t: 'contract' as Tab, v: 'main', n: d.contract.counts.d90 + d.contract.counts.d180, l: '180 天內到期（台）', cls: 'warn' },
    { t: 'contract' as Tab, v: 'none', n: d.contract.counts.none, l: '無合約資料（台）', cls: '' },
  ]
})

// 門檻（管理員）
const setOpen = ref(false)
const setDraft = reactive({ uptime_days: 365, disk_pct: 90 })
function openSettings() {
  setDraft.uptime_days = data.value.settings.uptime_days
  setDraft.disk_pct = data.value.settings.disk_pct
  setOpen.value = true
}
async function saveSettings() {
  try {
    await apiFetch('/api/risk/settings', { method: 'PUT', body: { ...setDraft } })
    showToast('門檻已儲存', 'success')
    setOpen.value = false
    await load()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '儲存失敗', 'error')
  }
}

useHead({ title: '設備風險清單' })
</script>

<template>
  <div class="wrap">
    <V2Banner to="/v2/risk" label="1.3 設備風險" />
    <div class="ey">WAR ROOM · 4-7</div>
    <h1>設備風險清單
      <InfoNote>
        單位是「台」（同一台多筆登記只算一次），退役不算。單點分頁的單位是「系統」。<br>
        未重開機：已收集規格的主機，收集當下的開機時間推回開機時刻，算到今天。<br>
        硬體異常：只判讀已經收到的資料，沒收到的不算異常；各來源覆蓋台數見分頁內的註。<br>
        單點規則：每個 APID 環境「正式」的現役機器——只有 1 台＝單機；2 台以上但全在同一間機房（機房都有填）＝同機房；
        有機房未填、有填的不超過一間＝無法判定（另列，不算單點）。「他機房備援」只供參考，不改變判定。<br>
        保固／合約：帳內（CIA）現役實體機；VM 不列。到期日依「聯絡資訊」頁的手動資料與合約匯入。
      </InfoNote>
    </h1>
    <p v-if="data" class="sub">{{ data.generated_at }}
      <button v-if="data.can_edit" class="lnk" @click="openSettings">門檻</button>
    </p>
    <p v-if="err" class="bad">載入失敗：{{ err }}</p>

    <div v-if="setOpen" class="setbox">
      <label>未重開機（天）<input v-model.number="setDraft.uptime_days" type="number" min="1" max="3650"></label>
      <label>磁碟使用率（%）<input v-model.number="setDraft.disk_pct" type="number" min="50" max="100"></label>
      <button class="btn pri" @click="saveSettings">儲存</button>
      <button class="btn" @click="setOpen = false">取消</button>
    </div>

    <template v-if="data">
      <div class="cards">
        <button v-for="(c, i) in cards" :key="i" type="button" class="card" :class="[c.cls, { on: tab === c.t && view === c.v }]"
                @click="go(c.t, c.v)">
          <span class="n">{{ c.n }}</span><span class="l">{{ c.l }}</span>
        </button>
      </div>

      <div class="tabs" role="tablist">
        <button v-for="t in TABS" :key="t.k" class="tb" :class="{ on: tab === t.k }" @click="go(t.k)">{{ t.label }}</button>
      </div>

      <div v-if="subs.length" class="subs">
        <button v-for="s in subs" :key="s.v" class="chip" :class="{ on: view === s.v }" @click="go(tab, s.v)">
          <b>{{ s.n }}</b> {{ s.l }}
        </button>
        <span v-if="tab === 'uptime'" class="cnt">
          有收集 {{ data.uptime.collected }} 台
          <InfoNote>「有收集」＝收過規格（host_spec）的現役台；同一台多筆登記各收過一次，只算一台。</InfoNote>
        </span>
      </div>
      <div v-else-if="tab === 'hardware'" class="subs">
        <span class="cnt">有收集 {{ data.hardware.collected }} 台
          <InfoNote>
            各來源有資料的台數：
            <span v-for="c in data.hardware.coverage" :key="c.source">{{ c.source }} {{ c.hosts }}　</span><br>
            健康基準＝每週健檢快照，看最新一份的磁碟使用率 ≥ {{ data.hardware.disk_pct }}%。
            PSU／風扇目前收集器沒有收，所以不判斷。
          </InfoNote>
        </span>
      </div>

      <div v-if="tab === 'contract' && !data.contract.has_data && view !== 'none'" class="empty">
        <b>尚無合約資料</b>
        <p>還沒有任何機器登錄合約迄日。
          <NuxtLink to="/contacts?tab=todo">到「聯絡資訊」看待補清單</NuxtLink>，或
          <NuxtLink to="/contacts?tab=import">匯入合約檔</NuxtLink>。</p>
      </div>
      <template v-else>
        <div class="bar">
          <input v-model="q" class="kw" placeholder="搜項次、主機名、IP、系統…">
          <span class="cnt">{{ sorted.length }} {{ tab === 'spof' ? '個系統' : '台' }}</span>
          <button class="btn" :disabled="exporting || !sorted.length" @click="doExport">{{ exporting ? '匯出中…' : '匯出 Excel' }}</button>
        </div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <SortTh k="_i" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
                <SortTh v-for="c in cols" :key="c.k" :k="c.k" :active="sortKey" :dir="sortDir" @sort="toggle">{{ c.label }}</SortTh>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sorted.length"><td :colspan="cols.length + 1" class="muted">沒有</td></tr>
              <template v-for="r in sorted" :key="r.no">
                <tr class="click" @click="toggleOpen(r.no)">
                  <td class="mono">{{ r.no }}</td>
                  <td v-for="c in cols" :key="c.k" :class="{ mono: ['ip', 'days', 'last_boot', 'contract_end', 'days_left', 'prod_count', 'dr_other_room', 'api_id', 'collected_at'].includes(c.k) }">
                    <NuxtLink v-if="c.k === 'hostname' && r.serial" :to="`/assets/${encodeURIComponent(r.serial)}`" @click.stop>{{ cell(r, c.k) }}</NuxtLink>
                    <NuxtLink v-else-if="c.k === 'api_id'" :to="`/topology?system=${encodeURIComponent(r.api_id)}`" @click.stop>{{ r.api_id }}</NuxtLink>
                    <span v-else :class="{ bad: c.k === 'reason' && r.reason === '單機', warnt: c.k === 'band_label' }">{{ cell(r, c.k) }}</span>
                  </td>
                </tr>
                <tr v-if="open.has(r.no)" class="det">
                  <td :colspan="cols.length + 1">
                    <div class="detgrid">
                      <div v-if="tab === 'hardware'">
                        <table class="inner">
                          <tr><th>#</th><th>來源</th><th>項目</th><th>內容</th><th>資料時間</th></tr>
                          <tr v-for="(f, i) in r.findings" :key="i">
                            <td>{{ i + 1 }}</td><td>{{ f.source }}</td><td>{{ f.item }}</td><td>{{ f.detail }}</td><td class="mono">{{ f.at }}</td>
                          </tr>
                        </table>
                      </div>
                      <div v-if="tab === 'spof'">
                        <table class="inner">
                          <tr><th>#</th><th>主機名</th><th>IP</th><th>機房</th><th>機櫃</th><th>OS</th></tr>
                          <tr v-for="(m, i) in r.machines" :key="m.key">
                            <td>{{ i + 1 }}</td>
                            <td><NuxtLink :to="`/assets/${encodeURIComponent(m.serial)}`">{{ m.hostname || m.serial }}</NuxtLink></td>
                            <td class="mono">{{ m.ip || '—' }}</td><td>{{ m.room }}</td><td>{{ m.rack || '—' }}</td><td>{{ m.os || '—' }}</td>
                          </tr>
                        </table>
                      </div>
                      <ContactCard v-if="tab === 'spof'" :api-id="r.api_id" compact />
                      <ContactCard v-else :serial="r.serial" compact />
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.wrap { padding: 16px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); }
.sub { color: var(--muted); margin: 4px 0 12px; font-size: 12px; }
.bad { color: var(--bad); }
.warnt { color: var(--warn-text); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.card { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 14px; min-width: 150px;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); cursor: pointer; color: inherit; font: inherit; }
.card.on { outline: 2px solid var(--brand); }
.card .n { font-size: 22px; font-weight: 700; font-family: var(--disp); color: var(--brand); }
.card.warn .n { color: var(--warn-text); }
.card.bad .n { color: var(--bad); }
.card .l { font-size: 12px; color: var(--ink-aux); }
.tabs { display: flex; gap: 4px; margin: 10px 0; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tb { font: inherit; padding: 8px 14px; border: 0; background: none; cursor: pointer; color: var(--ink-aux); border-bottom: 2px solid transparent; }
.tb.on { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.subs { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.chip { font: inherit; font-size: 12.5px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; color: var(--ink-aux); }
.chip.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); }
.bar { display: flex; gap: 10px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.kw { min-width: 240px; padding: 6px 8px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; }
.cnt { font-size: 12px; color: var(--muted); }
.btn { font: inherit; font-size: 12.5px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: #fff; }
.btn:disabled { opacity: .6; }
.lnk { font: inherit; font-size: 12px; border: 0; background: none; color: var(--link); cursor: pointer; }
.setbox { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; padding: 10px; margin-bottom: 10px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
.setbox label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); }
.setbox input { width: 110px; padding: 4px 6px; }
.empty { background: var(--card); border: 1px dashed var(--border-strong); border-radius: var(--radius); padding: 24px; text-align: center; color: var(--ink-aux); }
.empty a { color: var(--link); }
.tbl-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
td a { color: var(--link); }
tr.click { cursor: pointer; }
tr.click:hover { background: var(--mint); }
tr.det > td { background: var(--sub); }
.detgrid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 380px); gap: 12px; }
.detgrid > :only-child { grid-column: 1 / -1; max-width: 420px; }
@media (max-width: 900px) { .detgrid { grid-template-columns: 1fr; } }
.inner { background: var(--card); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
</style>
