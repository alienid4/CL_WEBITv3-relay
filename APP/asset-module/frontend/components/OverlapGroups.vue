<script setup lang="ts">
// 位置重疊待處理（2026-10-24）：一組一列（同櫃、U 位重疊連在一起的設備），附系統猜的原因與建議，
// 可以直接處理：合併為同一台（只影響畫面）／這筆已不在櫃上／位置更正到…／確認是兩台（半寬）；每筆都能復原。
// 4-1 的「位置重疊」頁籤（整個機房）與機櫃正面下方（這一櫃）共用。
interface Dev { key: string; name: string; ip: string | null; model: string | null; serial: string; systems: string[]; rack_raw: string | null; u: string; u_start: number; u_end: number; state: string | null }
interface Reloc { rack: string; diagram_rack: string; u_start: number | null; u_end: number | null; source: string; result: string }
interface Decision { id: number; action: string; action_label: string; note: string | null; decided_by: string; decided_at: string }
interface OvGroup { id: string; room: string; rack: string; u_lo: number; u_hi: number; keys: string[]; cause: string; cause_label: string; suggest: string; relocate: Record<string, Reloc>; decision: Decision | null; resolved: boolean; devices: Dev[] }
interface Done { id: number; room: string; rack: string; action: string; action_label: string; target_key: string | null; detail: any; note: string | null; decided_by: string; decided_at: string }

