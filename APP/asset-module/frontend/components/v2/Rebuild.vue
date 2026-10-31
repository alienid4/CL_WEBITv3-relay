<script setup lang="ts">
// 系統重建資料（精簡版，2026-10-27 使用者：「這部分可以再縮」）：一組一行摘要、可收合；組內一項一列（約 32px），
// 狀態＝小圖示＋短字（✓ 已收集／— 無此檔／! 缺），「必要」只在必要項缺的時候出現；看內容＝列下方展開全文。
// 有問題（必要項缺）的組預設打開，其餘收起來。
interface Item { no: number; group: string; id: string; title: string; necessity: string; state: string; status: 'ok' | 'none' | 'bad'; problem: boolean; note: string | null; content: string }
const props = defineProps<{ rows: Item[]; expandAll?: boolean }>()
const { showToast } = useToast()
const onlyBad = ref(false)
const openG = ref<Record<string, boolean>>({})
const openI = ref<Record<number, boolean>>({})
const groups = computed(() => {
  const out: { group: string; items: Item[]; ok: number; none: number; bad: number; problem: number }[] = []
  props.rows.forEach((r, i) => {
    let g = out.find(x => x.group === r.group)
    if (!g) { g = { group: r.group, items: [], ok: 0, none: 0, bad: 0, problem: 0 }; out.push(g) }
    g.items.push({ ...r, no: i + 1 })
    g[r.status] += 1
    if (r.problem) g.problem += 1
  })
  return out
})
function initOpen() {
  const o: Record<string, boolean> = {}
  for (const g of groups.value) o[g.group] = !!props.expandAll || g.problem > 0
  openG.value = o
  openI.value = {}
  if (props.expandAll) for (const r of props.rows) openI.value[props.rows.indexOf(r) + 1] = true
}
watch(() => [props.rows, props.expandAll], initOpen, { immediate: true })
const allOpen = computed(() => groups.value.every(g => openG.value[g.group]))
function toggleAll() { const v = !allOpen.value; for (const g of groups.value) openG.value[g.group] = v }
const shown = (g: { items: Item[] }) => (onlyBad.value ? g.items.filter(i => i.status === 'bad') : g.items)
const ICON = { ok: '✓', none: '—', bad: '!' }
const WORD = { ok: '已收集', none: '無此檔', bad: '缺' }
function summary(g: { items: Item[]; ok: number; none: number; bad: number; problem: number }) {
  const parts = [`${g.items.length} 項`]
  if (g.ok) parts.push(`${g.ok} 已收集`)
  if (g.none) parts.push(`${g.none} 無此檔`)
  if (g.bad) parts.push(`${g.bad} 缺${g.problem ? `（必要 ${g.problem}）` : ''}`)
  return parts.join('・')
}
async function copy(it: Item) {
  try { await navigator.clipboard.writeText(it.content || ''); showToast(`已複製：${it.title}`, 'success') } catch { showToast('瀏覽器不讓複製，請按「看內容」手動選取', 'warn') }
}
const badTotal = computed(() => props.rows.filter(r => r.status === 'bad').length)
</script>

<template>
  <div class="rb">
    <div class="top">
      <button type="button" class="tb" @click="toggleAll">{{ allOpen ? '全部收合' : '全部展開' }}</button>
      <label class="chk"><input v-model="onlyBad" type="checkbox"> 只看有問題的（{{ badTotal }}）</label>
    </div>
    <div v-for="g in groups" :key="g.group" class="g" :class="{ prob: g.problem }">
      <button type="button" class="gh" @click="openG[g.group] = !openG[g.group]">
        <span class="car">{{ openG[g.group] ? '▾' : '▸' }}</span><b>{{ g.group }}</b><span class="sum">　{{ summary(g) }}</span>
      </button>
      <table v-if="openG[g.group] && shown(g).length" class="t">
        <tbody>
          <template v-for="it in shown(g)" :key="it.no">
            <tr>
              <td class="no">B{{ it.no }}</td>
              <td class="ti">{{ it.title }}</td>
              <td class="st" :class="it.status" :title="it.state"><span class="ic">{{ ICON[it.status] }}</span> {{ WORD[it.status] }}<span v-if="it.problem" class="req">必要</span></td>
              <td class="ac">
                <button type="button" class="lb" @click="openI[it.no] = !openI[it.no]">{{ openI[it.no] ? '收起' : '看內容' }}</button>
                <button type="button" class="lb" :disabled="!it.content" @click="copy(it)">複製</button>
              </td>
            </tr>
            <tr v-if="openI[it.no]" class="exp">
              <td colspan="4">
                <div v-if="it.status !== 'ok' || it.note" class="why">{{ it.state }}{{ it.note ? `・${it.note}` : '' }}</div>
                <pre>{{ it.content || '（沒有內容）' }}</pre>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-else-if="openG[g.group]" class="muted">這組沒有問題</p>
    </div>
  </div>
</template>

<style scoped>
.rb { display: flex; flex-direction: column; gap: 4px; }
.top { display: flex; gap: 12px; align-items: center; margin-bottom: 4px; font-size: 13px; }
.tb { border: 1px solid var(--border-strong); background: var(--card); border-radius: 8px; padding: 3px 10px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chk { display: inline-flex; gap: 4px; align-items: center; color: var(--ink-soft); }
.g { border-bottom: 1px solid var(--line); }
.gh { width: 100%; text-align: left; background: none; border: 0; padding: 6px 2px; font-family: inherit; font-size: 14px; cursor: pointer; color: var(--ink); }
.g.prob .gh b { color: var(--st-missing); }
.car { display: inline-block; width: 16px; color: var(--ink-aux); }
.sum { font-size: 13px; color: var(--ink-aux); }
.t { width: 100%; border-collapse: collapse; font-size: 13px; margin: 0 0 6px 16px; width: calc(100% - 16px); }
.t td { height: 32px; padding: 0 8px; vertical-align: middle; border: 0; }
.t tr:not(.exp):hover td { background: var(--paper); }
.no { width: 52px; font-family: var(--disp); color: var(--ink-aux); }
.st { width: 130px; white-space: nowrap; }
.st.ok { color: var(--st-online); } .st.none { color: var(--ink-aux); } .st.bad { color: var(--st-missing); }
.ic { font-weight: 700; display: inline-block; width: 12px; text-align: center; }
.req { margin-left: 6px; font-size: 11px; padding: 0 5px; border-radius: 5px; background: var(--st-missing-bg); color: var(--st-missing); }
.ac { width: 120px; white-space: nowrap; text-align: right; }
.lb { background: none; border: 0; padding: 0 0 0 10px; font: inherit; color: var(--link); cursor: pointer; }
.lb:disabled { color: var(--muted); cursor: default; }
.exp td { height: auto; padding: 0 8px 8px 60px; }
.why { font-size: 12px; color: var(--st-missing); margin-bottom: 4px; }
pre { white-space: pre-wrap; word-break: break-all; font-family: var(--disp), monospace; font-size: 12px; background: var(--paper); border-radius: 6px; padding: 8px 10px; max-height: 320px; overflow: auto; margin: 0; }
.muted { color: var(--muted); font-size: 13px; margin: 0 0 6px 18px; }
@media print { .top, .ac { display: none; } pre { max-height: none; } }
</style>
