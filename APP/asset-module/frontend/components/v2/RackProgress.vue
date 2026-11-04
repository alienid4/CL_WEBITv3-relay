<script setup lang="ts">
// 7.1 機櫃盤點「各櫃進度」（2.043.4，舊版 /rack-audit 的「各櫃進度」頁籤搬過來，V68）：
// 一次盤點裡每一櫃盤了沒、在／不在／不符／沒登記、差異未處理、誰哪天盤的、第幾版、上傳還是網頁。
// 資料＝既有的 /api/rack-audits/{id}（只讀；跟「網頁填寫」同一支），這裡不另算。
import { rackDiffQuery } from '~/utils/rackDiffFilter'
const props = defineProps<{ taskId: number }>()
const { apiFetch } = useApi()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const racks = ref<any[]>([])
const err = ref('')
const loaded = ref(false)
async function load() {
  try { const d = await apiFetch<any>(`/api/rack-audits/${props.taskId}`); racks.value = d.racks ?? []; err.value = ''; loaded.value = true } catch (e: any) { err.value = `各櫃進度載入失敗：${errMsg(e)}` }
}
watch(() => props.taskId, load, { immediate: true })
const filter = ref<'all' | 'done' | 'todo'>('all')
const all = computed(() => racks.value.map((r: any, i: number) => ({
  ...r, __no: i + 1, name: `${r.room} ${r.rack}`, state: r.done ? `已盤${r.source ? (r.source === 'xlsx' ? '（上傳）' : '（網頁）') : ''}` : '未盤',
  ok: r.done ? (r.result_counts?.['在'] ?? 0) : '—', gone: r.done ? (r.result_counts?.['不在'] ?? 0) : '—', bad: r.done ? (r.result_counts?.['不符'] ?? 0) : '—',
  extra: r.done ? (r.extra_count ?? 0) : '—', open: r.diff_open ?? 0, by: r.counted_by || '—', on: r.counted_on || '—', ver: r.version || '—' })))
const rows = computed(() => all.value.filter((r: any) => filter.value === 'all' || (filter.value === 'done') === !!r.done))
const nDone = computed(() => all.value.filter((r: any) => r.done).length)
const t = computed(() => String(props.taskId))
// 機櫃名＝帶這櫃去「網頁填寫」並預選；差異數字＝帶這櫃＋這一類去「差異處理」（GPT 審查 4／5）
const qs = (o: Record<string, string>) => '?' + new URLSearchParams(o).toString()
const fillUrl = (r: any) => qs({ tab: 'fill', task: t.value, room: r.room, rack: r.rack })
const diffUrl = (r: any, dk: 'gone' | 'bad' | 'extra' | 'open') => qs(rackDiffQuery(t.value, r.room, r.rack, dk))
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="bar">
      <button type="button" class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ loaded ? all.length : '—' }}</button>
      <button type="button" class="chip" :class="{ on: filter === 'done' }" @click="filter = 'done'">已盤 {{ loaded ? nDone : '—' }}</button>
      <button type="button" class="chip" :class="{ on: filter === 'todo' }" @click="filter = 'todo'">未盤 {{ loaded ? all.length - nDone : '—' }}</button>
      <InfoNote>每一櫃這次盤了沒、結果幾台。點機櫃到「網頁填寫」並選好這一櫃；點不在／不符／沒登記／差異未處理，只列這一櫃那一類的差異。「—」＝這櫃還沒盤。</InfoNote>
    </div>
    <V2Table :rows="rows" prefix="R" export-name="各櫃進度" :empty="loaded ? '這次盤點沒有機櫃' : '載入中…'"
             :cols="[{ k: 'name', label: '機櫃', link: fillUrl }, { k: 'book_count', label: '帳上', mono: true }, { k: 'state', label: '狀態' },
                     { k: 'ok', label: '在', mono: true }, { k: 'gone', label: '不在', mono: true, link: (r: any) => (r.done && r.gone ? diffUrl(r, 'gone') : null) },
                     { k: 'bad', label: '不符', mono: true, link: (r: any) => (r.done && r.bad ? diffUrl(r, 'bad') : null) },
                     { k: 'extra', label: '沒登記', mono: true, link: (r: any) => (r.done && r.extra ? diffUrl(r, 'extra') : null) },
                     { k: 'open', label: '差異未處理', mono: true, link: (r: any) => (r.open ? diffUrl(r, 'open') : null) },
                     { k: 'by', label: '盤點人' }, { k: 'on', label: '日期', mono: true }, { k: 'ver', label: '版', mono: true }]" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 3px 10px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.err { color: var(--bad); font-size: 13px; }
</style>