const props = defineProps<{ room: string; rack?: string | null; compact?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const err = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'

interface SameBox { room: string; rack: string; keys: string[]; head: string; reasons: string[]; auto: boolean; decision_id: number | null; devices: { key: string; name: string; ip: string | null; model: string | null; serial: string; hw_serials: string[]; u: string }[] }
const data = ref<{ groups: OvGroup[]; open: number; resolved: number; mgmt: any[]; done: Done[]; can_edit: boolean; same_box: SameBox[]; auto_merged: number; engineered?: { room: string; rack: string; keys: string[]; model: string | null; u: string; label: string; names: string[] }[] } | null>(null)
const showEng = ref(false)
const showSame = ref(false)
async function split(b: SameBox) {
  const why = window.prompt(`把這 ${b.keys.length} 筆拆開（其實是不同台）？請寫原因：`, '')
  if (why === null) return
  try {
    await guardedFetch('/api/racks/overlaps/decide', { method: 'POST', body: { room: b.room, rack: b.rack, keys: b.keys, action: 'split', note: why || null } })
    showToast('已拆開；之後系統不會再自動併這幾筆', 'success')
    await load()
    emit('changed')
  } catch (e: any) { showToast(`拆開失敗：${err(e)}`, 'error') }
}
async function load() {
  try {
    data.value = await apiFetch('/api/racks/overlaps', { query: { room: props.room, rack: props.rack || undefined } })
  } catch (e: any) { showToast(`位置重疊載入失敗：${err(e)}`, 'error') }
}
watch(() => [props.room, props.rack], load, { immediate: true })
defineExpose({ load })

const showResolved = ref(false)
const rows = computed(() => (data.value?.groups ?? []).map((g, i) => ({ ...g, no: i + 1, names: g.devices.map(d => d.name).join('、'), u_text: g.u_lo === g.u_hi ? `${g.u_lo}` : `${g.u_lo}-${g.u_hi}`, st: g.resolved ? '已處理' : '待處理' }))
  .filter(g => showResolved.value || !g.resolved))
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
const open = ref<string | null>(null)
const note = reactive<Record<string, string>>({})
const reloc = reactive<Record<string, { rack: string; u_start: number | null }>>({})
function relocOf(g: OvGroup, d: Dev) {
  const k = g.id + d.key
  if (!reloc[k]) {
    const s = g.relocate?.[d.key]
    reloc[k] = { rack: s?.rack ?? g.rack, u_start: s?.u_start ?? null }
  }
  return reloc[k]
}
const busy = ref(false)
async function decide(g: OvGroup, action: string, target?: Dev) {
  const body: any = { room: g.room, rack: g.rack, keys: g.keys, action, note: note[g.id] || null }
  if (target) body.target_key = target.key
  if (action === 'relocate' && target) body.relocate = relocOf(g, target)
  if (action === 'removed' && target && !window.confirm(`把 ${target.name} 標成「已不在櫃上」？（會進 CIA 待更正，可以復原）`)) return
  busy.value = true
  try {
    await guardedFetch('/api/racks/overlaps/decide', { method: 'POST', body })
    showToast('已記下', 'success')
    await load()
    emit('changed')
  } catch (e: any) { showToast(`處理失敗：${err(e)}`, 'error') } finally { busy.value = false }
}
async function undo(id: number) {
  if (!window.confirm('復原這筆處理？')) return
  try {
    const r = await guardedFetch<{ warning: string | null }>(`/api/racks/overlaps/${id}/undo`, { method: 'POST', body: {} })
    showToast(r.warning ? `已復原（${r.warning}）` : '已復原', r.warning ? 'warn' : 'success')
    await load()
    emit('changed')
  } catch (e: any) { showToast(`復原失敗：${err(e)}`, 'error') }
}
const CAUSE_CLASS: Record<string, string> = { partition: 'c-part', duplicate: 'c-dup', diagram: 'c-diag', other: 'c-other' }
</script>

<template>
  <div v-if="data && (data.groups.length || data.done.length || !compact)" class="ovq">
    <div class="chips">
      <b>位置重疊待處理 {{ data.open }} 組</b>
      <button class="chip" :class="{ on: showResolved }" @click="showResolved = !showResolved">顯示已處理 {{ data.resolved }}</button>
      <button v-if="data.same_box.length" class="chip" :class="{ on: showSame }" @click="showSame = !showSame">同一台 {{ data.same_box.length }} 組（系統判斷 {{ data.auto_merged }}）</button>
      <button v-if="data.engineered?.length" class="chip" :class="{ on: showEng }" @click="showEng = !showEng">同一套、不同節點 {{ data.engineered.length }} 組</button>
      <span v-if="data.mgmt.length" class="muted">另有 {{ data.mgmt.length }} 組是同一台的管理卡，已在畫面上併掉（不用處理）</span>
      <InfoNote>每一組是同櫃、U 位互相重疊的設備。系統依資料猜原因：同機型同 U 的 Power／AIX（或共用機身序號）＝同一台實體機的分割區；同主機名或同 IP＝重複登記；4-5 最近一次匯入的現場圖把其中一台放在別處＝現場圖說位置不同；其他＝待現場確認。處理方式：「合併為同一台」只影響畫面（不算重疊、面板併成一塊），台數、用電、CIA 都不動；「這筆已不在櫃上」「位置更正」走既有的機櫃狀態／位置更正，會進 CIA 待更正；「確認是兩台（半寬）」就不再提醒。每筆都留誰、何時、備註，可以復原。</InfoNote>
    </div>
    <table v-if="sorted.length" class="tbl">
      <thead><tr>
        <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
        <SortTh v-if="!rack" k="rack" :active="sortKey" :dir="sortDir" @sort="toggle">櫃</SortTh>
        <SortTh k="u_lo" :active="sortKey" :dir="sortDir" @sort="toggle">U</SortTh>
        <SortTh k="names" :active="sortKey" :dir="sortDir" @sort="toggle">設備</SortTh>
        <SortTh k="cause_label" :active="sortKey" :dir="sortDir" @sort="toggle">可能原因</SortTh>
        <SortTh k="st" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
      </tr></thead>
      <tbody>
        <template v-for="g in sorted" :key="g.id">
          <tr class="grow" :class="{ done: g.resolved }" @click="open = open === g.id ? null : g.id">
            <td class="mono">O{{ g.no }}</td>
            <td v-if="!rack">{{ g.rack }}</td>
            <td class="mono">{{ g.u_text }}</td>
            <td><button class="linkbtn">{{ g.names }}</button></td>
            <td><span class="cause" :class="CAUSE_CLASS[g.cause]">{{ g.cause_label }}</span><div class="muted small">{{ g.suggest }}</div></td>
            <td>{{ g.st }}<div v-if="g.decision" class="muted small">{{ g.decision.action_label }}（{{ g.decision.decided_by }} {{ g.decision.decided_at.slice(5, 16) }}）</div></td>
          </tr>
          <tr v-if="open === g.id" class="detail">
            <td :colspan="rack ? 5 : 6">
              <table class="tbl inner">
                <thead><tr><th>設備</th><th>IP</th><th>機型</th><th>資產序號</th><th>系統</th><th>CIA 機櫃欄</th><th>U</th><th v-if="data.can_edit && !g.decision">處理這台</th></tr></thead>
                <tbody>
                  <tr v-for="d in g.devices" :key="d.key">
                    <td><NuxtLink :to="`/assets/${encodeURIComponent(d.serial)}`" class="lnk">{{ d.name }}</NuxtLink><div v-if="d.state" class="muted small">{{ d.state }}</div></td>
                    <td class="mono small">{{ d.ip || '—' }}</td><td>{{ upText(d.model) || '—' }}</td><td class="mono small">{{ upText(d.serial) }}</td>
                    <td>{{ d.systems.join(', ') || '—' }}</td><td class="small">{{ d.rack_raw || '—' }}</td><td class="mono">{{ d.u }}</td>
                    <td v-if="data.can_edit && !g.decision" class="acts">
                      <div v-if="g.relocate?.[d.key]" class="muted small">現場圖：{{ g.relocate[d.key].diagram_rack }} {{ g.relocate[d.key].u_start }}-{{ g.relocate[d.key].u_end }}U（{{ g.relocate[d.key].source }}）</div>
                      <button class="tb sm" :disabled="busy" @click="decide(g, 'removed', d)">這筆已不在櫃上</button>
                      <span class="reloc">位置更正到 <input v-model="relocOf(g, d).rack" class="cell" placeholder="櫃"> <input v-model.number="relocOf(g, d).u_start" class="cell n" type="number" min="1" max="60" placeholder="起始U">
                        <button class="tb sm" :disabled="busy" @click="decide(g, 'relocate', d)">更正</button></span>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div v-if="data.can_edit" class="chips">
                <template v-if="!g.decision">
                  <input v-model="note[g.id]" class="note" placeholder="備註（位置更正必填，例：依 10-23 現場圖）">
                  <button class="tb sm" :class="{ go: g.cause === 'partition' }" :disabled="busy" @click="decide(g, 'merge')">合併為同一台</button>
                  <button class="tb sm" :disabled="busy" @click="decide(g, 'accept_half')">確認是兩台（半寬）</button>
                </template>
                <button v-else class="tb sm" @click="undo(g.decision.id)">復原「{{ g.decision.action_label }}」</button>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <p v-else class="muted">{{ data.groups.length ? '全部處理完了' : '沒有位置重疊' }}</p>
    <div v-if="showSame && data.same_box.length" class="donebox">
      <b>畫成同一台的（只影響畫面與用電只算一次；台數、CIA 不動；CIA 待更正會列「建議合併」）</b>
      <div v-for="(b, i) in data.same_box" :key="b.rack + b.head" class="small">
        S{{ i + 1 }}　{{ b.rack }} {{ b.devices[0]?.u }}　{{ b.auto ? '系統判斷為同一台' : '人工合併' }}（{{ b.reasons.join('、') }}）：
        {{ b.devices.map(d => `${d.name}${d.serial ? '／' + d.serial : ''}`).join('、') }}
        <template v-if="data.can_edit">
          <button v-if="b.auto" class="linkbtn" @click="split(b)">拆開（其實是不同台）</button>
          <button v-else-if="b.decision_id" class="linkbtn" @click="undo(b.decision_id)">復原合併</button>
        </template>
      </div>
    </div>
    <div v-if="showEng && data.engineered?.length" class="donebox">
      <b>同一套、不同節點（ODA／Exadata 等工程系統：整套登記在同一段 U，各節點各算一台，不列位置重疊）</b>
      <div v-for="(g, i) in data.engineered" :key="g.rack + g.u + i" class="small">G{{ i + 1 }}　{{ g.rack }} {{ g.u }}　{{ g.model }}：{{ g.names.join('、') }}</div>
    </div>
    <div v-if="data.done.length" class="donebox">
      <b>已處理（這筆已不在櫃上／位置更正）{{ data.done.length }} 筆</b>
      <div v-for="(d, i) in data.done" :key="d.id" class="small">
        D{{ i + 1 }}　{{ d.rack }}　{{ d.detail?.device?.name }}：{{ d.action_label }}{{ d.detail?.relocate ? ` → ${d.detail.relocate.rack} ${d.detail.relocate.u_start}U` : '' }}
        <span class="muted">（{{ d.decided_by }} {{ d.decided_at.slice(5, 16) }}{{ d.note ? '；' + d.note : '' }}）</span>
        <button v-if="data.can_edit" class="linkbtn" @click="undo(d.id)">復原</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ovq { margin-top: 12px; font-size: 13px; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 6px 0; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 6px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 6px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); vertical-align: top; }
