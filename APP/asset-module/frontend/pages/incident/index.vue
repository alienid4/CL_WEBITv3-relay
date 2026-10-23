<script setup lang="ts">
// 1-6 事件應變（今日群最後一項；2026-10-23 板橋 IDC UPS 電力切換異常後做）：開事件、事件清單（進行中在前）。
// 一個事件裡面：貼告警找關聯 → 範圍 → 測一輪 → 共用檢查清單 → 回報文字 → 時間軸／結案。
interface Ev { id: number; type: string; title: string; started_at: string; status: string; created_by: string | null; created_at: string; closed_at: string | null }
const { apiFetch } = useApi()
const { showToast } = useToast()
const TYPES = ['電力', '網路', '主機', '其他']
const events = ref<Ev[]>([])
async function load() {
  try { events.value = await apiFetch<Ev[]>('/api/incidents') } catch (e: any) {
    showToast(`事件清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  }
}
await load()
function nowLocal() {
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}
const f = reactive({ type: '電力', title: '', started_at: nowLocal(), note: '' })
const saving = ref(false)
async function create() {
  if (!f.title.trim()) { showToast('要寫標題（例：板橋 UPS 電力切換異常）', 'warn'); return }
  saving.value = true
  try {
    const ev = await apiFetch<Ev>('/api/incidents', { method: 'POST', body: { ...f } })
    showToast('事件已開', 'success')
    await navigateTo(`/incident/${ev.id}`)
  } catch (e: any) {
    showToast(`開事件失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { saving.value = false }
}
const filter = ref<'all' | 'open' | 'closed'>('all')
const rows = computed(() => events.value.map((e, i) => ({ ...e, no: i + 1, st: e.status === 'open' ? '進行中' : '已結案' }))
  .filter(e => filter.value === 'all' || e.status === filter.value))
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
const nOpen = computed(() => events.value.filter(e => e.status === 'open').length)
</script>

<template>
  <div class="incpage">
    <div class="head">
      <div class="ey">TODAY · 事件應變</div>
      <h1>事件應變</h1>
      <InfoNote>出事時用：開一個事件 → 貼告警（IP／主機名）找出共同點（同機房／排／櫃／ESXi）→ 一鍵變成範圍 → 系統列出受影響機器、VM、系統、聯絡人 → 測一輪通不通 → 全隊在同一份檢查清單上改狀態 → 一鍵產生回報文字。v2 規劃：告警自動開事件＋通知值班（目前沒有，要人來開）。機櫃→UPS／PDU 對應需要先匯入配電表，v1 不支援。</InfoNote>
    </div>
    <div class="glass newev">
      <b>開事件</b>
      <label>類型 <select v-model="f.type"><option v-for="t in TYPES" :key="t">{{ t }}</option></select></label>
      <input v-model="f.title" class="kw" placeholder="標題，例：板橋 IDC UPS 電力切換異常">
      <label>開始 <input v-model="f.started_at" type="datetime-local"></label>
      <input v-model="f.note" class="kw sm" placeholder="備註（可空）">
      <button class="tb go" :disabled="saving" @click="create">{{ saving ? '開事件中…' : '開事件' }}</button>
    </div>
    <div class="chips">
      <button class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ events.length }}</button>
      <button class="chip" :class="{ on: filter === 'open' }" @click="filter = 'open'">進行中 {{ nOpen }}</button>
      <button class="chip" :class="{ on: filter === 'closed' }" @click="filter = 'closed'">已結案 {{ events.length - nOpen }}</button>
    </div>
    <table class="tbl">
      <thead><tr>
        <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
        <SortTh k="st" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
        <SortTh k="type" :active="sortKey" :dir="sortDir" @sort="toggle">類型</SortTh>
        <SortTh k="title" :active="sortKey" :dir="sortDir" @sort="toggle">標題</SortTh>
        <SortTh k="started_at" :active="sortKey" :dir="sortDir" @sort="toggle">開始</SortTh>
        <SortTh k="created_by" :active="sortKey" :dir="sortDir" @sort="toggle">開的人</SortTh>
        <SortTh k="closed_at" :active="sortKey" :dir="sortDir" @sort="toggle">結案</SortTh>
      </tr></thead>
      <tbody>
        <tr v-for="e in sorted" :key="e.id">
          <td class="mono">E{{ e.no }}</td>
          <td><span class="st" :class="{ open: e.status === 'open' }">{{ e.st }}</span></td>
          <td>{{ e.type }}</td>
          <td><NuxtLink :to="`/incident/${e.id}`" class="lnk">{{ e.title }}</NuxtLink></td>
          <td class="mono">{{ e.started_at.slice(0, 16) }}</td>
          <td>{{ e.created_by || '—' }}</td>
          <td class="mono">{{ e.closed_at ? e.closed_at.slice(0, 16) : '—' }}</td>
        </tr>
        <tr v-if="!sorted.length"><td colspan="7" class="muted">沒有事件</td></tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.incpage { font-family: 'Microsoft JhengHei', sans-serif; }
.head { margin-bottom: 12px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 0; color: var(--ink); display: inline-block; margin-right: 8px; }
.newev { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 14px; margin-bottom: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); font-size: 13px; }
.kw { font-family: inherit; font-size: 13px; padding: 7px 10px; min-width: 300px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.kw.sm { min-width: 180px; }
select, input[type=datetime-local] { font-family: inherit; font-size: 13px; padding: 5px 6px; }
.tb { font-family: inherit; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 9px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-soft); cursor: pointer; }
.tb.go { background: var(--brand); color: var(--card); border-color: var(--brand); }
.tb:disabled { opacity: .55; cursor: progress; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.chip { font-family: inherit; font-size: 12.5px; padding: 4px 12px; border-radius: 20px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 7px 8px; border-bottom: 1px solid var(--border); background: var(--sub); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--line); color: var(--ink-soft); }
.st { font-size: 12px; padding: 1px 8px; border-radius: 10px; background: var(--sub); }
.st.open { background: var(--bad-soft); color: var(--bad); font-weight: 600; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); }
.lnk { color: var(--link); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
</style>
