<script setup lang="ts">
// 7.1 機櫃盤點「網頁填寫」（2.007.56，舊版 /rack-audit?tab=fill 翻新）：選一櫃，預設每一箱都「在」，只點例外（不在／不符）；
// 不符時填正確 U／型號／序號；櫃上有但沒登記的設備填在最下面。存檔＝這一櫃交一次結果（同一櫃再交＝取代，舊的留歷史）。
// 資料與寫入＝既有的 /api/rack-audits/{id}/rack（GET 取表、PUT 存），邏輯全在後端 rack_inventory.py，這裡不另算。
const props = defineProps<{ taskId: number }>()
const canEdit = ref(false)
const emit = defineEmits<{ saved: [] }>()
interface Row { key: string; no: number; u: string; model: string | null; asset_serial: string; hw_serial: string; name_text: string; result: string; correct_u: string; correct_model: string; correct_serial: string; note: string }
interface Extra { model: string; serial: string; u: string; note: string }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const racks = ref<{ room: string; rack: string; book_count: number; done: boolean }[]>([])
const pickRack = ref('')
const sheet = ref<{ room: string; rack: string; rows: Row[]; extras: Extra[]; done: boolean; counted_by: string; counted_on: string; history: any[] } | null>(null)
const by = ref('')
const on = ref('')
const busy = ref(false)
const err = ref('')

async function loadRacks() {
  try { const d = await apiFetch<any>(`/api/rack-audits/${props.taskId}`); racks.value = d.racks; canEdit.value = !!d.can_edit; err.value = '' } catch (e: any) { err.value = `機櫃清單載入失敗：${errMsg(e)}` }
}
async function loadSheet() {
  sheet.value = null
  if (!pickRack.value) return
  const [room, rack] = pickRack.value.split('|')
  try {
    const s = await apiFetch<any>(`/api/rack-audits/${props.taskId}/rack`, { query: { room, rack } })
    while (s.extras.length < 5) s.extras.push({ model: '', serial: '', u: '', note: '' })
    sheet.value = s
    by.value = s.counted_by || by.value
    on.value = s.counted_on || new Date().toISOString().slice(0, 10)
  } catch (e: any) { showToast(`這櫃載入失敗：${errMsg(e)}`, 'error') }
}
watch(() => props.taskId, () => { pickRack.value = ''; sheet.value = null; loadRacks() }, { immediate: true })
watch(pickRack, loadSheet)

const rows = computed(() => sheet.value?.rows ?? [])
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
const exceptions = computed(() => rows.value.filter(r => r.result !== '在').length)

