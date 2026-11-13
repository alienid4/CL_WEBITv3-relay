<script setup lang="ts">
// 中繼匯入：外部系統（WhatsUp、儲存設備、vCenter）的資料由能連到它的管理主機收集成檔案，放到共用資料夾；這裡只讀檔匯入。
// admin 設定資料夾與啟用、可按「立即匯入」（盤中窗內 useApi 會統一跳確認）；其他人唯讀看鮮度燈與匯入紀錄。
// 鮮度燈三態：正常／資料過期／從未收到；卡片點下去 → 下方匯入紀錄只列該來源。未匹配 = 對不到資產的設備（資料有匯入，只是還沒對上資產）。
import type { Ref } from 'vue'
interface Cfg { enabled: boolean; dir: string; stale_days: number; interval_min: number; max_file_mb: number; last_scan: string; last_result: string }
interface Src { source: string; label: string; implemented: boolean; note: string; status: 'ok' | 'stale' | 'partial' | 'never'; status_label: string; last_batch: string | null; last_collected_at: string | null; age_hours: number | null; stale_days: number; problem_batches: number }
interface View { config: Cfg; sources: Src[]; dir_problem: string | null; batches_visible: number; in_quiet_window: boolean }
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const view = ref<View | null>(null) as Ref<View | null>
const batches = ref<any[]>([])
const unmatched = ref<any[]>([])
const err = ref('')
const busy = ref<'' | 'save' | 'run'>('')
const form = reactive({ enabled: false, dir: '', stale_days: 2, interval_min: 15 })
const sub = ref<'batches' | 'unmatched'>('batches')
// 網址 ?src=<來源> 直接篩那個來源的匯入紀錄（例：5.2 Unity 陣列的「資料時間」連過來）
const route = useRoute()
const srcFilter = ref('')
// 跟著網址變（同一頁換 ?src= 也會更新）；不認得的來源代號不套（來源清單載入後才判斷）
watch(() => [route.query.src, view.value?.sources?.length] as const, ([v]) => {
  if (typeof v === 'string' && (view.value?.sources ?? []).some(s => s.source === v)) srcFilter.value = v
}, { immediate: true })

async function load() {
  err.value = ''
  try {
    const v = await apiFetch<View>('/api/v2/relay-import')
    view.value = v
    Object.assign(form, { enabled: v.config.enabled, dir: v.config.dir, stale_days: v.config.stale_days, interval_min: v.config.interval_min })
    batches.value = (await apiFetch<{ rows: any[] }>('/api/v2/relay-import/batches')).rows
    unmatched.value = (await apiFetch<{ rows: any[] }>('/api/v2/relay-import/unmatched')).rows
  } catch (e: any) { err.value = `中繼匯入資料載入失敗：${errMsg(e)}` }
}
onMounted(load)

