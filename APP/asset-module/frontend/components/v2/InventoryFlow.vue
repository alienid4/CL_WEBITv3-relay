<script setup lang="ts">
// 7 盤點作業的共用流程（2.x 第二批）：盤點任務 → 盤點結果 → 差異處理 → 盤點報告（＋各盤點自己的頁籤）。
// 機櫃、帳號、軟體、服務四種盤點共用這一個元件與同一組 API（/api/v2/inv/{kind}/…，後端 inventory_flow.py）。
// 機櫃特有的動作（開始盤點選機櫃、下載／上傳盤點檔、一鍵處理差異）用 slot 或 kind 判斷，其餘完全相同。
const props = withDefaults(defineProps<{ kind: 'rack' | 'account' | 'software' | 'service'; no: string; title: string
  extraTabs?: { k: string; label: string }[]; itemLabel?: string }>(), { extraTabs: () => [], itemLabel: '項目' })
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const base = computed(() => `/api/v2/inv/${props.kind}`)

const tasks = ref<any[]>([])
const canEdit = ref(false)
const err = ref('')
async function loadTasks() {
  try { const r = await apiFetch<any>(`${base.value}/tasks`); tasks.value = r.items; canEdit.value = r.can_edit; err.value = '' } catch (e: any) { err.value = `盤點任務載入失敗：${errMsg(e)}` }
}
await loadTasks()
const TABS = computed(() => [{ k: 'tasks', label: `盤點任務 ${tasks.value.length}` }, { k: 'result', label: '盤點結果' }, { k: 'diffs', label: '差異處理' }, { k: 'report', label: '盤點報告' }, ...props.extraTabs])
const tab = computed({ get: () => (TABS.value.some(t => t.k === route.query.tab) ? String(route.query.tab) : 'tasks'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v, host: undefined } }) })
const tid = computed(() => Number(route.query.task) || tasks.value[0]?.id || null)
const task = computed(() => tasks.value.find(t => t.id === tid.value) || null)
function pickTask(id: number, next = 'result') { router.replace({ query: { ...route.query, task: String(id), tab: next, host: undefined } }) }
defineExpose({ loadTasks, tid, pickTask })

// ===== 盤點任務 =====
const taskRows = computed(() => tasks.value.map(t => ({ ...t, cur: t.id === tid.value ? '✓' : '' })))
const creating = ref(false)
async function createSnapshot() {
  if (!window.confirm(`以目前已收集的${props.itemLabel}資料開始一次${props.title}（凍結今天的結果，並與上一次比較）？`)) return
  creating.value = true
  try {
    const r = await guardedFetch<any>(`${base.value}/tasks`, { method: 'POST', body: {} })
    showToast(`已開始盤點 #${r.id}：${r.hosts} 台主機、${r.rows} 筆${props.itemLabel}、差異 ${r.diff_count} 筆`, 'success')
    await loadTasks()
    pickTask(r.id, 'diffs')
  } catch (e: any) { showToast(`建立失敗：${errMsg(e)}`, 'error') } finally { creating.value = false }
}

// ===== 盤點結果 =====
const result = ref<any>(null)
const host = computed(() => (route.query.host ? String(route.query.host) : ''))
async function loadResult() {
  if (!tid.value) { result.value = null; return }
  try { result.value = await apiFetch<any>(`${base.value}/tasks/${tid.value}/result`, { query: { host: host.value || undefined } }) } catch (e: any) { showToast(`盤點結果載入失敗：${errMsg(e)}`, 'error') }
}
const resultCols = computed(() => {
  const r = result.value
  if (!r) return []
  if (r.level === 'item') {
    const c = [{ k: 'item', label: r.item_label }, ...r.fields.map((f: any) => ({ k: f.k, label: f.label }))]
    // 跨主機清單（帳號盤點的特權帳號）：前面加主機
    return r.multi_host ? [{ k: 'name', label: '主機', link: (x: any) => `?tab=result&task=${tid.value}&host=${encodeURIComponent(x.host)}` }, { k: 'host', label: 'IP', mono: true }, ...c] : c
  }
  // 機櫃：點櫃名或台數 → 那一櫃每一箱的帳上與現場結果（host＝「機房:機櫃」）
  const drill = (x: any) => `?tab=result&task=${tid.value}&host=${encodeURIComponent(props.kind === 'rack' ? x.key : x.host)}`
  const c: any[] = [{ k: 'name', label: r.unit_label, link: drill }, { k: 'count', label: r.count_label, mono: true, link: drill }]
  if (props.kind === 'rack') c.push({ k: 'done', label: '狀態' }, { k: 'counted_by', label: '盤點人' }, { k: 'counted_on', label: '盤點日期', mono: true }, { k: 'diff_open', label: '差異待處理', mono: true })
  else c.splice(1, 0, { k: 'host', label: 'IP', mono: true })
  if (r.rows?.some((x: any) => 'collected_at' in x)) c.push({ k: 'collected_at', label: '收集時間', mono: true })
  return c
})

