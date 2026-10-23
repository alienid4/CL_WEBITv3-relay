<script setup lang="ts">
// 4-10 新設備生命週期（v1.499.1）：每台停在哪一步、卡了多久。後端 device_lifecycle.py；
// 第 4、5 步直接讀存在可信度與納管漏斗（2-3），不另算一套。篩選後的計數由同一批列在畫面上數，加總＝下鑽。
const { apiFetch } = useApi()
const { exportTable, busy: exporting } = useTableExport()
const data = ref<any>(null)
const err = ref('')
try {
  data.value = await apiFetch('/api/lifecycle')
} catch (e: any) {
  err.value = e?.data?.detail ?? e?.message ?? '載入失敗'
}

const fRoom = ref('')
const fSys = ref('')
const fUnit = ref('')
const fStage = ref('')
const q = ref('')
const allRows = computed(() => (data.value?.rows ?? []).map((r: any, i: number) => ({ ...r, no: `L${i + 1}`, _i: i })))
const rooms = computed(() => [...new Set(allRows.value.map((r: any) => r.room))].sort())
const systems = computed(() => [...new Set(allRows.value.flatMap((r: any) => r.api_ids))].sort())
const units = computed(() => [...new Set(allRows.value.map((r: any) => r.unit))].sort())
// 先套房間／系統／單位篩選 → 數各階段（卡片）；再套階段＋搜尋 → 表格
const scoped = computed(() => allRows.value.filter((r: any) =>
  (!fRoom.value || r.room === fRoom.value) && (!fSys.value || r.api_ids.includes(fSys.value))
  && (!fUnit.value || r.unit === fUnit.value)))
const counts = computed(() => {
  const c: Record<string, number> = {}
  for (const k of data.value?.stage_order ?? []) c[k] = 0
  for (const r of scoped.value) c[r.stage]++
  return c
})
const steps = computed(() => (data.value?.steps ?? []) as { key: string; label: string }[])
const reached = computed(() => {
  const order = steps.value.map((s) => s.key)
  const out: Record<string, number> = {}
  for (const [i, k] of order.entries()) {
    out[k] = order.slice(i + 1).reduce((a, x) => a + (counts.value[x] || 0), 0) + (counts.value.done || 0)
  }
  return out
})
const activeTotal = computed(() => steps.value.reduce((a, s) => a + (counts.value[s.key] || 0), 0) + (counts.value.done || 0))
const shown = computed(() => {
  const s = q.value.trim().toLowerCase()
  return scoped.value.filter((r: any) => (!fStage.value || r.stage === fStage.value)
    && (!s || JSON.stringify(r).toLowerCase().includes(s)))
})
const { sortKey, sortDir, toggle, sorted } = useSort(shown, '')
const COLS: [string, string][] = [['hostname', '主機名'], ['ip', 'IP'], ['stage_label', '階段'], ['stuck_days', '卡了（天）'],
  ['stuck_since', '起算時間'], ['stuck_since_src', '起算依據'], ['steps_done', '完成步數'], ['room', '機房'],
  ['systems', '系統'], ['unit', '使用單位']]
function doExport() {
  exportTable(`新設備生命週期${fStage.value ? '_' + (data.value.stage_labels[fStage.value] || fStage.value) : ''}`,
    ['項次', ...COLS.map((c) => c[1])], sorted.value.map((r: any) => [r.no, ...COLS.map((c) => r[c[0]] ?? '')]))
}
function pick(stage: string) {
  fStage.value = fStage.value === stage ? '' : stage
}
useHead({ title: '新設備生命週期' })
</script>