async function save() {
  const s = sheet.value
  if (!s) return
  if (!by.value.trim()) { showToast('要填盤點人', 'warn'); return }
  busy.value = true
  try {
    const body = { room: s.room, rack: s.rack, counted_by: by.value, counted_on: on.value, extras: s.extras,
      rows: Object.fromEntries(s.rows.map(r => [r.key, { result: r.result, correct_u: r.correct_u, correct_model: r.correct_model, correct_serial: r.correct_serial, note: r.note }])) }
    const r = await guardedFetch<{ version: number; replaced: boolean; warnings: string[] }>(`/api/rack-audits/${props.taskId}/rack`, { method: 'PUT', body })
    showToast(`${s.rack} 已存（第 ${r.version} 版${r.replaced ? '，取代前一版' : ''}）${r.warnings?.length ? '；' + r.warnings.join('；') : ''}`, 'success')
    emit('saved')
    await loadRacks()
    await loadSheet()
  } catch (e: any) { showToast(`存檔失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div class="fill">
    <p v-if="err" class="err">{{ err }}</p>
    <div class="bar">
      <select v-model="pickRack" aria-label="選擇機櫃">
        <option value="">選擇要填寫的機櫃</option>
        <option v-for="r in racks" :key="r.room + r.rack" :value="`${r.room}|${r.rack}`">{{ r.room }} {{ r.rack }}（{{ r.book_count }} 台）{{ r.done ? '・已交' : '' }}</option>
      </select>
      <template v-if="sheet">
        <label>盤點人 <input v-model="by" class="in" :disabled="!canEdit" aria-label="盤點人"></label>
        <label>盤點日 <input v-model="on" type="date" class="in" :disabled="!canEdit" aria-label="盤點日"></label>
        <button type="button" class="btn" :disabled="busy || !canEdit" @click="save">{{ busy ? '存檔中…' : '存這一櫃' }}</button>
        <span class="muted">預設全部「在」，只點例外；目前例外 {{ exceptions }} 筆</span>
      </template>
      <InfoNote>現場有網路時直接在這裡填；機房內沒網路請用「下載盤點檔」離線填、回座位上傳。同一櫃再交一次＝取代（舊的留在歷史）。「不符」時填正確的 U 位、型號、序號；櫃上有但帳上沒有的設備填在表格最下面。</InfoNote>
      <span v-if="!canEdit" class="muted">只有管理員能交盤點結果</span>
    </div>
    <p v-if="!pickRack" class="muted">請先選一個機櫃。</p>
    <table v-if="sheet" class="t">
      <thead><tr>
        <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
        <SortTh k="u" :active="sortKey" :dir="sortDir" @sort="toggle">U</SortTh>
        <SortTh k="model" :active="sortKey" :dir="sortDir" @sort="toggle">機型</SortTh>
        <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產編號</SortTh>
        <SortTh k="hw_serial" :active="sortKey" :dir="sortDir" @sort="toggle">原廠序號</SortTh>
        <SortTh k="name_text" :active="sortKey" :dir="sortDir" @sort="toggle">主機名</SortTh>
        <SortTh k="result" :active="sortKey" :dir="sortDir" @sort="toggle">結果</SortTh>
        <th>正確 U</th><th>正確型號</th><th>正確序號</th><th>備註</th>
      </tr></thead>
      <tbody>
        <tr v-for="r in sorted" :key="r.key" :class="{ ex: r.result !== '在' }">
          <td class="mono">F{{ r.no }}</td><td class="mono">{{ r.u || '位置不明' }}</td><td>{{ upText(r.model) }}</td><td class="mono small">{{ r.asset_serial }}</td>
          <td class="mono small">{{ upText(r.hw_serial) || '—' }}</td><td class="small">{{ r.name_text }}</td>
          <td><span class="seg"><button v-for="v in ['在', '不在', '不符']" :key="v" type="button" :class="{ on: r.result === v }" :disabled="!canEdit" @click="r.result = v">{{ v }}</button></span></td>
          <td><input v-model="r.correct_u" class="in s" :disabled="!canEdit || r.result !== '不符'" aria-label="正確 U"></td>
          <td><input v-model="r.correct_model" class="in" :disabled="!canEdit || r.result !== '不符'" aria-label="正確型號"></td>
          <td><input v-model="r.correct_serial" class="in" :disabled="!canEdit || r.result !== '不符'" aria-label="正確序號"></td>
          <td><input v-model="r.note" class="in" :disabled="!canEdit" aria-label="備註"></td>
        </tr>
        <tr><td colspan="11" class="sub"><b>櫃上有但沒登記</b></td></tr>
        <tr v-for="(e, i) in sheet.extras" :key="'x' + i">
          <td class="mono">新增{{ i + 1 }}</td><td><input v-model="e.u" class="in s" placeholder="U" :disabled="!canEdit" aria-label="U"></td><td><input v-model="e.model" class="in" placeholder="機型" :disabled="!canEdit" aria-label="機型"></td><td />
          <td><input v-model="e.serial" class="in" placeholder="序號" :disabled="!canEdit" aria-label="序號"></td><td colspan="5" /><td><input v-model="e.note" class="in" :disabled="!canEdit" aria-label="備註"></td>
        </tr>
      </tbody>
    </table>
    <p v-if="sheet?.history?.length" class="muted small">歷史：
      <span v-for="h in sheet.history" :key="h.id">第 {{ h.version }} 版 {{ h.source === 'xlsx' ? '上傳' : '網頁' }} {{ h.counted_by }} {{ String(h.submitted_at).slice(0, 16) }}{{ h.superseded ? '（已取代）' : '（現行）' }}　</span>
    </p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; cursor: default; }
select, .in { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.in { width: 130px; } .in.s { width: 60px; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t :deep(th) { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.t td { padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.t tr.ex td { background: var(--warn-soft); }
.t td.sub { background: var(--sub); }
.mono { font-family: var(--disp); white-space: nowrap; } .small { font-size: 12px; }
.seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: 8px; overflow: hidden; }
.seg button { font-family: inherit; font-size: 12px; padding: 3px 10px; border: 0; background: var(--card); color: var(--ink-aux); cursor: pointer; }
.seg button.on { background: var(--brand); color: var(--type-on); }
.seg button:disabled { cursor: default; opacity: .6; }
.muted { color: var(--muted); font-size: 13px; } .small { font-size: 12px; }
.err { color: var(--bad); }
</style>
