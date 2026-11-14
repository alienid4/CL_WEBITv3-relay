<script setup lang="ts">
// 主機履歷頁籤頂端「這台的狀態與資料時間」（2026-11-12，ESJE 定案第 2 點）。
// 判定＝後端 data_coverage.for_host（跟清單頁頁首 V2Coverage 同一把尺）：有資料／資料過期／收集失敗／已納管尚無資料／未納管／不支援／排除。
// 一個頁籤可能有兩類資料（軟體與服務＝軟體＋服務），一類一行。查不到不寫 0、不寫正常。
const props = defineProps<{ st: any[] | null | undefined }>()
const TONE: Record<string, string> = { ok: 'ok', stale: 'stale', pending: 'stale', failed: 'bad', unmanaged: 'bad', unsupported: 'na', excluded: 'na' }
const list = computed(() => (Array.isArray(props.st) ? props.st : []))
</script>

<template>
  <div v-if="list.length" class="dsw">
    <p v-for="s in list" :key="s.kind" class="ds" data-testid="data-state" :data-state="s.state">
      <span class="what">{{ s.label }}</span>
      <span class="chip" :class="TONE[s.state]">{{ s.state_label }}</span>
      <span v-if="s.updated_at" class="mono">最後更新 {{ s.updated_at }}</span>
      <span v-else class="muted">沒有資料時間</span>
      <span v-if="s.state !== 'ok'" class="why">{{ s.reason }}</span>
      <InfoNote>來源：{{ s.source }}。超過 {{ s.stale_days }} 天沒更新算「資料過期」；{{ s.reason }}</InfoNote>
    </p>
  </div>
</template>

<style scoped>
.dsw { margin: 0 0 10px; }
.ds { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 0 0 4px; font-size: 13px; color: var(--ink-soft); }
.what { color: var(--ink); }
.chip { padding: 2px 10px; border-radius: 999px; font-size: 12.5px; border: 1px solid var(--border); }
.chip.ok { background: var(--mint); color: var(--brand-dark); border-color: var(--brand); }
.chip.stale { background: var(--st-stale-bg); color: var(--st-stale); }
.chip.bad { background: var(--bad-soft); color: var(--bad); }
.chip.na { color: var(--ink-aux); }
.mono { font-family: var(--disp); }
.why, .muted { color: var(--ink-aux); }
</style>