<template>
  <div class="wrap">
    <div class="ey">WAR ROOM · 4-10</div>
    <h1>新設備生命週期
      <InfoNote>
        每台（同一台多筆登記只算一次）依序五步，停在第一個還沒做到的那一步：已登記 CIA → 有機櫃位置（VM 不適用直接過）→
        網路掃得到（掃描證據，或收集帳號連上過）→ 已納管（收集帳號 7 天內連得上）→ 收集正常（納管漏斗落在「資料齊全」或「已收集（設備）」）。<br>
        退役、報廢、非納管設備（漏斗的已豁免）是出口，不算卡住。<br>
        卡了幾天＝從上一步最早做到的時間算起：登記（資產庫建立／第一次匯入）、第一次掃到、第一次納管成功；機櫃位置沒有時間紀錄，卡在掃描那步從登記算。
        起算依據欄寫明用的是哪一個。
      </InfoNote>
    </h1>
    <p v-if="err" class="bad">載入失敗：{{ err }}</p>
    <template v-if="data">
      <div class="bar">
        <select v-model="fRoom"><option value="">全部機房</option><option v-for="r in rooms" :key="r" :value="r">{{ r }}</option></select>
        <select v-model="fSys"><option value="">全部系統</option><option v-for="s in systems" :key="s" :value="s">{{ s }}</option></select>
        <select v-model="fUnit"><option value="">全部使用單位</option><option v-for="u in units" :key="u" :value="u">{{ u }}</option></select>
        <span class="muted small">{{ scoped.length }} 台（進行中 {{ activeTotal }}）</span>
      </div>

      <div class="funnel">
        <template v-for="(s, i) in steps" :key="s.key">
          <button class="step" :class="{ on: fStage === s.key }" @click="pick(s.key)">
            <span class="sn">{{ i + 1 }}. {{ s.label }}</span>
            <span class="n">{{ counts[s.key] }}</span><span class="l">卡在這步（台）</span>
            <span class="r">已過 {{ reached[s.key] }} 台</span>
          </button>
        </template>
        <button class="step ok" :class="{ on: fStage === 'done' }" @click="pick('done')">
          <span class="sn">完成</span><span class="n">{{ counts.done }}</span><span class="l">五步都完成（台）</span>
        </button>
      </div>
      <div class="exits">
        <button v-for="e in data.exits" :key="e.key" class="chip" :class="{ on: fStage === e.key }" @click="pick(e.key)">
          <b>{{ counts[e.key] }}</b> {{ e.label }}
        </button>
      </div>

      <div class="bar">
        <input v-model="q" class="kw" placeholder="搜項次、主機名、IP…">
        <span class="muted small">{{ sorted.length }} 台</span>
        <button class="btn" :disabled="exporting || !sorted.length" @click="doExport">匯出 Excel</button>
      </div>
      <div class="tbl-wrap"><table>
        <thead><tr>
          <SortTh k="_i" :active="sortKey" :dir="sortDir" @sort="toggle">項次</SortTh>
          <SortTh v-for="c in COLS" :key="c[0]" :k="c[0]" :active="sortKey" :dir="sortDir" @sort="toggle">{{ c[1] }}</SortTh>
        </tr></thead>
        <tbody>
          <tr v-if="!sorted.length"><td :colspan="COLS.length + 1" class="muted">沒有</td></tr>
          <tr v-for="r in sorted" :key="r.no">
            <td class="mono">{{ r.no }}</td>
            <td><NuxtLink :to="`/assets/${encodeURIComponent(r.serial)}`">{{ r.hostname || r.serial }}</NuxtLink></td>
            <td class="mono">{{ r.ip || '—' }}</td>
            <td>{{ r.stage_label }}</td>
            <td class="num mono">{{ r.stuck_days ?? '—' }}</td>
            <td class="mono">{{ r.stuck_since || '—' }}</td>
            <td>{{ r.stuck_since_src || '—' }}</td>
            <td class="num">{{ r.steps_done }}／5</td>
            <td>{{ r.room }}</td><td>{{ r.systems || '—' }}</td><td>{{ r.unit }}</td>
          </tr>
        </tbody>
      </table></div>
    </template>
  </div>
</template>

<style scoped>
.wrap { padding: 16px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 12px; color: var(--ink); }
.bad { color: var(--bad); }
.funnel { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 8px 0; }
.step { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px; font: inherit; color: inherit;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); cursor: pointer; text-align: left; }
.step.on { outline: 2px solid var(--brand); }
.step .sn { font-size: 12px; color: var(--ink-aux); font-weight: 600; }
.step .n { font-size: 22px; font-weight: 700; font-family: var(--disp); color: var(--warn-text); }
.step.ok .n { color: var(--brand); }
.step .l, .step .r { font-size: 11.5px; color: var(--muted); }
.exits { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.chip { font: inherit; font-size: 12.5px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; color: var(--ink-aux); }
.chip.on { border-color: var(--brand); background: var(--mint); color: var(--brand-dark); }
.bar { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.bar select, .kw { padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; background: var(--card); color: var(--ink); }
.kw { min-width: 200px; }
.btn { font: inherit; font-size: 12.5px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.btn:disabled { opacity: .5; }
.tbl-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
td a { color: var(--link); }
.num { text-align: right; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
.small { font-size: 12px; }
</style>