// ===== 差異處理 =====
const diffs = ref<any>(null)
const dFilter = ref('待處理')
async function loadDiffs() {
  if (!tid.value) { diffs.value = null; return }
  try { diffs.value = await apiFetch<any>(`${base.value}/tasks/${tid.value}/diffs`) } catch (e: any) { showToast(`差異載入失敗：${errMsg(e)}`, 'error') }
}
const diffRows = computed(() => (diffs.value?.rows ?? []).map((d: any, i: number) => ({ ...d, no: i + 1, who: props.kind === 'rack' ? d.host : d.name, what: props.kind === 'rack' ? d.name : (d.item || '—') }))
  .filter((d: any) => !dFilter.value || d.status === dFilter.value))
const { sortKey: dKey, sortDir: dDir, toggle: dToggle, sorted: dSorted } = useSort(diffRows, 'no')
// 可編輯的差異表不是共用 V2Table（每列有狀態按鈕），匯出另做：目前篩選＋排序後的全部列，欄位同畫面
const { exportTable: _xt, busy: dExporting } = useTableExport()
const _xname = useV2ExportName()
function exportDiffs() {
  const who = props.kind === 'rack' ? '機櫃' : '主機'
  _xt(_xname('差異'), ['項次', who, props.kind === 'rack' ? '設備' : props.itemLabel, '種類', props.kind === 'rack' ? '帳上' : '上次',
    props.kind === 'rack' ? '現場' : '這次', '下一步', '狀態', '處理人', '處理時間', '原因'],
  dSorted.value.map((d: any) => [`D${d.no}`, d.who, d.what, d.kind, d.before, d.after, d.next_step, d.status, d.status_by || '', (d.status_at || '').slice(0, 16), d.reason || '']))
}
async function setStatus(d: any, status: string) {
  let reason: string | null = null
  if (status === '不處理') { reason = window.prompt('不處理的原因（必填）', ''); if (!reason) return }
  if (status === '已處理') { reason = window.prompt('處理說明（可空）', ''); if (reason === null) return }
  try { await guardedFetch(`${base.value}/tasks/${tid.value}/diffs/status`, { method: 'POST', body: { diff_key: d.diff_key, status, reason } }); await loadDiffs(); loadTasks() } catch (e: any) { showToast(`更新失敗：${errMsg(e)}`, 'error') }
}
async function act(d: any) {
  const what = d.action === 'mark_removed' ? `將「${d.name}」整箱標記已不在櫃上` : `將「${d.name}」整箱更正位置到 ${d.after}`
  if (!window.confirm(`${what}？`)) return
  try { const r = await guardedFetch<any>(`/api/rack-audits/${tid.value}/diffs/act`, { method: 'POST', body: { diff_key: d.diff_key, action: d.action } }); showToast(r.done, 'success'); await loadDiffs(); loadTasks() } catch (e: any) { showToast(`處理失敗：${errMsg(e)}`, 'error') }
}

