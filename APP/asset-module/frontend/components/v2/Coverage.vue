<script setup lang="ts">
// 頁首資料覆蓋率（同步碼 ESJE 第 2 點，2026-11-12）：「只涵蓋已納管主機」類頁面共用。
// 後端單一判定 data_coverage.coverage（/api/v2/coverage）：每台只落一格，格子數字＝點進去的清單（同一份計算）。
// 未知不算成 0 或正常：沒納管、不支援、收集失敗、過期都各自一格，點了看是哪幾台、原因、來源、更新時間。
const props = defineProps<{ kind: string }>()
const { apiFetch } = useApi()
const sum = ref<any>(null)
const err = ref('')
const pick = ref('')
const rows = ref<any[] | null>(null)
const dErr = ref('')
function why(e: any, what: string) {
  const s = e?.statusCode ?? e?.status ?? e?.response?.status
  if (s === 401 || s === 403) return `${what}：權限不足，無法查看`
  return `${what}載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
}
async function load() {
  err.value = ''
  try { sum.value = await apiFetch<any>('/api/v2/coverage', { query: { kind: props.kind } }) } catch (e: any) { err.value = why(e, '資料覆蓋率') }
}
async function drill(st: string) {
  if (pick.value === st) { pick.value = ''; rows.value = null; return }
  pick.value = st
  rows.value = null
  dErr.value = ''
  try { rows.value = (await apiFetch<any>('/api/v2/coverage', { query: { kind: props.kind, state: st } })).rows } catch (e: any) { dErr.value = why(e, '覆蓋率明細') }
}
onMounted(load)
const TONE: Record<string, string> = { ok: 'good', stale: 'warn', pending: 'warn', failed: 'bad', unmanaged: 'bad', unsupported: 'mute', excluded: 'mute' }
const chips = computed(() => {
  const s = sum.value
  if (!s) return []
  return [...s.in_scope_states, 'unsupported', 'excluded'].map((k: string) => ({ k, label: s.state_labels[k], n: s.counts[k] ?? 0, tone: TONE[k] }))
})
const pickLabel = computed(() => (pick.value === 'scope' ? '適用範圍全部' : pick.value === 'out' ? '不在範圍' : sum.value?.state_labels?.[pick.value] ?? ''))
const COLS = [
  { k: 'name', label: '主機', link: (r: any) => ((r.key || r.serial) ? `/v2/machine/${encodeURIComponent(r.key || r.serial)}` : null) },
  { k: 'ip', label: 'IP', mono: true }, { k: 'family', label: '作業系統大類' }, { k: 'state_label', label: '狀態' },
  { k: 'reason', label: '原因' }, { k: 'source', label: '資料來源' }, { k: 'updated_at', label: '最後更新', mono: true },
]
</script>

<template>
  <div class="cov" data-test="coverage">
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!sum" class="muted">資料覆蓋率載入中…</p>
    <template v-else>
      <div class="line">
        <b class="pct">資料覆蓋率 {{ sum.pct === null ? '—' : `${sum.pct}%` }}</b>
        <span class="muted">有效 <a href="#" @click.prevent="drill('ok')">{{ sum.valid.toLocaleString() }}</a>／適用
          <a href="#" data-test="cov-scope" @click.prevent="drill('scope')">{{ sum.scope_total.toLocaleString() }}</a> 台</span>
        <button v-for="c in chips" :key="c.k" type="button" class="chip" :class="[c.tone, { on: pick === c.k }]" :data-test="`cov-${c.k}`" @click="drill(c.k)">
          {{ c.label }} {{ c.n.toLocaleString() }}</button>
        <InfoNote>{{ sum.label }}：{{ sum.formula }}。資料來源：{{ sum.source }}。過期門檻 {{ sum.stale_days }} 天。每台只算一格：有資料（門檻內）／資料過期／收集失敗（連不上、指令全失敗＝多半權限不足、已納管但失聯、匯入失敗或截斷）／已納管尚無資料／未納管；不支援（平台不在 {{ sum.platforms.join('／') }}，VMware 設備依規定一律不收）與排除不計入分母。台數一台一列，與 2.1 主機查詢同一份分組；計算時間 {{ sum.generated_at }}。</InfoNote>
      </div>
      <div v-if="pick" class="drill">
        <div class="line"><b>{{ pickLabel }}</b><button type="button" class="chip mute" @click="drill(pick)">收起 ✕</button></div>
        <p v-if="dErr" class="err">{{ dErr }}</p>
        <p v-else-if="!rows" class="muted">載入中…</p>
        <V2Table v-else :rows="rows" prefix="C" :max="50" :cols="COLS" :export-name="`資料覆蓋率_${kind}_${pick}`" empty="這一格沒有主機" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.cov { margin-bottom: 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pct { font-family: var(--disp); }
.muted { color: var(--muted); }
.err { color: var(--bad); margin: 0; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 2px 10px; font-size: 12px; cursor: pointer; }
.chip.good { border-color: var(--good); background: var(--good-soft); }
.chip.warn { border-color: var(--warn); background: var(--warn-soft); }
.chip.bad { border-color: var(--bad); background: var(--bad-soft); }
.chip.mute { color: var(--ink-soft); }
.chip.on { outline: 2px solid var(--brand); }
.drill { margin-top: 8px; }
</style>