async function save() {
  busy.value = 'save'
  try {
    await apiFetch('/api/v2/relay-import/settings', { method: 'PUT', body: { enabled: form.enabled, dir: form.dir.trim(), stale_days: Number(form.stale_days), interval_min: Number(form.interval_min) } })
    showToast('已儲存設定', 'success')
    await load()
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}
async function runNow() {
  busy.value = 'run'
  try {
    const r = await apiFetch<{ status: string; message?: string; imported?: number; rejected?: number; failed?: number }>('/api/v2/relay-import/run', { method: 'POST' })
    const bad = (r.rejected ?? 0) + (r.failed ?? 0)
    showToast(r.message ?? '已執行', r.status !== 'ok' ? 'warn' : bad ? 'warn' : 'success', 8000)
    await load()
  } catch (e: any) { showToast(`匯入沒有完成：${errMsg(e)}`, 'error') } finally { busy.value = '' }
}

const STATUS_CLASS: Record<string, string> = { ok: 'ok', stale: 'stale', partial: 'stale', never: 'never' }   // partial＝收集不完整（2.058.22），跟過期同一個警示色
const age = (s: Src) => (s.age_hours === null ? '—' : s.age_hours < 48 ? `${s.age_hours} 小時前` : `${Math.round(s.age_hours / 24)} 天前`)
function pick(s: Src) { srcFilter.value = srcFilter.value === s.source ? '' : s.source; sub.value = 'batches' }

const RESULT: Record<string, string> = { imported: '成功', rejected: '拒收', failed: '失敗' }
const TRIGGER: Record<string, string> = { manual: '手動', schedule: '排程' }
const MATCH: Record<string, string> = { no_asset: '找不到資產', ambiguous: '對到多台不同機器', generic_name: '主機名太通用' }
const srcName = (k: string) => view.value?.sources.find(s => s.source === k)?.label ?? k
const batchRows = computed(() => batches.value.filter(b => !srcFilter.value || b.source === srcFilter.value))
const batchCols = [
  { k: 'batch_id', label: '批次', mono: true }, { k: 'source', label: '資料來源', text: (r: any) => srcName(r.source) },
  { k: 'status', label: '結果', text: (r: any) => RESULT[r.status] ?? r.status, tone: (r: any) => (r.status === 'imported' ? 'prod' : 'none') },
  { k: 'reason', label: '拒收／失敗原因' }, { k: 'imported_at', label: '匯入時間', mono: true }, { k: 'collected_at', label: '收集時間', mono: true },
  { k: 'range_to', label: '資料日', mono: true }, { k: 'devices', label: '設備數', mono: true }, { k: 'host_days', label: '設備日數', mono: true },
  { k: 'points', label: '資料點', mono: true }, { k: 'skipped_existing', label: '未覆蓋既有', mono: true }, { k: 'unmatched', label: '未匹配', mono: true },
  { k: 'trigger', label: '觸發', text: (r: any) => TRIGGER[r.trigger] ?? r.trigger }, { k: 'imported_by', label: '操作者' }, { k: 'collector_version', label: '收集腳本' },
]
const unmatchedCols = [
  { k: 'ident', label: 'IP', mono: true }, { k: 'name', label: '名稱' }, { k: 'source', label: '資料來源', text: (r: any) => srcName(r.source) },
  { k: 'reason', label: '原因', text: (r: any) => MATCH[r.reason] ?? r.reason }, { k: 'candidates', label: '候選資產數', mono: true },
  { k: 'last_batch', label: '最近批次', mono: true }, { k: 'first_seen', label: '首次出現', mono: true }, { k: 'last_seen', label: '最近出現', mono: true },
]
</script>

<template>
  <div class="box">
    <div class="hd">
      <b class="t">中繼匯入</b>
      <InfoNote>正式環境的 WhatsUp、儲存設備、vCenter 系統主機連不到。改由能連到它們的管理主機用只讀腳本收集成檔案，放到共用資料夾；這裡只讀檔案匯入，不連任何外部系統，檔案內也不會有密碼。收集腳本在「開發協作」頁（任務 D4）。每一批是一個資料夾，裡面有 manifest.json 與資料檔；驗證 sha256、拒收含密碼欄位的檔，同一批只匯入一次。盤中（平日 08:30–15:00）不會自動匯入，窗外補跑。</InfoNote>
      <button type="button" class="mini" @click="load">重新整理</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="view?.dir_problem" class="warn">{{ view.dir_problem }}</p>

    <div class="cards">
      <button v-for="s in view?.sources ?? []" :key="s.source" type="button" class="card" :class="[STATUS_CLASS[s.status], { on: srcFilter === s.source }]" :aria-label="`${s.label} ${s.status_label}`" @click="pick(s)">
        <span class="nm">{{ s.label }}</span>
        <span class="lamp" :class="STATUS_CLASS[s.status]">● {{ s.status_label }}</span>
        <span class="meta">最近一批：{{ s.last_batch ?? '—' }}</span>
        <span class="meta">收集時間：{{ s.last_collected_at ?? '—' }}（{{ age(s) }}）</span>
        <span v-if="s.problem_batches" class="meta bad">拒收／失敗 {{ s.problem_batches }} 批</span>
        <span v-if="!s.implemented" class="meta">尚未實作匯入</span>
      </button>
    </div>

    <dl class="kv">
      <dt>共用資料夾</dt>
      <dd>
        <input v-if="isAdmin" v-model="form.dir" class="in" placeholder="系統主機讀得到的完整路徑" aria-label="共用資料夾">
        <span v-else class="mono">{{ form.dir || '—' }}</span>
      </dd>
      <dt>自動匯入</dt>
      <dd>
        <label v-if="isAdmin" class="chk"><input v-model="form.enabled" type="checkbox"> 啟用，每 <input v-model="form.interval_min" type="number" min="1" class="in xs" aria-label="檢查間隔分鐘"> 分鐘看一次</label>
        <span v-else>{{ form.enabled ? `啟用（每 ${form.interval_min} 分鐘）` : '未啟用' }}</span>
      </dd>
      <dt>資料過期</dt>
      <dd>
        <label v-if="isAdmin" class="chk">超過 <input v-model="form.stale_days" type="number" min="1" max="60" class="in xs" aria-label="資料過期天數"> 天沒有新批次</label>
        <span v-else>超過 {{ form.stale_days }} 天沒有新批次</span>
      </dd>
      <dt>上次匯入</dt>
      <dd><span class="mono">{{ view?.config.last_scan || '—' }}</span> <span class="muted">{{ view?.config.last_result }}</span></dd>
    </dl>
    <div v-if="isAdmin" class="acts">
      <button type="button" class="btn" :disabled="!!busy" @click="save">{{ busy === 'save' ? '儲存中…' : '儲存設定' }}</button>
      <button type="button" class="btn pri" :disabled="!!busy" @click="runNow">{{ busy === 'run' ? '匯入中…' : '立即匯入' }}</button>
    </div>

    <div class="subs">
      <button type="button" class="sub" :class="{ on: sub === 'batches' }" @click="sub = 'batches'">匯入紀錄 {{ batchRows.length }}</button>
      <button type="button" class="sub" :class="{ on: sub === 'unmatched' }" @click="sub = 'unmatched'">未匹配 {{ unmatched.length }}</button>
      <label v-if="sub === 'batches'" class="flt">來源
        <select v-model="srcFilter" class="in s" aria-label="依資料來源篩選">
          <option value="">全部</option>
          <option v-for="s in view?.sources ?? []" :key="s.source" :value="s.source">{{ s.label }}</option>
        </select>
      </label>
    </div>
    <V2Table v-if="sub === 'batches'" :rows="batchRows" :cols="batchCols" prefix="B" empty="還沒有匯入紀錄；共用資料夾出現批次後，啟用自動匯入或按「立即匯入」" />
    <V2Table v-else :rows="unmatched" :cols="unmatchedCols" prefix="U" empty="沒有未匹配的設備" />
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 12px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.t { font-size: 16px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.card { display: flex; flex-direction: column; gap: 4px; text-align: left; font-family: inherit; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; cursor: pointer; color: var(--ink); }
.card.on { border-color: var(--brand); box-shadow: inset 0 0 0 1px var(--brand); }
.nm { font-weight: 700; }
.lamp { font-size: 13px; font-weight: 700; }
.lamp.ok { color: var(--st-online); } .lamp.stale { color: var(--warn-text); } .lamp.never { color: var(--st-unknown); }
.meta { font-size: 12.5px; color: var(--ink-aux); } .meta.bad { color: var(--bad); }
.kv { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px 12px; margin: 0; align-items: center; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.in { font-family: inherit; font-size: 13.5px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: min(520px, 100%); background: var(--card); color: var(--ink); }
.in.s { width: 200px; } .in.xs { width: 70px; }
.chk { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 6px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.btn:disabled { opacity: .6; cursor: default; }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.subs { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
.flt { margin-left: auto; font-size: 13px; color: var(--ink-aux); display: inline-flex; align-items: center; gap: 6px; }
.mono { font-family: var(--mono, monospace); } .muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); } .warn { background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--warn); border-radius: 10px; padding: 8px 14px; font-size: 13.5px; margin: 0; }
</style>
