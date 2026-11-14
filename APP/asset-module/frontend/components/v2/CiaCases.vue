<script setup lang="ts">
// CIA 第三批共用案件清單（2.061.x）：同機多筆（9.3）、機櫃編號（9.3）、實體機補登（9.4）共用。
// 卡片＝/api/cia-case/summary，下鑽＝/api/cia-case/items（同一份計算，verify_numbers 釘住）。條件放網址（cst＝狀態、room＝機房），可分享。
// 項次＝案件編號（固定，不隨篩選或排序改變）。確認的內容各類不同，放在 detail 插槽（選了一案才出現）。
// 重新比對、確認／撤回／例外、交出＝管理員（後端 ADMIN_WRITE），非管理員看不到這些按鈕（審查 P3-11）。
// categories＝分類篩選（網址 cat；同機多筆預設只看疑似真重複，cat=all 看全部）；rooms＝機房篩選，交出要先選一個機房（審查 P3-10）。
interface Col { k: string; label: string; mono?: boolean; link?: (r: any) => string | null; text?: (r: any) => any }
const props = withDefaults(defineProps<{
  kind: string; prefix: string; tab: string; page: string
  extraCols?: Col[]; mapRow?: (c: any) => Record<string, any>
  scanPath: string; handoffPath?: string; handoffLabel?: string
  bulkConfirm?: boolean; rooms?: string[] | null
  categories?: { k: string; label: string }[] | null; defaultCategory?: string
}>(), { extraCols: () => [], mapRow: undefined, handoffPath: '', handoffLabel: '交出已確認的', bulkConfirm: false, rooms: null, categories: null, defaultCategory: '' })
const emit = defineEmits<{ handed: [r: any] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isAdmin } = useAuth()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const n = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString())
// 保留網址上其他條件（例：機櫃編號的子頁 rv），只換這裡管的 cst／room
const u = (extra: Record<string, string | undefined>): string => {
  const base = Object.fromEntries(Object.entries(route.query).filter(([, v]) => typeof v === 'string')) as Record<string, string | undefined>
  return `${props.page}?` + new URLSearchParams(Object.entries({ ...base, tab: props.tab, cst: undefined, ...extra }).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()
}

const sum = ref<any>(null); const items = ref<any[] | null>(null); const err = ref('')
const st = computed(() => q('cst') || 'open_all')
const cat = computed(() => (props.categories ? (q('cat') || props.defaultCategory) : ''))
async function load() {
  try {
    // 卡片跟清單用同一組條件（機房、分類）：卡片數字＝下面的列數
    const f: Record<string, string> = { kind: props.kind }
    if (q('room')) f.room = q('room')
    if (cat.value && cat.value !== 'all') f.category = cat.value
    const [s, r] = await Promise.all([apiFetch<any>('/api/cia-case/summary', { query: f }), apiFetch<any>('/api/cia-case/items', { query: { ...f, status: st.value } })])
    sum.value = s; items.value = r.items; err.value = ''
  } catch (e: any) { err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
watch(() => [q('cst'), q('room'), cat.value], load)
defineExpose({ load })

const c = computed(() => sum.value?.counts ?? null)
const CARDS = computed(() => [
  { k: 'open_all', label: '未結案', tone: '' }, { k: 'open', label: '待確認', tone: '' }, { k: 'confirmed', label: '已確認', tone: '' },
  { k: 'handed', label: '已交出', tone: '' }, { k: 'closed', label: '已結案', tone: 'good' }, { k: 'exception', label: '例外', tone: 'mute' },
  { k: 'resolved', label: '已不存在', tone: 'mute' },
].map(x => ({ ...x, v: c.value?.[x.k], to: u({ cst: x.k === 'open_all' ? undefined : x.k, room: q('room') || undefined }), on: st.value === x.k })))

const rows = computed(() => (items.value ?? []).map((x: any) => ({
  ...x, ...(props.mapRow ? props.mapRow(x) : {}), __no: x.id,
  first: (x.first_seen || '').slice(0, 10), last: (x.last_seen || '').slice(0, 10), exc: x.exception_until ?? '—',
  handed: x.handed_ref ? x.handed_ref.replace('cia_fix:', '第 ') + ' 批' : '—',
})))
const cols = computed<Col[]>(() => [
  { k: 'title', label: '名稱' }, ...props.extraCols,
  { k: 'status_label', label: '狀態' }, { k: 'first', label: '第一次發現', mono: true }, { k: 'last', label: '最近一次比對', mono: true },
  { k: 'handed', label: '交出到', link: (r: any) => (r.handed_ref ? `/v2/ciafix?tab=batches&batch=${r.handed_ref.replace('cia_fix:', '')}` : null) },
  { k: 'exc', label: '例外到期', mono: true }, { k: 'pick', label: '' },
])

// ---- 一案的明細（插槽）＋事件
const cur = ref<any>(null); const ev = ref<any[]>([])
async function open(r: any) {
  cur.value = (items.value ?? []).find((x: any) => x.id === r.id) ?? null
  try {
    ev.value = (await apiFetch<any>(`/api/cia-case/items/${r.id}/events`)).items.map((e: any, i: number) => ({ ...e, __no: i + 1, at2: (e.at || '').slice(0, 19) }))
  } catch (e: any) { showToast(`紀錄載入失敗：${errMsg(e)}`, 'error') }
}
const evCols = [{ k: 'at2', label: '時間', mono: true }, { k: 'action_label', label: '動作' }, { k: 'to_label', label: '變成' }, { k: 'by', label: '誰' }, { k: 'note', label: '原因' }]

// ---- 批次動作（撤回／例外／取消例外；確認依類別）
const picked = ref<Record<string, boolean>>({})
const pickedIds = computed(() => Object.entries(picked.value).filter(([, v]) => v).map(([k]) => Number(k)))
const act = ref(props.bulkConfirm ? 'confirm' : 'withdraw')
const ACT_OK: Record<string, string[]> = { confirm: ['open'], withdraw: ['confirmed'], except: ['open', 'confirmed'], unexcept: ['exception'] }
const ACT_LABEL: Record<string, string> = { confirm: '確認', withdraw: '撤回確認', except: '標例外（不處理）', unexcept: '取消例外' }
const canPick = (r: any) => ACT_OK[act.value]?.includes(r.status)
watch(act, () => { picked.value = {} })
const note = ref(''); const until = ref(''); const busy = ref(false)
async function apply() {
  const ids = pickedIds.value
  if (!ids.length) return
  if (act.value !== 'confirm' && !note.value.trim()) { showToast('要填原因', 'error'); return }
  if (!window.confirm(`${ACT_LABEL[act.value]}：${ids.length} 案？每一案都會留紀錄。`)) return
  busy.value = true
  try {
    const r = await guardedFetch<any>('/api/cia-case/act', { method: 'POST', body: { kind: props.kind, ids, action: act.value, note: note.value || null, until: until.value || null } })
    showToast(`已處理 ${r.updated} 案${r.skipped?.length ? `，${r.skipped.length} 案沒處理（${r.skipped[0].why}）` : ''}`, r.skipped?.length ? 'warn' : 'success')
    picked.value = {}; note.value = ''
    await load()
  } catch (e: any) { showToast(`處理失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function refreshCur() { await load(); if (cur.value) await open(cur.value) }

const scanBusy = ref(false)
async function scan() {
  scanBusy.value = true
  try {
    const r = await guardedFetch<any>(props.scanPath, { method: 'POST', body: {} })
    showToast(`比對完成：新案 ${r.found}、再次出現 ${r.reappear}、已不存在 ${r.gone}`, 'success')
    await load()
  } catch (e: any) { showToast(`比對失敗：${errMsg(e)}`, 'error') } finally { scanBusy.value = false }
}
const needRoom = computed(() => !!props.rooms && !q('room'))
const hoBusy = ref(false)
async function handoff() {
  if (!c.value?.confirmed || needRoom.value) return
  if (!window.confirm(`${props.handoffLabel}：${c.value.confirmed} 案，產生一個 9.3 修正包批次？（只記錄建議，不改任何資產資料）`)) return
  hoBusy.value = true
  try {
    const r = await guardedFetch<any>(props.handoffPath, { method: 'POST', body: { room: q('room') || null } })
    if (r.batch_id) showToast(`已產生第 ${r.batch_id} 批：${r.cases} 案、${r.items} 筆${r.skipped?.length ? `；${r.skipped.length} 案沒交（${r.skipped[0].why}）` : ''}`, r.skipped?.length ? 'warn' : 'success', 10000)
    else showToast(`沒有交出${r.skipped?.length ? `：${r.skipped[0].why}` : ''}`, 'warn', 10000)
    await load(); emit('handed', r)
  } catch (e: any) { showToast(`交出失敗：${errMsg(e)}`, 'error') } finally { hoBusy.value = false }
}
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="bar">
      <span class="muted">上次比對 {{ sum?.last_scan?.at ? String(sum.last_scan.at).slice(0, 16) : '—' }}{{ sum?.last_scan?.by ? `（${sum.last_scan.by}）` : '' }}</span>
      <select v-if="rooms" :value="q('room')" aria-label="機房" @change="router.replace(u({ cst: q('cst') || undefined, room: ($event.target as HTMLSelectElement).value || undefined }))">
        <option value="">全部機房</option><option v-for="r in rooms" :key="r" :value="r">{{ r }}</option></select>
      <select v-if="categories" :value="cat" aria-label="分類" @change="router.replace(u({ cst: q('cst') || undefined, cat: ($event.target as HTMLSelectElement).value }))">
        <option v-for="x in categories" :key="x.k" :value="x.k">{{ x.label }}</option><option value="all">全部分類</option></select>
      <span class="sp" />
      <button v-if="isAdmin" type="button" class="btn ghost" :disabled="scanBusy" @click="scan">{{ scanBusy ? '比對中…' : '重新比對' }}</button>
      <button v-if="isAdmin && handoffPath" type="button" class="btn" :disabled="hoBusy || !c?.confirmed || needRoom" :title="needRoom ? '先選一個機房（每批只收一個機房）' : ''" @click="handoff">{{ hoBusy ? '交出中…' : needRoom ? '先選機房再交出' : `${handoffLabel}（${n(c?.confirmed)}）` }}</button>
      <slot name="note" />
    </div>
    <div v-if="c" class="cards">
      <NuxtLink v-for="k in CARDS" :key="k.k" class="kc" :class="[{ on: k.on }, k.tone]" :to="k.to">
        <span class="kl">{{ k.label }}</span><b class="kv mono">{{ n(k.v) }}<small> 案</small></b>
      </NuxtLink>
    </div>
    <form v-if="isAdmin" class="actbar" @submit.prevent="apply">
      <span>勾選 <b>{{ pickedIds.length }}</b> 案，</span>
      <select v-model="act" aria-label="動作">
        <option v-if="bulkConfirm" value="confirm">確認</option>
        <option value="withdraw">撤回確認</option><option value="except">標例外（不處理）</option><option value="unexcept">取消例外</option>
      </select>
      <input v-model="note" class="note" :placeholder="act === 'confirm' ? '備註（選填）' : '原因（必填，稽核會留）'" aria-label="原因">
      <input v-if="act === 'except'" v-model="until" type="date" class="inp" aria-label="例外到期日">
      <button type="submit" class="btn" :disabled="busy || !pickedIds.length">{{ busy ? '處理中…' : '套用' }}</button>
      <InfoNote>撤回＝已確認的回到待確認（舊的確認紀錄留著，另加一筆撤回紀錄）。例外＝確認不是問題或不處理，要寫原因；到期日沒填＝半年後，最多兩年，到期會出現在「未結案總覽」的例外到期要重審。已交出的要改，請在 9.3 修正包撤回那一批的列。</InfoNote>
    </form>
    <V2Table v-model:selected="picked" :selectable="isAdmin" :can-select="canPick" :rows="rows" :cols="cols" :prefix="prefix" row-key="id" unit="案" :max="200" empty="沒有符合的案件（按「重新比對」找最新的）">
      <template #cell-pick="{ row }"><button type="button" class="mini" @click="open(row)">{{ cur?.id === (row as any).id ? '看中' : '看明細' }}</button></template>
    </V2Table>
    <div v-if="cur" class="evbox">
      <div class="bar"><b>{{ prefix }}{{ cur.id }}　{{ cur.title }}</b><span class="muted">{{ cur.status_label }}</span><span class="sp" />
        <button type="button" class="mini" @click="cur = null">關閉</button></div>
      <slot name="detail" :case="cur" :reload="refreshCur" />
      <h4 class="h4">紀錄</h4>
      <V2Table :rows="ev" :cols="evCols" prefix="E" row-key="id" unit="筆紀錄" empty="沒有紀錄" />
    </div>
  </div>
</template>

<style scoped>
.mono { font-family: var(--disp); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.h4 { margin: 12px 0 6px; font-size: 14px; }
.cards { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; margin-bottom: 8px; }
@media (max-width: 900px) { .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.kc { display: flex; flex-direction: column; gap: 2px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; color: var(--ink); text-decoration: none; }
.kc:hover, .kc.on { border-color: var(--brand); }
.kl { font-size: 12px; color: var(--ink-soft); }
.kv { font-size: 22px; color: var(--brand-dark); }
.kv small { font-size: 12px; color: var(--muted); }
.kc.good .kv { color: var(--good); } .kc.mute .kv { color: var(--ink-aux); }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.bar select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.sp { flex: 1; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:hover:not(:disabled) { background: var(--brand-hover); }
.btn:disabled { opacity: .6; cursor: default; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
.mini { font-family: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.actbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.actbar select, .actbar .note, .inp { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.actbar .note { width: 260px; max-width: 100%; }
.evbox { margin-top: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--sub); }
</style>