.tbl.inner { margin: 4px 0 8px; }
.grow { cursor: pointer; }
.grow.done td { color: var(--muted); }
.detail > td { background: var(--sub); }
.cause { display: inline-block; font-size: 11.5px; padding: 1px 8px; border-radius: 9px; background: var(--card); border: 1px solid var(--border-strong); }
.cause.c-part { border-color: var(--brand); color: var(--brand-dark); }
.cause.c-dup { border-color: var(--warn); color: var(--warn-text); }
.cause.c-diag { border-color: var(--bad); color: var(--bad); }
.acts { min-width: 260px; }
.reloc { display: inline-flex; gap: 4px; align-items: center; margin-left: 4px; }
.cell { font-family: inherit; font-size: 12px; padding: 2px 5px; width: 64px; border: 1px solid var(--border-strong); border-radius: 4px; }
.cell.n { width: 56px; }
.note { font-family: inherit; font-size: 12px; padding: 3px 8px; width: 280px; border: 1px solid var(--border-strong); border-radius: 6px; }
.tb { font-family: inherit; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; margin: 2px 4px 2px 0; }
.tb.go { background: var(--brand); color: var(--card); border-color: var(--brand); }
.tb:disabled { opacity: .55; cursor: progress; }
.donebox { margin-top: 10px; padding: 8px 10px; border: 1px dashed var(--border-strong); border-radius: 8px; }
.mono { font-family: var(--disp); }
.small { font-size: 11.5px; }
.muted { color: var(--muted); }
.lnk { color: var(--link); text-decoration: none; }
.linkbtn { background: none; border: 0; padding: 0 0 0 6px; color: var(--link); cursor: pointer; font-family: inherit; font-size: inherit; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
</style>