// ===== 盤點報告 =====
const rep = ref<any>(null)
async function loadReport() {
  if (!tid.value) { rep.value = null; return }
  try { rep.value = await apiFetch<any>(`${base.value}/tasks/${tid.value}/report`) } catch (e: any) { showToast(`報告載入失敗：${errMsg(e)}`, 'error') }
}
watch([tab, tid, host], () => {
  if (tab.value === 'result') loadResult()
  else if (tab.value === 'diffs') loadDiffs()
  else if (tab.value === 'report') loadReport()
}, { immediate: true })
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">{{ no }} {{ title }}</h1></template>
    <template #conclusion>
      <span v-if="task">目前選擇：#{{ task.id }} {{ task.title }}（{{ task.created_at?.slice(0, 16) }}・{{ task.scope }}・差異待處理 {{ task.diff_open }}）</span>
      <span v-else class="muted">—</span>
      <slot name="aside" />
    </template>
    <template #actions><slot name="actions" /></template>
    <template #tab>
      <p v-if="err" class="err">{{ err }}</p>
      <template v-if="tab === 'tasks'">
        <div class="bar">
          <slot name="create" :can-edit="canEdit">
            <button v-if="canEdit" type="button" class="btn" :disabled="creating" @click="createSnapshot">{{ creating ? '盤點中…' : '開始盤點' }}</button>
          </slot>
          <InfoNote v-if="kind !== 'rack'">開始盤點＝把當下已收集的{{ itemLabel }}資料凍結成一份紀錄，並與上一次的盤點任務比較。只比較兩次都有收集到的主機；這次沒收集到的主機列一筆「主機未收集」，不會把它的每一筆都當成移除。</InfoNote>
        </div>
        <p v-if="!taskRows.length" class="muted">尚無盤點任務：按上方「開始盤點」開始第一次。</p>
        <V2Table v-else :rows="taskRows" prefix="T" empty="尚無盤點任務" :cols="[{ k: 'title', label: '盤點任務', link: (r: any) => `?tab=result&task=${r.id}` }, { k: 'created_at', label: '建立時間', mono: true }, { k: 'created_by', label: '建立者' }, { k: 'scope', label: '範圍' }, { k: 'size', label: kind === 'rack' ? '帳上台數' : `${itemLabel}數`, mono: true }, { k: 'diff_count', label: '差異', mono: true, link: (r: any) => `?tab=diffs&task=${r.id}` }, { k: 'diff_open', label: '待處理', mono: true, link: (r: any) => `?tab=diffs&task=${r.id}` }, { k: 'status', label: '狀態' }, { k: 'cur', label: '目前選擇' }]" />
      </template>
      <template v-else-if="tab === 'result'">
        <p v-if="!tid" class="muted">尚無盤點任務。<NuxtLink to="?tab=tasks" class="lnk">開始第一次盤點</NuxtLink></p>
        <template v-else-if="result">
          <slot name="result-actions" :task="task" />
          <div v-if="result.level === 'item'" class="bar"><NuxtLink :to="`?tab=result&task=${tid}`" class="lnk">‹ 全部{{ kind === 'rack' ? '機櫃' : '主機' }}</NuxtLink><b>{{ result.name }}</b><span v-if="result.host && kind !== 'rack'" class="muted mono">{{ result.host }}</span><span class="muted">{{ result.total }} {{ kind === 'rack' ? '台' : '筆' }}</span><span v-if="kind === 'rack'" class="muted">{{ result.done ? `已盤・${result.counted_by || '—'} ${result.counted_on || ''}` : '未盤（列的是帳上設備）' }}</span></div>
          <div v-if="kind === 'account' && result.level !== 'item'" class="bar"><NuxtLink :to="`?tab=result&task=${tid}&host=*priv`" class="lnk">特權帳號清單（管理員權限或 UID 0）</NuxtLink></div>
          <p v-if="result.level !== 'item'" class="muted">共 {{ result.rows.length }} {{ result.unit_label === '機櫃' ? '櫃' : '台' }}、{{ result.total }} {{ kind === 'rack' ? '台' : '筆' }}。點數字看明細。</p>
          <V2Table :rows="result.rows" :prefix="result.level === 'item' ? 'I' : 'U'" :max="2000" :cols="resultCols" />
        </template>
      </template>
      <template v-else-if="tab === 'diffs'">
        <p v-if="!tid" class="muted">尚無盤點任務。<NuxtLink to="?tab=tasks" class="lnk">開始第一次盤點</NuxtLink></p>
        <template v-else-if="diffs">
          <p v-if="diffs.note" class="muted">{{ diffs.note }}</p>
          <div class="bar">
            <button v-for="s in ['待處理', '已處理', '不處理', '']" :key="s" type="button" class="chip" :class="{ on: dFilter === s }" @click="dFilter = s">{{ s || '全部' }} {{ (diffs.rows || []).filter((d: any) => !s || d.status === s).length }}</button>
          </div>
          <div class="bar"><button type="button" class="chip" :disabled="dExporting || !diffRows.length" @click="exportDiffs">{{ dExporting ? '匯出中…' : `匯出（${diffRows.length} 筆）` }}</button></div>
          <table class="t">
            <thead><tr>
              <SortTh k="no" :active="dKey" :dir="dDir" @sort="dToggle">項次</SortTh><SortTh k="who" :active="dKey" :dir="dDir" @sort="dToggle">{{ kind === 'rack' ? '機櫃' : '主機' }}</SortTh>
              <SortTh k="what" :active="dKey" :dir="dDir" @sort="dToggle">{{ kind === 'rack' ? '設備' : itemLabel }}</SortTh><SortTh k="kind" :active="dKey" :dir="dDir" @sort="dToggle">種類</SortTh>
              <SortTh k="before" :active="dKey" :dir="dDir" @sort="dToggle">{{ kind === 'rack' ? '帳上' : '上次' }}</SortTh><SortTh k="after" :active="dKey" :dir="dDir" @sort="dToggle">{{ kind === 'rack' ? '現場' : '這次' }}</SortTh>
              <SortTh k="next_step" :active="dKey" :dir="dDir" @sort="dToggle">下一步</SortTh><SortTh k="status" :active="dKey" :dir="dDir" @sort="dToggle">狀態</SortTh></tr></thead>
            <tbody>
              <tr v-for="d in dSorted.slice(0, 2000)" :key="d.diff_key">
                <td class="mono">D{{ d.no }}</td>
                <td>{{ d.who }}<div v-if="kind !== 'rack'" class="muted mono small">{{ d.host }}</div></td>
                <td>{{ d.what }}</td><td>{{ d.kind }}</td><td class="small">{{ d.before }}</td><td class="small">{{ d.after }}</td>
                <td class="small">{{ d.next_step }}<button v-if="canEdit && d.action && d.status === '待處理'" type="button" class="chip on" @click="act(d)">{{ d.action === 'mark_removed' ? '標記已不在櫃上' : '位置更正' }}</button></td>
                <td>{{ d.status }}<div v-if="d.status_by" class="muted small">{{ d.status_by }} {{ (d.status_at || '').slice(0, 16) }}{{ d.reason ? '：' + d.reason : '' }}</div>
                  <div v-if="canEdit" class="small"><button v-for="s in ['待處理', '已處理', '不處理'].filter(x => x !== d.status)" :key="s" type="button" class="lb" @click="setStatus(d, s)">{{ s }}</button></div></td>
              </tr>
              <tr v-if="!diffRows.length"><td colspan="8" class="muted">沒有符合的差異</td></tr>
            </tbody>
          </table>
        </template>
      </template>
      <template v-else-if="tab === 'report'">
        <p v-if="!tid" class="muted">尚無盤點任務。<NuxtLink to="?tab=tasks" class="lnk">開始第一次盤點</NuxtLink></p>
        <template v-else-if="rep">
          <div class="bar"><a class="btn" :href="`${apiBase}${base}/tasks/${tid}/report.xlsx`">匯出盤點報告</a></div>
          <dl class="kv"><template v-for="x in rep.lines" :key="x.k"><dt>{{ x.k }}</dt><dd>{{ x.v }}</dd></template></dl>
          <V2Table :rows="Object.entries(rep.by_kind).map(([k, v]: any) => ({ kind: k, ...v }))" prefix="K" empty="沒有差異"
                   :cols="[{ k: 'kind', label: '差異種類' }, { k: 'total', label: '總數', mono: true, link: () => `?tab=diffs&task=${tid}` }, { k: '待處理', label: '待處理', mono: true }, { k: '已處理', label: '已處理', mono: true }, { k: '不處理', label: '不處理', mono: true }]" />
        </template>
      </template>
      <slot v-else name="extra" :tab="tab" :task="task" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; text-decoration: none; cursor: pointer; }
.btn:disabled { opacity: .5; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; margin-left: 4px; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.t td { padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; color: var(--ink-soft); }
.kv { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 12px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.lb { background: none; border: 0; padding: 0 6px 0 0; color: var(--link); cursor: pointer; font: inherit; text-decoration: underline; }
.lnk { color: var(--link); }
.mono { font-family: var(--disp); } .small { font-size: 12px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
