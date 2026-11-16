<script setup lang="ts">
// MICS 檢查清單（V23，2.047.0 V2 原生；取代舊 pages/blast/checklist/[id].vue）。同一批 API：
// GET /api/blast/snapshot/{id}、GET /api/blast/checklist/{id}、PUT /api/blast/checklist/item/{id}（狀態、備註就地存）。
// 一列＝一台機器 × 一位聯絡人，掛在一份凍結的快照底下；全隊登入看同一份，按「重新整理」看別人的更新（沒有即時推播）。
// 兩種單位分開寫：上排「以系統為單位」（整套都還沒動／處理中／整套處理完，點了只看那些系統的列）、
// 下排「以列為單位」各狀態（點了只看那個狀態）。預設照後端的優先序（嚴重度優先、隔越近越前面），點表頭才改排序。
interface Item { id: number; snapshot_id: number; asset_serial: string | null; hostname: string | null; ip: string | null; environment: string | null; physical_location: string | null; biz_system: string | null; severity: string | null; sort_depth: number | null; contact_name: string | null; contact_role: string | null; contact_department: string | null; contact_phone: string | null; status: string; note: string | null; updated_by: string | null; updated_at: string | null }
interface Snap { id: number; node_id: string; mode: string; reason: string | null; asked_by: string; asked_at: string; result: { label: string }; vm_export?: { exported_at: string | null; days: number | null; days_at: number | null; stale: boolean | null; stale_days: number } }
const props = defineProps<{ id: number }>()
const STATUS_OPTIONS = ['未聯絡', '聯絡中', '已確認正常', '已確認異常', '聯絡不到']
const TERMINAL = ['已確認正常', '已確認異常', '聯絡不到']
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const snapshot = ref<Snap | null>(null)
const items = ref<Item[]>([])
const loading = ref(false)
const err = ref('')
const saving = ref<Set<number>>(new Set())
async function load() {
  loading.value = true
  try {
    const [s, l] = await Promise.all([apiFetch<Snap>(`/api/blast/snapshot/${props.id}`), apiFetch<Item[]>(`/api/blast/checklist/${props.id}`)])
    snapshot.value = s
    items.value = l
    err.value = ''
  } catch (e: any) { err.value = `檢查清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
watch(() => props.id, load, { immediate: true })
async function saveItem(it: Item, patch: { status?: string; note?: string }) {
  saving.value.add(it.id)
  try {
    const u = await guardedFetch<Item>(`/api/blast/checklist/item/${it.id}`, { method: 'PUT', body: patch })
    const i = items.value.findIndex(x => x.id === it.id)
    if (i !== -1) items.value[i] = u
  } catch (e: any) { showToast(`存檔失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}，請重試`, 'error') } finally { saving.value.delete(it.id) }
}
function onNoteBlur(it: Item, note: string) { if (note !== (it.note || '')) saveItem(it, { note }) }      // 離開欄位才存，不是每個字都打 API
const backLink = computed(() => {
  const nid = snapshot.value?.node_id || ''
  if (nid.startsWith('bizsys:')) return `/v2/system/${encodeURIComponent(nid.slice(7))}`
  if (nid.startsWith('hw:')) return `/v2/machine/${encodeURIComponent(nid.slice(3))}?tab=impact`
  return `/v2/incident?tab=impact${nid ? '&iq=' + encodeURIComponent(nid) : ''}`
})
// ---- 統計（兩種單位）＋篩選
const bySystem = computed(() => {
  const m = new Map<string, Item[]>()
  for (const it of items.value) { const k = it.biz_system || '（未歸類）'; if (!m.has(k)) m.set(k, []); m.get(k)!.push(it) }
  const sysState: Record<string, 'todo' | 'doing' | 'done'> = {}
  for (const [k, rows] of m) sysState[k] = rows.every(r => r.status === '未聯絡') ? 'todo' : rows.every(r => TERMINAL.includes(r.status)) ? 'done' : 'doing'
  return sysState
})
const sysCount = computed(() => {
  const c = { total: 0, todo: 0, doing: 0, done: 0 }
  for (const v of Object.values(bySystem.value)) { c.total++; c[v]++ }
  return c
})
const rowCount = computed(() => Object.fromEntries(STATUS_OPTIONS.map(s => [s, items.value.filter(i => i.status === s).length])))
const qs = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const fSys = computed(() => qs('cs'))
const fStatus = computed(() => qs('cst'))
const setQ = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } })
const rows = computed(() => items.value.map((it, i) => ({
  ...it, __no: i + 1, host: it.hostname || it.asset_serial || '', where: [it.environment, it.physical_location].filter(Boolean).join(' · '),
  contact: it.contact_name ? it.contact_name + (it.contact_role ? ` · ${it.contact_role}` : '') : '查無聯絡人',
  phone: it.contact_phone || '⚠ 查無可用電話', upd: it.updated_by ? `${it.updated_by} ${it.updated_at}` : '',
})).filter(r => (!fStatus.value || r.status === fStatus.value) && (!fSys.value || bySystem.value[r.biz_system || '（未歸類）'] === fSys.value)))
const cardFor = ref<Item | null>(null)
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else>
      <p v-if="snapshot" class="note">針對 <b>{{ snapshot.result.label }}</b>（{{ snapshot.node_id }}）於 {{ snapshot.asked_at }} 由 {{ snapshot.asked_by }} 存證<span v-if="snapshot.reason">（{{ snapshot.reason }}）</span>
        <InfoNote>這份清單所有登入的人都看得到同一份、改了就地存——分派給每個人負責幾列，開始打電話確認。按「重新整理」看別人的更新。</InfoNote></p>
      <p v-if="snapshot?.vm_export" class="note" :class="{ warn: snapshot.vm_export.stale !== false }">
        <template v-if="snapshot.vm_export.exported_at">VM 位置資料：vCenter 匯出 <b class="mono">{{ snapshot.vm_export.exported_at }}</b>（存證當時 {{ snapshot.vm_export.days_at ?? '?' }} 天前，到今天 {{ snapshot.vm_export.days }} 天）</template>
        <template v-else>VM 位置資料：<b>匯出日期不明</b></template>
        <span v-if="snapshot.vm_export.stale !== false">　VM 可能已被搬到別台 ESXi，這份清單的 VM 可能不準——請重新匯出 RVTools 再匯入、重查一次。</span>
      </p>
      <div class="bar">
        <span class="unit">以「系統」為單位　共 {{ sysCount.total }} 套</span>
        <button type="button" class="chip" :class="{ on: !fSys }" @click="setQ({ cs: undefined, cst: undefined })">全部系統 {{ sysCount.total }}</button>
        <button type="button" class="chip" :class="{ on: fSys === 'todo' }" @click="setQ({ cs: 'todo', cst: undefined })">整套都還沒動 {{ sysCount.todo }}</button>
        <button type="button" class="chip" :class="{ on: fSys === 'doing' }" @click="setQ({ cs: 'doing', cst: undefined })">處理中 {{ sysCount.doing }}</button>
        <button type="button" class="chip" :class="{ on: fSys === 'done' }" @click="setQ({ cs: 'done', cst: undefined })">整套處理完 {{ sysCount.done }}</button>
        <InfoNote>一套系統要底下每一列都有結果（已確認正常／已確認異常／聯絡不到）才算整套處理完；只要有一列還沒動就不算。</InfoNote>
      </div>
      <div class="bar">
        <span class="unit">以「列」為單位（一台機器 × 一位聯絡人）　共 {{ items.length }} 列</span>
        <button type="button" class="chip" :class="{ on: !fStatus }" @click="setQ({ cst: undefined, cs: undefined })">全部 {{ items.length }}</button>
        <button v-for="s in STATUS_OPTIONS" :key="s" type="button" class="chip" :class="{ on: fStatus === s }" @click="setQ({ cst: s, cs: undefined })">{{ s }} {{ rowCount[s] }}</button>
        <NuxtLink class="btn2" :to="backLink">‹ 回查詢結果</NuxtLink>
        <button type="button" class="btn2" :disabled="loading" @click="load">{{ loading ? '載入中…' : '重新整理' }}</button>
      </div>
      <V2Table :rows="rows" prefix="" row-key="id" :export-name="`MICS檢查清單_${id}`"
               :empty="loading ? '載入中…' : (items.length ? '這個條件沒有列' : '這份快照沒有任何受影響資產')"
               :cols="[{ k: 'severity', label: '嚴重度', tone: (r: any) => (r.severity === '重大' ? 'prod' : '') }, { k: 'biz_system', label: '業務系統' },
                       { k: 'host', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'ip', label: 'IP', mono: true },
                       { k: 'where', label: '環境・機房' }, { k: 'contact', label: '聯絡人' }, { k: 'contact_department', label: '部門' }, { k: 'phone', label: '電話', mono: true },
                       { k: 'status', label: '狀態' }, { k: 'note', label: '備註' }, { k: 'upd', label: '更新' }, { k: 'card', label: '維護／值班', text: () => '' }]">
        <template #cell-status="{ row }">
          <select class="sel" :value="row.status" :disabled="isViewer || saving.has(row.id)" aria-label="狀態" @change="saveItem(row, { status: ($event.target as HTMLSelectElement).value })">
            <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
          </select>
        </template>
        <template #cell-note="{ row }">
          <input class="in" type="text" :value="row.note || ''" placeholder="備註…" :disabled="isViewer" aria-label="備註" @blur="onNoteBlur(row, ($event.target as HTMLInputElement).value)">
        </template>
        <template #cell-card="{ row }">
          <button v-if="row.asset_serial" type="button" class="lb" @click="cardFor = cardFor?.id === row.id ? null : row">{{ cardFor?.id === row.id ? '收合' : '看' }}</button>
        </template>
      </V2Table>
      <div v-if="cardFor?.asset_serial" class="box">
        <h3 class="h3">{{ cardFor.hostname || cardFor.asset_serial }} 的維護與值班<button type="button" class="lb" @click="cardFor = null">關閉</button></h3>
        <ContactCard :serial="cardFor.asset_serial" compact />
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.unit { font-size: 12px; color: var(--ink-soft); }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; text-decoration: none; color: var(--ink); }
.btn2:disabled { opacity: .5; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; }
.sel, .in { font-family: inherit; font-size: 12px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.in { min-width: 140px; }
.box { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.h3 { margin: 0 0 8px; font-size: 15px; display: flex; gap: 8px; align-items: center; }
.note { font-size: 13px; color: var(--ink-soft); margin: 4px 0 8px; }
.note.warn { color: var(--warn-text); }
.mono { font-family: var(--disp); }
.err { color: var(--bad); }
</style>
