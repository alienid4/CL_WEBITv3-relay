<script setup lang="ts">
// 主機履歷概要的「收集摘要」（codex 207）：每一類收集最後一次成功的時間、距今天數、是否過期。
// 只讀 GET /api/v2/machine/collect-summary（不連目標機、不寫庫）。門檻在系統設定 collect_stale_days（天，預設 8）。
// 誠實規則：從未成功＝「未取得」，不是 0 也不是正常；讀取失敗＝寫出原因；VMware 設備不由本系統收集＝不適用。
import { STATE_LABEL, STATE_TONE, countsOf, daysText, filterRows } from '~/composables/collectSummary'
import type { CollectData, CollectRow, CollectState } from '~/composables/collectSummary'

const props = defineProps<{ mkey: string }>()
const { apiFetch } = useApi()
const data = ref<CollectData | null>(null)
const failed = ref('')
const filter = ref<CollectState | 'all'>('all')
let seq = 0     // 切換主機時，較舊的回應不得蓋掉較新的畫面
async function load() {
  const mine = ++seq
  data.value = null; failed.value = ''; filter.value = 'all'
  try {
    const r = await apiFetch<CollectData>('/api/v2/machine/collect-summary', { query: { key: props.mkey } })
    if (mine === seq) data.value = r
  } catch (e: any) {
    if (mine === seq) failed.value = e?.data?.detail ?? e?.message ?? '讀取失敗'
  }
}
onMounted(load)
watch(() => props.mkey, load)

const counts = computed(() => countsOf(data.value?.rows ?? []))
const shown = computed(() => filterRows(data.value?.rows ?? [], filter.value))
const tiles: [CollectState, string][] = [['fresh', STATE_LABEL.fresh], ['stale', STATE_LABEL.stale], ['never', STATE_LABEL.never]]
function pick(s: CollectState) { filter.value = filter.value === s ? 'all' : s }
const cols = [
  { k: 'label', label: '收集類別' },
  { k: 'last', label: '最後成功時間', mono: true, text: (r: CollectRow) => r.last || '未取得' },
  { k: 'days', label: '距今', text: (r: CollectRow) => daysText(r.days) },
  { k: 'state_label', label: '狀態', tone: (r: CollectRow) => STATE_TONE[r.state] },
]
</script>

<template>
  <section class="cs">
    <h3>收集摘要<InfoNote>每一類收集「最後一次成功收到資料」的時間。超過 {{ data?.stale_days ?? 8 }} 天標「資料已過期」，從未成功標「未取得」。門檻可在系統設定（{{ data?.setting_key || 'collect_stale_days' }}）調整，預設 8 天。健康檢查只計連得上的那次。</InfoNote></h3>
    <p v-if="failed" class="err">收集摘要讀取失敗：{{ failed }}</p>
    <p v-else-if="!data" class="muted">讀取中…</p>
    <p v-else-if="!data.applicable" class="muted">不適用：{{ data.reason }}</p>
    <template v-else>
      <div class="tiles">
        <button v-for="[s, lb] in tiles" :key="s" type="button" class="tile" :class="[s, { on: filter === s }]" @click="pick(s)">{{ lb }} <b>{{ counts[s] }}</b></button>
      </div>
      <V2Table :rows="shown" :cols="cols" prefix="S" empty="這個狀態沒有項目" :max="20" />
    </template>
  </section>
</template>

<style scoped>
.cs { margin-top: 16px; }
.cs h3 { margin: 0 0 8px; font-size: 15px; }
.tiles { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.tile { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.tile.on { border-color: var(--brand); background: var(--mint); }
.tile.stale b { color: var(--warn-text); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
</style>
